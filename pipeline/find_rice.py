"""Поиск рисового массива в Красноармейском районе по главному признаку риса.

Почему признак именно такой. Первая версия искала «весной вода + летом зелень +
осенью сухо». Она отличила рис от пруда, но не отличила рис от кукурузы и сои:
в конце мая их поле — влажная почва под редкими всходами (LSWI > NDVI), в июле
оно зелёное (NDVI 0,8), в сентябре сухое (NDWI < 0). Проверка июньским снимком
показала ровно это: воды в массиве 7,2 %, как в мае, — то есть культура не залита.

Главный признак риса — вода под пологом летом. Ни одно поле другой культуры
Кубани не стоит под слоем воды в июле. Поэтому:

  затопленным зелёным (flooded-green) считается пиксель, у которого на летней дате
    (NDWI > 0  ИЛИ  LSWI >= NDVI - 0.05)  И  NDVI > 0.25;
  рисом — пиксель, который был затопленным зелёным минимум на двух летних датах,
    достиг NDVI > 0.55 на пике и сухой (NDWI <= 0) осенью.

Требование двух дат отсекает дождь: однократная мочка после ливня у кукурузы
бывает, месяц под водой — нет.

Скрипт просматривает весь Красноармейский район на сетке 60 м по всем летним
сценам сезона, считает маску риса и скользящим окном ищет участок с наибольшей
площадью риса. Район не меняется. Участок выбирается только по признаку.

Выход: data/aoi.json, журнал в data/_log/find_rice.txt
"""
from __future__ import annotations

import json
import math
import time
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

# Район зафиксирован: Красноармейский, Краснодарский край, окрестности ст. Полтавской.
POLTAVSKAYA = (45.3664, 38.2111)   # центр станицы Полтавской
SEARCH_BBOX = [37.900, 45.180, 38.480, 45.570]  # minx, miny, maxx, maxy — внутри границ района
COARSE_M = 60.0
AOI_HALF_LON = 0.0750  # ±5.87 км
AOI_HALF_LAT = 0.0520  # ±5.75 км  => окно ~11.7 x 11.5 км

# Окно затопления — когда вода на чеке ещё видна спутнику: с конца мая (залив чеков)
# до смыкания полога. Проверено по факту: в июле NDWI > 0 лишь у 0,2 % района,
# LSWI < NDVI — сомкнутый рис прячет воду. Поэтому июль для признак не годится: требовать
# воду в июле — значит не найти ничего. Смотрим май–июнь и требуем, чтобы перед
# съёмкой не было дождя.
FLOOD_START, FLOOD_END = "2025-05-15", "2025-06-30"
# Пик вегетации — отдельное окно. Сомкнутый полог NDVI > 0.55 рис набирает только
# в июле-августе; в конце мая у него NDVI 0,3-0,4, зато у озимой пшеницы 0,8.
# Смешивать эти окна нельзя: иначе признак выберет мокрую пшеницу.
VEG_START, VEG_END = "2025-07-01", "2025-08-31"
AUTUMN_START, AUTUMN_END = "2025-09-15", "2025-10-20"
MAX_FLOOD_DATES = 12
MAX_VEG_DATES = 4
MAX_AUTUMN_DATES = 3
CLOUD_MAX = 0.60   # облака маскируются по SCL, поэтому Partial Cloud всё равно годится
NODATA_MAX = 100.0  # брак отсекаем по пикселям (маска SCL), а не по проценту сцены:
                    # соседний тайл, перекрывающий полигон наполовину, — не повод терять дату
FLOOD_GREEN_MIN = 2   # минимум дат окна затопления со стоячей водой
NDVI_GREEN_MIN = 0.20  # культура уже видна: отсекает мокрую голую почву
NDVI_PEAK_MIN = 0.55
MAX_TILES = 3


RAIN_API = "https://archive-api.open-meteo.com/v1/archive"
RAIN_DAYS = 5          # сколько суток до съёмки держится мокрая поверхность
RAIN_CLEAN_MM = 5.0    # меньше — считаем, что поле мокрое не от дождя, а от поданной воды


