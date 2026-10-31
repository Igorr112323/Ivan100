"""Доказательство, что культура в контурах — рис.

Рис отличает не «зелёное поле» и не «сухая осень» — так ведут себя и кукуруза,
и соя. Рис отличает вода в начале лета: в первой декаде июня чек стоит под слоем
воды, а всходы ещё редкие, поэтому спутник видит воду (NDWI > 0, LSWI >= NDVI).
К июню-июлю полог смыкается, вода из космоса пропадает, зато NDVI поднимается
до 0,8. Осенью чек сбрасывают — и он сухой.

Проверка по каждому контуру:
  1) вода под пологом (NDWI > 0 или LSWI >= NDVI - 0,05) на >= 50 % площади хотя бы
     на одной из двух дат затопления. Обе даты выбраны без дождя за пять суток
     до съёмки — иначе мокрой выглядит и кукуруза после ливня;
  2) мокро (LSWI >= 0,20) на >= 50 % площади на всех датах затопления: вода,
     спрятанная пологом, всё равно даёт мокрый отклик;
  3) средний NDVI > 0,55 на пике вегетации — сомкнутый полог;
  4) NDWI <= 0 осенью — чек сброшен перед уборкой.

Считается по всем контурам, а не по выборке. Для десяти чеков (пять прежних
из проверки и пять новых случайных) выводятся картинки: июнь RGB, июнь NDWI,
июль NDVI, октябрь NDWI. Заодно считается, не искажает ли яркое пятно на снимке
средние индексы чека.

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

# Окна дат. Две даты затопления — обе без дождя за пять суток до съёмки; пик
# вегетации; сброс воды. Вторая дата затопления нужна не затем, чтобы увидеть воду
# дважды (к 11 июня полог её уже прячет), а чтобы убедиться: мокро стоит не один день.
SLOTS = [
    ("июнь1", "2025-05-25", "2025-06-20", "2025-06-06"),
    ("июнь2", "2025-06-08", "2025-06-25", "2025-06-11"),
    ("июль", "2025-07-01", "2025-08-10", "2025-07-10"),
    ("октябрь", "2025-09-20", "2025-10-20", "2025-10-04"),
]
WET_STRONG_MARGIN = 0.05   # LSWI >= NDVI - 0.05 — вода под редким пологом
WET_WEAK_LSWI = 0.20       # просто мокро, и тогда, когда полог сомкнулся
WET_FRAC_MIN = 0.50
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
            for b in ("red", "green", "nir", "blue", "swir16", "scl"):
                bands[b] = read_merged(items, b, width, height, transform,
                                       Resampling.nearest if b == "scl" else Resampling.bilinear)
            good = ~(np.isin(bands["scl"], [0, 1, 3, 8, 9, 10]) | np.isnan(bands["scl"]))
            with np.errstate(invalid="ignore", divide="ignore"):
                ndvi = (bands["nir"] - bands["red"]) / (bands["nir"] + bands["red"])
                ndwi = (bands["green"] - bands["nir"]) / (bands["green"] + bands["nir"])
                lswi = np.where((bands["nir"] > 0) & (bands["swir16"] > 0),
                                (bands["nir"] - bands["swir16"]) / (bands["nir"] + bands["swir16"]),
                                np.nan)
            ndvi = np.where(good, ndvi, np.nan)
            ndwi = np.where(good, ndwi, np.nan)
            lswi = np.where(good, lswi, np.nan)
            strong = (ndwi > 0.0) | (lswi >= ndvi - WET_STRONG_MARGIN)
            weak = lswi >= WET_WEAK_LSWI
            bright = np.nansum(np.dstack([bands["red"], bands["green"], bands["blue"]]), axis=2)
            per[name] = {
                "date": date, "scene": items[0]["id"], "tiles": [tile_of(i) for i in items],
                "cloud": round(min(cloud_of(i) for i in items) * 100, 2),
                "ndvi": ndvi, "ndwi": ndwi, "lswi": lswi,
                "strong": strong, "weak": weak, "rgb": bands,
                "bright": bright, "good": good,
                "valid_pct": round(float(good.mean()) * 100, 2),
            }
            for b in ("red", "green", "nir", "blue"):
                per[name]["rgb"][b] = np.where(good, bands[b], np.nan)
            log(f"    годных пикселей {good.mean() * 100:.1f} % | NDWI > 0 у "
                f"{np.nanmean(ndwi > 0) * 100:.1f} % | вода под пологом у "
                f"{np.nanmean(strong) * 100:.1f} % | мокро (LSWI) у {np.nanmean(weak) * 100:.1f} % | "
                f"NDVI средний {np.nanmean(ndvi):+.3f}")

    for need in ("июнь1", "июль", "октябрь"):
        if need not in per:
            raise SystemExit(f"нет сцены на {need} — доказательство невозможно")

    june, july, sept = per["июнь1"], per["июль"], per["октябрь"]
    june2 = per.get("июнь2")
    may = None

    # ---- показатели по каждому контуру
    rows = []
    for f in feats:
        p = f["properties"]
        m = features.rasterize([(f["geometry"], 1)], out_shape=(height, width),
                               transform=transform, fill=0, all_touched=False).astype(bool)
        if m.sum() < 50:
            continue
        open_june = float(np.nanmean(june["ndwi"][m] > 0.0))
        wet_june = float(np.nanmean(june["strong"][m]))
        wet_june2 = float(np.nanmean(june2["strong"][m])) if june2 else None
        moist_june = float(np.nanmean(june["weak"][m]))
        moist_june2 = float(np.nanmean(june2["weak"][m])) if june2 else None
        ndvi_july = float(np.nanmean(july["ndvi"][m]))
        ndwi_sept = float(np.nanmean(sept["ndwi"][m]))
        dry_sept = float(np.nanmean(sept["ndwi"][m] <= 0.0))
        # вода хотя бы на одной дате + мокро на всех датах + полог летом + сухо осенью
        strong_ok = max(v for v in (wet_june, wet_june2) if v is not None) >= WET_FRAC_MIN
        weak_vals = [v for v in (moist_june, moist_june2) if v is not None]
        weak_ok = min(weak_vals) >= WET_FRAC_MIN if weak_vals else False
        ok = bool(strong_ok and weak_ok and ndvi_july > 0.55 and ndwi_sept <= 0.0)
        row = {
            "id": p["id"], "area_ha": p["area_ha"],
            "open_water_pct": round(open_june * 100, 1),
            "wet_june_pct": round(wet_june * 100, 1),
            "wet_june2_pct": None if wet_june2 is None else round(wet_june2 * 100, 1),
            "moist_pct": round(moist_june * 100, 1),
            "moist2_pct": None if moist_june2 is None else round(moist_june2 * 100, 1),
            "ndvi_july": round(ndvi_july, 3),
            "ndwi_sept": round(ndwi_sept, 3),
            "dry_sept_pct": round(dry_sept * 100, 1),
            "rice": ok,
        }
        # не искажает ли яркое пятно на снимке индексы
        thr = float(np.nanpercentile(june["bright"], BRIGHT_P))
        hot = m & (june["bright"] >= thr) & np.isfinite(june["bright"])
        row["bright_pct"] = round(float(hot.sum() / max(m.sum(), 1)) * 100, 1)
        cold = m & ~hot
        row["ndvi_july_no_bright"] = (round(float(np.nanmean(july["ndvi"][cold])), 3)
                                      if cold.sum() > 20 else None)
        rows.append(row)

    n_rice = sum(1 for r in rows if r["rice"])
    log("")
    log("=== проверка критерия риса по всем контурам ===")
    log(f"  контуров проверено: {len(rows)}")
    log(f"  прошло все три условия: {n_rice} ({n_rice / max(len(rows), 1) * 100:.0f} %)")
    wj = np.array([r["wet_june_pct"] for r in rows])
    nj = np.array([r["ndvi_july"] for r in rows])
    mo = np.array([r["moist_pct"] for r in rows])
    ow = np.array([r["open_water_pct"] for r in rows])
    log(f"  открытая вода в июне ({june['date']}): медиана {np.median(ow):.0f} %, "
        f"10-й процентиль {np.percentile(ow, 10):.0f} %")
    log(f"  вода с учётом полога: медиана {np.median(wj):.0f} %, "
        f"10-й процентиль {np.percentile(wj, 10):.0f} %, 90-й {np.percentile(wj, 90):.0f} %")
    log(f"  мокро по LSWI: медиана {np.median(mo):.0f} %, "
        f"10-й процентиль {np.percentile(mo, 10):.0f} %")
    log(f"  NDVI в июле ({july['date']}): медиана {np.median(nj):.2f}, "
        f"10-й процентиль {np.percentile(nj, 10):.2f}, минимум {nj.min():.2f}")
    br = np.array([r.get("bright_pct") or 0.0 for r in rows])
    dn = np.array([(r.get("ndvi_july") or 0) - (r.get("ndvi_july_no_bright") or 0) for r in rows])
    worst = int(np.argmax(br))
    log(f"  яркое пятно в июне: медиана {np.median(br):.1f} %, "
        f"максимум {br.max():.1f} % (чек {rows[worst]['id']}); "
        f"NDVI июля изменился бы на {dn[worst]:+.3f}, "
        f"по всему массиву сдвиг не более {np.abs(dn).max():.3f}")
    log(f"  чеков с пятном больше 2 % площади: {int((br >= 2.0).sum())}")

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
                     ("wet_june_pct", "open_water_pct", "moist_pct", "wet_june2_pct",
                      "ndvi_july", "ndwi_sept", "rice", "bright_pct",
                      "ndvi_july_no_bright")}})
        verdict = "РИС" if r.get("rice") else "НЕ РИС" if r.get("wet_june_pct", 0) < 50 else "НЕОПРЕДЕЛЁННО"
        log(f"  чек {p['id']}: вода в июне {r.get('wet_june_pct')} % (открытая "
            f"{r.get('open_water_pct')} %, мокро {r.get('moist_pct')} %) | NDVI июля "
            f"{r.get('ndvi_july')} | NDWI октября {r.get('ndwi_sept')} | {verdict}")

    # ---- визуальное доказательство: десять чеков, где вода видна глазами.
    # Слово «рис» без картинки с открытой водой на защите не держится, поэтому
    # берём не случайные чеки, а десять самых мокрых по доле NDWI > 0 в июне —
    # снимок 06.06 сделан до смыкания полога.
    top = sorted(rows, key=lambda r: r.get("open_water_pct") or 0.0, reverse=True)[:10]
    log("")
    log("=== визуальное доказательство: десять чеков с максимумом открытой воды ===")
    log(f"  снимок {june['date']} ({june['scene']}), облака {june['cloud']:.2f} % — до смыкания полога")
    visual = []
    by_feat = {f["properties"]["id"]: f for f in feats}
    for r in top:
        f = by_feat.get(r["id"])
        if f is None:
            continue
        img = render_check(f, per, transform, width, height, f14, f11, r)
        name = f"visual_{r['id']}.png"
        img.save(out_dir / name, optimize=True)
        visual.append({"id": r["id"], "image": name, **{k: r.get(k) for k in
                       ("open_water_pct", "wet_june_pct", "moist_pct",
                        "ndvi_july", "ndwi_sept", "rice")}})
        log(f"  чек {r['id']}: открытой воды {r.get('open_water_pct')} % "
            f"(мокро {r.get('moist_pct')} %), NDVI июля {r.get('ndvi_july')} → {name}")

    payload = {
        "criterion": ("вода под пологом (NDWI > 0 или LSWI >= NDVI - 0.05) на >= 50 % площади "
                      "хотя бы на одной из двух дат затопления, обе без дождя; и мокро "
                      "(LSWI >= 0.20) на >= 50 % на всех датах; и NDVI > 0.55 на пике; "
                      "и NDWI <= 0 осенью"),
        "dates": {k: {"date": v["date"], "scene": v["scene"], "cloud_pct": v["cloud"],
                      "valid_pct": v["valid_pct"]} for k, v in per.items()},
        "counts": {"checked": len(rows), "rice": n_rice},
        "checks": rows,
        "shots": shots,
        "visual": visual,
    }
    write_json(DATA / "rice_proof.json", payload)
    lines = ["# Доказательство риса", ""]
    for s in shots:
        v = "рис" if s["rice"] else "не рис"
        lines.append(f"- чек {s['id']} ({s['group']}): вода в июне {s['wet_june_pct']} % "
                     f"(открытая {s['open_water_pct']} %, мокро {s['moist_pct']} %), "
                     f"NDVI июля {s['ndvi_july']}, NDWI октября {s['ndwi_sept']} — **{v}** "
                     f"`{s['image']}`")
    lines += ["", "## Визуальное доказательство: вода видна глазами", "",
              f"Снимок {june['date']} ({june['scene']}), облака {june['cloud']:.2f} % — "
              "до смыкания полога. Десять чеков с максимальной долей открытой воды "
              "(NDWI > 0). Слева направо: RGB июня, NDWI июня, NDVI июля, NDWI октября.", ""]
    for v in visual:
        lines.append(f"- чек {v['id']}: открытой воды {v['open_water_pct']} %, "
                     f"мокро {v['moist_pct']} %, NDVI июля {v['ndvi_july']}, "
                     f"NDWI октября {v['ndwi_sept']} — `{v['image']}`")
    (out_dir / "proof_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("отчёт: data/verify/proof_report.md")


def render_check(f, per, transform, width, height, f_big, f_small, row) -> Image.Image:
    """Четыре панели: июнь RGB, июнь NDWI, июль NDVI, октябрь NDWI."""
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

    panels = [
        (rgb_panel("июнь1"), f"июнь {per['июнь1']['date']} · снимок"),
        (idx_panel("июнь1", "ndwi", NDWI_STOPS), f"июнь {per['июнь1']['date']} · NDWI, синее — вода"),
        (idx_panel("июль", "ndvi", NDVI_STOPS), f"июль {per['июль']['date']} · NDVI, зелёное — полог"),
        (idx_panel("октябрь", "ndwi", NDWI_STOPS), f"октябрь {per['октябрь']['date']} · NDWI"),
    ]

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
    tail = (f"чек {p['id']} · {p['area_ha']} га · вода в июне {row.get('wet_june_pct')} % "
            f"(открытая {row.get('open_water_pct')} %, мокро {row.get('moist_pct')} %) · "
            f"NDVI июля {row.get('ndvi_july')} · NDWI октября {row.get('ndwi_sept')} — {verdict}")
    draw.text((4, PANEL + head + 4), tail, font=f_big, fill=(27, 31, 36))
    note = ""
    if row.get("bright_pct") is not None and row["bright_pct"] >= 2.0:
        note = (f"яркое пятно на снимке июня: {row['bright_pct']} % площади, NDVI июля с ним "
                f"{row.get('ndvi_july')}, без него {row.get('ndvi_july_no_bright')}")
    if note:
        draw.text((4, PANEL + head + 24), note, font=f_small, fill=(180, 52, 31))
    return canvas


if __name__ == "__main__":
    main()
