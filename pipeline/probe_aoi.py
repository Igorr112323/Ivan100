"""Поиск рисового массива по спутниковым признакам (разовый инструмент разведки).

Признаки рисового чека (Xiao et al., 2005; FAO):
  * в период затопления LSWI >= NDVI (вода под пологом), LSWI = (NIR - SWIR)/(NIR + SWIR);
  * максимум NDVI приходится на июль-август, а не на май (отличие от озимых);
  * структура массива блочная: контуры чеков дают периодичность 300-1000 м.

Скрипт перебирает сетку точек в рисовой зоне Кубани и печатает таблицу признаков.
"""
from __future__ import annotations

import numpy as np
import rasterio
from rasterio.env import Env
from rasterio.windows import from_bounds as win_from_bounds
from pyproj import Transformer

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MAX_RETRY="6",
    GDAL_HTTP_TIMEOUT="120",
    GDAL_HTTP_MULTIPLEX="YES",
    AWS_NO_SIGN_REQUEST="YES",
)

# Три окна сезона: затопление / пик вегетации / созревание
SLOTS = [
    ("2025-05-10", "2025-06-05", "май"),
    ("2025-07-05", "2025-07-31", "июль"),
    ("2025-08-20", "2025-09-20", "сентябрь"),
]
HALF_LON, HALF_LAT = 0.060, 0.050          # окно кандидата = рабочий полигон: ~10.6 x 11.1 км
PX = 80                                     # размер окна чтения, ~80 м на пиксель

LATS = [45.25, 45.35, 45.45]
LONS = [37.80, 37.90, 38.00]


def search(lon: float, lat: float, start: str, end: str):
    import requests

    bbox = [lon - HALF_LON, lat - HALF_LAT, lon + HALF_LON, lat + HALF_LAT]
    r = requests.get(
        f"{STAC}/search",
        params={
            "collections": COLLECTION,
            "bbox": ",".join(f"{v:.5f}" for v in bbox),
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "limit": 40,
        },
        timeout=90,
    )
    if r.status_code != 200:
        return None
    feats = r.json().get("features", [])
    good = [
        f
        for f in feats
        if (f.get("properties", {}).get("eo:cloud_cover") or 100) < 0.25
        and (f.get("properties", {}).get("s2:nodata_pixel_percentage") or 100) < 30
    ]
    if not good:
        return None
    good.sort(key=lambda f: f["properties"].get("eo:cloud_cover", 1))
    return good[0]


def read(href: str, lon: float, lat: float) -> np.ndarray | None:
    try:
        with rasterio.open(f"/vsicurl/{href}") as src:
            to_utm = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            x0, y0 = to_utm.transform(lon - HALF_LON, lat - HALF_LAT)
            x1, y1 = to_utm.transform(lon + HALF_LON, lat + HALF_LAT)
            win = win_from_bounds(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), src.transform)
            return src.read(1, window=win, out_shape=(PX, PX), boundless=True, fill_value=0).astype("float32")
    except Exception as exc:  # сеть тоже бывает разная
        print(f"    чтение не удалось: {type(exc).__name__}: {exc}"[:200])
        return None


def blockiness(a: np.ndarray) -> float:
    """Доля энергии спектра в полосе периодов 300-1000 м (блочная структура чеков)."""
    a = np.where(np.isfinite(a), a, float(np.nanmean(a)))
    a = a - a.mean()
    if not np.isfinite(a).all() or a.std() < 1e-6:
        return 0.0
    f = np.abs(np.fft.rfft(a - a.mean(axis=1, keepdims=True), axis=0))
    e = f.mean(axis=1)
    total = e[1:].sum()
    if total <= 0:
        return 0.0
    k = np.arange(len(e))
    meters = np.where(k > 0, (a.shape[0] * 80.0) / k, np.inf)
    band = (k >= 1) & (meters >= 300) & (meters <= 1200)
    return float(e[band].sum() / total)


