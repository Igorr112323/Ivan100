"""Ядро «АКВАЧЕК»: водный баланс чека, оптимизация водоподачи, статистика.

Модель (открыто документируется на сайте):
  * ET0 — FAO-56 Penman–Monteith на реальных суточных данных ERA5;
  * ETc = Kc(фаза) × ET0, фазы и Kc — по заявке (1,05 / 1,20 / 0,95; 30 / 60 / 53 сут);
  * фильтрация F_i = F0 + k_f·Δz_i (F0 калибруется по нормативной норме 16 009 м³/га);
  * КПД подачи kpd_i = 0,80 − 0,012·Δz_i (упрощённая модель этапа 1, калибровка — этап 2);
  * эффективные осадки Pe = 0,7·P;
  * отклик урожая — FAO-33: 1 − Y/Ymax = Σ Ky_p·(1 − ETa/ETm).

Оптимизация: задача ЛП (PuLP + CBC). Переменные — подача воды по чекам и декадам,
целевая функция — валовой сбор риса массива при лимите водозабора.
"""
from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import pulp

from .fao56 import actual_vapour_pressure, doy_of, et0_penman_monteith

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SENTINEL = DATA / "sentinel"

# --- параметры модели (публикуются на сайте как «открытая модель») ---
SEASON_START = "05-10"      # начало вегетационного сезона риса на Кубани
SEASON_DAYS = 143
PHASES = [                  # (длительность, Kc, весовой коэффициент чувствительности)
    (30, 1.05, 0.85),       # затопление, всходы, кущение
    (60, 1.20, 1.30),       # выметывание, цветение — критическая фаза
    (53, 0.95, 0.80),       # налив, созревание, предподсушка
]
INITIAL_MM = 200.0          # разовое затопление и насыщение: 2 000 м³/га (методика ВНИИ)
KF = 0.15                   # мм/сут на метр превышения чека
KPD_BASE, KPD_SLOPE = 0.80, 0.012
TARGET_NORM_M3_HA = 16_009  # нормативная оросительная норма, м³/га (апробация проекта)
RAIN_EFF = 0.70             # коэффициент использования осадков
YIELD_BASE_C_HA = 73.1      # урожайность риса в Краснодарском крае, 2025 г., ц/га (Росстат)
PRICE_RUB_KG = 25.0         # цена риса-сырца, руб/кг (вилка 22–30)
KRAI_AREA_HA = 112_300      # площадь сева риса в крае, 2025 г., га (Национальный рисовый союз)
KRAI_GROSS_T = 829_700      # валовой сбор риса-сырца в крае, 2025 г., т (Росстат)

MM_TO_M3_HA = 10.0


@dataclass
class Check:
    id: int
    area_ha: float
    elev_m: float
    dz_m: float
    kpd: float
    centroid: list[float]
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
    days: list[Day]
    decades: list[list[int]] = field(default_factory=list)


def load_weather(year: int) -> list[dict]:
    path = DATA / "weather" / f"era5_daily_{year}.csv"
    if not path.exists():
        raise FileNotFoundError(f"нет файла метеоряда {path}")
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_season(rows: list[dict], lat: float, elev: float, start_md: str = SEASON_START, days: int = SEASON_DAYS) -> Season:
    month, day = (int(x) for x in start_md.split("-"))
    start_idx = next(
        (i for i, r in enumerate(rows) if int(r["date"][5:7]) == month and int(r["date"][8:10]) >= day),
        0,
    )
    window = rows[start_idx : start_idx + days]
    out: list[Day] = []
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


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def phase_of(day_index: int) -> int:
    pos = 0
    for i, (length, _kc, _w) in enumerate(PHASES):
        pos += length
        if day_index < pos:
            return i
    return len(PHASES) - 1


def load_checks() -> tuple[list[Check], dict]:
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


# --------------------------------------------------------------------------------------
# Водный баланс
# --------------------------------------------------------------------------------------

def kpd_of(dz: float) -> float:
    return max(0.55, min(0.92, KPD_BASE - KPD_SLOPE * dz))


