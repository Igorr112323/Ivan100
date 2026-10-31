"""Диагностика признака затопления.

Вопрос: почему контур, на 77 % затопленный 6 июня, 11 июня не затоплен совсем?
Без ответа на него отбирать чеки нельзя — отбор либо пропустит кукурузу, либо
выбросит весь рис. Скрипт печатает для обоих дат доли затопления, их пересечение
и поведение индексов на одних и тех же пикселях.

Выход: журнал в data/_log/diag.txt
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

from build_checks import load_raster
from common import SENTINEL, log

DATES = ("2025-06-06", "2025-06-11")


def idx(date: str) -> dict:
    out = {"date": date}
    for name in ("ndvi", "ndwi", "lswi"):
        out[name] = load_raster(SENTINEL / f"{name}_{date}.tif")
    out["valid"] = load_raster(SENTINEL / f"valid_{date}.tif").astype(bool)
    return out


def wet_masks(a: dict) -> dict:
    ndvi, ndwi, lswi, v = a["ndvi"], a["ndwi"], a["lswi"], a["valid"]
    ok = v & np.isfinite(ndvi) & np.isfinite(ndwi) & np.isfinite(lswi)
    return {
        "NDWI > 0": ok & (ndwi > 0.0),
        "LSWI >= NDVI": ok & (lswi >= ndvi),
        "LSWI >= NDVI - 0.05": ok & (lswi >= ndvi - 0.05),
        "LSWI >= NDVI - 0.15": ok & (lswi >= ndvi - 0.15),
        "LSWI >= NDVI - 0.25": ok & (lswi >= ndvi - 0.25),
        "LSWI >= 0.30": ok & (lswi >= 0.30),
        "LSWI >= 0.20": ok & (lswi >= 0.20),
    }


def share(m: np.ndarray, ok: np.ndarray) -> float:
    return float(m.sum()) / max(int(ok.sum()), 1)


def main() -> None:
    a, b = idx(DATES[0]), idx(DATES[1])
    ok_a = a["valid"] & np.isfinite(a["ndwi"]) & np.isfinite(a["ndvi"]) & np.isfinite(a["lswi"])
    ok_b = b["valid"] & np.isfinite(b["ndwi"]) & np.isfinite(b["ndvi"]) & np.isfinite(b["lswi"])
    both = ok_a & ok_b
    log(f"годных пикселей: {DATES[0]} {ok_a.mean() * 100:.1f} %, "
        f"{DATES[1]} {ok_b.mean() * 100:.1f} %, общих {both.mean() * 100:.1f} %")
    for nm, arr in (("NDVI", a["ndvi"]), ("NDWI", a["ndwi"]), ("LSWI", a["lswi"])):
        log(f"  {nm}: {DATES[0]} средний {np.nanmean(a[nm.lower()]):+.3f} | "
            f"{DATES[1]} средний {np.nanmean(b[nm.lower()]):+.3f} | "
            f"сдвиг {np.nanmean(b[nm.lower()][both]) - np.nanmean(a[nm.lower()][both]):+.3f}")

    wa, wb = wet_masks(a), wet_masks(b)
    log("")
    log("=== устойчивость признака затопления между двумя датами ===")
    log(f"{'признак':22s} {'6 июн':>7s} {'11 июн':>7s} {'пересеч':>8s} {'Жаккар':>8s} {'доля 6-х, выживших 11-го':>26s}")
    for key in wa:
        ma, mb = wa[key], wb[key]
        inter = float((ma & mb).sum()) / max(float((ma | mb).sum()), 1)
        surv = float((ma & mb).sum()) / max(float(ma.sum()), 1)
        log(f"{key:22s} {share(ma, ok_a) * 100:6.2f}% {share(mb, ok_b) * 100:6.2f}% "
            f"{share(ma & mb, both) * 100:7.2f}% {inter * 100:7.2f}% {surv * 100:25.1f}%")

    log("")
    log("=== поведение индексов на пикселях, затопленных 6 июня (LSWI >= NDVI - 0.05) ===")
    sel = wa["LSWI >= NDVI - 0.05"]
    for nm in ("ndvi", "ndwi", "lswi"):
        x, y = a[nm][sel], b[nm][sel]
        log(f"  {nm.upper()}: 6 июн {np.nanmean(x):+.3f} (медиана {np.nanmedian(x):+.3f}) -> "
            f"11 июн {np.nanmean(y):+.3f} (медиана {np.nanmedian(y):+.3f})")
    diff6 = (a["lswi"] - a["ndvi"])[sel]
    diff11 = (b["lswi"] - b["ndvi"])[sel]
    log(f"  LSWI - NDVI на этих пикселях: 6 июн {np.nanmean(diff6):+.3f} -> 11 июн "
        f"{np.nanmean(diff11):+.3f} (порог признака -0,05)")

    log("")
    log("=== то же для пикселей с NDWI > 0 6 июня ===")
    sel = wa["NDWI > 0"]
    for nm in ("ndvi", "ndwi", "lswi"):
        x, y = a[nm][sel], b[nm][sel]
        log(f"  {nm.upper()}: 6 июн {np.nanmean(x):+.3f} -> 11 июн {np.nanmean(y):+.3f}")

    log("")
    log("=== карты затопления (север сверху, # — вода, . — сухо, ~ — нет данных) ===")
    for key in ("NDWI > 0", "LSWI >= NDVI - 0.05", "LSWI >= 0.30"):
        for a_ in (wa, wb):
            m = a_[key]
            ok = ok_a if a_ is wa else ok_b
            h, w = m.shape
            cols = 76
            rows = max(1, int(round(cols * h / w * 0.5)))
            ys = np.linspace(0, h, rows + 1).astype(int)
            xs = np.linspace(0, w, cols + 1).astype(int)
            log(f"  {a_['date']} · {key}")
            for i in range(rows):
                line = ""
                for j in range(cols):
                    blk_m = m[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
                    blk_o = ok[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
                    if blk_o.size == 0 or blk_o.mean() < 0.5:
                        line += "~"
                    elif blk_m.mean() >= 0.5:
                        line += "#"
                    elif blk_m.mean() >= 0.2:
                        line += "+"
                    else:
                        line += "."
                log(f"  |{line}|")


if __name__ == "__main__":
    main()
