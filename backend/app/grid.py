"""Предрасчёт сетки лимитов водоподачи.

Слайдер лимита 100 % → 70 % с шагом 1 % не должен ждать решатель: все 31 положение
считаются заранее и складываются в data/optimization/grid.json. Интерфейс читает
готовое, поэтому отклик — это чтение строки из уже загруженного массива.

На каждом лимите считаются три управления:
  * практика вывода чеков из севооборота — база сравнения;
  * уравнительное урезание — показывается второй строкой, в практике не применяется;
  * оптимизатор в двух режимах: с ограничением «недолив ≤ 10 % на чек» (основной)
    и без него (для ответа на вопрос, откуда берётся выгода).

Запуск:  python backend/app/grid.py   (из корня репозитория)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # запуск и как скрипт, и как модуль — ядро импортируется пакетом
    sys.path.insert(0, str(ROOT))

from backend.app import engine, main as api
OUT = ROOT / "data" / "optimization" / "grid.json"
WEB = ROOT / "web" / "public" / "data" / "grid.json"

KY = 1.1


def limits() -> list[float]:
    """100 % .. 70 % с шагом 1 %: ниже 70 % модель не проверялась."""
    return [round(1.00 - 0.01 * i, 2) for i in range(31)]


def build(year: int | None = None, ky: float = KY) -> dict:
    years = api.years_available()
    if not years:
        raise SystemExit("нет метеоряда: сначала запустите пайплайн")
    year = year or years[-1]
    checks, meta, season, perc_base, _rows = api.context(year)
    demands = engine.season_demands(checks, season, perc_base)
    etm, perc = demands["etm"], demands["perc"]
    area_ha = sum(c.area_ha for c in checks)
    ids = [c.id for c in checks]

    def pct(a: float, b: float) -> float:
        return round(100.0 * (a / b - 1.0), 3) if b else 0.0

    full = engine.baseline_uniform(checks, etm, perc, 0.0, ky)
    rows = []
    t0 = time.time()
    for limit in limits():
        deficit = round(1.0 - limit, 2)
        uniform = engine.baseline_uniform(checks, etm, perc, deficit, ky)
        fallow = engine.baseline_fallowing(checks, etm, perc, deficit, ky)
        capped = engine.solve_lp(checks, etm, perc, deficit, ky, max_cut=engine.MAX_CUT)
        free = engine.solve_lp(checks, etm, perc, deficit, ky, max_cut=1.0)
        if not capped["ok"] or not free["ok"]:
            print(f"  лимит {limit:.2f}: решатель не нашёл решения "
                  f"({capped.get('status')}, {free.get('status')}) — пропуск")
            continue
        gain_cap = pct(capped["yield_tons"], fallow["yield_tons"])
        row = {
            "limit": limit,
            "deficit": deficit,
            "yield_tons": {
                "full": round(full["yield_tons"], 1),
                "uniform": round(uniform["yield_tons"], 1),
                "fallowing": round(fallow["yield_tons"], 1),
                "capped": round(capped["yield_tons"], 1),
                "free": round(free["yield_tons"], 1),
            },
            "water_m3": {
                "full": round(full["water_m3"], 0),
                "uniform": round(uniform["water_m3"], 0),
                "fallowing": round(fallow["water_m3"], 0),
                "capped": round(capped["water_m3"], 0),
                "free": round(free["water_m3"], 0),
                "limit": round(capped["budget_m3"], 0),
            },
            "gain_pct": {
                "capped_vs_fallowing": gain_cap,
                "free_vs_fallowing": pct(free["yield_tons"], fallow["yield_tons"]),
                "capped_vs_uniform": pct(capped["yield_tons"], uniform["yield_tons"]),
                "uniform_vs_full": pct(uniform["yield_tons"], full["yield_tons"]),
            },
            "fallowed": {
                "fallowing": sorted(int(i) for i in fallow.get("fallowed", [])),
                "capped": sorted(int(i) for i in capped.get("fallowed", [])),
                "free": sorted(int(i) for i in free.get("fallowed", [])),
            },
            "cut_pct": [round(v, 1) for v in _cut_series(checks, capped, etm, perc)],
            "yield_frac": [round(pc["yield_frac"], 4) for pc in capped["per_check"]],
            "economics": engine.economics(gain_cap, area_ha),
        }
        rows.append(row)
        print(f"  лимит {limit:.0%}: вывод чеков {fallow['yield_tons']:.0f} т → оптимум "
              f"{capped['yield_tons']:.0f} т ({gain_cap:+.2f} %) | выведено чеков: "
              f"практика {len(fallow.get('fallowed', []))}, оптимум {len(capped.get('fallowed', []))}"
              + ("" if capped.get("fallowed") else " (без вывода)"), flush=True)

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "year": year,
        "ky": ky,
        "max_cut": engine.MAX_CUT,
        "limit_range": [engine.LIMIT_MIN, engine.LIMIT_MAX, engine.LIMIT_STEP],
        "area_ha": round(area_ha, 1),
        "check_ids": ids,
        "checks_count": len(checks),
        "build_s": round(time.time() - t0, 1),
        "base": "практика вывода чеков из севооборота",
        "naive": "уравнительное урезание — наивная база, в практике не применяется",
        "rows": rows,
    }


def _cut_series(checks, lp, etm, perc) -> list[float]:
    """Недолив воды по каждому чеку, % к его полной норме."""
    out = []
    for i, c in enumerate(checks):
        gross = lp["gross_mm"][i]
        active = lp["per_check"][i].get("active", True)
        total_mm = sum(gross) + (engine.INITIAL_MM / c.kpd if active else 0.0)
        full_mm = (sum(etm[i]) + sum(perc[i]) + engine.INITIAL_MM) / c.kpd
        out.append(100.0 * (1.0 - total_mm / full_mm) if full_mm else 0.0)
    return out


def save(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    WEB.parent.mkdir(parents=True, exist_ok=True)
    WEB.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"записано {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    save(build())
