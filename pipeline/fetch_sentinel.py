"""Загрузка реальных снимков Sentinel-2 L2A (Copernicus/ESA) над рисовым массивом.

Источник: STAC-каталог Earth Search v1 (AWS Open Data, публичные COG, без ключей).
Полигон лежит на стыке двух тайлов MGRS, поэтому на каждую дату берутся сцены обоих
тайлов и сшиваются: иначе часть чеков на части дат остаётся без наблюдений.

Для каждой даты сохраняются:
  * ndvi_<дата>.tif, ndwi_<дата>.tif  — индексы на единой сетке 10 м в EPSG:4326;
  * valid_<дата>.tif                  — маска кондиционных пикселей (без облаков и теней по SCL);
  * array_rgb.jpg + привязка          — реальный цветной снимок массива для карты.

Выход также содержит scenes.json — честный журнал: какие сцены найдены, какая облачность,
какая доля кондиционных пикселей. Это и есть экспериментальная база «наблюдаемости по спутнику».
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.env import Env
from rasterio.transform import from_bounds
from rasterio.warp import reproject
from pyproj import Transformer
from PIL import Image

from common import AOI, SENTINEL, WEB, die, grid_shape, log, write_json

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MAX_RETRY="6",
    GDAL_HTTP_TIMEOUT="120",
    GDAL_HTTP_MULTIPLEX="YES",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="100000000",
    AWS_NO_SIGN_REQUEST="YES",
)

# Сцены, которые нужны: пик затопления (июнь–июль) + равномерное покрытие сезона для NDVI-ряда
SCENE_SLOTS = [
    ("2025-05-01", "2025-06-05", "май–начало июня: всходы, затопление"),
    ("2025-06-06", "2025-07-10", "июнь–начало июля: кущение, пик затопления"),
    ("2025-07-11", "2025-08-05", "июль: выметывание"),
    ("2025-08-06", "2025-08-31", "август: цветение, налив зерна"),
    ("2025-09-01", "2025-09-30", "сентябрь: созревание, сброс воды"),
]
MAX_TILES = 3  # полигон перекрывают два тайла MGRS; третий — на случай съёмки вне зоны


def stac_search(start: str, end: str, limit: int = 60) -> list[dict]:
    import requests

    params = {
        "collections": COLLECTION,
        "bbox": ",".join(f"{v:.5f}" for v in AOI["bbox"]),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "limit": limit,
    }
    r = requests.get(f"{STAC}/search", params=params, timeout=180)
    if r.status_code != 200:
        die(f"STAC {start}..{end}: HTTP {r.status_code} {r.text[:200]}")
    return r.json().get("features", [])


def cloud_of(f: dict) -> float:
    return float(f.get("properties", {}).get("eo:cloud_cover") or 1.0)


def tile_of(f: dict) -> str:
    return f.get("properties", {}).get("grid:code") or f["id"].split("_")[1]


def pick_scenes(features: list[dict]) -> list[dict]:
    """Лучшая сцена каждого тайла MGRS: сначала минимум облачности, затем минимум пропусков."""
    by_tile: dict[str, dict] = {}
    for f in features:
        p = f.get("properties", {})
        cloud = p.get("eo:cloud_cover")
        nodata = p.get("s2:nodata_pixel_percentage")
        if cloud is None or nodata is None:
            continue
        # тайл может перекрывать полигон частично — такая сцена всё равно нужна для сшивки
        if cloud > 0.5 or nodata > 70.0:
            continue
        t = tile_of(f)
        if t not in by_tile or (cloud, nodata) < (cloud_of(by_tile[t]),
                                                  by_tile[t]["properties"].get("s2:nodata_pixel_percentage", 0)):
            by_tile[t] = f
    return sorted(by_tile.values(), key=cloud_of)[:MAX_TILES]


def read_window(href: str, dst_width: int, dst_height: int, dst_transform, resampling: Resampling) -> np.ndarray:
    """Оконное чтение канала COG по полигону с перепроецированием на расчётную сетку."""
    url = f"/vsicurl/{href}"
    with rasterio.open(url) as src:
        to_utm = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        w, s, e, n = AOI["bbox"]
        ux0, uy0 = to_utm.transform(w, s)
        ux1, uy1 = to_utm.transform(e, n)
        from rasterio.windows import from_bounds as win_from_bounds

        win = win_from_bounds(min(ux0, ux1), min(uy0, uy1), max(ux0, ux1), max(uy0, uy1), src.transform)
        arr = src.read(1, window=win, boundless=True, fill_value=0)
        win_transform = src.window_transform(win)

        out = np.zeros((dst_height, dst_width), dtype="float32")
        reproject(
            source=arr.astype("float32"),
            destination=out,
            src_transform=win_transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=CRS.from_epsg(4326),
            resampling=resampling,
            src_nodata=0,
            dst_nodata=np.nan,
        )
    return out


def read_merged(items: list[dict], band: str, width: int, height: int, transform,
                resampling: Resampling) -> np.ndarray:
    """Канал, сшитый из сцен соседних тайлов: полигон попадает на их стык."""
    out = np.full((height, width), np.nan, dtype="float32")
    for it in items:
        arr = read_window(it["assets"][band]["href"], width, height, transform, resampling)
        out = np.where(np.isfinite(out), out, arr)
    return out


def main() -> None:
    width, height, dst_transform = grid_shape()
    log(f"расчётная сетка: {width} x {height} пикселей, 10 м, EPSG:4326")

    catalogue: list[dict] = []
    saved: list[dict] = []
    slot_scenes: dict[str, list[dict]] = {}

    with Env(**GDAL_ENV):
        for start, end, label in SCENE_SLOTS:
            features = stac_search(start, end)
            items = pick_scenes(features)
            catalogue.append(
                {
                    "slot": label,
                    "window": f"{start}..{end}",
                    "start": start,
                    "end": end,
                    "found": len(features),
                    "tiles": [tile_of(i) for i in items],
                    "scenes": [
                        {
                            "id": i["id"],
                            "datetime": i["properties"]["datetime"],
                            "platform": i["properties"].get("platform"),
                            "cloud_cover": round(cloud_of(i) * 100, 3),
                            "nodata_pct": round(i["properties"].get("s2:nodata_pixel_percentage", 0), 2),
                        }
                        for i in items
                    ],
                }
            )
            if not items:
                log(f"{label}: кондиционной сцены нет")
                continue

            date = items[0]["properties"]["datetime"][:10]
            log(f"{label}: {date}, тайлы {[tile_of(i) for i in items]}, "
                f"облачность {[round(cloud_of(i) * 100, 2) for i in items]} %")

            red = read_merged(items, "red", width, height, dst_transform, Resampling.bilinear)
            green = read_merged(items, "green", width, height, dst_transform, Resampling.bilinear)
            nir = read_merged(items, "nir", width, height, dst_transform, Resampling.bilinear)

            # SCL: 0 нет данных, 1 насыщенный/дефектный, 3 тень облака, 8/9 облако, 10 перистые
            good = np.zeros((height, width), dtype=bool)
            for it in items:
                scl = read_window(it["assets"]["scl"]["href"], width, height, dst_transform, Resampling.nearest)
                good |= ~(np.isin(scl, [0, 1, 3, 8, 9, 10]) | np.isnan(scl))

            with np.errstate(invalid="ignore", divide="ignore"):
                ndvi = (nir - red) / (nir + red)
                ndwi = (green - nir) / (green + nir)

            valid = (good & np.isfinite(ndvi) & np.isfinite(ndwi)).astype("uint8")
            ndvi = np.where(valid == 1, ndvi, np.nan).astype("float32")
            ndwi = np.where(valid == 1, ndwi, np.nan).astype("float32")

            profile = {
                "driver": "GTiff",
                "height": height,
                "width": width,
                "count": 1,
                "dtype": "float32",
                "crs": CRS.from_epsg(4326),
                "transform": dst_transform,
                "compress": "deflate",
                "predictor": 3,
                "nodata": float("nan"),
            }
            for name, arr in (("ndvi", ndvi), ("ndwi", ndwi)):
                with rasterio.open(SENTINEL / f"{name}_{date}.tif", "w", **profile) as dst:
                    dst.write(arr, 1)
            p = profile.copy()
            p["dtype"] = "uint8"
            p["nodata"] = 0
            p["predictor"] = 2  # для целочисленных данных PREDICTOR=3 (плавающая точка) недопустим
            with rasterio.open(SENTINEL / f"valid_{date}.tif", "w", **p) as dst:
                dst.write(valid, 1)

            slot_scenes[label] = items
            saved.append(
                {
                    "date": date,
                    "id": items[0]["id"],
                    "label": label,
                    "tiles": [tile_of(i) for i in items],
                    "cloud_cover_pct": round(cloud_of(items[0]) * 100, 3),
                    "valid_pct": round(float(valid.mean()) * 100, 2),
                    "ndvi_mean": round(float(np.nanmean(ndvi)), 4),
                    "ndwi_mean": round(float(np.nanmean(ndwi)), 4),
                    "water_pct": round(float(np.nanmean(ndwi > 0.0)) * 100, 2),
                }
            )
            log(f"  кондиционных пикселей {valid.mean() * 100:.1f} %, водное зеркало "
                f"{np.nanmean(ndwi > 0.0) * 100:.1f} %")

    if not saved:
        die("ни одной кондиционной сцены Sentinel-2 не получено")

    # Реальный цветной снимок массива (пик затопления — самая «водная» сцена по NDWI)
    best = max(saved, key=lambda s: (s["water_pct"] or 0))
    write_json(SENTINEL / "peak_scene.json", best)
    write_json(SENTINEL / "scenes.json", {"catalogue": catalogue, "saved": saved})
    log(f"пиковая сцена: {best['date']} (вода {best['water_pct']} %)")

    with Env(**GDAL_ENV):
        items = slot_scenes[best["label"]]
        blue = read_merged(items, "blue", width, height, dst_transform, Resampling.bilinear)
        red = read_merged(items, "red", width, height, dst_transform, Resampling.bilinear)
        green = read_merged(items, "green", width, height, dst_transform, Resampling.bilinear)

    stack = np.dstack([red, green, blue]).astype("float32")
    finite = np.isfinite(stack).all(axis=2)
    lo, hi = np.nanpercentile(stack[finite], [2, 98])
    rgb = np.clip((stack - lo) / max(hi - lo, 1e-6), 0, 1)
    rgb = np.clip(rgb ** (1 / 1.35) * 255, 0, 255)  # мягкая гамма-коррекция, как в полевых планшетах
    rgb = np.where(finite[..., None], rgb, 0).astype("uint8")

    img = Image.fromarray(rgb, mode="RGB")
    img.save(SENTINEL / "array_rgb.jpg", quality=86, optimize=True)
    log(f"array_rgb.jpg: {img.size[0]}x{img.size[1]}")

    w, s, e, n = AOI["bbox"]
    write_json(
        SENTINEL / "array_rgb.json",
        {
            "image": "array_rgb.jpg",
            "width": width,
            "height": height,
            "bounds": [[s, w], [n, e]],
            "date": best["date"],
            "scene": best["id"],
            "tiles": best.get("tiles", []),
            "source": "Sentinel-2 L2A, Copernicus/ESA, COG на AWS Open Data",
            "attribution": "Содержит обработанные данные Copernicus Sentinel (2025)",
        },
    )

    # Копия для фронтенда
    WEB.mkdir(parents=True, exist_ok=True)
    shutil.copy(SENTINEL / "array_rgb.jpg", WEB / "array_rgb.jpg")
    shutil.copy(SENTINEL / "array_rgb.json", WEB / "array_rgb.json")
    log("снимок скопирован в web/public/data")


if __name__ == "__main__":
    main()
