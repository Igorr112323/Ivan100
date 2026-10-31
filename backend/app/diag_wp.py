"""Диагностика модели: есть ли на самом деле экономия воды.

Ключевая величина — продуктивность воды WP = урожай (т) / поданная вода (м³).
Выигрыш ΔWP предъявляется в одной из двух форм, но не в обеих сразу:
  * «тот же объём воды → больше урожая на X %»;
  * «тот же урожай → меньше воды на X %».
Если X в обеих формах совпадает — это двойной счёт.

Каждый тест — отдельный прогон. Параметры прогона сохраняются в результат.

Запуск:  python backend/app/diag_wp.py <тест>
Тесты:   placebo | decomp | filt | inverse | ky | baselines | all
"""
from __future__ import annotations

from dataclasses import replace as dc_replace
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pulp  # noqa: E402

from backend.app import engine, main as api  # noqa: E402

OUT = ROOT / "data" / "optimization" / "diag_wp.json"
DEFICIT = 0.15
KY = 1.1
CAPACITY = 3.5
MM = engine.MM_TO_M3_HA
Y_HA = engine.YIELD_T_HA


# --------------------------------------------------------------------- обвязка

def ctx(deficit: float = DEFICIT, ky: float = KY, kappa: float = 0.0,
        linear: bool = False, slope2: float | None = None):
    """Контекст прогона: чеки, сезон, потребности."""
    engine.PERC_KAPPA = kappa
    if linear:
        engine.SLOPE2 = engine.SLOPE1
    elif slope2 is not None:
        engine.SLOPE2 = slope2
    else:
        engine.SLOPE2 = 1.60
    years = api.years_available()
    checks, meta, season, perc_base, _rows = api.context(years[-1])
    demands = engine.season_demands(checks, season, perc_base)
    return checks, meta, season, perc_base, demands["etm"], demands["perc"]


def reset():
    engine.PERC_KAPPA = 0.0
    engine.SLOPE2 = 1.60


def wp(yield_t: float, water_m3: float) -> float:
    """Продуктивность воды, т/м³."""
    return yield_t / water_m3 if water_m3 else 0.0


def gain_pct(a: float, b: float) -> float:
    """Прирост a к базе b, %."""
    return round(100.0 * (a / b - 1.0), 3) if b else 0.0


# ------------------------------------------------------- универсальный решатель

