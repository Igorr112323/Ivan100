"""Общие настройки пайплайна сбора реальных данных проекта «АКВАЧЕК».

Все данные, которые появляются в data/, получены из открытых источников:
  * Sentinel-2 L2A (Copernicus/ESA) — через STAC-каталог Earth Search v1 + публичные COG на AWS Open Data;
  * ERA5 (ECMWF) — через Historical Weather API (Open-Meteo), ряд основан на реанализе ERA5;
  * Copernicus DEM GLO-30 — публичный набор на AWS Open Data.
Никаких синтетических данных: если источник недоступен, пайплайн падает, а не подставляет заглушку.
"""
from __future__ import annotations

import json
import math
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

# Полигон: Красноармейский район Краснодарского края, окрестности ст. Полтавской.
# Точные границы подбирает pipeline/find_rice.py по признаку риса и пишет в data/aoi.json;
# ниже — значение по умолчанию на случай, если подбор ещё не выполнялся.
DEFAULT_AOI = {
    "name": "Рисовый массив, Красноармейский район, ст. Полтавская",
    "lat": 45.3733,
    "lon": 38.1725,
    "bbox": [38.0975, 45.3213, 38.2475, 45.4253],  # minx, miny, maxx, maxy (WGS84)
    "utm_epsg": 32637,  # UTM 37N — зона съёмки Sentinel-2 для этой территории
}

AOI_FILE = DATA / "aoi.json"
AOI = DEFAULT_AOI
if AOI_FILE.exists():
    try:
        AOI = json.loads(AOI_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        AOI = DEFAULT_AOI

# Сезон вегетации риса на Кубани
SEASON_START_MD = "05-01"
SEASON_END_MD = "09-30"

# Расчётная сетка 10 м в географических координатах (единая для всех растров)
LAT_M = 110_574.0
LON_M = 111_320.0 * math.cos(math.radians((AOI["bbox"][1] + AOI["bbox"][3]) / 2.0))
GRID_DX = 10.0 / (LON_M)      # градусы долготы на 10 м на широте полигона
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