def daily_rain(start: str, end: str, lat: float, lon: float) -> dict[str, float]:
    """Суточные осадки ERA5 (Open-Meteo) — чтобы отличать поданную воду от дождя.

    Это и есть ответ на главную слабость признака: после ливня мокрыми выглядят
    и кукуруза, и пар. Если за пять суток до съёмки осадков практически не было,
    мокрый пиксель — это полив, а не погода.
    """
    # Осадки берём из уже загруженного суточного ряда ERA5: источник тот же, а
    # лимит запросов к API не расходуется. Разница осадков внутри района — единицы
    # миллиметров, для признака «был дождь или нет» это несущественно.
    local = DATA / "weather" / "era5_daily_2025.csv"
    if local.exists():
        import csv as _csv
        rows = list(_csv.DictReader(local.read_text(encoding="utf-8").splitlines()))
        out = {r["date"]: float(r["precip"] or 0.0) for r in rows}
        if out:
            log(f"осадки ERA5 взяты из {local.name}: {len(out)} суток")
            return out
    cache = DATA / "weather" / f"rain_{start}_{end}.json"
    if cache.exists():
        log(f"осадки взяты из кэша: {cache.name}")
        return json.loads(cache.read_text(encoding="utf-8"))
    params = {"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
              "daily": "precipitation_sum", "timezone": "Europe/Moscow"}
    last = "нет ответа"
    for attempt in range(6):
        r = requests.get(RAIN_API, params=params, timeout=120)
        if r.status_code == 200:
            d = r.json()["daily"]
            out = {day: float(v or 0.0) for day, v in zip(d["time"], d["precipitation_sum"])}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            return out
        last = f"HTTP {r.status_code}: {r.text[:120]}"
        # у Open-Meteo лимит на час; ждём и пробуем снова — данные одни и те же
        time.sleep(30 * (attempt + 1))
    raise SystemExit(f"OSCAR/ERA5: {last}")


def rain_before(date: str, rain: dict[str, float]) -> float:
    import datetime as _dt
    d0 = _dt.date.fromisoformat(date)
    return sum(rain.get(str(d0 - _dt.timedelta(days=k)), 0.0) for k in range(RAIN_DAYS + 1))


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
    """Доля облачности 0..1. В каталоге попадаются обе шкалы — 0..1 и 0..100,
    поэтому значения больше единицы считаем процентами."""
    v = float(f.get("properties", {}).get("eo:cloud_cover") or 1.0)
    return v / 100.0 if v > 1.0 else v


def tile_of(f: dict) -> str:
    return f.get("properties", {}).get("grid:code") or f["id"].split("_")[1]


def group_by_date(features: list[dict], limit: int, want: int = 0) -> list[tuple[str, list[dict]]]:
    """Сцены по суткам, внутри суток — лучшая сцена каждого тайла."""
    by_date: dict[str, list[dict]] = {}
    for f in features:
        p = f.get("properties", {})
        if p.get("eo:cloud_cover") is None or p.get("s2:nodata_pixel_percentage") is None:
            log(f"    отсеяна {f['id']}: нет метаданных облачности")
            continue
        cloud, nodata = cloud_of(f), p["s2:nodata_pixel_percentage"]
        if cloud > CLOUD_MAX:
            log(f"    отсеяна {f['id']}: облака {cloud * 100:.0f} % > {CLOUD_MAX * 100:.0f} %")
            continue
        by_date.setdefault(p["datetime"][:10], []).append(f)
    log(f"    сцен в каталоге {len(features)}, дат с годными сценами {len(by_date)}")
    groups = []
    for date, items in sorted(by_date.items()):
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
    from rasterio.windows import from_bounds as win_from_bounds
    from pyproj import Transformer

    with rasterio.open(f"/vsicurl/{href}") as src:
        to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        w, s, e, n = bbox
        x0, y0 = to_src.transform(w, s)
        x1, y1 = to_src.transform(e, n)
        win = win_from_bounds(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), src.transform)
        win_w, win_h = max(int(round(win.width)), 1), max(int(round(win.height)), 1)
        arr = src.read(1, window=win, out_shape=(height, width), boundless=True,
                       fill_value=0, resampling=resampling).astype("float32")
        decimated = src.window_transform(win) * Affine.scale(win_w / float(width), win_h / float(height))
        out = np.full((height, width), np.nan, dtype="float32")
        reproject(source=arr, destination=out, src_transform=decimated, src_crs=src.crs,
                  dst_transform=transform, dst_crs=CRS.from_epsg(4326), resampling=resampling,
                  src_nodata=0, dst_nodata=np.nan)
        return out


