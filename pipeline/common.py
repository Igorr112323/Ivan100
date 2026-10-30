"""Общие настройки пайплайна сбора реальных данных проекта «АКВАЧЕК».

Все данные, которые появляются в data/, получены из открытых источников:
  * Sentinel-2 L2A (Copernicus/ESA) — через STAC-каталог Earth Search v1 + публичные COG на AWS Open Data;
  * ERA5 (ECMWF) — через Historical Weather API (Open-Meteo), ряд основан на реанализе ERA5;
  * Copernicus DEM GLO-30 — публичный набор на AWS Open Data.
Никаких синтетических данных: если источник недоступен, пайплайн падает, а не подставляет заглушку.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WEATHER = DATA / "weather"
SENTINEL = DATA / "sentinel"
OPT = DATA / "optimization"
WEB = ROOT / "web" / "public" / "data"

for _p in (DATA, WEATHER, SENTINEL, OPT):
    _p.mkdir(parents=True, exist_ok=True)

# Полигон выбран по спутниковым признакам риса (см. pipeline/probe_aoi.py):
# доля открытой воды до 35 %, сигнал затопления LSWI >= NDVI у 36-46 % пикселей,
# максимум NDVI приходится на июль-август. Это дельтовый рисовый массив Краснодарского края.
AOI = {
    "name": "Рисовый массив в дельте Кубани, Краснодарский край",
    "lat": 45.50,
    "lon": 37.75,
    "bbox": [37.630, 45.400, 37.870, 45.600],  # minx, miny, maxx, maxy (WGS84)
    "utm_epsg": 32637,  # UTM 37N — зона съёмки Sentinel-2 для этой территории
}

# Сезон вегетации риса на Кубани
SEASON_START_MD = "05-01"
SEASON_END_MD = "09-30"

# Расчётная сетка 10 м в географических координатах (единая для всех растров)
LAT_M = 110_574.0
LON_M = 111_320.0 * 0.7005  # cos(45.50°) ≈ 0.7005, метров в градусе долготы на широте полигона
GRID_DX = 10.0 / (LON_M)      # градусы долготы на 10 м
GRID_DY = 10.0 / LAT_M        # градусы широты на 10 м


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def die(msg: str) -> None:
    log(f"ОШИБКА: {msg}")
    sys.exit(1)


def grid_shape(bbox: list[float] | None = None) -> tuple[int, int, "object"]:
    """Размер и аффинное преобразование расчётной сетки 10 м в EPSG:4326."""
    from rasterio.transform import from_bounds

    if bbox is None:
        bbox = AOI["bbox"]
    w, s, e, n = bbox
    width = int(round((e - w) / GRID_DX))
    height = int(round((n - s) / GRID_DY))
    return width, height, from_bounds(w, s, e, n, width, height)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"записано {path.relative_to(ROOT)} ({path.stat().st_size // 1024} КБ)")
