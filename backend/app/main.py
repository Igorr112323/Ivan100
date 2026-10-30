"""API «АКВАЧЕК». Отдаёт реальные данные и считает оптимизацию водоподачи на лету."""
from __future__ import annotations

import json
import threading
import time
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import engine

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DIST = ROOT / "web" / "dist"

app = FastAPI(title="АКВАЧЕК", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

CACHE: dict[str, object] = {}
CACHE_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- контекст

BASE_PERC: dict[str, float] = {}


def base_percolation(checks: list, meta: dict, mean_elev: float) -> tuple[float, int]:
    """Фильтрация калибруется один раз — по последнему сезону с данными.

    Почва не меняется от года к году, поэтому подгонять её под каждый сезон нельзя:
    тогда норма была бы одинаковой при любой погоде. Калибруем по базовому году,
    а в остальные годы норма меняется вместе с испарением — это честнее.
    """
    base_year = max(years_available())
    key = f"{len(checks)}|{round(mean_elev, 3)}|{base_year}"
    if key not in BASE_PERC:
        lat = float((meta.get("aoi") or {}).get("lat", 45.50))
        rows = engine.load_weather(base_year)
        season = engine.build_season(rows, lat=lat, elev=mean_elev)
        BASE_PERC[key] = engine.calibrate_percolation(checks, season)
    return BASE_PERC[key], base_year


@lru_cache(maxsize=8)
def context(year: int):
    checks, meta = engine.load_checks()
    rows = engine.load_weather(year)
    mean_elev = sum(c.elev_m for c in checks) / max(len(checks), 1)
    lat = float((meta.get("aoi") or {}).get("lat", 45.50))
    season = engine.build_season(rows, lat=lat, elev=mean_elev)
    perc_base, base_year = base_percolation(checks, meta, mean_elev)
    return checks, meta, season, perc_base, rows


def years_available() -> list[int]:
    return sorted(int(p.stem.split("_")[-1]) for p in (DATA / "weather").glob("era5_daily_*.csv"))


def cached(key: str, producer):
    with CACHE_LOCK:
        if key in CACHE:
            return CACHE[key]
    value = producer()
    with CACHE_LOCK:
        CACHE[key] = value
    return value


# ----------------------------------------------------------------------------- метаданные

@app.get("/api/meta")
def api_meta():
    years = years_available()
    if not years:
        raise HTTPException(503, "данные ещё не загружены пайплайном")
    checks, meta, season, perc_base, rows = context(years[-1])
    scenes_path = DATA / "sentinel" / "scenes.json"
    scenes = json.loads(scenes_path.read_text(encoding="utf-8")) if scenes_path.exists() else {}
    rgb_path = DATA / "sentinel" / "array_rgb.json"
    rgb = json.loads(rgb_path.read_text(encoding="utf-8")) if rgb_path.exists() else None
    wmanifest = DATA / "weather" / "manifest.json"
    wmeta = json.loads(wmanifest.read_text(encoding="utf-8")) if wmanifest.exists() else {}

    demands = engine.season_demands(checks, season, perc_base)
    norms = [engine.gross_norm(demands["etm"][i], demands["perc"][i], checks[i].kpd)
             for i in range(len(checks))]
    return {
        "metadata": {k: v for k, v in meta.items() if k != "aoi"},
        "project": {
            "name": "АКВАЧЕК",
            "full": "Система спутникового мониторинга и оптимизации водоподачи "
                    "на рисовых оросительных системах Краснодарского края",
            "polygon": (meta.get("aoi") or {}).get("name", "Рисовый массив, Краснодарский край"),
        },
        "years": years,
        "checks": {
            "count": len(checks),
            "total_ha": round(sum(c.area_ha for c in checks), 1),
            "min_ha": round(min(c.area_ha for c in checks), 2),
            "max_ha": round(max(c.area_ha for c in checks), 2),
            "mean_ha": round(sum(c.area_ha for c in checks) / len(checks), 2),
            "mean_elev_m": round(sum(c.elev_m for c in checks) / len(checks), 2),
            "elev_span_m": round(max(c.elev_m for c in checks) - min(c.elev_m for c in checks), 2),
            "digitised": meta,
        },
        "model": {
            "percolation_base_mm_day": round(perc_base, 3),
            "kf": engine.KF,
            "kpd_base": engine.KPD_BASE,
            "kpd_slope": engine.KPD_SLOPE,
            "initial_mm": engine.INITIAL_MM,
            "target_norm_m3_ha": engine.TARGET_NORM_M3_HA,
            "calibrated_norm_m3_ha": round(sum(norms) / len(norms), 0),
            "season_days": len(season.days),
            "season_start": season.days[0].date,
            "season_end": season.days[-1].date,
            "phases": [
                {"days": d, "kc": kc, "weight": w} for (d, kc, w) in engine.PHASES
            ],
        },
        "imagery": rgb,
        "scenes": scenes,
        "weather_manifest": wmeta,
    }


@app.get("/api/checks")
def api_checks():
    path = DATA / "checks.geojson"
    if not path.exists():
        raise HTTPException(503, "контуры чеков ещё не построены")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/series")
def api_series(limit: int = Query(24, ge=1, le=400)):
    path = DATA / "checks_series.json"
    if not path.exists():
        raise HTTPException(503, "спутниковый ряд ещё не построен")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["checks"] = payload["checks"][:limit]
    return payload


@app.get("/api/weather")
def api_weather(year: int = 2025):
    years = years_available()
    if year not in years:
        year = years[-1]
    checks, _meta, season, _p, _rows = context(year)
    return {
        "year": year,
        "days": [
            {"date": d.date, "et0": round(d.et0, 3), "et0_ref": round(d.et0_ref, 3), "precip": d.precip}
            for d in season.days
        ],
    }


@app.get("/api/validation")
def api_validation(year: int = 2025):
    """Независимая сверка: наш FAO-56 Penman–Monteith против эталонной ET0 из ERA5."""
    years = years_available()
    if year not in years:
        year = years[-1]
    checks, _m, season, _p, _r = context(year)
    ours = [d.et0 for d in season.days]
    ref = [d.et0_ref for d in season.days]
    pairs = [(a, b) for a, b in zip(ours, ref) if b and b > 0]
    if not pairs:
        raise HTTPException(503, "эталонный ряд ET0 недоступен")
    diffs = [a - b for a, b in pairs]
    rmse = (sum(d * d for d in diffs) / len(diffs)) ** 0.5
    mbe = sum(diffs) / len(diffs)
    return {
        "year": year,
        "days": len(pairs),
        "ours_mm": round(sum(ours), 1),
        "reference_mm": round(sum(b for _a, b in pairs), 1),
        "difference_pct": round(100.0 * (sum(ours) / sum(b for _a, b in pairs) - 1.0), 2),
        "rmse_mm_day": round(rmse, 3),
        "bias_mm_day": round(mbe, 3),
        "series": [
            {"date": d.date, "ours": round(d.et0, 3), "ref": round(d.et0_ref, 3)}
            for d in season.days
            if d.et0_ref > 0
        ],
    }


# ----------------------------------------------------------------------------- оптимизация

@app.get("/api/optimize")
def api_optimize(
    year: int = 2025,
    deficit: float = Query(0.15, ge=0.0, le=0.40),
    ky: float = Query(1.1, ge=0.6, le=1.6),
    intermittent: int = 0,
):
    years = years_available()
    if year not in years:
        year = years[-1]
    key = f"opt:{year}:{deficit:.3f}:{ky:.3f}:{intermittent}"
    return cached(key, lambda: _optimize(year, deficit, ky, bool(intermittent)))


def _optimize(year: int, deficit: float, ky: float, intermittent: bool):
    t0 = time.time()
    checks, _m, season, perc_base, _r = context(year)
    demands = engine.season_demands(checks, season, perc_base, intermittent=intermittent)
    etm, perc = demands["etm"], demands["perc"]

    lp = engine.solve_lp(checks, etm, perc, deficit, ky)
    if not lp["ok"]:
        raise HTTPException(500, f"решатель: {lp['status']}")
    uniform = engine.baseline_uniform(checks, etm, perc, deficit, ky)
    fallow = engine.baseline_fallowing(checks, etm, perc, deficit, ky)
    full = engine.baseline_uniform(checks, etm, perc, 0.0, ky)

    def pct(a: float, b: float) -> float:
        return round(100.0 * (a / b - 1.0), 3) if b else 0.0

    water_saved = uniform["water_m3"] - lp["water_m3"]
    return {
        "params": {"year": year, "deficit": deficit, "ky": ky, "intermittent": intermittent},
        "timing_s": round(time.time() - t0, 2),
        "solver": lp["status"],
        "areas_ha": round(sum(c.area_ha for c in checks), 1),
        "yield_tons": {
            "full": round(full["yield_tons"], 1),
            "uniform": round(uniform["yield_tons"], 1),
            "fallowing": round(fallow["yield_tons"], 1),
            "optimal": round(lp["yield_tons"], 1),
        },
        "gain_pct": {
            "vs_uniform": pct(lp["yield_tons"], uniform["yield_tons"]),
            "vs_fallowing": pct(lp["yield_tons"], fallow["yield_tons"]),
            "uniform_vs_full": pct(uniform["yield_tons"], full["yield_tons"]),
        },
        "water_m3": {
            "full": round(full["water_m3"], 0),
            "uniform": round(uniform["water_m3"], 0),
            "optimal": round(lp["water_m3"], 0),
            "limit": round(lp["budget_m3"], 0),
            "limit_shortage": round(full["water_m3"] - lp["budget_m3"], 0),
        },
        "water_saved_pct": round(100.0 * water_saved / uniform["water_m3"], 3) if uniform["water_m3"] else 0.0,
        "fallowed_checks": fallow.get("fallowed", []),
        "per_check": lp["per_check"],
        "vedomost": _vedomost(checks, season, lp, etm, perc),
        "economics": engine.economics(
            pct(lp["yield_tons"], uniform["yield_tons"]), sum(c.area_ha for c in checks)
        ),
        "economics_fallowing": engine.economics(
            pct(lp["yield_tons"], fallow["yield_tons"]), sum(c.area_ha for c in checks)
        ),
        "norms_m3_ha": {
            "mean": round(
                sum(engine.gross_norm(etm[i], perc[i], checks[i].kpd) for i in range(len(checks)))
                / len(checks), 0
            ),
            "min": round(min(engine.gross_norm(etm[i], perc[i], checks[i].kpd)
                             for i in range(len(checks))), 0),
            "max": round(max(engine.gross_norm(etm[i], perc[i], checks[i].kpd)
                             for i in range(len(checks))), 0),
        },
        "fallowed_by_optimizer": lp.get("fallowed", []),
    }


def _vedomost(checks, season, lp, etm, perc) -> list[dict]:
    """Ведомость водоподачи — тот самый выходной документ для диспетчера."""
    rows = []
    for i, c in enumerate(checks):
        gross = lp["gross_mm"][i]
        active = lp["per_check"][i].get("active", True)
        total_mm = sum(gross) + (engine.INITIAL_MM / c.kpd if active else 0.0)
        full_mm = (sum(etm[i]) + sum(perc[i]) + engine.INITIAL_MM) / c.kpd  # брутто, мм
        rows.append(
            {
                "id": c.id,
                "area_ha": round(c.area_ha, 2),
                "elev_m": round(c.elev_m, 2),
                "kpd": round(c.kpd, 3),
                "season_m3": round(c.area_ha * total_mm * engine.MM_TO_M3_HA, 0),
                "norm_full_m3": round(c.area_ha * full_mm * engine.MM_TO_M3_HA, 0),
                "cut_pct": round(100.0 * (1.0 - total_mm / full_mm), 1) if full_mm else 0.0,
                "active": active,
                "yield_frac": lp["per_check"][i]["yield_frac"],
                "decades": [
                    {
                        "decade": p + 1,
                        "start": season.days[season.decades[p][0]].date,
                        "m3": round(c.area_ha * gross[p] * engine.MM_TO_M3_HA, 0),
                        "l_s": round(
                            c.area_ha * gross[p] * engine.MM_TO_M3_HA / (len(season.decades[p]) * 86.4), 1
                        ),
                    }
                    for p in range(len(gross))
                ],
            }
        )
    rows.sort(key=lambda r: r["cut_pct"])
    return rows


@app.get("/api/scenarios")
def api_scenarios(year: int = 2025, intermittent: int = 0):
    key = f"scn:{year}:{intermittent}"
    return cached(key, lambda: _scenarios(year, bool(intermittent)))


def _scenarios(year: int, intermittent: bool):
    rows = []
    for deficit in (0.10, 0.15, 0.20):
        for ky in (1.0, 1.1, 1.3):
            res = _optimize(year, deficit, ky, intermittent)
            rows.append(
                {
                    "deficit": deficit,
                    "ky": ky,
                    "gain_vs_uniform": res["gain_pct"]["vs_uniform"],
                    "gain_vs_fallowing": res["gain_pct"]["vs_fallowing"],
                    "uniform_loss": res["gain_pct"]["uniform_vs_full"],
                    "optimal_t": res["yield_tons"]["optimal"],
                    "uniform_t": res["yield_tons"]["uniform"],
                    "fallowing_t": res["yield_tons"]["fallowing"],
                    "solver": res["solver"],
                }
            )
    return {"rows": rows, "year": year, "intermittent": intermittent}


@app.get("/api/montecarlo")
def api_montecarlo(
    year: int = 2025,
    deficit: float = Query(0.15, ge=0.0, le=0.40),
    ky: float = Query(1.1, ge=0.6, le=1.6),
    sigma: float = Query(0.5, ge=0.05, le=6.0),
    runs: int = Query(30, ge=10, le=300),
):
    key = f"mc:{year}:{deficit:.3f}:{ky:.3f}:{sigma:.3f}:{runs}"
    return cached(key, lambda: _montecarlo(year, deficit, ky, sigma, runs))


def _montecarlo(year, deficit, ky, sigma, runs):
    checks, _m, season, perc_base, _r = context(year)
    result = engine.monte_carlo(checks, season, perc_base, deficit, ky, sigma, runs)
    result["note"] = (
        "Решение принимается по зашумлённым отметкам рельефа, результат оценивается "
        "в истинной модели. Проверка именно такая, как в поле: рельеф известен с ошибкой."
    )
    return result


@app.get("/api/economics")
def api_economics(gain_pct: float = Query(1.29, ge=0.0, le=20.0), price: float = Query(25.0, ge=5.0, le=80.0)):
    years = years_available()
    checks, _m, _s, _p, _r = context(years[-1])
    return engine.economics(gain_pct, sum(c.area_ha for c in checks), price)


# ----------------------------------------------------------------------------- статика

if DATA.exists():
    app.mount("/data", StaticFiles(directory=str(DATA)), name="data")

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = DIST / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")


# ----------------------------------------------------------------------------- прогрев

def warm() -> None:
    try:
        years = years_available()
        if not years:
            return
        year = years[-1]
        api_meta()
        _optimize(year, 0.15, 1.1, False)
        _scenarios(year, False)
        for deficit in (0.10, 0.20, 0.30):
            _optimize(year, deficit, 1.1, False)
        for sigma in (0.25, 0.5, 1.0, 3.0):
            _montecarlo(year, 0.15, 1.1, sigma, 30)
        print("[warm] кэш прогрет", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[warm] пропущено: {exc!r}", flush=True)


threading.Thread(target=warm, daemon=True).start()