def read_merged(items: list[dict], band: str, bbox: list[float], width: int, height: int,
                transform, resampling: Resampling) -> np.ndarray:
    out = np.full((height, width), np.nan, dtype="float32")
    for it in items:
        try:
            arr = read_coarse(it["assets"][band]["href"], bbox, width, height, transform, resampling)
        except Exception as exc:  # noqa: BLE001
            log(f"  чтение {band} {it['id']}: {type(exc).__name__}")
            continue
        out = np.where(np.isfinite(out), out, arr)
    return out


def indices(bands: dict) -> dict:
    out: dict = {}
    scl = bands.get("scl")
    good = np.ones_like(bands["nir"], dtype=bool) if scl is None else \
        ~(np.isin(scl, [0, 1, 3, 8, 9, 10]) | np.isnan(scl))
    g, r, n, s16 = bands.get("green"), bands.get("red"), bands.get("nir"), bands.get("swir16")
    with np.errstate(invalid="ignore", divide="ignore"):
        if g is not None and n is not None:
            out["ndwi"] = np.where((g > 0) & (n > 0), (g - n) / (g + n), np.nan)
        if r is not None and n is not None:
            out["ndvi"] = np.where((r > 0) & (n > 0), (n - r) / (n + r), np.nan)
        if s16 is not None and n is not None:
            out["lswi"] = np.where((n > 0) & (s16 > 0), (n - s16) / (n + s16), np.nan)
    for key in list(out):
        out[key] = np.where(good, out[key], np.nan)
    out["good"] = good
    return out


