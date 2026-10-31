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
# Жёсткие фильтры рисового чека (по ТЗ проекта)
MIN_HA, MAX_HA = 10.0, 60.0      # площадь чека: всё вне диапазона — в брак
RICE_FRAC_MIN = 0.50             # доля пикселей чека с NDVI > 0.55 на пике вегетации
DRY_FRAC_MIN = 0.50              # доля пикселей чека с NDWI <= 0 в сентябре (чек сброшен)
# Главный признак риса: вода под зелёным пологом. Открытым зеркалом (NDWI > 0) затопленный
# чек виден только до смыкания всходов; потом воду показывает LSWI >= NDVI - 0.05.
# Требуем воду минимум на двух летних датах: однократная мочка после дождя у пропашных
# бывает, месяц под водой — только у риса.
SUMMER_FROM, SUMMER_TO = "2025-06-01", "2025-08-25"
FLOOD_GREEN_MIN_DATES = 2        # минимум дат затопления
FLOOD_FRAC_MIN = 0.50            # и такая доля площади чека
NDVI_GREEN_MIN = 0.25            # культура видна, полог ещё не сомкнут
LSWI_MARGIN = 0.05               # допуск на шум индекса
# Признак считаем по контуру, а не по пикселю: на 10 м внутри одного чека попадают
# и вал, и вода, и просвет между всходами. Окно 5x5 пикселей (50 м) даёт долю
# затопленной площади — ровно ту величину, которая и стоит в критерии.
FLOOD_SMOOTH_PX = 5
SEEN_FRAC_MIN = 0.50             # ниже этой доли наблюдённых пикселей дата не в счёт
FIELD_NDVI_MIN = 0.50            # границы полей ищем по сомкнутой зелёной массе
NDVI_PEAK_MEAN_MIN = 0.55        # средний NDVI чека на пике вегетации
SOLIDITY_MIN = 0.78              # площадь / площадь выпуклой оболочки
RECT_FILL_MIN = 0.52             # площадь / площадь минимального повёрнутого прямоугольника
CANAL_WIDTH_MAX_M = 60.0         # канал — водная полоса уже 60 м (чеки 200-800 м)
CANAL_REACH_PX = 5               # канал считается своим, если подходит к чеку ближе 50 м
NEIGHBOR_PX = 4                  # соседний чек — не дальше 40 м (часть массива, не одиночное пятно)
NDVI_RICE_MIN = 0.55             # порог сомкнутого полога риса
DEM_HOSTS = (
    "https://copernicus-dem-30m.s3.amazonaws.com",
    "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com",
)