def lp_alloc(checks, etm, perc, ky=KY, deficit=DEFICIT, budget=None, min_yield=None,
             max_cut=engine.MAX_CUT, per_decade=True, active=None, capacity=CAPACITY):
    """Распределение воды по чекам и декадам.

    Два режима:
      * budget задан     — минимум потери урожая при лимите воды;
      * min_yield задан  — минимум водозабора при условии на урожай.
    per_decade=True  — свои переменные дефицита по каждой декаде (дифференциация по фазам);
    per_decade=False — один дефицит на чек на весь сезон (фазы не различаются).
    active           — список чеков в севообороте; None — все.
    """
    n = len(checks)
    act = list(range(n)) if active is None else list(active)
    p_dec = len(etm[0])
    w = engine.et_weights(etm[0])
    dec_len = engine.decade_lengths(engine.SEASON_DAYS)
    dims = p_dec if per_decade else 1

    prob = pulp.LpProblem("diag", pulp.LpMinimize)
    d1 = {(i, p): pulp.LpVariable(f"d1_{i}_{p}", lowBound=0, upBound=engine.SEG1)
          for i in act for p in range(dims)}
    d2 = {(i, p): pulp.LpVariable(f"d2_{i}_{p}", lowBound=0, upBound=1.0 - engine.SEG1)
          for i in act for p in range(dims)}

    def d_of(i, p):
        """Дефицит чека i в декаде p."""
        q = p if per_decade else 0
        return d1[(i, q)] + d2[(i, q)]

    loss = pulp.lpSum(
        checks[i].area_ha * Y_HA * ky * engine.PHASES_KY_W[engine.phase_of_dec(p)] * w[p]
        * (engine.SLOPE1 * d1[(i, p if per_decade else 0)] + engine.SLOPE2 * d2[(i, p if per_decade else 0)])
        for i in act for p in range(p_dec)
    )
    water = (
        pulp.lpSum(checks[i].area_ha * engine.gross_decade(etm[i][p], perc[i][p], d_of(i, p),
                                                           checks[i].kpd) * MM
                   for i in act for p in range(p_dec))
        + sum(checks[i].area_ha * engine.initial_mm(checks[i].kpd) * MM for i in act)
    )

    if min_yield is None:
        prob += loss
        nd = engine.net_demand(checks, etm, perc)
        fixed_all = sum(checks[i].area_ha * engine.initial_mm(checks[i].kpd) * MM for i in range(n))
        variable_all = sum(checks[i].area_ha * sum(nd[i]) / checks[i].kpd * MM for i in range(n))
        prob += water <= (budget if budget is not None
                          else (1.0 - deficit) * (fixed_all + variable_all))
    else:
        # суммарный урожай не ниже заданного: потеря не больше допустимой
        potential = sum(checks[i].area_ha for i in act) * Y_HA
        prob += water
        prob += loss <= potential - min_yield

    for i in act:
        prob += pulp.lpSum(
            ky * engine.PHASES_KY_W[engine.phase_of_dec(p)] * w[p]
            * (engine.SLOPE1 * d1[(i, p if per_decade else 0)] + engine.SLOPE2 * d2[(i, p if per_decade else 0)])
            for p in range(p_dec)
        ) <= 1.0
        if max_cut < 1.0:
            prob += pulp.lpSum(etm[i][p] * d_of(i, p) for p in range(p_dec)) \
                <= max_cut * sum(etm[i][p] + perc[i][p] for p in range(p_dec))
        for p in range(p_dec):
            prob += engine.gross_decade(etm[i][p], perc[i][p], d_of(i, p), checks[i].kpd) \
                <= capacity * 8.64 * dec_len[p]

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        return {"ok": False, "status": pulp.LpStatus[status]}

    deltas = [[float(d_of(i, p).value() or 0.0) for p in range(p_dec)] if i in act
              else [1.0] * p_dec for i in range(n)]
    gross = [[engine.gross_decade(etm[i][p], perc[i][p], deltas[i][p], checks[i].kpd)
              if i in act else 0.0 for p in range(p_dec)] for i in range(n)]
    water_m3 = sum(checks[i].area_ha * (sum(gross[i]) + (engine.initial_mm(checks[i].kpd) if i in act else 0.0))
                   * MM * (1.0 if i in act else 0.0) for i in range(n))
    fracs = [engine.yield_fraction(deltas[i], etm[i], ky) if i in act else 0.0 for i in range(n)]
    return {
        "ok": True, "status": "Optimal",
        "deltas": deltas, "gross_mm": gross, "water_m3": water_m3,
        "yield_tons": sum(checks[i].area_ha * Y_HA * fracs[i] for i in range(n)),
        "per_check": [{"id": checks[i].id,
                       "deficit_pct": round(100.0 * sum(deltas[i]) / p_dec, 2),
                       "yield_frac": round(fracs[i], 4)} for i in range(n)],
    }


def practice(checks, etm, perc, deficit=DEFICIT, ky=KY):
    """Практика вывода чеков из севооборота — база сравнения."""
    return engine.baseline_fallowing(checks, etm, perc, deficit, ky)


# ------------------------------------------------------- ТЕСТ 6: плацебо

