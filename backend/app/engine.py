"""Ядро «АКВАЧЕК»: водный баланс чека, оптимизация водоподачи, статистика.

Модель открыта и публикуется на сайте:

  * ET0 — FAO-56 Penman–Monteith на реальных суточных данных ERA5;
  * ETc = Kc(фаза)·ET0, фазы и Kc — 1,05 / 1,20 / 0,95 при 30 / 60 / 53 сутках;
  * фильтрация F_i = F0 + k_f·Δz_i (F0 калибруется по нормативной норме 16 009 м³/га);
  * КПД подачи kpd_i = 0,80 − 0,012·Δz_i (упрощённая модель этапа 1; калибровка — этап 2);
  * эффективные осадки Pe = 0,7·P;
  * разовое затопление и насыщение — 2 000 м³/га (методика ВНИИ), входит в первую декаду;
  * отклик урожая — FAO-33: потеря = Σ Ky,p · w_p · g(δ_p),
      w_p — доля фазы в суммарном испарении сезона, δ_p — дефицит подачи в декаде,
      g(δ) — кусочно-линейная: 0,7·δ при δ ≤ 0,30 и 0,21 + 1,6·(δ − 0,30) глубже.
    Кусочно-линейность задаёт убывающую отдачу воды: лёгкий дефицит культура переносит
    сравнительно легко, глубокий резко снижает урожай. Без этого задача вырождалась бы
    в «залить одни чеки и бросить другие», что не соответствует практике.

Оптимизация: задача ЛП (PuLP + CBC). Переменные — дефицит водоподачи по чекам и декадам,
целевая функция — минимум суммарной потери урожая при лимите водозабора.
"""
from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import pulp

from .fao56 import actual_vapour_pressure, doy_of, et0_penman_monteith

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SENTINEL = DATA / "sentinel"

# --- параметры модели ---
SEASON_START = "05-10"
SEASON_DAYS = 143
PHASES = [
    (30, 1.05, 0.85),  # затопление, всходы, кущение
    (60, 1.20, 1.30),  # выметывание, цветение — критическая фаза
    (53, 0.95, 0.80),  # налив, созревание, предподсушка
]
PHASES_KY_W = [w for (_l, _kc, w) in PHASES]
INITIAL_MM = 200.0          # 2 000 м³/га разовое затопление и насыщение
KF = 0.15                   # мм/сут на метр превышения чека
KPD_BASE, KPD_SLOPE = 0.80, 0.012
TARGET_NORM_M3_HA = 16_009
RAIN_EFF = 0.70
SEG1, SLOPE1, SLOPE2 = 0.30, 0.70, 1.60

# Ограничение режима: чек в севообороте недополучает не более 10 % своей нормы воды.
# Иначе оптимизатор «экономит», собирая весь дефицит на немногих чеках — на бумаге выгода
# есть, в поле такие чеки просто теряют урожай. Включено по умолчанию.
MAX_CUT = 0.10
LIMIT_MIN = 0.70   # слайдер лимита водоподачи: 100 % .. 70 %
LIMIT_MAX = 1.00
LIMIT_STEP = 0.01

YIELD_BASE_C_HA = 73.1      # урожайность риса в крае, 2025 г., ц/га (Росстат)
YIELD_T_HA = YIELD_BASE_C_HA / 10.0   # 1 ц = 0,1 т -> т/га
PRICE_RUB_KG = 25.0
KRAI_AREA_HA = 112_300
KRAI_GROSS_T = 829_700

MM_TO_M3_HA = 10.0


@dataclass
class Check:
    id: int
    area_ha: float
    elev_m: float
    dz_m: float
    kpd: float
    centroid: list
    ndvi_peak: float | None = None
    ndwi_peak: float | None = None


@dataclass
class Day:
    date: str
    et0: float
    et0_ref: float
    precip: float
    tmean: float