def season_demands(checks: list[Check], season: Season, perc_base: float, intermittent: bool = False) -> dict:
    """Подекадные потребности: ETm и нетто-фильтрация (уже за вычетом эффективных осадков)."""
    factor = 0.60 if intermittent else 1.0  # прерывистое затопление снижает фильтрацию ~на 40 %
    etm: list[list[float]] = []   # [check][decade], мм
    perc: list[list[float]] = []  # [check][decade], мм (нетто после осадков)
    for c in checks:
        row_etm, row_perc = [], []
        for dec in season.decades:
            et_sum = 0.0
            for i in dec:
                kc = PHASES[phase_of(i)][1]
                et_sum += kc * season.days[i].et0
            days = len(dec)
            rain = RAIN_EFF * sum(season.days[i].precip for i in dec)
            f_mm = (perc_base + KF * c.dz_m) * days * factor
            row_etm.append(et_sum)
            row_perc.append(max(0.0, f_mm - rain))
        etm.append(row_etm)
        perc.append(row_perc)
    return {"etm": etm, "perc": perc}


def gross_norm_mm(check: Check, etm: list[float], perc: list[float]) -> float:
    return sum(etm) + sum(perc) + INITIAL_MM


def calibrate_percolation(checks: list[Check], season: Season, target: float = TARGET_NORM_M3_HA) -> float:
    """Подбор базовой фильтрации так, чтобы средняя норма массива сошлась с нормативной."""
    lo, hi = 0.0, 8.0
    for _ in range(40):
        mid = (lo + hi) / 2
        d = season_demands(checks, season, mid)
        norms = [
            gross_norm_mm(c, d["etm"][i], d["perc"][i]) * MM_TO_M3_HA for i, c in enumerate(checks)
        ]
        mean_norm = sum(norms) / len(norms)
        if mean_norm < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------------------
# Отклик урожая и оптимизация
# --------------------------------------------------------------------------------------

def yield_fraction(s2: list[list[float]], etm: list[list[float]], ky_global: float) -> list[float]:
    """Доля урожая по FAO-33: 1 − Σ Ky_p (1 − ETa/ETm)."""
    out = []
    for i in range(len(etm)):
        loss = 0.0
        for p in range(len(etm[i])):
            ky = ky_global * PHASES_KY_W[p]
            if etm[i][p] > 0:
                loss += ky * max(0.0, 1.0 - s2[i][p] / etm[i][p])
        out.append(max(0.0, 1.0 - loss))
    return out


PHASES_KY_W = [w for (_l, _kc, w) in PHASES]


def solve_lp(
    checks: list[Check],
    etm: list[list[float]],
    perc: list[list[float]],
    deficit: float,
    ky_global: float,
    capacity_l_s_ha: float = 2.5,
) -> dict:
    """Задача ЛП: максимум валового сбора при лимите водозабора (1 − deficit)·нормы."""
    n, p_dec = len(checks), len(etm[0])
    demand_full = [
        sum(etm[i][p] + perc[i][p] for p in range(p_dec)) / checks[i].kpd + INITIAL_MM / checks[i].kpd
        for i in range(n)
    ]
    budget = (1.0 - deficit) * sum(demand_full[i] * checks[i].area_ha for i in range(n))

    prob = pulp.LpProblem("akvachek", pulp.LpMaximize)
    s1 = {(i, p): pulp.LpVariable(f"s1_{i}_{p}", lowBound=0, upBound=max(perc[i][p], 1e-6)) for i in range(n) for p in range(p_dec)}
    s2 = {(i, p): pulp.LpVariable(f"s2_{i}_{p}", lowBound=0, upBound=max(etm[i][p], 1e-6)) for i in range(n) for p in range(p_dec)}

    # целевая функция: валовой сбор, ц
    prob += pulp.lpSum(
        checks[i].area_ha * YIELD_BASE_C_HA * (1.0 - pulp.lpSum(
            ky_global * PHASES_KY_W[phase_of_dec(p, season_len=SEASON_DAYS)] * (1.0 - s2[(i, p)] / max(etm[i][p], 1e-6))
            for p in range(p_dec)
        ))
        for i in range(n)
    )

    # лимит водозабора на сезон
    prob += (
        pulp.lpSum(
            checks[i].area_ha * (s1[(i, p)] + s2[(i, p)]) / checks[i].kpd
            for i in range(n)
            for p in range(p_dec)
        )
        + sum(checks[i].area_ha * INITIAL_MM / checks[i].kpd for i in range(n))
        <= budget
    )

    # пропускная способность водовыпуска (гидромодуль), мм за декаду
    for i in range(n):
        for p, dec in enumerate(decade_lengths(SEASON_DAYS)):
            cap_mm = capacity_l_s_ha * 8.64 * dec
            prob += (s1[(i, p)] + s2[(i, p)]) / checks[i].kpd <= cap_mm

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    status_name = pulp.LpStatus[status]

    if status_name != "Optimal":
        return {"status": status_name, "ok": False}

    s1v = [[s1[(i, p)].value() or 0.0 for p in range(p_dec)] for i in range(n)]
    s2v = [[s2[(i, p)].value() or 0.0 for p in range(p_dec)] for i in range(n)]
    gross = [[(s1v[i][p] + s2v[i][p]) / checks[i].kpd for p in range(p_dec)] for i in range(n)]
    water_used = sum(
        checks[i].area_ha * (sum(gross[i]) + INITIAL_MM / checks[i].kpd) for i in range(n)
    )
    return {
        "status": status_name,
        "ok": True,
        "s1": s1v,
        "s2": s2v,
        "gross_mm": gross,
        "water_m3": water_used,
        "budget_m3": budget,
        "yield_tons": sum(
            checks[i].area_ha * YIELD_BASE_C_HA / 100.0 * yield_fraction([s2v[i]], [etm[i]], ky_global)[0]
            for i in range(n)
        ),
        "per_check": [
            {
                "id": checks[i].id,
                "gross_m3": round(checks[i].area_ha * (sum(gross[i]) + INITIAL_MM / checks[i].kpd) * MM_TO_M3_HA, 0),
                "deficit_pct": round(
                    100.0 * (1.0 - (sum(s2v[i]) / max(sum(etm[i]), 1e-6))), 2
                ),
                "yield_frac": round(yield_fraction([s2v[i]], [etm[i]], ky_global)[0], 4),
            }
            for i in range(n)
        ],
    }


