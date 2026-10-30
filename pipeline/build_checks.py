"""Сегментация реальных рисовых чеков по снимку Sentinel-2 и расчёт их характеристик.

Метод:
  1. Маска водного зеркала: NDWI > 0 по пиковой сцене (пик затопления).
  2. Морфологическая очистка: удаление шума, замыкание разрывов.
  3. Разделение слипшихся чеков водоразделом (watershed) по карте расстояний — валы между чеками
     работают как перегородки, и водораздел восстанавливает границы карты.
  4. Векторизация, фильтр по площади, расчёт площади на эллипсоиде (WGS84).
  5. Отметки чеков — из Copernicus DEM GLO-30 (реальный рельеф, публичный набор AWS Open Data).
  6. По всем сценам сезона считаются зональные средние NDVI/NDWI — это и есть реальный
     спутниковый ряд наблюдений за каждым чеком.

Выход: data/checks.geojson, data/checks_series.json, web/public/data/checks.geojson
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod
from rasterio import features
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.env import Env
from rasterio.warp import reproject
from scipy import ndimage as ndi
from skimage.feature import peak_local_max
from skimage.segmentation import watershed

from common import AOI, DATA, SENTINEL, WEB, grid_shape, log, write_json

GEOD = Geod(ellps="WGS84")
MIN_HA, MAX_HA = 8.0, 220.0
DEM_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_30_N45_00_E038_00_DEM/"
    "Copernicus_DSM_COG_30_N45_00_E038_00_DEM.tif"
)
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MAX_RETRY="6",
    GDAL_HTTP_TIMEOUT="180",
    VSI_CACHE="TRUE",
    AWS_NO_SIGN_REQUEST="YES",
)


def load_raster(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read(1)


def read_dem(width: int, height: int, transform) -> np.ndarray | None:
    """Отметки рельефа из Copernicus DEM GLO-30 (публичный набор, без ключей)."""
    try:
        with Env(**GDAL_ENV), rasterio.open(f"/vsicurl/{DEM_URL}") as src:
            from rasterio.windows import from_bounds as win_from_bounds

            w, s, e, n = AOI["bbox"]
            win = win_from_bounds(w, s, e, n, src.transform)
            raw = src.read(1, window=win, boundless=True, fill_value=src.nodata).astype("float32")
            out = np.full((height, width), np.nan, dtype="float32")
            reproject(
                raw,
                out,
                src_transform=src.window_transform(win),
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=CRS.from_epsg(4326),
                resampling=Resampling.bilinear,
                src_nodata=src.nodata,
            )
            return out
    except Exception as exc:  # noqa: BLE001
        log(f"DEM недоступен: {exc!r}")
        return None


def segment(water: np.ndarray) -> np.ndarray:
    clean = ndi.binary_opening(water, structure=np.ones((3, 3)))
    clean = ndi.binary_closing(clean, structure=np.ones((3, 3)))
    px_per_ha = 100
    clean = ndi.binary_opening(clean, structure=np.ones((2, 2)))
    dist = ndi.distance_transform_edt(clean)
    # Минимум 40 м между маркерами: чек ~500-800 м, вал ~10-20 м
    coords = peak_local_max(dist, min_distance=4, labels=clean, exclude_border=False)
    if len(coords) == 0:
        return ndi.label(clean)[0]
    mask = np.zeros(dist.shape, dtype=bool)
    mask[tuple(coords.T)] = True
    markers, _ = ndi.label(mask)
    labels = watershed(-dist, markers, mask=clean)
    # Убираем région меньше 2 га (100 м × 100 м)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    small = sizes < (2 * px_per_ha)
    labels[small[labels]] = 0
    return labels


def main() -> None:
    peak = json.loads((SENTINEL / "peak_scene.json").read_text(encoding="utf-8"))
    date = peak["date"]
    log(f"сегментация по сцене {date} ({peak['id']})")

    width, height, transform = grid_shape()
    ndwi = load_raster(SENTINEL / f"ndwi_{date}.tif")
    ndvi = load_raster(SENTINEL / f"ndvi_{date}.tif")
    valid = load_raster(SENTINEL / f"valid_{date}.tif").astype(bool)

    water = np.isfinite(ndwi) & valid & (ndwi > 0.0)
    log(f"водное зеркало: {water.mean() * 100:.1f} % полигона")

    labels = segment(water)
    n_labels = int(labels.max())
    log(f"водораздел выделил {n_labels} контуров")

    dem = read_dem(width, height, transform)

    scenes = json.loads((SENTINEL / "scenes.json").read_text(encoding="utf-8"))["saved"]
    series_stack = {}
    for sc in scenes:
        d = sc["date"]
        series_stack[d] = {
            "ndvi": load_raster(SENTINEL / f"ndvi_{d}.tif"),
            "ndwi": load_raster(SENTINEL / f"ndwi_{d}.tif"),
            "valid": load_raster(SENTINEL / f"valid_{d}.tif").astype(bool),
        }

    checks: list[dict] = []
    series: list[dict] = []

    for label_id in range(1, n_labels + 1):
        geom_mask = labels == label_id
        ys, xs = np.nonzero(geom_mask)
        area_px = int(geom_mask.sum())
        area_m2 = area_px * 100.0  # 10 м × 10 м
        area_ha = area_m2 / 10_000.0
        if area_ha < MIN_HA or area_ha > MAX_HA:
            continue

        shapes = list(features.shapes(geom_mask.astype("uint8"), mask=geom_mask, transform=transform))
        if not shapes:
            continue
        geom, _ = max(shapes, key=lambda s: s[1])
        ring = [[round(float(x), 6), round(float(y), 6)] for x, y in geom["coordinates"][0]]
        geom = {"type": "Polygon", "coordinates": [ring]}
        if len(ring) < 5:
            continue
        # уточняем площадь по эллипсоиду, а не по счёту пикселей
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        try:
            poly_area_m2 = abs(GEOD.geometry_area_perimeter({"type": "Polygon", "coordinates": [ring]})[0])
            area_ha = poly_area_m2 / 10_000.0
        except Exception:  # noqa: BLE001
            poly_area_m2 = area_m2

        elev = float(np.nanmean(dem[geom_mask])) if dem is not None else None
        cy, cx = float(ys.mean()), float(xs.mean())
        clon, clat = transform * (cx, cy)

        row = {
            "id": len(checks) + 1,
            "area_ha": round(area_ha, 2),
            "elev_m": round(elev, 2) if elev is not None else None,
            "ndvi_peak": round(float(np.nanmean(ndvi[geom_mask])), 4) if np.isfinite(ndvi[geom_mask]).any() else None,
            "ndwi_peak": round(float(np.nanmean(ndwi[geom_mask])), 4) if np.isfinite(ndwi[geom_mask]).any() else None,
            "centroid": [round(clon, 6), round(clat, 6)],
        }
        checks.append({"row": row, "geom": geom, "mask": geom_mask, "area_ha": area_ha})

    if not checks:
        from common import die

        die("контуры чеков не выделены — сегментация не сработала")

    elevations = [c["row"]["elev_m"] for c in checks if c["row"]["elev_m"] is not None]
    mean_elev = float(np.mean(elevations)) if elevations else 0.0

    features_out = []
    for c in checks:
        row = c["row"]
        dz = (row["elev_m"] - mean_elev) if row["elev_m"] is not None else 0.0
        row["dz_m"] = round(dz, 2)
        # Гидравлический КПД подачи: упрощённая модель (калибровка — этап 2 проекта)
        row["kpd"] = round(max(0.55, min(0.92, 0.80 - 0.012 * dz)), 3)
        features_out.append(
            {"type": "Feature", "properties": row, "geometry": c["geom"]}
        )

        ser = []
        for sc in scenes:
            d = sc["date"]
            st = series_stack[d]
            m = c["mask"] & st["valid"] & np.isfinite(st["ndvi"])
            if m.sum() < 20:
                continue
            ser.append(
                {
                    "date": d,
                    "ndvi": round(float(np.nanmean(st["ndvi"][m])), 4),
                    "ndwi": round(float(np.nanmean(st["ndwi"][m])), 4),
                    "water_pct": round(float(np.nanmean(st["ndwi"][m] > 0.0)) * 100, 1),
                    "px": int(m.sum()),
                }
            )
        series.append({"id": row["id"], "series": ser})

    collection = {
        "type": "FeatureCollection",
        "name": "Рисовые чеки полигона (оцифрованы по Sentinel-2)",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "metadata": {
            "source": "Контуры получены автоматической сегментацией водного зеркала по снимку Sentinel-2 L2A",
            "scene_date": date,
            "scene_id": peak["id"],
            "method": "NDWI > 0 -> морфологическая очистка -> водораздел по карте расстояний -> векторизация",
            "dem": "Copernicus DEM GLO-30 (публичный набор AWS Open Data)" if dem is not None else "недоступен",
            "count": len(features_out),
            "total_ha": round(sum(c["area_ha"] for c in checks), 1),
            "mean_elev_m": round(mean_elev, 2),
            "min_ha": MIN_HA,
            "max_ha": MAX_HA,
        },
        "features": features_out,
    }

    write_json(DATA / "checks.geojson", collection)
    write_json(DATA / "checks_series.json", {"checks": series, "scenes": scenes})

    WEB.mkdir(parents=True, exist_ok=True)
    shutil.copy(DATA / "checks.geojson", WEB / "checks.geojson")
    shutil.copy(DATA / "checks_series.json", WEB / "checks_series.json")
    log(f"чеков: {len(features_out)}, площадь: {collection['metadata']['total_ha']} га, "
        f"средняя отметка: {mean_elev:.2f} м")


if __name__ == "__main__":
    main()