@dataclass
class Season:
    days: list
    decades: list = None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_weather(year: int) -> list:
    path = DATA / "weather" / f"era5_daily_{year}.csv"
    if not path.exists():
        raise FileNotFoundError(f"нет файла метеоряда {path}")
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_season(rows: list, lat: float, elev: float, start_md: str = SEASON_START, days: int = SEASON_DAYS) -> Season:
    month, day = (int(x) for x in start_md.split("-"))
    start_idx = next(
        (i for i, r in enumerate(rows) if int(r["date"][5:7]) == month and int(r["date"][8:10]) >= day), 0
    )
    window = rows[start_idx : start_idx + days]
    out = []
    for r in window:
        tmean = float(r["tmean"])
        ea = actual_vapour_pressure(tmean, _f(r["rh_mean"]), _f(r["td_mean"]))
        et0 = et0_penman_monteith(
            tmin=float(r["tmin"]),
            tmax=float(r["tmax"]),
            tmean=tmean,
            u2=float(r["u2"]),
            rs=float(r["rs"]),
            ea=ea,
            doy=doy_of(r["date"]),
            lat_deg=lat,
            z=elev,
            pressure=_f(r["pressure"]),
        )
        out.append(Day(r["date"], et0, float(r["et0_ref"] or 0.0), float(r["precip"] or 0.0), tmean))
    season = Season(days=out)
    idx = list(range(len(out)))
    season.decades = [idx[i : i + 10] for i in range(0, len(idx), 10)]
    return season


def phase_of(day_index: int) -> int:
    pos = 0
    for i, (length, _kc, _w) in enumerate(PHASES):
        pos += length
        if day_index < pos:
            return i
    return len(PHASES) - 1


def decade_lengths(total: int) -> list:
    return [len(list(range(i, min(i + 10, total)))) for i in range(0, total, 10)]


def phase_of_dec(dec_index: int, season_len: int = SEASON_DAYS) -> int:
    return phase_of(min(dec_index * 10 + 5, season_len - 1))


def load_checks():
    path = DATA / "checks.geojson"
    if not path.exists():
        raise FileNotFoundError("нет data/checks.geojson — сначала выполните пайплайн данных")
    gj = json.loads(path.read_text(encoding="utf-8"))
    checks = []
    for f in gj["features"]:
        p = f["properties"]
        checks.append(
            Check(
                id=int(p["id"]),
                area_ha=float(p["area_ha"]),
                elev_m=float(p.get("elev_m") or 0.0),
                dz_m=float(p.get("dz_m") or 0.0),
                kpd=float(p.get("kpd") or KPD_BASE),
                centroid=p.get("centroid", [0.0, 0.0]),
                ndvi_peak=p.get("ndvi_peak"),
                ndwi_peak=p.get("ndwi_peak"),
            )
        )
    return checks, gj.get("metadata", {})


# ------------------------------------------------------------------ водный баланс

def kpd_of(dz: float) -> float:
    return max(0.55, min(0.92, KPD_BASE - KPD_SLOPE * dz))


def season_demands(checks: list, season: Season, perc_base: float, intermittent: bool = False) -> dict:
    factor = 0.60 if intermittent else 1.0
    etm, perc = [], []
    for c in checks:
        row_etm, row_perc = [], []
        for dec in season.decades:
            et_sum = sum(PHASES[phase_of(i)][1] * season.days[i].et0 for i in dec)
            rain = RAIN_EFF * sum(season.days[i].precip for i in dec)
            f_mm = (perc_base + KF * c.dz_m) * len(dec) * factor
            row_etm.append(et_sum)
            row_perc.append(max(0.0, f_mm - rain))
        etm.append(row_etm)
        perc.append(row_perc)
    return {"etm": etm, "perc": perc}


def net_demand(checks: list, etm: list, perc: list) -> list:
    """Суммарная эксплуатационная потребность по декадам (нетто), мм — для ведомостей.

    Разовое затопление и насыщение (2 000 м³/га) сюда не входит: по кубанской технологии
    его проводят до сева, это постоянная часть сезонного водозабора, а не декадный расход.
    """
    return [[etm[i][p] + perc[i][p] for p in range(len(etm[i]))] for i in range(len(checks))]


def gross_decade(etm_p: float, perc_p: float, delta: float, kpd: float) -> float:
    """Подача за декаду брутто, мм.

    Дефицит урезает испарение: именно ETa/ETm определяет урожай по FAO-33. Фильтрация —
    обязательная статья баланса, её нельзя «недолить», не осушив чек: слой воды держится
    валиками, и при сокращении подачи в первую очередь падает испарение, а не инфильтрация.
    """
    return ((1.0 - delta) * etm_p + perc_p) / kpd


def et_deficit_of(gross_p: float, etm_p: float, perc_p: float, kpd: float) -> float:
    """Обратная задача: какой дефицит испарения соответствует данной подаче."""
    if etm_p <= 1e-9:
        return 0.0
    available = gross_p * kpd - perc_p
    return max(0.0, min(1.0, 1.0 - available / etm_p))