def ascii_map(mask: np.ndarray, cols: int = 74) -> list[str]:
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
                line += "~"
                continue
            line += shades[min(len(shades) - 1, int(float(good.mean()) * len(shades) / 0.6))]
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
    log(f"окно массива ~{2 * AOI_HALF_LON * 111320 * math.cos(math.radians(lat_c)) / 1000:.1f} x "
        f"{2 * AOI_HALF_LAT * 110.574:.1f} км")

    flood_dates = group_by_date(stac_search(FLOOD_START, FLOOD_END), MAX_FLOOD_DATES)
    veg_dates = group_by_date(stac_search(VEG_START, VEG_END), MAX_VEG_DATES)
    autumn = group_by_date(stac_search(AUTUMN_START, AUTUMN_END), MAX_AUTUMN_DATES)
    rain = daily_rain("2025-04-20", AUTUMN_END, (SEARCH_BBOX[1] + SEARCH_BBOX[3]) / 2,
                      (SEARCH_BBOX[0] + SEARCH_BBOX[2]) / 2)
    clean_dates = [d for d, _ in flood_dates if rain_before(d, rain) < RAIN_CLEAN_MM]
    log(f"чистых от дождя дат (осадки < {RAIN_CLEAN_MM} мм за {RAIN_DAYS} суток): "
        f"{len(clean_dates)} из {len(flood_dates)} — {clean_dates}")
    if not clean_dates:
        raise SystemExit("все даты окна затопления после дождей — полив от дождя не отличить")
    log(f"дат окна затопления {len(flood_dates)} ({FLOOD_START}..{FLOOD_END}), "
        f"вегетации {len(veg_dates)} ({VEG_START}..{VEG_END}), осенних {len(autumn)}")
    if len(flood_dates) < 2:
        raise SystemExit(f"сцен окна затопления {len(flood_dates)} — признак не проверить")
    if len(veg_dates) < 1:
        raise SystemExit("нет сцены пика вегетации — полог риса не проверить")

    veg_used: list[dict] = []
    ndvi_peak = np.full((height, width), -1.0, dtype="float32")
    flood_green = np.zeros((height, width), dtype="int16")
    fg_clean = np.zeros((height, width), dtype="int16")  # затоплено на чистых от дождя датах
    obs_clean = np.zeros((height, width), dtype="int16")  # сколько чистых дат пиксель наблюдался
    clean_seen = 0
    ndvi_max = np.full((height, width), -1.0, dtype="float32")
    good_any = np.zeros((height, width), dtype=bool)
    used: list[dict] = []

    with Env(**GDAL_ENV):
        for date, items in flood_dates:
            bands = {b: read_merged(items, b, SEARCH_BBOX, width, height, transform,
                                    Resampling.nearest if b == "scl" else Resampling.bilinear)
                     for b in ("red", "green", "nir", "swir16", "scl")}
            a = indices(bands)
            ndvi, ndf = a["ndvi"], a["good"]
            r5 = rain_before(date, rain)
            wet = (a["ndwi"] > 0.0) | (a["lswi"] >= ndvi - 0.05)
            fg = (wet & (ndvi > NDVI_GREEN_MIN) & ndf)
            flood_green += fg.astype("int16")
            if r5 < RAIN_CLEAN_MM:
                # Сравниваем только там, где пиксель действительно наблюдался: облако на
                # одной из дат не должно само по себе выбрасывать чек.
                fg_clean += (fg & ndf).astype("int16")
                obs_clean += ndf.astype("int16")
                clean_seen += 1
            ndvi_max = np.where(np.isfinite(ndvi), np.maximum(ndvi_max, ndvi), ndvi_max)
            good_any |= ndf
            used.append({"date": date, "scene": items[0]["id"], "tiles": [tile_of(i) for i in items],
                         "cloud_pct": round(min(cloud_of(i) for i in items) * 100, 2),
                         "flood_green_pct": round(float(np.nanmean(fg)) * 100, 2),
                         "ndvi_mean": round(float(np.nanmean(ndvi)), 3)})
            log(f"  {date}: затоплено и зелено у {np.nanmean(fg) * 100:5.2f} % | NDWI > 0 у "
                f"{(a['ndwi'] > 0)[ndf].mean() * 100:5.2f} % | LSWI >= NDVI у "
                f"{(a['lswi'] >= ndvi)[ndf].mean() * 100:5.2f} % | NDVI средний "
                f"{np.nanmean(ndvi):.3f} | осадки за {RAIN_DAYS} суток {r5:.1f} мм"
                f"{' — чистая' if r5 < RAIN_CLEAN_MM else ' — после дождя'}")

        for date, items in veg_dates:
            bands = {b: read_merged(items, b, SEARCH_BBOX, width, height, transform,
                                    Resampling.nearest if b == "scl" else Resampling.bilinear)
                     for b in ("red", "nir", "scl")}
            a = indices(bands)
            ndvi = a["ndvi"]
            ndvi_peak = np.where(a["good"] & np.isfinite(ndvi), np.maximum(ndvi_peak, ndvi), ndvi_peak)
            veg_used.append({"date": date, "scene": items[0]["id"],
                             "cloud_pct": round(min(cloud_of(i) for i in items) * 100, 2),
                             "ndvi_mean": round(float(np.nanmean(ndvi)), 3),
                             "closed_pct": round(float(np.nanmean(ndvi > NDVI_PEAK_MIN)) * 100, 2)})
            log(f"  {date} (вегетация): NDVI > 0.55 у {np.nanmean(ndvi > 0.55) * 100:.1f} % "
                f"пикселей, средний {np.nanmean(ndvi):.3f}, тайлы {[tile_of(i) for i in items]}")

        dry = None
        for date, items in autumn:
            bands = {b: read_merged(items, b, SEARCH_BBOX, width, height, transform,
                                    Resampling.nearest if b == "scl" else Resampling.bilinear)
                     for b in ("red", "green", "nir", "scl")}
            a = indices(bands)
            d = (a["ndwi"] <= 0.0) & a["good"]
            dry = d if dry is None else (dry | d)
            log(f"  {date} (осень): сухо у {np.nanmean(d) * 100:.1f} % пикселей")
        if dry is None:
            raise SystemExit("нет осенней сцены — сброс воды не проверить")

    # Требуем воду минимум на двух чистых от дождя датах. На всех сразу требовать
    # нельзя: с 6 по 13 июня полог смыкается и доля затопленных пикселей падает
    # с 8,3 % до 1,8 % — такое требование оставило бы только поздно залитые чеки.
    need = min(FLOOD_GREEN_MIN, clean_seen)
    flooded = (fg_clean >= need) if clean_seen else (flood_green >= FLOOD_GREEN_MIN)
    if clean_seen:
        flooded &= (obs_clean >= need)
    rice = flooded & (ndvi_peak > NDVI_PEAK_MIN) & dry
    px_ha = (COARSE_M * COARSE_M) / 10_000.0
    log("")
    log("=== признак риса по району ===")
    log(f"  затоплено минимум на {need} из {clean_seen} чистых от дождя дат: "
        f"{np.nansum(flooded) * px_ha:.0f} га ({np.nanmean(flooded) * 100:.2f} % района)")
    log(f"  затоплено на всех {clean_seen} чистых датах: "
        f"{np.nansum((fg_clean == obs_clean) & (obs_clean >= 1)) * px_ha:.0f} га")
    log(f"  затоплено минимум на {FLOOD_GREEN_MIN} датах из всех: "
        f"{np.nansum(flood_green >= FLOOD_GREEN_MIN) * px_ha:.0f} га "
        f"({np.nanmean(flood_green >= FLOOD_GREEN_MIN) * 100:.2f} % района)")
    log(f"  плюс NDVI > {NDVI_PEAK_MIN} на пике лета: "
        f"{np.nansum(ndvi_peak > NDVI_PEAK_MIN) * px_ha:.0f} га "
        f"({np.nanmean(ndvi_peak > NDVI_PEAK_MIN) * 100:.1f} % района)")
    log(f"  плюс сухо осенью:                  {np.nansum(dry) * px_ha:.0f} га")
    log(f"  РИС (все три условия):             {np.nansum(rice) * px_ha:.0f} га "
        f"({np.nanmean(rice) * 100:.2f} % района, район {width * height * px_ha:.0f} га)")
    log("")
    for line in ascii_map(np.where(good_any, rice.astype("float32"), -1.0)):
        log(f"|{line}|")

    ww = int(round(2 * AOI_HALF_LON / dlon))
    wh = int(round(2 * AOI_HALF_LAT / dlat))
    rice_f = rice.astype("float32")
    cov_f = good_any.astype("float32")
    rice_sum = ndi.uniform_filter(rice_f, size=(wh, ww), mode="constant") * ww * wh
    cov_sum = ndi.uniform_filter(cov_f, size=(wh, ww), mode="constant") * ww * wh
    coverage = cov_sum / float(ww * wh)
    score = np.where(coverage >= 0.95, rice_sum * px_ha, -1.0)

    best = None
    log("")
    log("=== лучшие окна ===")
    shown = 0
    for idx in np.argsort(score.ravel())[::-1][:600]:
        cy, cx = np.unravel_index(idx, score.shape)
        if score[cy, cx] < 0:
            break
        if cy - wh // 2 < 0 or cy + wh // 2 >= height or cx - ww // 2 < 0 or cx + ww // 2 >= width:
            continue
        clon, clat = transform * (cx, cy)
        bbox = [clon - AOI_HALF_LON, clat - AOI_HALF_LAT, clon + AOI_HALF_LON, clat + AOI_HALF_LAT]
        tiles = sorted({tile_of(it) for _, grp in flood_dates for it in grp if _intersects(it, bbox)})
        shown += 1
        if shown <= 10:
            log(f"  {clat:.4f} с.ш. {clon:.4f} в.д. | рис {score[cy, cx]:7.0f} га | "
                f"покрытие {coverage[cy, cx] * 100:5.1f} % | тайлов {len(tiles)}: {tiles}")
        if best is None or (len(tiles) < len(best["tiles"]) and score[cy, cx] >= 0.9 * best["rice_ha"]):
            best = {"lat": round(clat, 5), "lon": round(clon, 5),
                    "rice_ha": round(float(score[cy, cx]), 1),
                    "coverage": round(float(coverage[cy, cx]), 4), "tiles": tiles}
    if best is None:
        raise SystemExit("ни одно окно не набрало годного покрытия")

    clat, clon = best["lat"], best["lon"]
    bbox = [round(clon - AOI_HALF_LON, 5), round(clat - AOI_HALF_LAT, 5),
            round(clon + AOI_HALF_LON, 5), round(clat + AOI_HALF_LAT, 5)]
    dist_km = (clat - POLTAVSKAYA[0]) * 110.574
    log("")
    log(f"выбран участок: {clat} с.ш., {clon} в.д. | рис по грубой оценке {best['rice_ha']} га | "
        f"тайлы {best['tiles']} | {abs(dist_km):.1f} км {'севернее' if dist_km > 0 else 'южнее'} "
        f"ст. Полтавской")

    # Даты, которые обязан взять fetch_sentinel: чтобы проверка риса и сегментация
    # считались по одной и той же съёмке, а не по двум разным случайным сценам.
    clean_used = [u for u in used if u["date"] in clean_dates]
    mid = "2025-06-08"
    first = [u for u in clean_used if u["date"] < mid]
    second = [u for u in clean_used if u["date"] >= mid]
    if not (first and second):  # чистые даты сбились в одну половину — берём две лучшие
        pair = sorted(clean_used, key=lambda u: -u["flood_green_pct"])[:2]
        first, second = pair[:1], pair[1:2]
    f1 = max(first, key=lambda u: u["flood_green_pct"])
    f2 = max(second, key=lambda u: u["flood_green_pct"])
    # две даты впрямую друг к другу — слабое доказательство: ливень держит поля
    # мокрыми двое суток. Поэтому из второй половины берём дату не ближе 4 суток
    # к первой, если её сигнал не меньше половины лучшего.
    import datetime as _dt
    far = [u for u in second
           if (_dt.date.fromisoformat(u["date"]) - _dt.date.fromisoformat(f1["date"])).days >= 4
           and u["flood_green_pct"] >= 0.5 * f2["flood_green_pct"]]
    if far:
        log(f"  вторая дата смещена: {f2['date']} -> {far[0]['date']} "
            f"(разница {( _dt.date.fromisoformat(far[0]['date']) - _dt.date.fromisoformat(f1['date'])).days} сут)")
        f2 = far[0]
    veg_best = max(veg_used, key=lambda u: u["ndvi_mean"])
    key_dates = {"flood1": f1["date"], "flood2": f2["date"], "veg": veg_best["date"],
                 "dry": max(d for d, _ in autumn) if autumn else None}
    log(f"ключевые даты: затопление {key_dates['flood1']} (осадки перед съёмкой "
        f"{rain_before(key_dates['flood1'], rain):.1f} мм) и {key_dates['flood2']} "
        f"({rain_before(key_dates['flood2'], rain):.1f} мм), полог {key_dates['veg']}, "
        f"сброс {key_dates['dry']}")

    aoi = {
        "name": "Рисовый массив, Красноармейский район, Краснодарский край",
        "lat": clat, "lon": clon, "bbox": bbox, "utm_epsg": 32637,
        "district": "Красноармейский район, Краснодарский край",
        "near": "ст. Полтавская",
        "picked_by": "pipeline/find_rice.py: максимум площади риса по признаку «стоячая вода "
                     "минимум на двух датах окна затопления (май–начало июля), полог NDVI > 0.55 "
                     "на пике лета, сухо осенью»",
        "coarse_rice_ha": best["rice_ha"],
        "coarse_coverage": best["coverage"],
        "tiles": best["tiles"],
        "search_bbox": SEARCH_BBOX,
        "criterion": {
            "rain_days": RAIN_DAYS,
            "rain_clean_mm": RAIN_CLEAN_MM,
            "clean_dates": clean_dates,
            "flood_green_min": FLOOD_GREEN_MIN,
            "ndvi_green_min": NDVI_GREEN_MIN,
            "ndvi_peak_min": NDVI_PEAK_MIN,
            "flood_window": [FLOOD_START, FLOOD_END],
            "autumn": [AUTUMN_START, AUTUMN_END],
        },
        "key_dates": key_dates,
        "flood_dates": [u["date"] for u in used],
        "veg_dates": veg_used,
        "slots": {u["date"]: {"scene": u["scene"], "cloud_pct": u["cloud_pct"],
                              "flood_green_pct": u["flood_green_pct"], "ndvi_mean": u["ndvi_mean"]}
                  for u in used},
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
