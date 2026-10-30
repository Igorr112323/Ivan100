"""Визуальная проверка чеков человеком: пять случайных контуров тремя снимками.

На каждый чек — одна картинка из трёх панелей:
  слева  — цветной снимок на дату затопления (видно зеркало воды);
  centre — NDVI на дату полога (видно сомкнутую зелень риса);
  справа — NDWI на дату сброса (видно, что чек сухой).
На всех трёх панелях нарисован контур чека. Числа не заменяют глаз: по картинке
решается, похож контур на рисовый чек или нет.

Выход: data/verify/check_<id>.png, data/verify/report.md, data/verify/verify.json.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio import features
from rasterio.windows import Window
from scipy import ndimage as ndi

from common import DATA, SENTINEL, grid_shape, log, write_json

SEED = 20251031
N_CHECKS = 5
PANEL = 260
MARGIN = 0.45  # запас вокруг чека, доля от его размера

NDVI_STOPS = [(-0.2, (150, 130, 105)), (0.1, (206, 196, 158)), (0.35, (226, 220, 150)),
              (0.55, (156, 186, 96)), (0.75, (86, 132, 60)), (0.9, (36, 84, 36))]
NDWI_STOPS = [(-0.6, (176, 156, 124)), (-0.2, (206, 194, 164)), (0.0, (222, 216, 186)),
              (0.15, (96, 148, 168)), (0.5, (26, 74, 104))]
INK = (27, 31, 36)
SEAL = (180, 52, 31)


def ramp(values: np.ndarray, stops: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    """Кусочно-линейная цветовая шкала: значения -> RGB."""
    xs = np.array([s[0] for s in stops], dtype="float32")
    cols = np.array([s[1] for s in stops], dtype="float32")
    out = np.zeros(values.shape + (3,), dtype="float32")
    for ch in range(3):
        out[..., ch] = np.interp(values, xs, cols[:, ch])
    return out


def square_window(coords: list[list[float]], transform, width: int, height: int) -> Window:
    """Квадратное подокно вокруг контура: панели остаются с правильными пропорциями."""
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    span = max(max(xs) - min(xs), max(ys) - min(ys)) * (1.0 + 2.0 * MARGIN)
    col_c, row_c = ~transform * (cx, cy)
    dlon = span / 2.0
    dlat = span / 2.0
    col0, row1 = ~transform * (cx - dlon, cy - dlat)
    col1, row0 = ~transform * (cx + dlon, cy + dlat)
    side = int(round(max(abs(row1 - row0), abs(col1 - col0))))
    side = max(side, 8)
    r0 = int(round(row_c - side / 2.0))
    c0 = int(round(col_c - side / 2.0))
    r0 = max(0, min(r0, height - side)) if height > side else 0
    c0 = max(0, min(c0, width - side)) if width > side else 0
    side = min(side, height - r0, width - c0)
    return Window(c0, r0, side, side)


def main() -> None:
    gj = json.loads((DATA / "checks.geojson").read_text(encoding="utf-8"))
    feats = gj["features"]
    meta = gj["metadata"]
    test = meta["rice_test"]

    width, height, transform = grid_shape()
    rgb_arr = np.asarray(Image.open(SENTINEL / "array_rgb.jpg").convert("RGB"))
    with rasterio.open(SENTINEL / f"ndvi_{test['veg_date']}.tif") as src:
        ndvi_veg = src.read(1)
    with rasterio.open(SENTINEL / f"ndwi_{test['dry_date']}.tif") as src:
        ndwi_dry = src.read(1)

    out_dir = DATA / "verify"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("check_*.png"):
        old.unlink()

    rng = random.Random(SEED)
    picks = rng.sample(feats, min(N_CHECKS, len(feats)))
    log(f"на проверку взято {len(picks)} чеков из {len(feats)} (seed {SEED})")

    rows = []
    for f in picks:
        p = f["properties"]
        win = square_window(f["geometry"]["coordinates"][0], transform, width, height)
        r0, c0 = int(win.row_off), int(win.col_off)
        side = int(win.width)
        win_transform = rasterio.windows.transform(win, transform)

        mask = features.rasterize(
            [(f["geometry"], 1)], out_shape=(side, side), transform=win_transform, fill=0, all_touched=True
        ).astype(bool)
        if mask.sum() == 0:
            log(f"чек {p['id']}: контур не попал в подокно, пропуск")
            continue
        outline = mask & ~ndi.binary_erosion(mask, structure=np.ones((3, 3)))
        outline = ndi.binary_dilation(outline, structure=np.ones((3, 3)))

        sub_rgb = rgb_arr[r0:r0 + side, c0:c0 + side]
        sub_veg = np.nan_to_num(ndvi_veg[r0:r0 + side, c0:c0 + side].astype("float32"), nan=-1.0)
        sub_dry = np.nan_to_num(ndwi_dry[r0:r0 + side, c0:c0 + side].astype("float32"), nan=-1.0)

        p1 = Image.fromarray(sub_rgb, mode="RGB").resize((PANEL, PANEL), Image.BILINEAR)
        p2 = Image.fromarray(ramp(sub_veg, NDVI_STOPS).astype("uint8"), mode="RGB").resize(
            (PANEL, PANEL), Image.BILINEAR)
        p3 = Image.fromarray(ramp(sub_dry, NDWI_STOPS).astype("uint8"), mode="RGB").resize(
            (PANEL, PANEL), Image.BILINEAR)

        canvas = Image.new("RGB", (PANEL * 3 + 24, PANEL + 34), (239, 234, 224))
        canvas.paste(p1, (0, 30))
        canvas.paste(p2, (PANEL + 12, 30))
        canvas.paste(p3, (2 * (PANEL + 12), 30))

        # контур поверх всех трёх панелей
        edge = outline
        ov = np.asarray(Image.fromarray((edge * 255).astype("uint8"), mode="L").resize(
            (PANEL, PANEL), Image.NEAREST)) > 96
        for x_off in (0, PANEL + 12, 2 * (PANEL + 12)):
            box = np.asarray(canvas.crop((x_off, 30, x_off + PANEL, 30 + PANEL)))
            box[ov] = SEAL
            canvas.paste(Image.fromarray(box, mode="RGB"), (x_off, 30))

        draw = ImageDraw.Draw(canvas)
        draw.text((4, 9), f"чек {p['id']} · {p['area_ha']} га · затопление {test['flood_date']}", fill=INK)
        draw.text((PANEL + 16, 9), f"NDVI · полог {test['veg_date']}", fill=INK)
        draw.text((2 * (PANEL + 12) + 4, 9), f"NDWI · сброс {test['dry_date']}", fill=INK)

        path = out_dir / f"check_{p['id']}.png"
        canvas.save(path, optimize=True)
        log(f"  чек {p['id']}: {path.name} | {p['area_ha']} га | отметка {p['elev_m']} м | "
            f"полог {p['rice_frac']} | сухо {p['dry_frac']} | канал {p['canal_m']} м | "
            f"прямоугольник {p['rect_fill']} | соседей {p.get('neighbors')}")
        rows.append(
            {
                "id": p["id"],
                "image": path.name,
                "area_ha": p["area_ha"],
                "elev_m": p["elev_m"],
                "rice_frac": p["rice_frac"],
                "dry_frac": p["dry_frac"],
                "canal_m": p["canal_m"],
                "solidity": p["solidity"],
                "rect_fill": p["rect_fill"],
                "neighbors": p.get("neighbors"),
            }
        )

    lines = [
        "# Визуальная проверка чеков",
        "",
        f"Полигон: {meta['aoi']['name']} — {meta['count']} чеков, {meta['total_ha']} га.",
        f"Случайная выборка {len(rows)} чеков, seed {SEED}.",
        "",
        "Панели слева направо: цветной снимок на дату затопления, NDVI на дату полога, NDWI на дату сброса.",
        f"Красный контур — граница чека. Окно картинки: сторона контура плюс {int(MARGIN * 100)} % запаса.",
        "",
        "| чек | га | отметка, м | доля полога | доля сухого | до канала, м | прямоугольник | соседей | картинка |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['area_ha']} | {r['elev_m']} | {r['rice_frac']} | {r['dry_frac']} | "
            f"{r['canal_m']} | {r['rect_fill']} | {r['neighbors']} | `{r['image']}` |"
        )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(out_dir / "verify.json", {"seed": SEED, "checks": rows, "rice_test": test})
    log(f"отчёт: data/verify/report.md ({len(rows)} чеков)")


if __name__ == "__main__":
    main()