def main() -> None:
    print(f"{'широта':>7} {'долгота':>8} {'NDVI май':>9} {'NDVI июл':>9} {'NDVI сен':>9} "
          f"{'рис.подъём':>11} {'LSWI>NDVI май':>14} {'LSWI>NDVI июл':>14} {'вода май':>9} {'блочность':>10}")
    rows = []
    with Env(**GDAL_ENV):
        for lat in LATS:
            for lon in LONS:
                per = {}
                for start, end, name in SLOTS:
                    sc = search(lon, lat, start, end)
                    if sc is None:
                        per[name] = None
                        continue
                    a = sc["assets"]
                    green = read(a["green"]["href"], lon, lat)
                    red = read(a["red"]["href"], lon, lat)
                    nir = read(a["nir"]["href"], lon, lat)
                    swir_key = "swir16" if "swir16" in a else ("swir22" if "swir22" in a else None)
                    swir = read(a[swir_key]["href"], lon, lat) if swir_key else None
                    if min(green is not None, red is not None, nir is not None, swir is not None) == 0:
                        per[name] = None
                        continue
                    ok = (green > 0) & (red > 0) & (nir > 0) & (swir > 0)
                    with np.errstate(invalid="ignore", divide="ignore"):
                        ndvi = np.where(ok, (nir - red) / (nir + red), np.nan)
                        ndwi = np.where(ok, (green - nir) / (green + nir), np.nan)
                        lswi = np.where(ok, (nir - swir) / (nir + swir), np.nan)
                    per[name] = {
                        "date": sc["properties"]["datetime"][:10],
                        "cloud": sc["properties"].get("eo:cloud_cover", 0),
                        "ndvi": ndvi,
                        "ndwi": ndwi,
                        "lswi": lswi,
                        "ok": ok,
                    }
                if any(v is None for v in per.values()):
                    print(f"{lat:7.2f} {lon:8.2f}  — нет кондиционных снимков")
                    continue
                may, jul, sep = per["май"], per["июль"], per["сентябрь"]
                ok = may["ok"] & jul["ok"] & sep["ok"]
                if ok.mean() < 0.5:
                    print(f"{lat:7.2f} {lon:8.2f}  — мало общих пикселей")
                    continue
                rise = float(np.nanmean((jul["ndvi"][ok] - may["ndvi"][ok]) > 0.05))
                like = float(np.nanmean(((jul["ndvi"][ok] - may["ndvi"][ok]) > 0.05) & (jul["ndvi"][ok] > 0.5)))
                f_may = float(np.nanmean(may["lswi"][ok] > may["ndvi"][ok] - 0.05))
                f_jul = float(np.nanmean(jul["lswi"][ok] > jul["ndvi"][ok] - 0.05))
                wat = float(np.nanmean(may["ndwi"][ok] > 0))
                blk = blockiness(may["ndvi"])
                rows.append((lat, lon, np.nanmean(may["ndvi"][ok]), np.nanmean(jul["ndvi"][ok]),
                             np.nanmean(sep["ndvi"][ok]), like, f_may, f_jul, wat, blk))
                print(f"{lat:7.2f} {lon:8.2f} {rows[-1][2]:9.3f} {rows[-1][3]:9.3f} {rows[-1][4]:9.3f} "
                      f"{like:11.2f} {f_may:14.2f} {f_jul:14.2f} {wat:9.2f} {blk:10.2f}", flush=True)

    if rows:
        print("\n=== Рейтинг по совокупности признаков ===")
        for r in sorted(rows, key=lambda r: -(0.35 * r[5] + 0.25 * r[6] + 0.15 * r[7] + 0.15 * r[9] + 0.10 * r[8]))[:8]:
            print(f"  {r[0]:.2f} {r[1]:.2f} | признак риса {r[5]:.2f} | затопление май {r[6]:.2f} "
                  f"июль {r[7]:.2f} | вода {r[8]:.2f} | блочность {r[9]:.2f}")


if __name__ == "__main__":
    main()