def initial_mm(kpd: float) -> float:
    """Разовое затопление и насыщение, мм брутто."""
    return INITIAL_MM / kpd


def gross_norm(etm_row: list, perc_row: list, kpd: float) -> float:
    """Сезонная оросительная норма брутто, м³/га."""
    return (sum(etm_row) + sum(perc_row) + INITIAL_MM) / kpd * MM_TO_M3_HA


def gross_norm_mm(nd: list, kpd: float) -> float:
    return (sum(nd) + INITIAL_MM) / kpd * MM_TO_M3_HA


def calibrate_percolation(checks: list, season: Season, target: float = TARGET_NORM_M3_HA) -> float:
    lo, hi = 0.0, 8.0
    for _ in range(40):
        mid = (lo + hi) / 2
        d = season_demands(checks, season, mid)
        mean_norm = sum(gross_norm(d["etm"][i], d["perc"][i], checks[i].kpd)
                        for i in range(len(checks))) / len(checks)
        if mean_norm < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ------------------------------------------------------------------ отклик урожая

def deficit_cost(delta: float) -> tuple:
    """Кусочно-линейная функция потери: возвращает (ступень 1, ступень 2)."""
    d = max(0.0, min(1.0, delta))
    s1 = min(d, SEG1)
    s2 = max(0.0, d - SEG1)
    return s1, s2


def loss_from_cost(s1: float, s2: float) -> float:
    return SLOPE1 * s1 + SLOPE2 * s2


def et_weights(etm_row: list) -> list:
    total = sum(etm_row)
    if total <= 0:
        return [1.0 / max(len(etm_row), 1)] * len(etm_row)
    return [v / total for v in etm_row]


def yield_fraction(deltas: list, etm_row: list, ky_global: float) -> float:
    """Доля урожая по FAO-33 с взвешиванием по доле фазы в суммарном испарении."""
    w = et_weights(etm_row)
    loss = 0.0
    for p, d in enumerate(deltas):
        ky = ky_global * PHASES_KY_W[phase_of_dec(p)]
        s1, s2 = deficit_cost(d)
        loss += ky * w[p] * loss_from_cost(s1, s2)
    return max(0.0, 1.0 - loss)


def _lp_fixed(checks: list, etm: list, perc: list, active: list, budget: float,
              ky_global: float, capacity_l_s_ha: float, max_cut: float = MAX_CUT) -> dict:
    """Чистое ЛП при заданном составе чеков в севообороте.

    Переменные — дефицит испарения по чеку и декаде (две ступени кусочно-линейной функции
    потери). Ограничения: лимит водозабора на сезон и пропускная способность водовыпуска.
    """
    p_dec = len(etm[0])
    w = et_weights(etm[0])  # ETm одинаково для всех чеков: погода и Kc общие
    dec_len = decade_lengths(SEASON_DAYS)

    prob = pulp.LpProblem("akvachek", pulp.LpMinimize)
    d1 = {(i, p): pulp.LpVariable(f"d1_{i}_{p}", lowBound=0, upBound=SEG1) for i in active for p in range(p_dec)}
    d2 = {(i, p): pulp.LpVariable(f"d2_{i}_{p}", lowBound=0, upBound=1.0 - SEG1) for i in active for p in range(p_dec)}

    prob += pulp.lpSum(
        checks[i].area_ha * YIELD_T_HA * ky_global * PHASES_KY_W[phase_of_dec(p)] * w[p]
        * (SLOPE1 * d1[(i, p)] + SLOPE2 * d2[(i, p)])
        for i in active for p in range(p_dec)
    )

    for i in active:
        prob += pulp.lpSum(
            ky_global * PHASES_KY_W[phase_of_dec(p)] * w[p] * (SLOPE1 * d1[(i, p)] + SLOPE2 * d2[(i, p)])
            for p in range(p_dec)
        ) <= 1.0
        if max_cut < 1.0:
            # недолив воды на чек за сезон: сумма ETm*дефицит <= доля * полная потребность чека
            prob += pulp.lpSum(etm[i][p] * (d1[(i, p)] + d2[(i, p)]) for p in range(p_dec)) \
                    <= max_cut * sum(etm[i][p] + perc[i][p] for p in range(p_dec))
        for p in range(p_dec):
            prob += gross_decade(etm[i][p], perc[i][p], d1[(i, p)] + d2[(i, p)], checks[i].kpd) \
                    <= capacity_l_s_ha * 8.64 * dec_len[p]

    prob += (
        pulp.lpSum(
            checks[i].area_ha * gross_decade(etm[i][p], perc[i][p], d1[(i, p)] + d2[(i, p)], checks[i].kpd)
            * MM_TO_M3_HA
            for i in active for p in range(p_dec)
        )
        + pulp.lpSum(checks[i].area_ha * initial_mm(checks[i].kpd) * MM_TO_M3_HA for i in active)
        <= budget
    )

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        return {"ok": False, "status": pulp.LpStatus[status]}

    n = len(checks)
    deltas = [[(d1[(i, p)].value() or 0.0) + (d2[(i, p)].value() or 0.0) if i in active else 1.0
               for p in range(p_dec)] for i in range(n)]
    gross = [[gross_decade(etm[i][p], perc[i][p], deltas[i][p], checks[i].kpd) if i in active else 0.0
              for p in range(p_dec)] for i in range(n)]
    water = sum(checks[i].area_ha * (sum(gross[i]) + initial_mm(checks[i].kpd)) * MM_TO_M3_HA for i in active)
    fracs = [yield_fraction(deltas[i], etm[i], ky_global) for i in range(n)]
    return {
        "ok": True,
        "status": "Optimal",
        "k": len(checks) - len(active),
        "deltas": deltas,
        "gross_mm": gross,
        "water_m3": water,
        "yield_tons": sum(checks[i].area_ha * YIELD_T_HA * fracs[i] for i in range(n)),
        "fracs": fracs,
    }