def decade_lengths(total: int) -> list[int]:
    return [len(r) for r in [list(range(i, min(i + 10, total))) for i in range(0, total, 10)]]


def phase_of_dec(dec_index: int, season_len: int) -> int:
    start = dec_index * 10
    return phase_of(min(start + 5, season_len - 1))


def baseline_uniform(
    checks: list[Check], etm: list[list[float]], perc: list[list[float]], deficit: float, ky_global: float
) -> dict:
    """Нормативно-уравнительное распределение: всем чекам — (1 − deficit) от нормы."""
    n, p_dec = len(checks), len(etm[0])
    s2 = [[0.0] * p_dec for _ in range(n)]
    water = 0.0
    for i in range(n):
        for p in range(p_dec):
            netto = (1.0 - deficit) * (perc[i][p] + etm[i][p])
            covered = min(perc[i][p], netto)
            s2[i][p] = max(0.0, netto - covered)
            water += checks[i].area_ha * (netto / checks[i].kpd)
        water += checks[i].area_ha * INITIAL_MM / checks[i].kpd
    fracs = yield_fraction(s2, etm, ky_global)
    return {
        "s2": s2,
        "water_m3": water,
        "yield_tons": sum(checks[i].area_ha * YIELD_BASE_C_HA / 100.0 * fracs[i] for i in range(n)),
        "per_check": [
            {"id": checks[i].id, "deficit_pct": round(deficit * 100, 2), "yield_frac": round(fracs[i], 4)}
            for i in range(n)
        ],
    }


def baseline_fallowing(
    checks: list[Check], etm: list[list[float]], perc: list[list[float]], deficit: float, ky_global: float
) -> dict:
    """Реальная практика маловодья: вывод чеков с высокими отметками из севооборота."""
    n, p_dec = len(checks), len(etm[0])
    demand_full = [
        (sum(etm[i]) + sum(perc[i]) + INITIAL_MM) / checks[i].kpd for i in range(n)
    ]
    budget = (1.0 - deficit) * sum(demand_full[i] * checks[i].area_ha for i in range(n))
    order = sorted(range(n), key=lambda i: -checks[i].dz_m)
    active = set(range(n))
    used = sum(demand_full[i] * checks[i].area_ha for i in active)
    for i in order:
        if used <= budget:
            break
        active.discard(i)
        used -= demand_full[i] * checks[i].area_ha
    scale = 1.0 if used <= budget else budget / used
    s2 = [[0.0] * p_dec for _ in range(n)]
    water = 0.0
    for i in range(n):
        if i not in active:
            continue
        for p in range(p_dec):
            netto = scale * (perc[i][p] + etm[i][p])
            covered = min(perc[i][p], netto)
            s2[i][p] = max(0.0, netto - covered)
            water += checks[i].area_ha * (netto / checks[i].kpd)
        water += checks[i].area_ha * INITIAL_MM / checks[i].kpd
    fracs = yield_fraction(s2, etm, ky_global)
    return {
        "s2": s2,
        "water_m3": water,
        "yield_tons": sum(checks[i].area_ha * YIELD_BASE_C_HA / 100.0 * fracs[i] for i in range(n)),
        "fallowed": [checks[i].id for i in range(n) if i not in active],
        "per_check": [
            {
                "id": checks[i].id,
                "active": i in active,
                "deficit_pct": 0.0 if i not in active else round((1 - scale) * 100, 2),
                "yield_frac": round(fracs[i], 4),
            }
            for i in range(n)
        ],
    }