def dem_urls() -> list[str]:
    """Тайлы Copernicus DEM GLO-30, покрывающие полигон (1° x 1°, публичный набор AWS)."""
    w, s_, e, n = AOI["bbox"]
    urls = []
    for lat in {int(s_ // 1), int(n // 1)}:
        for lon in {int(w // 1), int(e // 1)}:
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            name = f"Copernicus_DSM_COG_30_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"
            for host in DEM_HOSTS:
                urls.append(f"{host}/{name}/{name}.tif")
    return urls
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
    """Отметки рельефа: сначала Copernicus DEM GLO-30, при отказе — публичные тайлы Terrarium."""
    global DEM_SOURCE
    for url in dem_urls():
        try:
            dem = _read_dem_one(url, width, height, transform)
            if dem is not None and np.isfinite(dem).mean() > 0.5:
                log(f"DEM: {url.rsplit('/', 2)[-2]}")
                DEM_SOURCE = "Copernicus DEM GLO-30 (публичный набор AWS Open Data)"
                return dem
        except Exception as exc:  # noqa: BLE001
            log(f"DEM {url.rsplit('/', 2)[-2]}: {type(exc).__name__}")
    try:
        dem = read_dem_terrarium(width, height, transform)
        if dem is not None and np.isfinite(dem).mean() > 0.5:
            log("DEM: AWS Terrain Tiles (terrarium), z=13")
            DEM_SOURCE = "AWS Terrain Tiles (публичный тайловый набор высот, ~30 м)"
            return dem
    except Exception as exc:  # noqa: BLE001
        log(f"DEM terrarium: {type(exc).__name__}: {exc}"[:200])
    log("DEM недоступен: отметки не получены")
    return None


def read_dem_terrarium(width: int, height: int, transform) -> np.ndarray:
    """Резервный источник: публичные тайлы высот AWS Terrain Tiles, без ключей."""
    import io
    import math

    import requests
    from PIL import Image
    from rasterio.transform import from_origin

    z = 13
    w, s_, e, n = AOI["bbox"]

    def tile_xy(lat: float, lon: float) -> tuple[int, int]:
        k = 2 ** z
        x = int((lon + 180.0) / 360.0 * k)
        lat_r = math.radians(lat)
        y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * k)
        return x, y

    x0, y1 = tile_xy(s_, w)
    x1, y0 = tile_xy(n, e)
    res = 40_075_016.686 / (256 * 2 ** z)
    origin_x = -20_037_508.34 + x0 * 256 * res
    origin_y = 20_037_508.34 - y0 * 256 * res
    tile_transform = from_origin(origin_x, origin_y, res, res)

    mosaic = np.zeros((256 * (y1 - y0 + 1), 256 * (x1 - x0 + 1)), dtype="float32")
    log(f"тайлы рельефа: {x1 - x0 + 1} x {y1 - y0 + 1} на зуме {z}")
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{tx}/{ty}.png"
            r = requests.get(url, timeout=90)
            r.raise_for_status()
            im = np.asarray(Image.open(io.BytesIO(r.content))).astype("float32")
            mosaic[(ty - y0) * 256:(ty - y0 + 1) * 256, (tx - x0) * 256:(tx - x0 + 1) * 256] = (
                im[..., 0] * 256.0 + im[..., 1] + im[..., 2] / 256.0 - 32768.0
            )

    out = np.full((height, width), np.nan, dtype="float32")
    reproject(
        mosaic,
        out,
        src_transform=tile_transform,
        src_crs=CRS.from_epsg(3857),
        dst_transform=transform,
        dst_crs=CRS.from_epsg(4326),
        resampling=Resampling.bilinear,
        src_nodata=-32768.0,
    )
    return out


def _read_dem_one(url: str, width: int, height: int, transform) -> np.ndarray:
    with Env(**GDAL_ENV), rasterio.open(f"/vsicurl/{url}") as src:
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


def segment(field: np.ndarray, min_distance: int = 10, smooth: float = 2.5) -> np.ndarray:
    """Разделение слипшихся чеков водоразделом по карте расстояний.

    Карта расстояний сглаживается гауссианой: без этого на рваной кромке маски
    находится десяток ложных максимумов внутри одного чека, и он рассыпается
    на обрывки по 2-3 га. Маркеры ставятся не ближе 100 м друг к другу —
    вал между чеками 10-30 м, сам чек 300-800 м.
    """
    clean = ndi.binary_opening(field, structure=np.ones((3, 3)))
    clean = ndi.binary_closing(clean, structure=np.ones((3, 3)))
    px_per_ha = 100
    clean = ndi.binary_opening(clean, structure=np.ones((2, 2)))
    dist = ndi.distance_transform_edt(clean)
    dist_s = ndi.gaussian_filter(dist, smooth)
    coords = peak_local_max(dist_s, min_distance=min_distance, labels=clean, exclude_border=False)
    if len(coords) == 0:
        return ndi.label(clean)[0]
    mask = np.zeros(dist.shape, dtype=bool)
    mask[tuple(coords.T)] = True
    markers, _ = ndi.label(mask)
    labels = watershed(-dist_s, markers, mask=clean)
    # Убираем région меньше 3 га: это огрехи маски, а не чеки
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    small = sizes < (3 * px_per_ha)
    labels[small[labels]] = 0
    return labels


def _size_histogram(labels: np.ndarray) -> None:
    """Размеры всех контуров до фильтров: видно, чем кончилась сегментация."""
    sizes = np.bincount(labels.ravel())[1:] * 100.0 / 10_000.0
    if sizes.size == 0:
        return
    edges = [0, 1, 3, 5, 10, 20, 40, 60, 100, 1e9]
    log("  размеры контуров до фильтров, га:")
    for i in range(len(edges) - 1):
        n = int(((sizes >= edges[i]) & (sizes < edges[i + 1])).sum())
        ha = float(sizes[(sizes >= edges[i]) & (sizes < edges[i + 1])].sum())
        if n:
            left = "0" if i == 0 else f"{edges[i]:.0f}"
            right = "∞" if edges[i + 1] > 1e8 else f"{edges[i + 1]:.0f}"
            log(f"    {left}..{right} га: {n:5d} шт, {ha:8.1f} га")


def canal_mask(water: np.ndarray) -> np.ndarray:
    """Каналы: узкие водные полосы. Широкие пятна — это чеки и пруды, не каналы.

    Ширина одной маской расстояний не ловится: у чека своя кромка тоже «узкая».
    Поэтому широкие пятна выделяются морфологическим раскрытием (эрозия 7x7 →
    наращивание 7x7: прямоугольный чек возвращается целиком, полоса до 60 м исчезает),
    а каналом остаётся вода вне широких пятен и не шире 60 м.
    """
    dist = ndi.distance_transform_edt(water)  # расстояние до сухого пикселя, пиксель 10 м
    width_m = (2.0 * dist - 1.0) * 10.0  # полная ширина водной полосы в метрах
    inner = ndi.binary_erosion(water, structure=np.ones((7, 7)))
    wide = ndi.binary_dilation(inner, structure=np.ones((7, 7)))
    return water & ~wide & (width_m <= CANAL_WIDTH_MAX_M)


def solidity_and_rect(geom: dict) -> tuple[float, float]:
    """Две меры «четырёхугольности»: по выпуклой оболочке и по описанному прямоугольнику."""
    from shapely.geometry import shape

    poly = shape(geom)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return 0.0, 0.0
    hull = poly.convex_hull
    rect = poly.minimum_rotated_rectangle
    solidity = float(poly.area / hull.area) if hull.area > 0 else 0.0
    rect_fill = float(poly.area / rect.area) if rect.area > 0 else 0.0
    return solidity, rect_fill


def pick_dates(scenes: list[dict]) -> tuple[list[str], str, str]:
    """Даты затопления (обе, обе без дождя), пик вегетации и сброс воды.

    Роли датам присваивает fetch_sentinel по ключевым датам из разведки, поэтому
    здесь не приходится угадывать, какая из майско-июньских сцен «про затопление».
    """
    dated = sorted(s["date"] for s in scenes)
    roles = {s["date"]: s.get("role", "") for s in scenes}
    ndvi_mean = {s["date"]: (s.get("ndvi_mean") or -1.0) for s in scenes}
    flood = [d for d in dated if roles.get(d, "").startswith("flood")]
    if len(flood) < FLOOD_GREEN_MIN_DATES:
        flood = [d for d in dated if SUMMER_FROM <= d <= SUMMER_TO]
    vegs = [d for d in dated if roles.get(d, "").startswith("veg")] or dated
    dry_date = next((d for d in dated if roles.get(d) == "dry"), dated[-1])
    peak_date = max(vegs, key=lambda d: ndvi_mean[d])
    return flood, dry_date, peak_date


def main() -> None:
    scenes = json.loads((SENTINEL / "scenes.json").read_text(encoding="utf-8"))["saved"]
    summer_dates, dry_date, peak_date = pick_dates(scenes)
    log(f"летних дат {len(summer_dates)}: {summer_dates} | сброс воды {dry_date} | "
        f"пик NDVI {peak_date}")
    if len(summer_dates) < FLOOD_GREEN_MIN_DATES:
        raise SystemExit(f"летних сцен {len(summer_dates)} — признак риса по минимум "
                         f"{FLOOD_GREEN_MIN_DATES} датам не проверить")
    date = peak_date
    peak = next((s for s in scenes if s["date"] == date), scenes[0])

    width, height, transform = grid_shape()
    # Затопление по каждой дате: сразу в виде доли площади в окне 50 м. На 10 м
    # внутри чека попадают и вал, и вода, и просвет между всходами, поэтому
    # поштучный пиксель шумит, а окно даёт ровно ту величину, что в критерии.
    flood_frac_img: dict[str, np.ndarray] = {}
    flood_seen_img: dict[str, np.ndarray] = {}
    flooded_any = np.zeros((height, width), dtype=bool)
    for d in summer_dates:
        ndvi_d = load_raster(SENTINEL / f"ndvi_{d}.tif")
        ndwi_d = load_raster(SENTINEL / f"ndwi_{d}.tif")
        lswi_d = load_raster(SENTINEL / f"lswi_{d}.tif")
        valid_d = load_raster(SENTINEL / f"valid_{d}.tif").astype(bool)
        wet = ((ndwi_d > 0.0) | (lswi_d >= ndvi_d - LSWI_MARGIN)) & valid_d & np.isfinite(ndwi_d)
        # Считаем долю по наблюдённым пикселям: иначе облако над чеком читается
        # как «воды нет» и чек выбрасывается ни за что.
        num = ndi.uniform_filter(wet.astype("float32"), size=FLOOD_SMOOTH_PX, mode="nearest")
        den = ndi.uniform_filter(valid_d.astype("float32"), size=FLOOD_SMOOTH_PX, mode="nearest")
        flood_frac_img[d] = num / np.maximum(den, 1e-6)
        flood_seen_img[d] = den  # uniform_filter возвращает среднее, то есть уже долю
        flooded_any |= wet
        log(f"  {d}: затопление {wet.mean() * 100:5.2f} % полигона | NDWI > 0 у "
            f"{(ndwi_d > 0.0)[valid_d].mean() * 100:5.2f} % | NDVI средний "
            f"{np.nanmean(ndvi_d):.3f} | в окне {FLOOD_SMOOTH_PX * 10} м >= {FLOOD_FRAC_MIN:.0%} у "
            f"{(flood_frac_img[d] >= FLOOD_FRAC_MIN).mean() * 100:5.2f} %")
    ndvi = load_raster(SENTINEL / f"ndvi_{peak_date}.tif")
    ndwi = load_raster(SENTINEL / f"ndwi_{peak_date}.tif")
    ndwi_dry = load_raster(SENTINEL / f"ndwi_{dry_date}.tif")
    valid_dry = load_raster(SENTINEL / f"valid_{dry_date}.tif").astype(bool)

    # Границы полей — по сомкнутой зелёной массе на пике: валы между чеками
    # на снимке видны как просветы, и водораздел по ним разделяет поля.
    ndvi_peak_img = load_raster(SENTINEL / f"ndvi_{peak_date}.tif")
    valid_peak = load_raster(SENTINEL / f"valid_{peak_date}.tif").astype(bool)
    field = (ndvi_peak_img > FIELD_NDVI_MIN) & valid_peak
    field = ndi.binary_opening(field, structure=np.ones((3, 3)))
    field = ndi.binary_closing(field, structure=np.ones((5, 5)))
    log(f"зелёные поля на пике: {field.mean() * 100:.2f} % полигона "
        f"({field.sum() * 100.0 / 10_000:.0f} га)")
    canals = canal_mask(flooded_any)
    rice_field = field & ~canals
    log(f"каналы (полосы до {CANAL_WIDTH_MAX_M:.0f} м): {canals.mean() * 100:.2f} % полигона")
    dist_to_canal = ndi.distance_transform_edt(~canals).astype("float32") * 10.0  # метры

    labels = segment(rice_field)
    n_labels = int(labels.max())
    log(f"водораздел выделил {n_labels} контуров")
    _size_histogram(labels)

    dem = read_dem(width, height, transform)

    stack = {}
    for sc in scenes:
        d = sc["date"]
        stack[d] = {
            "ndvi": load_raster(SENTINEL / f"ndvi_{d}.tif"),
            "ndwi": load_raster(SENTINEL / f"ndwi_{d}.tif"),
            "valid": load_raster(SENTINEL / f"valid_{d}.tif").astype(bool),
        }

    rejects: dict[str, int] = {}
    stats = {"solidity": [], "rect": [], "rice": [], "dry": [], "canal_m": [], "flood": []}

    def reject(reason: str) -> None:
        rejects[reason] = rejects.get(reason, 0) + 1

    checks: list[dict] = []
    series: list[dict] = []

    for label_id in range(1, n_labels + 1):
        geom_mask = labels == label_id
        ys, xs = np.nonzero(geom_mask)
        area_ha_px = float(geom_mask.sum()) * 100.0 / 10_000.0
        if area_ha_px < MIN_HA or area_ha_px > MAX_HA:
            reject(f"площадь вне {MIN_HA:.0f}-{MAX_HA:.0f} га")
            continue

        shapes = list(features.shapes(geom_mask.astype("uint8"), mask=geom_mask, transform=transform))
        if not shapes:
            reject("не векторизуется")
            continue
        geom, _ = max(shapes, key=lambda s: s[1])
        ring = [[round(float(x), 6), round(float(y), 6)] for x, y in geom["coordinates"][0]]
        if len(ring) < 5:
            reject("вырожденный контур")
            continue
        geom = {"type": "Polygon", "coordinates": [ring]}

        try:
            area_m2 = abs(GEOD.geometry_area_perimeter(geom)[0])
        except Exception:  # noqa: BLE001
            area_m2 = area_ha_px * 10_000.0
        area_ha = area_m2 / 10_000.0
        if area_ha < MIN_HA or area_ha > MAX_HA:
            reject(f"площадь вне {MIN_HA:.0f}-{MAX_HA:.0f} га (эллипсоид)")
            continue

        # признак риса по контуру: полог на пике и сброс воды осенью
        m_veg = geom_mask & valid_peak & np.isfinite(ndvi_peak_img)
        if m_veg.sum() < 20:
            reject("нет годных пикселей на дату пика")
            continue
        peak_ndvi = float(np.nanmean(ndvi_peak_img[m_veg]))
        rice_frac = float(np.nanmean(ndvi_peak_img[m_veg] > NDVI_RICE_MIN))
        stats["rice"].append(rice_frac)
        if rice_frac < RICE_FRAC_MIN:
            reject(f"нет полога риса (NDVI > {NDVI_RICE_MIN}) на пике")
            continue
        if peak_ndvi < NDVI_PEAK_MEAN_MIN:
            reject(f"средний NDVI на пике ниже {NDVI_PEAK_MEAN_MIN}")
            continue

        # главный признак: вода минимум на двух датах затопления, оба раза без
        # дождя перед съёмкой. Доля считается по площади контура.
        seen = {d: float(flood_seen_img[d][geom_mask].mean()) for d in summer_dates}
        if min(seen.values()) < SEEN_FRAC_MIN:
            reject(f"на дате затопления облако закрывает более "
                   f"{(1 - SEEN_FRAC_MIN):.0%} чека: "
                   f"{', '.join(f'{d} {v:.2f}' for d, v in seen.items())}")
            continue
        fracs = {d: float(flood_frac_img[d][geom_mask].mean()) for d in summer_dates}
        flood_frac = min(fracs.values())
        stats["flood"].append(flood_frac)
        if flood_frac < FLOOD_FRAC_MIN:
            reject(f"затоплено менее {FLOOD_FRAC_MIN:.0%} площади: "
                   f"{', '.join(f'{d} {v:.2f}' for d, v in fracs.items())}")
            continue

        m_dry = geom_mask & valid_dry & np.isfinite(ndwi_dry)
        dry_frac = float(np.nanmean(ndwi_dry[m_dry] <= 0.0)) if m_dry.sum() >= 20 else 0.0
        stats["dry"].append(dry_frac)
        if dry_frac < DRY_FRAC_MIN:
            reject("не сброшен в сентябре (вода стоит круглый год)")
            continue

        solidity, rect_fill = solidity_and_rect(geom)
        stats["solidity"].append(solidity)
        stats["rect"].append(rect_fill)
        if solidity < SOLIDITY_MIN:
            reject("форма не чек (изрезанный контур)")
            continue
        if rect_fill < RECT_FILL_MIN:
            reject("форма не четырёхугольная")
            continue

        canal_m = float(dist_to_canal[geom_mask].min())
        if canal_m > CANAL_REACH_PX * 10.0:
            reject(f"нет канала ближе {CANAL_REACH_PX * 10} м")
            continue
        stats["canal_m"].append(canal_m)

        others = (labels > 0) & ~geom_mask
        if not (ndi.binary_dilation(geom_mask, iterations=NEIGHBOR_PX) & others).any():
            reject("одиночное пятно, вне массива")
            continue

        elev = float(np.nanmean(dem[geom_mask])) if dem is not None else None
        cy, cx = float(ys.mean()), float(xs.mean())
        clon, clat = transform * (cx, cy)

        row = {
            "id": len(checks) + 1,
            "area_ha": round(area_ha, 2),
            "elev_m": round(elev, 2) if elev is not None else None,
            "rice_frac": round(rice_frac, 3),
            "peak_ndvi": round(peak_ndvi, 3),
            "flood_frac": round(flood_frac, 3),
            "flood_fracs": {d: round(v, 3) for d, v in fracs.items()},
            "dry_frac": round(dry_frac, 3),
            "canal_m": round(canal_m, 1),
            "solidity": round(solidity, 3),
            "rect_fill": round(rect_fill, 3),
            "ndvi_peak": round(float(np.nanmean(ndvi[geom_mask])), 4) if np.isfinite(ndvi[geom_mask]).any() else None,
            "ndwi_peak": round(float(np.nanmean(ndwi[geom_mask])), 4) if np.isfinite(ndwi[geom_mask]).any() else None,
            "centroid": [round(clon, 6), round(clat, 6)],
        }
        checks.append({"row": row, "geom": geom, "mask": geom_mask, "area_ha": area_ha})

        # сколько соседних чеков у контура — признак принадлежности массиву
        ring_mask = ndi.binary_dilation(geom_mask, iterations=NEIGHBOR_PX) & ~geom_mask
        nb = np.unique(labels[ring_mask])
        row["neighbors"] = int(sum(1 for v in nb if v > 0))

    log("")
    log("=== отбраковка контуров ===")
    log(f"  всего контуров после водораздела: {n_labels}")
    for reason, n in sorted(rejects.items(), key=lambda kv: -kv[1]):
        log(f"  отбраковано {n:5d} — {reason}")
    for key, label in (("solidity", "заполнение выпуклой оболочки"), ("rect", "заполнение прямоугольника"),
                       ("rice", "доля пикселей с пологом риса"), ("flood", "доля пикселей под водой летом"),
                       ("dry", "доля пикселей без воды в сентябре"),
                       ("canal_m", "расстояние до канала, м")):
        if stats[key]:
            arr = np.array(stats[key])
            log(f"  {label}: медиана {np.median(arr):.2f}, 10-й процентиль {np.percentile(arr, 10):.2f}, "
                f"90-й {np.percentile(arr, 90):.2f}")
    log(f"  принято чеков: {len(checks)}")
    log("")

    if not checks:
        from common import die

        die("контуры чеков не выделены — сегментация или фильтры не сработали")

    elevations = [c["row"]["elev_m"] for c in checks if c["row"]["elev_m"] is not None]
    mean_elev = float(np.mean(elevations)) if elevations else 0.0

    features_out = []
    for c in checks:
        row = c["row"]
        dz = (row["elev_m"] - mean_elev) if row["elev_m"] is not None else 0.0
        row["dz_m"] = round(dz, 2)
        # Гидравлический КПД подачи: упрощённая модель (калибровка — этап 2 проекта)
        row["kpd"] = round(max(0.55, min(0.92, 0.80 - 0.012 * dz)), 3)
        features_out.append({"type": "Feature", "properties": row, "geometry": c["geom"]})

        ser = []
        for sc in scenes:
            d = sc["date"]
            st = stack[d]
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

    areas = [c["area_ha"] for c in checks]
    collection = {
        "type": "FeatureCollection",
        "name": "Рисовые чеки полигона (оцифрованы по Sentinel-2)",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "metadata": {
            "source": "Контуры получены сегментацией маски риса по снимкам Sentinel-2 L2A "
                      "за три даты сезона 2025",
            "scene_date": date,
            "scene_id": peak["id"],
            "method": "маска риса (вода под зелёным пологом минимум на двух летних датах + "
                      "NDVI > 0.55 на пике + сухо осенью) -> морфологическая очистка -> "
                      "водораздел по карте расстояний -> векторизация -> фильтры рисового чека",
            "rice_test": {
                "flood_dates": summer_dates,
                "flood_date": summer_dates[0],
                "veg_date": peak_date,
                "dry_date": dry_date,
                "veg_ndvi_min": NDVI_RICE_MIN,
                "rice_frac_min": RICE_FRAC_MIN,
                "dry_frac_min": DRY_FRAC_MIN,
                "flood_green_min_dates": FLOOD_GREEN_MIN_DATES,
                "flood_frac_min": FLOOD_FRAC_MIN,
                "ndvi_green_min": NDVI_GREEN_MIN,
                "ndvi_rice_min": NDVI_RICE_MIN,
                "lswi_margin": LSWI_MARGIN,
                "summer_dates": summer_dates,
                "area_ha": [MIN_HA, MAX_HA],
                "solidity_min": SOLIDITY_MIN,
                "rect_fill_min": RECT_FILL_MIN,
                "canal_reach_m": CANAL_REACH_PX * 10,
            },
            "dem": DEM_SOURCE,
            "aoi": {
                "name": AOI["name"],
                "bbox": AOI["bbox"],
                "lat": round((AOI["bbox"][1] + AOI["bbox"][3]) / 2, 4),
                "lon": round((AOI["bbox"][0] + AOI["bbox"][2]) / 2, 4),
            },
            "rice_mask_ha": round(float(rice_field.sum()) * 100.0 / 10_000.0, 1),
            "count": len(features_out),
            "total_ha": round(sum(areas), 1),
            "min_ha": round(float(np.min(areas)), 2),
            "median_ha": round(float(np.median(areas)), 2),
            "max_ha": round(float(np.max(areas)), 2),
            "mean_elev_m": round(mean_elev, 2),
            "rejects": rejects,
        },
        "features": features_out,
    }

    write_json(DATA / "checks.geojson", collection)
    write_json(DATA / "checks_series.json", {"checks": series, "scenes": scenes})

    WEB.mkdir(parents=True, exist_ok=True)
    shutil.copy(DATA / "checks.geojson", WEB / "checks.geojson")
    shutil.copy(DATA / "checks_series.json", WEB / "checks_series.json")
    log(f"чеков: {len(features_out)}, площадь: {collection['metadata']['total_ha']} га, "
        f"медиана {collection['metadata']['median_ha']} га, средняя отметка: {mean_elev:.2f} м")


if __name__ == "__main__":
    main()