def solve_lp(checks: list, etm: list, perc: list, deficit: float, ky_global: float,
             capacity_l_s_ha: float = 3.5, max_fallow_frac: float = 0.6,
             budget_m3: float | None = None, max_cut: float = MAX_CUT) -> dict:
    """Оптимизатор водоподачи.

    Два уровня решений: сколько чеков вывести из севооборота (целочисленный параметр k,
    перебирается тернарным поиском, что быстро и даёт предсказуемый результат) и как
    распределить остаток воды между чеками и декадами (чистое ЛП, решается точно).

    Чеки на вывод упорядочены по удельной воде на единицу урожая — это наименее
    выгодные чеки: чем выше отметка, тем больше фильтрация и ниже КПД подачи.
    """
    n = len(checks)
    p_dec = len(etm[0])
    nd = net_demand(checks, etm, perc)

    fixed_all = sum(checks[i].area_ha * initial_mm(checks[i].kpd) * MM_TO_M3_HA for i in range(n))
    variable_all = sum(checks[i].area_ha * sum(nd[i]) / checks[i].kpd * MM_TO_M3_HA for i in range(n))
    full_water = fixed_all + variable_all
    budget = budget_m3 if budget_m3 is not None else (1.0 - deficit) * full_water

    norm_per_ha = [gross_norm(etm[i], perc[i], checks[i].kpd) for i in range(n)]
    worst_first = sorted(range(n), key=lambda i: -norm_per_ha[i])
    k_max = int(max_fallow_frac * n)

    def run(k: int) -> dict:
        active = worst_first[k:]
        r = _lp_fixed(checks, etm, perc, active, budget, ky_global, capacity_l_s_ha, max_cut)
        r["active"] = sorted(active)
        return r

    cache: dict[int, dict] = {}

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
    best_k = max(range(lo, min(hi, k_max) + 1), key=evaluate)
    res = cache[best_k]

    deltas = res["deltas"]
    gross = res["gross_mm"]
    in_rotation = [i in set(res["active"]) for i in range(n)]
    return {
        "status": res["status"],
        "ok": True,
        "max_cut": max_cut,
        "deltas": deltas,
        "gross_mm": gross,
        "in_rotation": in_rotation,
        "fallowed": [checks[i].id for i in range(n) if not in_rotation[i]],
        "water_m3": res["water_m3"],
        "budget_m3": budget,
        "full_water_m3": full_water,
        "yield_tons": res["yield_tons"],
        "per_check": [
            {
                "id": checks[i].id,
                "active": in_rotation[i],
                "deficit_pct": round(100.0 * (1.0 - sum((1.0 - d) * etm[i][p] for p, d in enumerate(deltas[i]))
                                              / max(sum(etm[i]), 1e-9)), 2),
                "cut_pct": round(100.0 * (1.0 - sum(gross[i]) / max(sum(nd[i]) / checks[i].kpd, 1e-9)), 2),
                "yield_frac": round(res["fracs"][i], 4),
            }
            for i in range(n)
        ],
    }


