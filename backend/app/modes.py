"""Три режима водораспределения по чекам для переключателя на карте.

На каждый лимит (100 % → 70 % с шагом 1 %) считаются два управления:
  * «поровну»    — уравнительный срез, как делят сейчас;
  * «по фазам»   — распределение модели по декадам;
и разница между ними. Выход — data/optimization/modes.json, его читает экран.

Запуск: python backend/app/modes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import engine, main as api  # noqa: E402

OUT = ROOT / "data" / "optimization" / "modes.json"
WEB = ROOT / "web" / "public" / "data" / "modes.json"
MM = engine.MM_TO_M3_HA
KY = 1.1


def build() -> dict:
    years = api.years_available()
    checks, meta, season, perc_base, _rows = api.context(years[-1])
    demands = engine.season_demands(checks, season, perc_base)
    etm, perc = demands["etm"], demands["perc"]
    n = len(checks)
    p_dec = len(etm[0])
    nd = engine.net_demand(checks, etm, perc)

    out = {
        "_meta": {
            "generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            "ky": KY,
            "checks": n,
            "decades": p_dec,
            "source": "расчёт модели",
        },
        "check_ids": [c.id for c in checks],
        "limits": [],
    }

    for i in range(31):
        limit = round(1.00 - 0.01 * i, 2)
        deficit = round(1.0 - limit, 2)
        uni = engine.baseline_uniform(checks, etm, perc, deficit, KY)
        opt = engine.solve_lp(checks, etm, perc, deficit, KY)

        def water_of(gross_mm, active=None):
            total = 0.0
            for j in range(n):
                if active is not None and not active[j]:
                    continue
                total += checks[j].area_ha * (sum(gross_mm[j])
                                              + engine.initial_mm(checks[j].kpd)) * MM
            return total

        active = opt["in_rotation"]
        rows = []
        for j in range(n):
            uni_m3 = checks[j].area_ha * (sum(uni["gross_mm"][j])
                                          + engine.initial_mm(checks[j].kpd)) * MM
            opt_m3 = checks[j].area_ha * (sum(opt["gross_mm"][j])
                                          + (engine.initial_mm(checks[j].kpd) if active[j] else 0.0)) * MM
            rows.append({
                "w_uni": round(uni_m3),
                "w_opt": round(opt_m3),
                "d_uni": round(uni["per_check"][j]["deficit_pct"], 2),
                "d_opt": round(opt["per_check"][j]["deficit_pct"], 2),
                "y_uni": round(uni["per_check"][j]["yield_frac"], 4),
                "y_opt": round(opt["per_check"][j]["yield_frac"], 4),
                "out": 0 if active[j] else 1,
            })
        out["limits"].append({
            "limit": limit,
            "water_uni_m3": round(water_of(uni["gross_mm"])),
            "water_opt_m3": round(opt["water_m3"]),
            "yield_uni_t": round(uni["yield_tons"], 1),
            "yield_opt_t": round(opt["yield_tons"], 1),
            "per_ha_uni": [round(r["w_uni"] / checks[j].area_ha) for j, r in enumerate(rows)],
            "per_ha_opt": [round(r["w_opt"] / checks[j].area_ha) for j, r in enumerate(rows)],
            "rows": rows,
        })
    return out


if __name__ == "__main__":
    data = build()
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    WEB.parent.mkdir(parents=True, exist_ok=True)
    WEB.write_text(text, encoding="utf-8")
    print(f"записано {OUT} ({len(text) / 1024:.0f} КБ), лимитов {len(data['limits'])}")