def test_placebo(runs: int = 100, seed: int = 20251031):
    """Сто прогонов со случайно перемешанными свойствами чеков.

    Перемешиваем dz_m: именно он задаёт КПД подачи и фильтрацию, то есть всю
    ту неоднородность, которой модель и пользуется. Фазы и Ky в модели общие
    для всех чеков, перемешивать их нечего.
    """
    checks, meta, season, perc_base, etm, perc = ctx()
    base = engine.solve_lp(checks, etm, perc, DEFICIT, KY)
    prac = practice(checks, etm, perc)
    real = gain_pct(base["yield_tons"], prac["yield_tons"])

    rng = random.Random(seed)
    gains = []
    for _ in range(runs):
        order = list(range(len(checks)))
        rng.shuffle(order)
        sh = [dc_replace(checks[i], dz_m=checks[order[i]].dz_m,
                         kpd=checks[order[i]].kpd) for i in range(len(checks))]
        d = engine.season_demands(sh, season, perc_base)
        r = engine.solve_lp(sh, d["etm"], d["perc"], DEFICIT, KY)
        if not r["ok"]:
            continue
        p = practice(sh, d["etm"], d["perc"])
        gains.append(gain_pct(r["yield_tons"], p["yield_tons"]))
    gains.sort()
    n = len(gains)

    def pct(q):
        return gains[min(n - 1, max(0, int(round(q * (n - 1)))))]

    res = {
        "test": "плацебо: 100 прогонов со случайной перестановкой отметок и КПД",
        "runs": n, "seed": seed, "deficit": DEFICIT, "ky": KY,
        "real_gain_pct": real,
        "placebo_median_pct": round(pct(0.5), 3),
        "placebo_p05_pct": round(pct(0.05), 3),
        "placebo_p95_pct": round(pct(0.95), 3),
        "placebo_min_pct": round(gains[0], 3),
        "placebo_max_pct": round(gains[-1], 3),
        "share_above_real": round(100.0 * sum(1 for g in gains if g >= real) / n, 1),
        "verdict": ("реальный выигрыш выше 95-го процентиля плацебо — эффект не случаен"
                    if real > pct(0.95) else
                    "реальный выигрыш внутри распределения плацебо — дифференциация ни при чём"),
    }
    return res


# ------------------------------------------- ТЕСТ 5: разложение выигрыша

def solve_variant(checks, etm, perc, deficit=DEFICIT, ky=KY, per_decade=True,
                  max_cut=engine.MAX_CUT, budget_m3=None, max_fallow_frac=0.6):
    """Тот же двухуровневый поиск, что и в engine.solve_lp, но с другим внутренним ЛП.

    Уровень 1 — сколько чеков вывести из севооборота (перебор k), уровень 2 —
    распределение остатка. Без вывода чеков бюджет не выполняется: фильтрация и
    разовое затопление не режутся вместе с поливом, и при лимите 85 % урезанием
    одного испарения бюджет не собрать.
    """
    n = len(checks)
    nd = engine.net_demand(checks, etm, perc)
    fixed_all = sum(checks[i].area_ha * engine.initial_mm(checks[i].kpd) * MM for i in range(n))
    variable_all = sum(checks[i].area_ha * sum(nd[i]) / checks[i].kpd * MM for i in range(n))
    budget = budget_m3 if budget_m3 is not None else (1.0 - deficit) * (fixed_all + variable_all)
    norm_per_ha = [engine.gross_norm(etm[i], perc[i], checks[i].kpd) for i in range(n)]
    worst_first = sorted(range(n), key=lambda i: -norm_per_ha[i])
    k_max = int(max_fallow_frac * n)

    cache: dict[int, dict] = {}

    def run(k: int) -> dict:
        active = worst_first[k:]
        r = lp_alloc(checks, etm, perc, ky=ky, budget=budget, max_cut=max_cut,
                     per_decade=per_decade, active=active)
        return r

    def evaluate(k: int) -> float:
        if k not in cache:
            cache[k] = run(k)
        r = cache[k]
        return -1e18 if not r["ok"] else r["yield_tons"]

    lo, hi = 0, k_max
    while hi - lo > 3:
        third = (hi - lo) // 3
        m1, m2 = lo + third, hi - third
        if evaluate(m1) < evaluate(m2):
            lo = m1
        else:
            hi = m2
    best = max(range(lo, min(hi, k_max) + 1), key=evaluate)
    return cache[best]


