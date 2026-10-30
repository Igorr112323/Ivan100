"""Доказательство, что культура в контурах — рис.

Критерий (все три условия одновременно, по пикселям внутри контура):
  1) NDWI > 0 не менее чем на 50 % площади чека в июне — чек залит;
  2) средний NDVI > 0,55 в июле — сомкнутый полог;
  3) NDWI <= 0 в сентябре — чек сброшен перед уборкой.

Проверяется на всех контурах, а не на выборке. Для десяти чеков (пять прежних из
проверки и пять новых случайных) выводятся картинки: четыре панели на каждый чек —
май, июнь, июль, сентябрь.

Дополнительно проверяется белое пятно на чеке 18: считается доля ярких пикселей
и их влияние на средний NDVI.

Выход: data/rice_proof.json, data/verify/proof_*.png, журнал в data/_log/rice_proof.txt
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio import features
from rasterio.enums import Resampling
from rasterio.env import Env
from rasterio.windows import Window
from scipy import ndimage as ndi

from common import AOI, DATA, SENTINEL, grid_shape, log, write_json
from fetch_sentinel import GDAL_ENV, cloud_of, pick_scenes, read_merged, stac_search, tile_of
from verify_checks import NDVI_STOPS, NDWI_STOPS, SEAL, ramp

SEED_OLD = 20251031  # прежняя пятёрка: чеки 65, 93, 38, 111, 18
SEED_NEW = 20261102  # новая случайная пятёрка
PANEL = 260
MARGIN = 0.45

# Окна дат. Июнь — основное доказательство: в конце мая чеки могут быть ещё не залиты.
SLOTS = [
    ("май", "2025-05-15", "2025-05-31", "2025-05-22"),
    ("июнь", "2025-06-05", "2025-06-22", None),
    ("июль", "2025-07-20", "2025-08-05", None),
    ("сентябрь", "2025-09-05", "2025-09-30", None),
]
CLOUD_MAX = 0.25
BRIGHT_P = 98.0  # яркие пиксели: выше 98-го процентиля по сумме каналов


def font(size: int) -> ImageFont.FreeTypeFont:
    """Кириллический шрифт: DejaVu Sans поставляется вместе с matplotlib."""
    import matplotlib

    path = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
    return ImageFont.truetype(str(path), size)


def pick(items_all: list[dict], prefer: str | None) -> list[dict]:
    return pick_scenes(items_all, prefer)


def stats_in(mask: np.ndarray, *arrays) -> list[float]:
    out = []
    for a in arrays:
        v = a[mask]
        v = v[np.isfinite(v)]
        out.append(float(v.mean()) if v.size else float("nan"))
    return out


def main() -> None:
    gj = json.loads((DATA / "checks.geojson").read_text(encoding="utf-8"))
    feats = gj["features"]
    width, height, transform = grid_shape()
    log(f"полигон {AOI['bbox']}, сетка {width} x {height}, чеков {len(feats)}")

    per: dict[str, dict] = {}
    with Env(**GDAL_ENV):
        for name, start, end, prefer in SLOTS:
            found = stac_search(start, end)
            items = pick(found, prefer)
            if not items:
                log(f"{name}: кондиционной сцены нет")
                continue
            date = items[0]["properties"]["datetime"][:10]
            log(f"{name}: {date} | сцен {len(found)} | тайлы {[tile_of(i) for i in items]} | "
                f"облака {min(cloud_of(i) for i in items) * 100:.2f} %")
            bands = {}
            for b in ("red", "green", "nir", "blue", "scl"):
                bands[b] = read_merged(items, b, width, height, transform,
                                       Resampling.nearest if b == "scl" else Resampling.bilinear)
            good = ~(np.isin(bands["scl"], [0, 1, 3, 8, 9, 10]) | np.isnan(bands["scl"]))
            with np.errstate(invalid="ignore", divide="ignore"):
                ndvi = (bands["nir"] - bands["red"]) / (bands["nir"] + bands["red"])
                ndwi = (bands["green"] - bands["nir"]) / (bands["green"] + bands["nir"])
            ndvi = np.where(good, ndvi, np.nan)
            ndwi = np.where(good, ndwi, np.nan)
            bright = np.nansum(np.dstack([bands["red"], bands["green"], bands["blue"]]), axis=2)
            per[name] = {
                "date": date, "scene": items[0]["id"], "tiles": [tile_of(i) for i in items],
                "cloud": round(min(cloud_of(i) for i in items) * 100, 2),
                "ndvi": ndvi, "ndwi": ndwi, "rgb": bands,
                "bright": bright, "good": good,
                "valid_pct": round(float(good.mean()) * 100, 2),
            }
            for b in ("red", "green", "nir", "blue"):
                per[name]["rgb"][b] = np.where(good, bands[b], np.nan)
            log(f"    годных пикселей {good.mean() * 100:.1f} % | NDWI > 0 у "
                f"{np.nanmean(ndwi > 0) * 100:.1f} % | NDVI средний {np.nanmean(ndvi):+.3f}")

    for need in ("июнь", "июль", "сентябрь"):
        if need not in per:
            raise SystemExit(f"нет сцены на {need} — доказательство невозможно")

    june, july, sept = per["июнь"], per["июль"], per["сентябрь"]
    may = per.get("май")

    # ---- показатели по каждому контуру
    rows = []
    for f in feats:
        p = f["properties"]
        m = features.rasterize([(f["geometry"], 1)], out_shape=(height, width),
                               transform=transform, fill=0, all_touched=False).astype(bool)
        if m.sum() < 50:
            continue
        wet_june = float(np.nanmean(june["ndwi"][m] > 0.0))
        ndvi_july = float(np.nanmean(july["ndvi"][m]))
        ndwi_sept = float(np.nanmean(sept["ndwi"][m]))
        dry_sept = float(np.nanmean(sept["ndwi"][m] <= 0.0))
        ok = wet_june >= 0.50 and ndvi_july > 0.55 and ndwi_sept <= 0.0
        row = {
            "id": p["id"], "area_ha": p["area_ha"],
            "wet_june_pct": round(wet_june * 100, 1),
            "ndvi_july": round(ndvi_july, 3),
            "ndwi_sept": round(ndwi_sept, 3),
            "dry_sept_pct": round(dry_sept * 100, 1),
            "rice": bool(ok),
        }
        if may is not None:
            row["wet_may_pct"] = round(float(np.nanmean(may["ndwi"][m] > 0.0)) * 100, 1)
            row["ndvi_may"] = round(float(np.nanmean(may["ndvi"][m])), 3)
            # влияние яркого пятна на NDVI мая
            br = may["bright"][m]
            thr = float(np.nanpercentile(br, BRIGHT_P)) if np.isfinite(br).any() else np.inf
            hot = m & (br >= thr) & np.isfinite(br)
            row["bright_pct"] = round(float(hot.sum() / max(m.sum(), 1)) * 100, 1)
            cold = m & ~hot
            row["ndvi_may_no_bright"] = round(float(np.nanmean(may["ndvi"][cold])), 3) if cold.sum() > 20 else None
        rows.append(row)

    n_rice = sum(1 for r in rows if r["rice"])
    log("")
    log("=== проверка критерия риса по всем контурам ===")
    log(f"  контуров проверено: {len(rows)}")
    log(f"  прошло все три условия: {n_rice} ({n_rice / max(len(rows), 1) * 100:.0f} %)")
    wj = np.array([r["wet_june_pct"] for r in rows])
    nj = np.array([r["ndvi_july"] for r in rows])
    log(f"  доля пикселей с водой в июне ({june['date']}): медиана {np.median(wj):.0f} %, "
        f"10-й процентиль {np.percentile(wj, 10):.0f} %, 90-й {np.percentile(wj, 90):.0f} %")
    log(f"  NDVI в июле ({july['date']}): медиана {np.median(nj):.2f}, "
        f"10-й процентиль {np.percentile(nj, 10):.2f}")
    if may is not None:
        wm = np.array([r["wet_may_pct"] for r in rows])
        log(f"  для сравнения, доля воды в мае ({may['date']}): медиана {np.median(wm):.0f} %")
    buckets = [(0, 10), (10, 25), (25, 50), (50, 75), (75, 101)]
    log("  корзины по доле воды в июне:")
    for lo, hi in buckets:
        n = int(((wj >= lo) & (wj < hi)).sum())
        log(f"    {lo:3d}..{hi:3d} %: {n:4d} чеков")

    # ---- картинки: пять прежних и пять новых
    out_dir = DATA / "verify"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("proof_*.png"):
        old.unlink()

    picks_old = random.Random(SEED_OLD).sample(feats, 5)
    ids_old = {f["properties"]["id"] for f in picks_old}
    rest = [f for f in feats if f["properties"]["id"] not in ids_old]
    picks_new = random.Random(SEED_NEW).sample(rest, 5)
    by_id = {r["id"]: r for r in rows}

    f14, f11 = font(14), font(11)
    shots = []
    for tag, f in [("прежние", x) for x in picks_old] + [("новые", x) for x in picks_new]:
        p = f["properties"]
        r = by_id.get(p["id"], {})
        img = render_check(f, per, transform, width, height, f14, f11, r)
        name = f"proof_{p['id']}.png"
        img.save(out_dir / name, optimize=True)
        shots.append({"group": tag, "id": p["id"], "image": name, **{k: r.get(k) for k in
                     ("wet_june_pct", "ndvi_july", "ndwi_sept", "rice", "wet_may_pct", "bright_pct",
                      "ndvi_may", "ndvi_may_no_bright")}})
        verdict = "РИС" if r.get("rice") else "НЕ РИС" if r.get("wet_june_pct", 0) < 50 else "НЕОПРЕДЕЛЁННО"
        log(f"  чек {p['id']}: вода в июне {r.get('wet_june_pct')} % | NDVI июля {r.get('ndvi_july')} | "
            f"NDWI сентября {r.get('ndwi_sept')} | {verdict}")

    payload = {
        "criterion": "NDWI > 0 на >= 50 % площади в июне и NDVI > 0.55 в июле и NDWI <= 0 в сентябре",
        "dates": {k: {"date": v["date"], "scene": v["scene"], "cloud_pct": v["cloud"],
                      "valid_pct": v["valid_pct"]} for k, v in per.items()},
        "counts": {"checked": len(rows), "rice": n_rice},
        "checks": rows,
        "shots": shots,
    }
    write_json(DATA / "rice_proof.json", payload)
    lines = ["# Доказательство риса", ""]
    for s in shots:
        v = "рис" if s["rice"] else "не рис"
        lines.append(f"- чек {s['id']} ({s['group']}): вода в июне {s['wet_june_pct']} %, "
                     f"NDVI июля {s['ndvi_july']}, NDWI сентября {s['ndwi_sept']} — **{v}** "
                     f"`{s['image']}`")
    (out_dir / "proof_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("отчёт: data/verify/proof_report.md")


def render_check(f, per, transform, width, height, f_big, f_small, row) -> Image.Image:
    """Четыре панели: май RGB, июнь NDWI, июль NDVI, сентябрь NDWI."""
    coords = f["geometry"]["coordinates"][0]
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys)) * (1.0 + 2.0 * MARGIN)
    col_c, row_c = ~transform * (cx, cy)
    d = span / 2.0
    _, r0 = ~transform * (cx, cy + d)
    c1, _ = ~transform * (cx + d, cy)
    side = int(round(max(abs(r0 - row_c), abs(c1 - col_c))))
    side = max(side, 8)
    r0 = int(round(row_c - side / 2.0))
    c0 = int(round(col_c - side / 2.0))
    r0 = max(0, min(r0, height - side)) if height > side else 0
    c0 = max(0, min(c0, width - side)) if width > side else 0
    side = min(side, height - r0, width - c0)
    win = Window(c0, r0, side, side)
    wt = rasterio.windows.transform(win, transform)

    mask = features.rasterize([(f["geometry"], 1)], out_shape=(side, side), transform=wt,
                              fill=0, all_touched=True).astype(bool)
    edge = mask & ~ndi.binary_erosion(mask, structure=np.ones((3, 3)))
    edge = ndi.binary_dilation(edge, structure=np.ones((3, 3)))

    def rgb_panel(name: str) -> Image.Image:
        a = per[name]
        stack = np.dstack([np.nan_to_num(a["rgb"]["red"][r0:r0 + side, c0:c0 + side]),
                           np.nan_to_num(a["rgb"]["green"][r0:r0 + side, c0:c0 + side]),
                           np.nan_to_num(a["rgb"]["blue"][r0:r0 + side, c0:c0 + side])])
        lo, hi = np.nanpercentile(stack, [2, 98])
        arr = np.clip((stack - lo) / max(hi - lo, 1e-6), 0, 1) ** (1 / 1.35) * 255
        return Image.fromarray(arr.astype("uint8"), mode="RGB").resize((PANEL, PANEL), Image.BILINEAR)

    def idx_panel(name: str, key: str, stops) -> Image.Image:
        a = per[name]
        v = np.nan_to_num(a[key][r0:r0 + side, c0:c0 + side].astype("float32"), nan=-1.0)
        return Image.fromarray(ramp(v, stops).astype("uint8"), mode="RGB").resize(
            (PANEL, PANEL), Image.BILINEAR)

    panels = []
    if "май" in per:
        panels.append((rgb_panel("май"), f"май {per['май']['date']} · снимок"))
    panels.append((idx_panel("июнь", "ndwi", NDWI_STOPS), f"июнь {per['июнь']['date']} · NDWI, синее — вода"))
    panels.append((idx_panel("июль", "ndvi", NDVI_STOPS), f"июль {per['июль']['date']} · NDVI, зелёное — полог"))
    panels.append((idx_panel("сентябрь", "ndwi", NDWI_STOPS), f"сентябрь {per['сентябрь']['date']} · NDWI"))

    head, foot = 22, 46
    canvas = Image.new("RGB", (PANEL * len(panels) + 12 * (len(panels) - 1), PANEL + head + foot), (239, 234, 224))
    draw = ImageDraw.Draw(canvas)
    ov = np.asarray(Image.fromarray((edge * 255).astype("uint8"), mode="L").resize(
        (PANEL, PANEL), Image.NEAREST)) > 96
    for i, (im, cap) in enumerate(panels):
        x = i * (PANEL + 12)
        box = np.asarray(im).copy()
        box[ov] = SEAL
        canvas.paste(Image.fromarray(box, mode="RGB"), (x, head))
        draw.text((x + 2, 4), cap, font=f_small, fill=(27, 31, 36))

    p = f["properties"]
    verdict = "рис" if row.get("rice") else "не рис"
    tail = (f"чек {p['id']} · {p['area_ha']} га · вода в июне {row.get('wet_june_pct')} % · "
            f"NDVI июля {row.get('ndvi_july')} · NDWI сентября {row.get('ndwi_sept')} — {verdict}")
    draw.text((4, PANEL + head + 4), tail, font=f_big, fill=(27, 31, 36))
    note = ""
    if row.get("bright_pct") is not None and row["bright_pct"] >= 2.0:
        note = (f"яркое пятно в мае: {row['bright_pct']} % площади, NDVI с ним {row.get('ndvi_may')}, "
                f"без него {row.get('ndvi_may_no_bright')}")
    if note:
        draw.text((4, PANEL + head + 24), note, font=f_small, fill=(180, 52, 31))
    return canvas


if __name__ == "__main__":
    main()
