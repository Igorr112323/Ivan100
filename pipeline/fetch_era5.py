"""Загрузка реальных метеорологических данных реанализа ERA5 (ECMWF).

Источник: Historical Weather API (Open-Meteo) — ряд построен на реанализе ERA5 (ECMWF).
Запрашиваются почасовые значения и агрегируются в суточные по местному времени (Europe/Moscow),
чтобы расчёт по FAO-56 Penman–Monteith выполнялся на честных суточных средних, а не на суррогатах.

Выход: data/weather/era5_daily_<год>.csv
  date,tmin,tmax,tmean,rh_mean,td_mean,u2,rs,precip,et0_ref,pressure
  rs       — суммарная коротковолновая радиация, МДж/м²·сут
  u2       — скорость ветра, приведённая к высоте 2 м, м/с
  et0_ref  — эталонная ET0 FAO-56 из источника (ERA5), мм/сут — независимая сверка нашего расчёта
"""
from __future__ import annotations

import argparse
import csv
import time
from collections import defaultdict
from pathlib import Path

import requests

from common import AOI, SEASON_END_MD, SEASON_START_MD, WEATHER, die, log

API = "https://archive-api.open-meteo.com/v1/archive"

HOURLY = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "precipitation",
    "et0_fao_evapotranspiration",
    "surface_pressure",
]

HEADER = ["date", "tmin", "tmax", "tmean", "rh_mean", "td_mean", "u2", "rs", "precip", "et0_ref", "pressure"]


def fetch_window(start: str, end: str, retries: int = 3) -> dict:
    params = {
        "latitude": AOI["lat"],
        "longitude": AOI["lon"],
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(HOURLY),
        "timezone": "Europe/Moscow",
        "wind_speed_unit": "ms",
    }
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(API, params=params, timeout=90)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            last_err = repr(exc)
        sleep = 10 * (attempt + 1)
        log(f"повтор через {sleep} с ({last_err})")
        time.sleep(sleep)
    die(f"источник ERA5 не отвечает для {start}..{end}: {last_err}")
    return {}


def aggregate(payload: dict) -> list[dict]:
    h = payload["hourly"]
    times = h["time"]
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(times):
        if t:
            by_day[t[:10]].append(i)

    rows: list[dict] = []
    for date in sorted(by_day):
        idx = by_day[date]

        def _vals(key: str) -> list[float]:
            series = h.get(key) or []
            return [float(series[i]) for i in idx if i < len(series) and series[i] is not None]

        t = _vals("temperature_2m")
        if not t:
            continue
        rh = _vals("relative_humidity_2m")
        td = _vals("dew_point_2m")
        wind = _vals("wind_speed_10m")
        rad = _vals("shortwave_radiation")
        pr = _vals("precipitation")
        et0 = _vals("et0_fao_evapotranspiration")
        ps = _vals("surface_pressure")

        def _mean(v: list[float]) -> float | None:
            return round(sum(v) / len(v), 3) if v else None

        u10 = _mean(wind)
        # FAO-56, уравнение 47: u2 = uz * 4.87 / ln(67.8 * z - 5.42); для z = 10 м множитель 0,748
        u2 = round(u10 * 0.748, 3) if u10 is not None else None
        rows.append(
            {
                "date": date,
                "tmin": round(min(t), 2),
                "tmax": round(max(t), 2),
                "tmean": _mean(t),
                "rh_mean": _mean(rh),
                "td_mean": _mean(td),
                "u2": u2,
                # Вт/м² за час -> МДж/м² за сутки
                "rs": round(sum(rad) * 3600.0 / 1e6, 4) if rad else None,
                "precip": round(sum(pr), 2) if pr else None,
                "et0_ref": round(sum(et0), 3) if et0 else None,
                "pressure": _mean(ps),
            }
        )
    return rows


def year_rows(year: int) -> list[dict]:
    """Сезон запрашиваем одним обращением: так быстрее и меньше нагрузка на источник."""
    start = f"{year}-{SEASON_START_MD}"
    last_day = {"05": 31, "06": 30, "07": 31, "08": 31, "09": 30}[SEASON_END_MD[:2]]
    end = f"{year}-{SEASON_END_MD[:2]}-{last_day:02d}"
    log(f"ERA5 {start} .. {end}")
    rows = aggregate(fetch_window(start, end))
    if not rows:
        die(f"ERA5: пустой ответ за {year} год")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2015,2016,2017,2018,2019,2020,2021,2022,2023,2024,2025")
    args = ap.parse_args()

    manifest = {
        "source": "ERA5 (ECMWF) через Historical Weather API, агрегация почасовых значений в суточные",
        "point": {"lat": AOI["lat"], "lon": AOI["lon"], "name": AOI["name"]},
        "timezone": "Europe/Moscow",
        "variables": HEADER,
        "years": [],
    }

    for year_s in args.years.split(","):
        year = int(year_s.strip())
        rows = year_rows(year)
        path = WEATHER / f"era5_daily_{year}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=HEADER)
            w.writeheader()
            w.writerows(rows)
        log(f"ERA5 {year}: {len(rows)} суток -> {path}")
        manifest["years"].append(
            {
                "year": year,
                "days": len(rows),
                "et0_sum_mm": round(sum(r["et0_ref"] or 0 for r in rows), 1),
                "precip_sum_mm": round(sum(r["precip"] or 0 for r in rows), 1),
                "tmean_c": round(sum(r["tmean"] for r in rows) / len(rows), 2),
            }
        )

    from common import write_json

    write_json(WEATHER / "manifest.json", manifest)


if __name__ == "__main__":
    main()