def test_decomp():
    """Три источника выигрыша, отключаем по одному."""
    checks, meta, season, perc_base, etm, perc = ctx()
    n = len(checks)

    full = engine.solve_lp(checks, etm, perc, DEFICIT, KY)
    prac = practice(checks, etm, perc)
    g_full = gain_pct(full["yield_tons"], prac["yield_tons"])

    # 1. без дифференциации по фазам: один дефицит на чек на весь сезон
    v_phase = solve_variant(checks, etm, perc, per_decade=False)
    g_phase = gain_pct(v_phase["yield_tons"], prac["yield_tons"])

    # 2. без гидравлики: все чеки одинаковы по отметке, КПД и фильтрации
    dz_mean = sum(c.dz_m for c in checks) / n
    flat = [dc_replace(c, dz_m=dz_mean, kpd=engine.kpd_of(dz_mean)) for c in checks]
    d_flat = engine.season_demands(flat, season, perc_base)
    v_hyd = engine.solve_lp(flat, d_flat["etm"], d_flat["perc"], DEFICIT, KY)
    prac_flat = practice(flat, d_flat["etm"], d_flat["perc"])
    g_hyd = gain_pct(v_hyd["yield_tons"], prac_flat["yield_tons"])

    # 3. без добора лимита: оптимум урезан по воде до фактической подачи практики
    v_eq = solve_variant(checks, etm, perc, budget_m3=prac["water_m3"])
    g_eq = gain_pct(v_eq["yield_tons"], prac["yield_tons"])

    return {
        "test": "разложение выигрыша на три источника (отключение по одному)",
        "deficit": DEFICIT, "ky": KY,
        "gain_full_pct": g_full,
        "gain_no_phases_pct": g_phase,
        "gain_no_hydraulics_pct": g_hyd,
        "gain_equal_water_pct": g_eq,
        "share_phases_pct": round(g_full - g_phase, 3),
        "share_hydraulics_pct": round(g_full - g_hyd, 3),
        "share_limit_uptake_pct": round(g_full - g_eq, 3),
        "water_practice_m3": round(prac["water_m3"]),
        "water_optimum_m3": round(full["water_m3"]),
        "water_underuse_m3": round(full["water_m3"] - prac["water_m3"]),
        "note": "источники пересекаются: доли при последовательном отключении, в сумме не обязаны давать 100 %",
    }


# ------------------------------------- ТЕСТ 1: фильтрация константа или по слою

def test_filtration():
    """Обратная задача в двух режимах фильтрации.

    А — фильтрация константа (нынешняя модель): сколько ни режь полив,
    инфильтрация идёт та же.
    Б — фильтрация падает вместе с слоем: F = F0·(1 − κ·δ), κ = 0,5 и 1,0.
    """
    out = {"test": "фильтрация: константа против пропорциональной слою", "deficit": DEFICIT,
           "ky": KY, "modes": []}
    for kappa in (0.0, 0.5, 1.0):
        checks, meta, season, perc_base, etm, perc = ctx(kappa=kappa)
        prac = practice(checks, etm, perc)
        opt = engine.solve_lp(checks, etm, perc, DEFICIT, KY)
        inv = lp_alloc(checks, etm, perc, min_yield=prac["yield_tons"])
        inv_free = lp_alloc(checks, etm, perc, min_yield=prac["yield_tons"], max_cut=1.0)
        area = sum(c.area_ha for c in checks)
        out["modes"].append({
            "kappa": kappa,
            "mode": "фильтрация константа" if kappa == 0 else f"фильтрация × (1 − {kappa}·δ)",
            "yield_practice_t": round(prac["yield_tons"], 1),
            "yield_optimum_t": round(opt["yield_tons"], 1),
            "gain_yield_pct": gain_pct(opt["yield_tons"], prac["yield_tons"]),
            "water_practice_m3": round(prac["water_m3"]),
            "water_optimum_m3": round(opt["water_m3"]),
            "water_inverse_m3": round(inv["water_m3"]) if inv["ok"] else None,
            "water_inverse_nocap_m3": round(inv_free["water_m3"]) if inv_free["ok"] else None,
            "saving_m3_per_ha": round((prac["water_m3"] - inv["water_m3"]) / area) if inv["ok"] else None,
            "saving_mln_m3": round((prac["water_m3"] - inv["water_m3"]) / 1e6, 3) if inv["ok"] else None,
            "saving_pct": round(100.0 * (1 - inv["water_m3"] / prac["water_m3"]), 2) if inv["ok"] else None,
            "saving_nocap_m3_per_ha": (round((prac["water_m3"] - inv_free["water_m3"]) / area)
                                       if inv_free["ok"] else None),
            "saving_nocap_pct": (round(100.0 * (1 - inv_free["water_m3"] / prac["water_m3"]), 2)
                                 if inv_free["ok"] else None),
            "wp_practice": round(wp(prac["yield_tons"], prac["water_m3"]), 8),
            "wp_optimum": round(wp(opt["yield_tons"], opt["water_m3"]), 8),
        })
        reset()
    return out