def evaluate_decision(
    checks: list[Check], gross_mm: list[list[float]], etm_true: list[list[float]],
    perc_true: list[list[float]], ky_global: float
) -> float:
    """Оценка принятого решения в ИСТИННОЙ модели: сколько урожая реально получится."""
    n, p_dec = len(checks), len(etm_true[0])
    s2 = [[0.0] * p_dec for _ in range(n)]
    for i in range(n):
        for p in range(p_dec):
            netto = gross_mm[i][p] * checks[i].kpd
            covered = min(perc_true[i][p], netto)
            s2[i][p] = max(0.0, netto - covered)
    fracs = yield_fraction(s2, etm_true, ky_global)
    return sum(checks[i].area_ha * YIELD_BASE_C_HA / 100.0 * fracs[i] for i in range(n))


def monte_carlo(
    checks: list[Check], season: Season, perc_base: float, deficit: float, ky_global: float,
    sigma_m: float, runs: int = 200, seed: int = 20261030
) -> dict:
    """Стресс-тест: решение принимается по зашумлённым отметкам, оценивается в истинной модели.

    Это корректная проверка устойчивости: шум портит ВХОД решению, а результат считается
    по настоящему рельефу. Именно так и бывает в поле: рельеф знаем с ошибкой.
    """
    rng = random.Random(seed)
    etm_true = season_demands(checks, season, perc_base)
    etm_t, perc_t = etm_true["etm"], etm_true["perc"]

    base_uniform = baseline_uniform(checks, etm_t, perc_t, deficit, ky_global)["yield_tons"]

    gains: list[float] = []
    for _ in range(runs):
        noisy = []
        for c in checks:
            dz = c.dz_m + rng.gauss(0.0, sigma_m)
            noisy.append(Check(c.id, c.area_ha, c.elev_m, dz, kpd_of(dz), c.centroid))
        d_noisy = season_demands(noisy, season, perc_base)
        res = solve_lp(noisy, d_noisy["etm"], d_noisy["perc"], deficit, ky_global)
        if not res["ok"]:
            continue
        truth = evaluate_decision(checks, res["gross_mm"], etm_t, perc_t, ky_global)
        gains.append(100.0 * (truth / base_uniform - 1.0))

    if not gains:
        return {"runs": 0}
    gains.sort()
    n = len(gains)
    return {
        "runs": n,
        "sigma_m": sigma_m,
        "mean": sum(gains) / n,
        "p05": gains[int(0.05 * n)],
        "p50": gains[n // 2],
        "p95": gains[min(int(0.95 * n), n - 1)],
        "min": gains[0],
        "max": gains[-1],
        "histogram": _histogram(gains, 24),
    }


def _histogram(values: list[float], bins: int) -> list[dict]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [{"x": lo, "count": len(values)}]
    step = (hi - lo) / bins
    out = [{"x": round(lo + step * (i + 0.5), 3), "count": 0} for i in range(bins)]
    for v in values:
        idx = min(int((v - lo) / step), bins - 1)
        out[idx]["count"] += 1
    return out


def economics(gain_pct: float, area_ha: float, price: float = PRICE_RUB_KG) -> dict:
    """Экономика: честно, с разделением масштабов (1 га / пилот / край)."""
    per_ha_kg = YIELD_BASE_C_HA * 10.0 * gain_pct / 100.0   # ц/га -> кг/га
    per_ha_rub = per_ha_kg * price
    krai_tons = KRAI_GROSS_T * gain_pct / 100.0
    krai_rub = krai_tons * 1000.0 * price
    pilot_ha = 500.0
    return {
        "gain_pct": gain_pct,
        "per_ha_kg": round(per_ha_kg, 2),
        "per_ha_rub": round(per_ha_rub, 0),
        "array_rub": round(per_ha_rub * area_ha, 0),
        "pilot_rub": round(per_ha_rub * pilot_ha, 0),
        "pilot_ha": pilot_ha,
        "krai_tons": round(krai_tons, 0),
        "krai_rub": round(krai_rub, 0),
        "price": price,
        "note": "Эффект возникает только в годы лимитирования водоподачи (10–20 % дефицита)",
    }
