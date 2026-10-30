"""Загрузка реальных снимков Sentinel-2 L2A (Copernicus/ESA) над рисовым массивом.

Источник: STAC-каталог Earth Search v1 (AWS Open Data, публичные COG, без ключей).
Для каждого выбранного сцены читается окно 10 м над полигоном и сохраняются:
  * ndvi_<дата>.tif, ndwi_<дата>.tif  — индексы на единой сетке 10 м в EPSG:4326;
  * valid_<дата>.tif                  — маска кондиционных пикселей (без облаков и теней по SCL);
  * array_rgb.jpg + привязка          — реальный цветной снимок массива для карты.

Выход также содержит scenes.json — честный журнал: какие сцены найдены, какая облачность,
какая доля кондиционных пикселей. Это и есть экспериментальная база «наблюдаемости по спутнику».
"""
from __future__ import annotations

import json
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


def pick_scene(features: list[dict]) -> dict | None:
    """Выбор лучшей сцены: минимум облачности, затем максимум кондиционных пикселей."""
    good = []
    for f in features:
        p = f.get("properties", {})
        cloud = p.get("eo:cloud_cover", 100.0)
        nodata = p.get("s2:nodata_pixel_percentage", 100.0)
        if cloud is None or nodata is None:
            continue
        if cloud > 0.35 or nodata > 25.0:
            continue
        good.append((cloud, nodata, f))
    if not good:
        return None
    good.sort(key=lambda t: (t[0], t[1]))
    return good[0][2]


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


def main() -> None:
    width, height, dst_transform = grid_shape()
    log(f"расчётная сетка: {width} x {height} пикселей, 10 м, EPSG:4326")

    catalogue: list[dict] = []
    saved: list[dict] = []

    with Env(**GDAL_ENV):
        for start, end, label in SCENE_SLOTS:
            features = stac_search(start, end)
            scene = pick_scene(features)
            catalogue.append(
                {
                    "slot": label,
                    "window": f"{start}..{end}",
                    "start": start,
                    "end": end,
                    "found": len(features),
                    "best": (
                        {
                            "id": scene["id"],
                            "datetime": scene["properties"]["datetime"],
                            "platform": scene["properties"].get("platform"),
                            "cloud_cover": round(scene["properties"].get("eo:cloud_cover", 0) * 100, 3),
                            "nodata_pct": round(scene["properties"].get("s2:nodata_pixel_percentage", 0), 2),
                            "water_pct": round(scene["properties"].get("s2:water_percentage", 0), 2),
                            "vegetation_pct": round(scene["properties"].get("s2:vegetation_percentage", 0), 2),
                        }
                        if scene
                        else None
                    ),
                }
            )
            if scene is None:
                log(f"{label}: кондиционной сцены нет")
                continue

            date = scene["properties"]["datetime"][:10]
            assets = scene["assets"]
            log(f"{label}: {scene['id']} ({date}, облачность "
                f"{round(scene['properties'].get('eo:cloud_cover', 0) * 100, 2)} %)")

            red = read_window(assets["red"]["href"], width, height, dst_transform, Resampling.bilinear)
            green = read_window(assets["green"]["href"], width, height, dst_transform, Resampling.bilinear)
            nir = read_window(assets["nir"]["href"], width, height, dst_transform, Resampling.bilinear)
            scl = read_window(assets["scl"]["href"], width, height, dst_transform, Resampling.nearest)

            with np.errstate(invalid="ignore", divide="ignore"):
                ndvi = (nir - red) / (nir + red)
                ndwi = (green - nir) / (green + nir)

            # SCL: 0 нет данных, 1 насыщенный/дефектный, 3 тень облака, 8/9 облако, 10 перистые
            bad = np.isin(scl, [0, 1, 3, 8, 9, 10]) | np.isnan(scl)
            valid = (~bad & np.isfinite(ndvi)).astype("uint8")
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
            for name, arr, dtype in (("ndvi", ndvi, "float32"), ("ndwi", ndwi, "float32")):
                p = profile.copy()
                p["dtype"] = dtype
                path = SENTINEL / f"{name}_{date}.tif"
                with rasterio.open(path, "w", **p) as dst:
                    dst.write(arr.astype(dtype), 1)
            p = profile.copy()
            p["dtype"] = "uint8"
            p["nodata"] = 0
            p["predictor"] = 2  # для целочисленных данных PREDICTOR=3 (плавающая точка) недопустим
            with rasterio.open(SENTINEL / f"valid_{date}.tif", "w", **p) as dst:
                dst.write(valid, 1)

            fin = np.isfinite(ndvi) & np.isfinite(ndwi)
            saved.append(
                {
                    "date": date,
                    "id": scene["id"],
                    "label": label,
                    "cloud_cover_pct": round(scene["properties"].get("eo:cloud_cover", 0) * 100, 3),
                    "valid_pct": round(float(valid.mean()) * 100, 2),
                    "ndvi_mean": round(float(np.nanmean(ndvi[fin])), 4) if fin.any() else None,
                    "ndwi_mean": round(float(np.nanmean(ndwi[fin])), 4) if fin.any() else None,
                    "water_pct": round(float(np.nanmean(ndwi[fin] > 0.0)) * 100, 2) if fin.any() else None,
                }
            )

    if not saved:
        die("ни одной кондиционной сцены Sentinel-2 не получено")

    # Реальный цветной снимок массива (пик затопления — самая «водная» сцена по NDWI)
    best = max(saved, key=lambda s: (s["water_pct"] or 0))
    write_json(SENTINEL / "peak_scene.json", best)
    write_json(SENTINEL / "scenes.json", {"catalogue": catalogue, "saved": saved})

    log(f"пиковая сцена: {best['date']} (вода {best['water_pct']} %)")

    with Env(**GDAL_ENV):
        from common import AOI as _AOI  # noqa: F401

        # Цветное изображение собираем из сцены пика: перечитываем красный/зелёный/синий
        slot = next(c for c in catalogue if c["slot"] == best["label"])
        features = stac_search(slot["start"], slot["end"])
        scene = next(f for f in features if f["id"] == best["id"])
        blue = read_window(scene["assets"]["blue"]["href"], width, height, dst_transform, Resampling.bilinear)
        red = read_window(scene["assets"]["red"]["href"], width, height, dst_transform, Resampling.bilinear)
        green = read_window(scene["assets"]["green"]["href"], width, height, dst_transform, Resampling.bilinear)

    stack = np.dstack([red, green, blue]).astype("float32")
    lo, hi = np.nanpercentile(stack[np.isfinite(stack)], [2, 98])
    rgb = np.clip((stack - lo) / max(hi - lo, 1e-6), 0, 1)
    rgb = (rgb ** (1 / 1.35) * 255).astype("uint8")  # мягкая гамма-коррекция, как в полевых планшетах
    rgb = np.where(np.isfinite(stack), rgb, 0).astype("uint8")

    img = Image.fromarray(rgb, mode="RGB")
    out_dir = SENTINEL
    img.save(out_dir / "array_rgb.jpg", quality=88, optimize=True)
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
            "source": "Sentinel-2 L2A, Copernicus/ESA, COG на AWS Open Data",
            "attribution": "Содержит обработанные данные Copernicus Sentinel (2025)",
        },
    )
    log(f"цветной снимок массива: {width}x{height}, {best['date']}")

    # Копия для фронтенда
    WEB.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy(out_dir / "array_rgb.jpg", WEB / "array_rgb.jpg")
    shutil.copy(SENTINEL / "array_rgb.json", WEB / "array_rgb.json")
    log("снимок скопирован в web/public/data")


if __name__ == "__main__":
    main()