# ---------------------------------- ТЕСТ 2: правильная постановка обратной задачи

def test_inverse():
    """Минимум водозабора при урожае не ниже практики.

    Условия: недолив по чеку — непрерывная величина, потолок 10 % (и прогон без
    потолка), никакого вывода чеков внутри задачи.
    """
    checks, meta, season, perc_base, etm, perc = ctx()
    area = sum(c.area_ha for c in checks)
    prac = practice(checks, etm, perc)
    opt = engine.solve_lp(checks, etm, perc, DEFICIT, KY)
    rows = []
    for cap, label in ((engine.MAX_CUT, "потолок недолива 10 %"), (1.0, "без потолка")):
        inv = lp_alloc(checks, etm, perc, min_yield=prac["yield_tons"], max_cut=cap)
        if not inv["ok"]:
            rows.append({"cap": cap, "ok": False, "status": inv.get("status")})
            continue
        cuts = sorted(((p["deficit_pct"], p["id"]) for p in inv["per_check"]), reverse=True)
        rows.append({
            "cap": cap, "label": label, "ok": True,
            "water_practice_m3": round(prac["water_m3"]),
            "water_inverse_m3": round(inv["water_m3"]),
            "saving_m3": round(prac["water_m3"] - inv["water_m3"]),
            "saving_m3_per_ha": round((prac["water_m3"] - inv["water_m3"]) / area),
            "saving_mln_m3": round((prac["water_m3"] - inv["water_m3"]) / 1e6, 3),
            "saving_pct": round(100.0 * (1 - inv["water_m3"] / prac["water_m3"]), 2),
            "yield_t": round(inv["yield_tons"], 1),
            "max_cut_check": {"id": cuts[0][1], "deficit_pct": cuts[0][0]},
            "min_cut_check": {"id": cuts[-1][1], "deficit_pct": cuts[-1][0]},
            "checks_at_zero": sum(1 for p in inv["per_check"] if p["deficit_pct"] < 0.05),
            "checks_at_cap": sum(1 for p in inv["per_check"] if p["deficit_pct"] > 9.95),
        })
    return {
        "test": "обратная задача: минимум воды при урожае не ниже практики",
        "deficit": DEFICIT, "ky": KY, "area_ha": round(area, 1),
        "yield_practice_t": round(prac["yield_tons"], 1),
        "yield_optimum_same_limit_t": round(opt["yield_tons"], 1),
        "gain_yield_pct": gain_pct(opt["yield_tons"], prac["yield_tons"]),
        "rows": rows,
        "wp_practice": round(wp(prac["yield_tons"], prac["water_m3"]), 8),
        "wp_optimum": round(wp(opt["yield_tons"], opt["water_m3"]), 8),
    }


# ------------------------------- ТЕСТ 3: откуда взялась кривизна отклика

def test_ky():
    """Обратная задача при Ky = 0,9 / 1,0 / 1,1 / 1,2 и при линейном отклике."""
    out = {"test": "кривизна отклика: Ky и линейная функция потерь", "deficit": DEFICIT, "rows": []}
    for ky in (0.9, 1.0, 1.1, 1.2):
        checks, meta, season, perc_base, etm, perc = ctx(ky=ky)
        area = sum(c.area_ha for c in checks)
        prac = practice(checks, etm, perc, ky=ky)
        opt = engine.solve_lp(checks, etm, perc, DEFICIT, ky)
        inv = lp_alloc(checks, etm, perc, ky=ky, min_yield=prac["yield_tons"])
        out["rows"].append({
            "ky": ky, "response": "кусочно-линейная 0,70 / 1,60",
            "gain_yield_pct": gain_pct(opt["yield_tons"], prac["yield_tons"]),
            "saving_pct": round(100.0 * (1 - inv["water_m3"] / prac["water_m3"]), 2) if inv["ok"] else None,
            "saving_m3_per_ha": round((prac["water_m3"] - inv["water_m3"]) / area) if inv["ok"] else None,
            "yield_practice_t": round(prac["yield_tons"], 1),
        })
    # линейный отклик: один сегмент, без излома
    checks, meta, season, perc_base, etm, perc = ctx(linear=True)
    area = sum(c.area_ha for c in checks)
    prac = practice(checks, etm, perc)
    opt = engine.solve_lp(checks, etm, perc, DEFICIT, KY)
    inv = lp_alloc(checks, etm, perc, min_yield=prac["yield_tons"])
    out["rows"].append({
        "ky": KY, "response": "линейная 0,70 (без излома)",
        "gain_yield_pct": gain_pct(opt["yield_tons"], prac["yield_tons"]),
        "saving_pct": round(100.0 * (1 - inv["water_m3"] / prac["water_m3"]), 2) if inv["ok"] else None,
        "saving_m3_per_ha": round((prac["water_m3"] - inv["water_m3"]) / area) if inv["ok"] else None,
        "yield_practice_t": round(prac["yield_tons"], 1),
    })
    reset()
    return out


