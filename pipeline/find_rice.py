"""Поиск настоящего рисового массива: Красноармейский район, окрестности ст. Полтавской.

Признак риса — на уровне пикселя, все три условия одновременно:
  1) вода в мае-июне:  NDWI > 0  либо  LSWI > NDVI  (чек под слоем воды);
  2) NDVI > 0,55 в июле-августе — сомкнутый полог риса;
  3) NDWI <= 0 в сентябре — чек сброшен перед уборкой.

Пруд удовлетворяет условию 1, но не проходит 2 и 3. Озимые проходят 3, но не 1.
Поэтому совпадение всех трёх условий и есть рис, а не «вода, похожая на рис».

Скрипт делает грубый просмотр района (120 м/пиксель) на трёх датах сезона, считает
маску риса, скользящим окном ищет участок с наибольшей площадью риса и записывает
его границы в data/aoi.json — дальше по этому полигону работают fetch_sentinel.py
и build_checks.py.

Выход: data/aoi.json, подробный журнал в stdout (уходит в data/_log/find_rice.txt).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.env import Env
from rasterio.transform import Affine, from_bounds
from rasterio.warp import reproject
from scipy import ndimage as ndi

from common import DATA, ROOT, log, write_json

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MAX_RETRY="6",
    GDAL_HTTP_TIMEOUT="180",
    GDAL_HTTP_MULTIPLEX="YES",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="200000000",
    AWS_NO_SIGN_REQUEST="YES",
)

# Область поиска: Красноармейский район Краснодарского края, окрестности ст. Полтавской
# (45.373 с.ш., 38.172 в.д.). Рамка ~39 x 36 км лежит внутри границ района.
POLTAVSKAYA = (45.3733, 38.1725)
SEARCH_BBOX = [37.920, 45.210, 38.420, 45.530]  # minx, miny, maxx, maxy
COARSE_M = 60.0   # шаг грубой сетки просмотра (чек ~500 м — это 8 пикселей)
AOI_HALF_LON = 0.0750  # ±5.87 км по долготе
AOI_HALF_LAT = 0.0520  # ±5.75 км по широте  => окно ~11.7 x 11.5 км, ~13 500 га

# Три окна сезона: затопление, пик вегетации, сброс воды
SLOTS = [
    ("flood", "2025-05-01", "2025-06-25"),
    ("veg", "2025-07-20", "2025-08-25"),
    ("dry", "2025-09-12", "2025-10-15"),
]
# На дату смотрит не облачность, а сам признак: для затопления — максимум воды,
# для полога — максимум сомкнутой зелени, для сброса — максимум сухой поверхности.
CANDIDATES_PER_SLOT = 6

MAX_TILES = 4
CLOUD_MAX = 0.35
NODATA_MAX = 60.0
NDVI_VEG_MIN = 0.55


def stac_search(start: str, end: str, limit: int = 80) -> list[dict]:
    params = {
        "collections": COLLECTION,
        "bbox": ",".join(f"{v:.5f}" for v in SEARCH_BBOX),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "limit": limit,
    }
    r = requests.get(f"{STAC}/search", params=params, timeout=180)
    if r.status_code != 200:
        raise SystemExit(f"STAC {start}..{end}: HTTP {r.status_code} {r.text[:200]}")
    return r.json().get("features", [])


def cloud_of(f: dict) -> float:
    return float(f.get("properties", {}).get("eo:cloud_cover") or 1.0)


def tile_of(f: dict) -> str:
    return f.get("properties", {}).get("grid:code") or f["id"].split("_")[1]


def candidate_dates(features: list[dict], limit: int = CANDIDATES_PER_SLOT) -> list[tuple[str, list[dict]]]:
    """Кандидаты на дату окна: по одной группе сцен на сутки, сшивка тайлов внутри суток.

    Дату выбирает не облачность, а сам признак окна (см. main), поэтому кандидатов
    несколько и все они просматриваются на грубой сетке.
    """
    by_date: dict[str, list[dict]] = {}
    for f in features:
        p = f.get("properties", {})
        cloud = p.get("eo:cloud_cover")
        nodata = p.get("s2:nodata_pixel_percentage")
        if cloud is None or nodata is None:
            continue
        if cloud > CLOUD_MAX or nodata > NODATA_MAX:
            continue
        date = p["datetime"][:10]
        by_date.setdefault(date, []).append(f)
    groups = []
    for date, items in by_date.items():
        by_tile: dict[str, dict] = {}
        for f in items:
            t = tile_of(f)
            cur = by_tile.get(t)
            if cur is None or (cloud_of(f), f["properties"]["s2:nodata_pixel_percentage"]) < \
                    (cloud_of(cur), cur["properties"]["s2:nodata_pixel_percentage"]):
                by_tile[t] = f
        groups.append((date, sorted(by_tile.values(), key=cloud_of)[:MAX_TILES]))
    groups.sort(key=lambda g: min(cloud_of(i) for i in g[1]))
    return groups[:limit]


def read_coarse(href: str, bbox: list[float], width: int, height: int, transform,
                resampling: Resampling) -> np.ndarray:
    """Оконное чтение канала COG на грубую сетку района поиска (EPSG:4326).

    Читается не весь канал, а его окно с прореживанием до грубой сетки: сначала
    считается окно в пикселях источника, затем аффинное преобразование этого окна
    масштабируется под out_shape — так перепроецирование остаётся корректным.
    """
    from rasterio.windows import from_bounds as win_from_bounds
    from pyproj import Transformer

    with rasterio.open(f"/vsicurl/{href}") as src:
        to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        w, s, e, n = bbox
        x0, y0 = to_src.transform(w, s)
        x1, y1 = to_src.transform(e, n)
        win = win_from_bounds(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), src.transform)
        win_w, win_h = max(int(round(win.width)), 1), max(int(round(win.height)), 1)
        arr = src.read(
            1,
            window=win,
            out_shape=(height, width),
            boundless=True,
            fill_value=0,
            resampling=resampling,
        ).astype("float32")
        decimated = src.window_transform(win) * Affine.scale(win_w / float(width), win_h / float(height))
        out = np.full((height, width), np.nan, dtype="float32")
        reproject(
            source=arr,
            destination=out,
            src_transform=decimated,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=CRS.from_epsg(4326),
            resampling=resampling,
            src_nodata=0,
            dst_nodata=np.nan,
        )
        return out


def read_merged(items: list[dict], band: str, bbox: list[float], width: int, height: int,
                transform, resampling: Resampling) -> np.ndarray:
    """Канал, сшитый из сцен соседних тайлов: район поиска попадает на их стык."""
    out = np.full((height, width), np.nan, dtype="float32")
    for it in items:
        try:
            arr = read_coarse(it["assets"][band]["href"], bbox, width, height, transform, resampling)
        except Exception as exc:  # noqa: BLE001
            log(f"  чтение {band} {it['id']}: {type(exc).__name__}")
            continue
        out = np.where(np.isfinite(out), out, arr)
    return out


def ascii_map(mask: np.ndarray, cols: int = 74) -> list[str]:
    """Карта маски символами: журнал раннера всё равно текстовый."""
    h, w = mask.shape
    rows = max(1, int(round(cols * h / w * 0.5)))
    ys = np.linspace(0, h, rows + 1).astype(int)
    xs = np.linspace(0, w, cols + 1).astype(int)
    shades = " .:-=+*#%@"
    lines = []
    for i in range(rows):
        line = ""
        for j in range(cols):
            block = mask[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
            if block.size == 0:
                line += " "
                continue
            good = block[block >= 0]
            if good.size < block.size * 0.5:
                line += "~"  # снимка нет: облака или стык тайлов
                continue
            v = float(good.mean())
            line += shades[min(len(shades) - 1, int(v * len(shades) / 0.6))]
        lines.append(line)
    return lines


def main() -> None:
    w, s, e, n = SEARCH_BBOX
    lat_c = (s + n) / 2.0
    dlon = COARSE_M / (111_320.0 * math.cos(math.radians(lat_c)))
    dlat = COARSE_M / 110_574.0
    width = int(round((e - w) / dlon))
    height = int(round((n - s) / dlat))
    transform = from_bounds(w, s, e, n, width, height)
    log(f"район поиска: {w}..{e} в.д., {s}..{n} с.ш. — {width} x {height} пикселей по {COARSE_M:.0f} м")
    log(f"окно массива: {2 * AOI_HALF_LON:.4f}° x {2 * AOI_HALF_LAT:.4f}° "
        f"(~{2 * AOI_HALF_LON * 111320 * math.cos(math.radians(lat_c)) / 1000:.1f} x "
        f"{2 * AOI_HALF_LAT * 110.574:.1f} км)")

    bands_of = {
        "flood": ("red", "green", "nir", "swir16", "scl"),
        "veg": ("red", "nir", "scl"),
        "dry": ("green", "nir", "scl"),
    }

    def indices(bands: dict) -> dict:
        out: dict = {}
        scl = bands.get("scl")
        if scl is None:
            good = np.ones((height, width), dtype=bool)
        else:
            good = ~(np.isin(scl, [0, 1, 3, 8, 9, 10]) | np.isnan(scl))
        g, r, n, s16 = bands.get("green"), bands.get("red"), bands.get("nir"), bands.get("swir16")
        with np.errstate(invalid="ignore", divide="ignore"):
            if g is not None and n is not None:
                ok = (g > 0) & (n > 0)
                out["ndwi"] = np.where(ok, (g - n) / (g + n), np.nan)
            if r is not None and n is not None:
                ok = (r > 0) & (n > 0)
                out["ndvi"] = np.where(ok, (n - r) / (n + r), np.nan)
            if s16 is not None and n is not None:
                ok = (s16 > 0) & (n > 0)
                out["lswi"] = np.where(ok, (n - s16) / (n + s16), np.nan)
        good = good & np.ones((height, width), dtype=bool)
        for key in list(out):
            out[key] = np.where(good, out[key], np.nan)
        out["good"] = good
        return out

    def metric(name: str, a: dict) -> np.ndarray:
        if name == "flood":
            return (a["ndwi"] > 0.0) | (a["lswi"] > a["ndvi"])
        if name == "veg":
            return a["ndvi"] > NDVI_VEG_MIN
        return a["ndwi"] <= 0.0

    per: dict[str, dict] = {}
    flood_items: list[dict] = []

    with Env(**GDAL_ENV):
        for name, start_md, end_md in SLOTS:
            feats = stac_search(start_md, end_md)
            groups = candidate_dates(feats)
            log(f"окно {name} ({start_md}..{end_md}): сцен {len(feats)}, дат-кандидатов {len(groups)}")
            if not groups:
                raise SystemExit(f"нет кондиционных сцен на окно {name} — поиск невозможен")
            best: dict | None = None
            for date, items in groups:
                bands = {
                    b: read_merged(items, b, SEARCH_BBOX, width, height, transform,
                                   Resampling.nearest if b == "scl" else Resampling.bilinear)
                    for b in bands_of[name]
                }
                arrs = indices(bands)
                score = float(np.nanmean(metric(name, arrs) & arrs["good"]))
                log(f"    дата {date} | тайлы {[tile_of(i) for i in items]} | облака "
                    f"{min(cloud_of(i) for i in items) * 100:.1f} % | годных {arrs['good'].mean() * 100:.1f} % "
                    f"| признак окна у {score * 100:.1f} % пикселей")
                if best is None or score > best["score"]:
                    best = {"score": score, "arrs": arrs, "date": date, "items": items}
            assert best is not None
            arrs = best["arrs"]
            arrs["date"] = best["date"]
            arrs["scene"] = best["items"][0]["id"]
            arrs["tiles"] = [tile_of(i) for i in best["items"]]
            arrs["cloud"] = round(min(cloud_of(i) for i in best["items"]) * 100, 2)
            arrs["score"] = best["score"]
            per[name] = arrs
            if name == "flood":
                flood_items = best["items"]
            log(f"    выбрана дата {best['date']} ({best['items'][0]['id']}) — признак у "
                f"{best['score'] * 100:.1f} % пикселей")

    f, v, d = per["flood"], per["veg"], per["dry"]
    common = f["good"] & v["good"] & d["good"]
    log(f"общих кондиционных пикселей на трёх датах: {common.mean() * 100:.1f} %")

    flooded = (f["ndwi"] > 0.0) | (f["lswi"] > f["ndvi"])
    green_peak = v["ndvi"] > NDVI_VEG_MIN
    drained = d["ndwi"] <= 0.0
    rice = common & flooded & green_peak & drained

    px_ha = (COARSE_M * COARSE_M) / 10_000.0
    total_valid = float(common.sum()) * px_ha
    log("")
    log("=== признак риса по району поиска ===")
    log(f"  затоплено в мае-июне ({f['date']}):     {np.nansum(common & flooded) * px_ha:9.0f} га "
        f"({np.nansum(common & flooded) / max(common.sum(), 1) * 100:4.1f} % от годных)")
    log(f"  сомкнутый полог в июле-августе ({v['date']}): {np.nansum(common & green_peak) * px_ha:9.0f} га "
        f"({np.nansum(common & green_peak) / max(common.sum(), 1) * 100:4.1f} %)")
    log(f"  сухо в сентябре ({d['date']}):         {np.nansum(common & drained) * px_ha:9.0f} га "
        f"({np.nansum(common & drained) / max(common.sum(), 1) * 100:4.1f} %)")
    log(f"  РИС (все три признака сразу):    {np.nansum(rice) * px_ha:9.0f} га "
        f"({np.nansum(rice) / max(common.sum(), 1) * 100:4.1f} % от годных, {total_valid:.0f} га годных)")
    log("")
    log("=== карта риса по району поиска (север сверху) ===")
    log("  '~' — снимка нет (облака, стык тайлов); пробел — рис не подтверждён")
    for line in ascii_map(np.where(common, rice.astype("float32"), -1.0)):
        log(f"|{line}|")

    # Скользящее окно: ищем участок с наибольшей площадью риса
    ww = int(round(2 * AOI_HALF_LON / dlon))
    wh = int(round(2 * AOI_HALF_LAT / dlat))
    rice_f = np.where(common, rice.astype("float32"), 0.0)
    valid_f = common.astype("float32")
    rice_sum = ndi.uniform_filter(rice_f, size=(wh, ww), mode="constant") * ww * wh
    valid_sum = ndi.uniform_filter(valid_f, size=(wh, ww), mode="constant") * ww * wh
    coverage = valid_sum / float(ww * wh)

    # окна с покрытием ниже 95 % не годятся: там будут дырки в наблюдениях
    score = np.where(coverage >= 0.95, rice_sum * px_ha, -1.0)
    order = np.argsort(score.ravel())[::-1]
    best: dict | None = None
    log("")
    log("=== лучшие окна (площадь риса в окне, га) ===")
    shown = 0
    for idx in order[:400]:
        cy, cx = np.unravel_index(idx, score.shape)
        if score[cy, cx] < 0:
            break
        # край окна: центр должен отстоять от границы района на пол-окна
        if cy - wh // 2 < 0 or cy + wh // 2 >= height or cx - ww // 2 < 0 or cx + ww // 2 >= width:
            continue
        clon, clat = transform * (cx, cy)
        bbox = [clon - AOI_HALF_LON, clat - AOI_HALF_LAT, clon + AOI_HALF_LON, clat + AOI_HALF_LAT]
        tiles = sorted({tile_of(it) for it in flood_items if _intersects(it, bbox)})
        shown += 1
        if shown <= 15:
            log(f"  {clat:.4f} с.ш. {clon:.4f} в.д. | рис {score[cy, cx]:7.0f} га | "
                f"покрытие {coverage[cy, cx] * 100:5.1f} % | тайлов {len(tiles)}: {tiles}")
        if best is None:
            best = {"lat": round(clat, 5), "lon": round(clon, 5), "rice_ha": round(float(score[cy, cx]), 1),
                    "coverage": round(float(coverage[cy, cx]), 4), "tiles": tiles}
        elif len(tiles) < len(best["tiles"]) and score[cy, cx] >= 0.90 * best["rice_ha"]:
            # один тайл вместо двух — ряд наблюдений будет полным на всех датах
            best = {"lat": round(clat, 5), "lon": round(clon, 5), "rice_ha": round(float(score[cy, cx]), 1),
                    "coverage": round(float(coverage[cy, cx]), 4), "tiles": tiles}
        if shown >= 400:
            break

    if best is None:
        raise SystemExit("ни одно окно не набрало годного покрытия — район не просматривается")

    clat, clon = best["lat"], best["lon"]
    bbox = [round(clon - AOI_HALF_LON, 5), round(clat - AOI_HALF_LAT, 5),
            round(clon + AOI_HALF_LON, 5), round(clat + AOI_HALF_LAT, 5)]
    log("")
    log(f"выбран участок: центр {clat} с.ш., {clon} в.д., тайлы {best['tiles']}, "
        f"рис по грубой оценке {best['rice_ha']} га")

    aoi = {
        "name": "Рисовый массив, Красноармейский район, ст. Полтавская",
        "lat": clat,
        "lon": clon,
        "bbox": bbox,
        "utm_epsg": 32637,
        "district": "Красноармейский район, Краснодарский край",
        "near": "ст. Полтавская",
        "picked_by": "pipeline/find_rice.py: максимум площади риса по трём признакам "
                     "(вода в мае-июне + NDVI > 0.55 в июле-августе + сухо в сентябре)",
        "key_dates": {k: per[k]["date"] for k in ("flood", "veg", "dry")},
        "coarse_rice_ha": best["rice_ha"],
        "coarse_coverage": best["coverage"],
        "tiles": best["tiles"],
        "search_bbox": SEARCH_BBOX,
        "slots": {k: {"date": per[k]["date"], "scene": per[k]["scene"],
                      "cloud_pct": per[k]["cloud"], "signal_pct": round(per[k]["score"] * 100, 2)}
                  for k in per},
    }
    write_json(DATA / "aoi.json", aoi)
    (ROOT / "web" / "public" / "data").mkdir(parents=True, exist_ok=True)
    write_json(ROOT / "web" / "public" / "data" / "aoi.json", aoi)
    log(f"записан полигон: {bbox}")


def _intersects(item: dict, bbox: list[float]) -> bool:
    ib = item.get("bbox")
    if not ib:
        return False
    return not (ib[2] < bbox[0] or ib[0] > bbox[2] or ib[3] < bbox[1] or ib[1] > bbox[3])


if __name__ == "__main__":
    main()