def uniform_delta(checks: list, etm: list, perc: list, budget: float, active=None) -> float:
    """Единый для всех срез по испарению, который укладывается ровно в заданный бюджет воды."""
    n = len(checks)
    act = range(n) if active is None else active
    fixed = sum(checks[i].area_ha * initial_mm(checks[i].kpd) * MM_TO_M3_HA for i in act)
    et_term = sum(checks[i].area_ha * sum(etm[i]) / checks[i].kpd * MM_TO_M3_HA for i in act)
    perc_term = sum(checks[i].area_ha * sum(perc[i]) / checks[i].kpd * MM_TO_M3_HA for i in act)
    if et_term <= 1e-9:
        return 1.0
    rest = budget - fixed - perc_term
    return max(0.0, min(1.0, 1.0 - rest / et_term))


def baseline_uniform(checks: list, etm: list, perc: list, deficit: float, ky_global: float) -> dict:
    """Практика «всем поровну»: тот же лимит воды, что и у оптимизатора, срез одинаков для всех."""
    nd = net_demand(checks, etm, perc)
    n = len(checks)
    fixed = sum(checks[i].area_ha * initial_mm(checks[i].kpd) * MM_TO_M3_HA for i in range(n))
    variable = sum(checks[i].area_ha * sum(nd[i]) / checks[i].kpd * MM_TO_M3_HA for i in range(n))
    budget = (1.0 - deficit) * (fixed + variable)
    delta = uniform_delta(checks, etm, perc, budget)
    deltas = [[delta] * len(nd[0]) for _ in range(n)]
    fracs = [yield_fraction(deltas[i], etm[i], ky_global) for i in range(n)]
    gross = [[gross_decade(etm[i][p], perc[i][p], delta, checks[i].kpd) for p in range(len(nd[i]))]
             for i in range(n)]
    water = fixed + sum(checks[i].area_ha * sum(gross[i]) * MM_TO_M3_HA for i in range(n))
    return {
        "deltas": deltas,
        "gross_mm": gross,
        "water_m3": water,
        "budget_m3": budget,
        "yield_tons": sum(checks[i].area_ha * YIELD_T_HA * fracs[i] for i in range(n)),
        "per_check": [{"id": checks[i].id, "deficit_pct": round(delta * 100, 2),
                       "cut_pct": round(100 * (1 - sum(gross[i]) / max(sum(nd[i]) / checks[i].kpd, 1e-9)), 2),
                       "yield_frac": round(fracs[i], 4)} for i in range(n)],
    }


def baseline_fallowing(checks: list, etm: list, perc: list, deficit: float, ky_global: float) -> dict:
    """Реальная практика маловодья: чеки с высокими отметками выводятся из севооборота."""
    nd = net_demand(checks, etm, perc)
    n = len(checks)
    demand = [(sum(nd[i]) / checks[i].kpd + initial_mm(checks[i].kpd)) * MM_TO_M3_HA for i in range(n)]
    budget = (1.0 - deficit) * sum(demand[i] * checks[i].area_ha for i in range(n))

    order = sorted(range(n), key=lambda i: -checks[i].dz_m)
    active = set(range(n))
    used = sum(demand[i] * checks[i].area_ha for i in active)
    for i in order:
        if used <= budget:
            break
        active.discard(i)
        used -= demand[i] * checks[i].area_ha
    # оставшимся чекам урезаем эксплуатационную часть ровно до бюджета
    delta = uniform_delta(checks, etm, perc, budget, active=sorted(active))
    deltas = [[delta if i in active else 1.0] * len(nd[0]) for i in range(n)]
    fracs = [yield_fraction(deltas[i], etm[i], ky_global) for i in range(n)]
    # выведенный из севооборота чек не получает воду вовсе: ни затопления, ни эксплуатационной подачи
    water = sum(
        checks[i].area_ha * (sum(gross_decade(etm[i][p], perc[i][p], deltas[i][p], checks[i].kpd)
                                 for p in range(len(nd[i])))
                             + (initial_mm(checks[i].kpd) if i in active else 0.0))
        * MM_TO_M3_HA * (1.0 if i in active else 0.0)
        for i in range(n)
    )
    return {
        "water_m3": water,
        "delta_pct": round(delta * 100, 2),
        "yield_tons": sum(checks[i].area_ha * YIELD_T_HA * fracs[i] for i in range(n)),
        "fallowed": [checks[i].id for i in range(n) if i not in active],
        "per_check": [{"id": checks[i].id, "active": i in active,
                       "deficit_pct": round(deltas[i][0] * 100, 2),
                       "yield_frac": round(fracs[i], 4)} for i in range(n)],
        "gross_mm": [[gross_decade(etm[i][p], perc[i][p], deltas[i][p], checks[i].kpd)
                      if i in active else 0.0
                      for p in range(len(nd[i]))] for i in range(n)],
    }