# ------------------------------------- ТЕСТ 4: три базовые линии вместо одной

def test_baselines():
    """Четыре управления: уравниловка, практика, идеальный агроном, оптимум."""
    checks, meta, season, perc_base, etm, perc = ctx()
    area = sum(c.area_ha for c in checks)
    nd = engine.net_demand(checks, etm, perc)
    full_water = sum(checks[i].area_ha * (sum(nd[i]) / checks[i].kpd + engine.initial_mm(checks[i].kpd))
                     * MM for i in range(len(checks)))
    budget = (1.0 - DEFICIT) * full_water

    uni = engine.baseline_uniform(checks, etm, perc, DEFICIT, KY)
    prac = practice(checks, etm, perc)
    ideal = lp_alloc(checks, etm, perc, budget=budget, max_cut=1.0)      # по фазам, без ограничений
    opt = engine.solve_lp(checks, etm, perc, DEFICIT, KY)               # оптимум продукта

    rows = []
    for name, r in (("Уравниловка (поровну)", uni), ("Практика: вывод чеков", prac),
                    ("Идеальный агроном (по фазам, без ограничений)", ideal),
                    ("Оптимум АКВАЧЕК", opt)):
        rows.append({"baseline": name, "yield_t": round(r["yield_tons"], 1),
                     "water_mln_m3": round(r["water_m3"] / 1e6, 3),
                     "wp_t_per_m3": round(wp(r["yield_tons"], r["water_m3"]), 8),
                     "wp_gain_vs_practice_pct": None})
    p_wp = wp(prac["yield_tons"], prac["water_m3"])
    for row in rows:
        row["wp_gain_vs_practice_pct"] = round(100.0 * (wp(row["yield_t"], row["water_mln_m3"] * 1e6) / p_wp - 1), 2)
    return {"test": "четыре управления: урожай, вода, продуктивность воды",
            "deficit": DEFICIT, "ky": KY, "area_ha": round(area, 1),
            "budget_mln_m3": round(budget / 1e6, 3), "rows": rows}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    t0 = time.time()
    results = {}
    if which in ("all", "placebo"):
        results["placebo"] = test_placebo()
        print(json.dumps(results["placebo"], ensure_ascii=False, indent=2))
    if which in ("all", "decomp"):
        results["decomposition"] = test_decomp()
        print(json.dumps(results["decomposition"], ensure_ascii=False, indent=2))
    if which in ("all", "filt"):
        results["filtration"] = test_filtration()
        print(json.dumps(results["filtration"], ensure_ascii=False, indent=2))
    if which in ("all", "inverse"):
        results["inverse"] = test_inverse()
        print(json.dumps(results["inverse"], ensure_ascii=False, indent=2))
    if which in ("all", "ky"):
        results["ky"] = test_ky()
        print(json.dumps(results["ky"], ensure_ascii=False, indent=2))
    if which in ("all", "baselines"):
        results["baselines"] = test_baselines()
        print(json.dumps(results["baselines"], ensure_ascii=False, indent=2))
    results["_seconds"] = round(time.time() - t0, 1)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"записано {OUT} ({time.time() - t0:.1f} с)")


if __name__ == "__main__":
    main()
