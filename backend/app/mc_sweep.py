"""Поиск порога: при какой ошибке отметок вывод перестаёт держаться.

σ = 0,25 м даёт +0,8 %, σ = 3 м даёт −1,5 %. Это не противоречие, а граница
применимости: надо найти σ, на котором эффект обнуляется. Прогон по сетке σ.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import engine, main as api  # noqa: E402

OUT = ROOT / "data" / "optimization" / "montecarlo_sweep.json"
DEFICIT, KY, RUNS = 0.15, 1.1, 40


def main(sigmas: list[float]) -> None:
    years = api.years_available()
    checks, _meta, season, perc_base, _rows = api.context(years[-1])
    base_path = ROOT / "data" / "optimization" / "montecarlo.json"
    out = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else {
        "deficit": DEFICIT, "ky": KY, "max_cut": engine.MAX_CUT,
        "base": "практика вывода чеков из севооборота", "sigmas": []}
    known = {s["sigma_m"] for s in out["sigmas"]}
    for sigma in sigmas:
        if sigma in known:
            continue
        mc = engine.monte_carlo(checks, season, perc_base, DEFICIT, KY, sigma_m=sigma,
                                runs=RUNS, max_cut=engine.MAX_CUT)
        out["sigmas"].append(mc)
        print(f"σ = {sigma} м: {mc['mean']:.2f} % | 5..95 %: {mc['p05']:.2f}..{mc['p95']:.2f}", flush=True)
    out["sigmas"].sort(key=lambda s: s["sigma_m"])
    out["runs_sweep"] = RUNS
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    (ROOT / "web" / "public" / "data" / "montecarlo.json").write_text(text, encoding="utf-8")
    print(f"записано {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    args = [float(a) for a in sys.argv[1:]] or [0.5, 1.0, 1.5]
    main(args)