def evaluate_decision(checks: list, gross_mm: list, etm: list, perc: list, ky_global: float) -> float:
    """Оценка принятого решения в истинной модели: сколько урожая реально получится."""
    total = 0.0
    for i in range(len(checks)):
        deltas = [et_deficit_of(gross_mm[i][p], etm[i][p], perc[i][p], checks[i].kpd)
                  for p in range(len(etm[i]))]
        total += checks[i].area_ha * YIELD_T_HA * yield_fraction(deltas, etm[i], ky_global)
    return total


def monte_carlo(checks: list, season: Season, perc_base: float, deficit: float, ky_global: float,
                sigma_m: float, runs: int = 60, seed: int = 20261030,
                max_cut: float = MAX_CUT) -> dict:
    """Стресс-тест: решение принимается по зашумлённым отметкам, оценивается в истинной модели."""
    rng = random.Random(seed)
    d_true = season_demands(checks, season, perc_base)
    etm_t, perc_t = d_true["etm"], d_true["perc"]
    base_fallowing = baseline_fallowing(checks, etm_t, perc_t, deficit, ky_global)["yield_tons"]

    gains = []
    for _ in range(runs):
        noisy = []
        for c in checks:
            dz = c.dz_m + rng.gauss(0.0, sigma_m)
            noisy.append(Check(c.id, c.area_ha, c.elev_m + (dz - c.dz_m), dz, kpd_of(dz), c.centroid))
        d_noisy = season_demands(noisy, season, perc_base)
        res = solve_lp(noisy, d_noisy["etm"], d_noisy["perc"], deficit, ky_global, max_cut=max_cut)
        if not res["ok"]:
            continue
        truth = evaluate_decision(checks, res["gross_mm"], etm_t, perc_t, ky_global)
        gains.append(100.0 * (truth / base_fallowing - 1.0))

    if not gains:
        return {"runs": 0}
    gains.sort()
    n = len(gains)
    return {
        "runs": n,
        "sigma_m": sigma_m,
        "base": "практика вывода чеков из севооборота",
        "max_cut": max_cut,
        "mean": sum(gains) / n,
        "p05": gains[int(0.05 * n)],
        "p50": gains[n // 2],
        "p95": gains[min(int(0.95 * n), n - 1)],
        "min": gains[0],
        "max": gains[-1],
        "histogram": _histogram(gains, 24),
    }


def _histogram(values: list, bins: int) -> list:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [{"x": lo, "count": len(values)}]
    step = (hi - lo) / bins
    out = [{"x": round(lo + step * (i + 0.5), 3), "count": 0} for i in range(bins)]
    for v in values:
        out[min(int((v - lo) / step), bins - 1)]["count"] += 1
    return out


def economics(gain_pct: float, area_ha: float, price: float = PRICE_RUB_KG) -> dict:
    """Эффект в рублях на гектар массива и на весь массив. Экстраполяции на край нет:
    модельный результат получен на одном массиве одного сезона."""
    per_ha_kg = YIELD_T_HA * 1000.0 * gain_pct / 100.0  # т/га -> кг/га
    per_ha_rub = per_ha_kg * price
    return {
        "gain_pct": gain_pct,
        "per_ha_kg": round(per_ha_kg, 2),
        "per_ha_rub": round(per_ha_rub, 0),
        "array_rub": round(per_ha_rub * area_ha, 0),
        "area_ha": round(area_ha, 1),
        "price": price,
        "base": "к практике вывода чеков из севооборота",
        "note": "Эффект возникает только в годы лимитирования водоподачи",
    }
