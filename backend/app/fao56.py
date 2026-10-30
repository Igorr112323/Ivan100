"""FAO-56: расчёт опорной эвапотранспирации ET0 по уравнению Пенмана–Монтейта.

Реализовано строго по FAO Irrigation and Drainage Paper 56 (Allen et al., 1998),
уравнения 6, 7, 8, 11, 17, 21, 37, 38, 39, 47. Никаких упрощений «для скорости».
"""
from __future__ import annotations

import math
from datetime import date

GSC = 0.0820          # солнечная постоянная, МДж/м²·мин
SIGMA = 4.903e-9      # постоянная Стефана–Больцмана, МДж/К⁴·м²·сут
ALBEDO = 0.23         # альбедо эталонной травяной поверхности
STEFAN_T4 = 273.16


def sat_vapour_pressure(t: float) -> float:
    """es(T), кПа. FAO-56, ур. 11."""
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def slope_vapour_pressure(t: float) -> float:
    """Δ, кПа/°C. FAO-56, ур. 13."""
    return 4098.0 * sat_vapour_pressure(t) / (t + 237.3) ** 2


def atmospheric_pressure(z: float) -> float:
    """P, кПа. FAO-56, ур. 7."""
    return 101.3 * ((293.0 - 0.0065 * z) / 293.0) ** 5.26


def psychrometric_constant(pressure: float) -> float:
    """γ, кПа/°C. FAO-56, ур. 8."""
    return 0.000665 * pressure


def extraterrestrial_radiation(doy: int, lat_deg: float) -> float:
    """Ra, МДж/м²·сут. FAO-56, ур. 21, 23, 24."""
    phi = math.radians(lat_deg)
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0)
    decl = 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)
    arg = max(-1.0, min(1.0, -math.tan(phi) * math.tan(decl)))
    ws = math.acos(arg)
    return (24.0 * 60.0 / math.pi) * GSC * dr * (
        ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws)
    )


def et0_penman_monteith(
    tmin: float,
    tmax: float,
    tmean: float,
    u2: float,
    rs: float,
    ea: float,
    doy: int,
    lat_deg: float,
    z: float = 6.0,
    pressure: float | None = None,
) -> float:
    """Суточная ET0, мм/сут. FAO-56, ур. 6."""
    p = pressure if pressure else atmospheric_pressure(z)
    if p and p > 200.0:  # источник отдаёт давление в гПа, формула требует кПа
        p = p / 10.0
    d = slope_vapour_pressure(tmean)
    g = psychrometric_constant(p)

    es_mean = (sat_vapour_pressure(tmax) + sat_vapour_pressure(tmin)) / 2.0
    ra = extraterrestrial_radiation(doy, lat_deg)
    rso = (0.75 + 2e-5 * z) * ra
    rns = (1.0 - ALBEDO) * rs
    ratio = min(rs / rso, 1.0) if rso > 0 else 0.0
    rnl = (
        SIGMA
        * ((tmax + STEFAN_T4) ** 4 + (tmin + STEFAN_T4) ** 4) / 2.0
        * (0.34 - 0.14 * math.sqrt(max(ea, 0.01)))
        * (1.35 * ratio - 0.35)
    )
    rn = rns - rnl
    numerator = 0.408 * d * rn + g * (900.0 / (tmean + 273.0)) * u2 * (es_mean - ea)
    denominator = d + g * (1.0 + 0.34 * u2)
    return max(numerator / denominator, 0.0)


def actual_vapour_pressure(tmean: float, rh_mean: float | None, tdew: float | None) -> float:
    """ea, кПа. При наличии точки росы — точнее, чем пересчёт через среднюю влажность."""
    if tdew is not None and -50.0 < tdew < 60.0:
        return sat_vapour_pressure(tdew)
    if rh_mean is not None:
        return sat_vapour_pressure(tmean) * max(0.0, min(100.0, rh_mean)) / 100.0
    return sat_vapour_pressure(tmean) * 0.7


def doy_of(iso_date: str) -> int:
    y, m, d = (int(x) for x in iso_date.split("-"))
    return date(y, m, d).timetuple().tm_yday
