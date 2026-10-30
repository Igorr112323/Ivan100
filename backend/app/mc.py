"""Стресс-тест решения на ошибке отметок.

Отметки чеков взяты из цифровой модели рельефа, а не с нивелира. Поэтому решение
проверяется дважды: при реалистичном разбросе σ = 0,25 м (точность съёмки после
привязки к массиву) и при завышенном σ = 3 м — если вывод продержится и там,
значит, он не держится на точности рельефа.

Решение принимается по зашумлённым отметкам, а оценивается в истинной модели:
это проверка не устойчивости чисел, а устойчивости решения.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import engine, main as api  # noqa: E402

OUT = ROOT / "data" / "optimization" / "montecarlo.json"
WEB = ROOT / "web" / "public" / "data" / "montecarlo.json"
DEFICIT = 0.15
KY = 1.1
RUNS = 60
SIGMAS = (0.25, 3.0)


def run() -> dict:
    years = api.years_available()
    checks, _meta, season, perc_base, _rows = api.context(years[-1])
    out = {"deficit": DEFICIT, "ky": KY, "runs": RUNS, "max_cut": engine.MAX_CUT,
           "base": "практика вывода чеков из севооборота", "sigmas": []}
    for sigma in SIGMAS:
        t0 = time.time()
        mc = engine.monte_carlo(checks, season, perc_base, DEFICIT, KY, sigma_m=sigma,
                                runs=RUNS, max_cut=engine.MAX_CUT)
        mc["seconds"] = round(time.time() - t0, 1)
        out["sigmas"].append(mc)
        print(f"σ = {sigma} м: средний прирост {mc.get('mean')} % "
              f"| 5..95 %: {mc.get('p05')}..{mc.get('p95')} | прогонов {mc.get('runs')} "
              f"| {mc['seconds']} с", flush=True)
    return out


if __name__ == "__main__":
    payload = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    WEB.parent.mkdir(parents=True, exist_ok=True)
    WEB.write_text(text, encoding="utf-8")
    print(f"записано {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} КБ)")
