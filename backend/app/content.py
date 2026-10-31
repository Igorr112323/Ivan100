"""content.md и content.json — единственный источник чисел для интерфейса.

Правило проекта: в вёрстке нет ни одной цифры, набранной руками. Все числа
интерфейса берутся из content.json, который этот скрипт собирает из:
  * data/checks.geojson, data/checks_series.json — чеки и их спутниковые ряды;
  * data/sentinel/scenes.json — журнал снимков;
  * data/optimization/grid.json — предрасчёт сетки лимитов;
  * data/optimization/montecarlo.json — стресс-тест на ошибке отметок;
  * прямой пересчёт моделью ET0, ETm, фильтрации и норм.

Каждая запись содержит `source` — откуда число. Без источника числа не бывает.

Запуск: python backend/app/content.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import engine, main as api  # noqa: E402

DATA = ROOT / "data"
WEB = ROOT / "web" / "public" / "data"
SENTINEL = "Sentinel-2 L2A"
ERA5 = "ERA5"
FAO = "FAO-56"
MODEL = "расчёт модели"
SCENARIO = "сценарий модели"
BALANCE = "водный баланс"
ROSSTAT = "73,1 ц/га (Росстат, 2025) × 25 000 ₽/т"

BASE_LIMIT = 0.85  # базовый сценарий: лимит водоподачи 85 % полной потребности


def num(v, digits: int = 1):
    return round(float(v), digits)


def build() -> dict:
    years = api.years_available()
    year = years[-1]
    checks, meta, season, perc_base, _rows = api.context(year)
    demands = engine.season_demands(checks, season, perc_base)
    etm, perc = demands["etm"], demands["perc"]

    gj = json.loads((DATA / "checks.geojson").read_text(encoding="utf-8"))
    m = gj["metadata"]
    aoi = json.loads((DATA / "aoi.json").read_text(encoding="utf-8"))
    scenes = json.loads((DATA / "sentinel" / "scenes.json").read_text(encoding="utf-8"))["saved"]
    series = json.loads((DATA / "checks_series.json").read_text(encoding="utf-8"))
    grid = json.loads((DATA / "optimization" / "grid.json").read_text(encoding="utf-8"))
    mc_path = DATA / "optimization" / "montecarlo.json"
    mc = json.loads(mc_path.read_text(encoding="utf-8")) if mc_path.exists() else {}
    sweep_path = DATA / "optimization" / "montecarlo_sweep.json"
    if sweep_path.exists():  # в прогоне-свитке вся кривая, а не две крайние точки
        mc = json.loads(sweep_path.read_text(encoding="utf-8"))

    row = next(r for r in grid["rows"] if abs(r["limit"] - BASE_LIMIT) < 1e-9)

    # Обратная задача: сколько воды нужно модели, чтобы собрать тот же урожай,
    # что даёт практика вывода чеков. Ищем на сетке лимитов предел, при котором
    # урожай модели сравнивается с урожаем практики при базовом лимите.
    area_ha = sum(c.area_ha for c in checks)
    y_practice = row["yield_tons"]["fallowing"]
    w_practice = row["water_m3"]["fallowing"]
    inverse = {}
    desc = sorted(grid["rows"], key=lambda r: -r["limit"])
    for a, b in zip(desc, desc[1:]):
        ya, yb = a["yield_tons"]["capped"], b["yield_tons"]["capped"]
        if ya == yb or (ya - y_practice) * (yb - y_practice) > 0:
            continue
        frac = (ya - y_practice) / (ya - yb)
        inv_limit = a["limit"] + frac * (b["limit"] - a["limit"])
        inv_water = a["water_m3"]["capped"] + frac * (b["water_m3"]["capped"] - a["water_m3"]["capped"])
        inverse = {
            "limit_pct": num(inv_limit * 100, 1),
            "water_m3": num(inv_water / 1e6, 2),
            "water_per_ha": num(inv_water / area_ha, 0),
            "yield_tons": num(y_practice, 1),
            "saving_m3": num((w_practice - inv_water) / 1e6, 2),
            "saving_per_ha": num((w_practice - inv_water) / area_ha, 0),
            "saving_pct": num(100.0 * (1.0 - inv_water / w_practice), 1) if w_practice else None,
            "value": (f"урожай практики {num(y_practice, 1)} т — модель берёт "
                      f"{num(inv_water / 1e6, 2)} млн м³ при лимите {num(inv_limit * 100, 1)} %"),
            "source": MODEL,
            "note": "обратная задача: сколько воды нужно, чтобы повторить урожай практики вывода чеков",
        }
        break
    if not inverse:
        inverse = {"value": "урожай практики на сетке лимитов не достигнут", "source": MODEL}
    dates = sorted(s["date"] for s in scenes)
    n_dates = len(dates)
    incomplete = sum(1 for c in series["checks"] if len(c["series"]) < n_dates)

    et0_ours = sum(d.et0 for d in season.days)
    et0_ref = sum(d.et0_ref for d in season.days)
    etm_mm = sum(sum(r) for r in etm) / len(etm)
    perc_mm = sum(sum(r) for r in perc) / len(perc)
    norms = [engine.gross_norm(etm[i], perc[i], checks[i].kpd) for i in range(len(checks))]
    area_ha = sum(c.area_ha for c in checks)
    elevs = [c.elev_m for c in checks]

    mc_out = {}
    for s in mc.get("sigmas", []):
        mc_out[str(s["sigma_m"]).replace(".0", "")] = {
            "mean": num(s["mean"], 2), "p05": num(s["p05"], 2), "p95": num(s["p95"], 2),
            "runs": s["runs"],
        }

    # порог точности отметок: где эффект обнуляется
    sigmas = sorted(mc.get("sigmas", []), key=lambda s: s["sigma_m"])
    zero_sigma = None
    for a, b in zip(sigmas, sigmas[1:]):
        if a["mean"] > 0 >= b["mean"]:
            zero_sigma = a["sigma_m"] + (b["sigma_m"] - a["sigma_m"]) * a["mean"] / (a["mean"] - b["mean"])
            break

    content = {
        "_meta": {
            "generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            "rule": "в вёрстке нет чисел, набранных руками; источник — только этот файл",
            "base_limit": BASE_LIMIT,
        },
        "massif": {
            "district": aoi.get("district", "Красноармейский район"),
            "near": aoi.get("near", "ст. Полтавская"),
            "center_lat": aoi["lat"], "center_lon": aoi["lon"],
            "checks": len(checks), "area_ha": num(area_ha, 1),
            "value": f"{aoi.get('district')}, {aoi.get('near')}: {len(checks)} чеков, {num(area_ha,1)} га",
            "source": SENTINEL,
        },
        "image_date": {
            "value": m["scene_date"], "scene": m["scene_id"],
            "source": SENTINEL,
        },
        "control_dates": {
            "flood": m["rice_test"]["flood_date"],
            "veg": m["rice_test"]["veg_date"],
            "dry": m["rice_test"]["dry_date"],
            "value": f"{m['rice_test']['flood_date']} / {m['rice_test']['veg_date']} / {m['rice_test']['dry_date']}",
            "source": SENTINEL,
        },
        "limit_base": {
            "value": -int(round((1.0 - BASE_LIMIT) * 100)), "unit": "%",
            "source": SCENARIO,
        },
        "effect_pct": {
            "value": num(row["gain_pct"]["capped_vs_fallowing"], 2), "unit": "%",
            "base": "к практике вывода чеков из севооборота",
            "source": MODEL,
        },
        "effect_rub_ha": {
            "value": int(row["economics"]["per_ha_rub"]), "unit": "₽/га",
            "base": "к практике вывода чеков из севооборота",
            "source": ROSSTAT,
        },
        "effect_rub_array": {
            "value": num(row["economics"]["array_rub"] / 1e6, 2), "unit": "млн ₽",
            "source": MODEL,
        },
        "water_full": {
            "value": num(row["water_m3"]["full"] / 1e6, 2), "unit": "млн м³",
            "note": "полная потребность массива без лимита", "source": BALANCE,
        },
        "water_supply": {
            "value": num(row["water_m3"]["capped"] / 1e6, 2), "unit": "млн м³",
            "note": "подача при лимите 85 %", "source": BALANCE,
        },
        "water_shortage": {
            "value": num((row["water_m3"]["full"] - row["water_m3"]["capped"]) / 1e6, 2), "unit": "млн м³",
            "note": "недоподача к полной потребности — это и есть лимит", "source": BALANCE,
        },
        "inverse": inverse,
        "water_vs_practice": {
            "value": num((row["water_m3"]["capped"] - row["water_m3"]["fallowing"]) / 1e6, 2), "unit": "млн м³",
            "note": "продукт не экономит воду: он возвращает чеки в севооборот и тратит тот же лимит полнее",
            "source": BALANCE,
        },
        "water_saving": {
            "value": inverse.get("saving_m3"), "unit": "млн м³",
            "per_ha": inverse.get("saving_per_ha"), "pct": inverse.get("saving_pct"),
            "note": "при том же урожае, что у практики вывода чеков",
            "source": MODEL,
        },
        "checks_fallowed": {
            "practice": len(row["fallowed"]["fallowing"]),
            "optimal": len(row["fallowed"]["capped"]),
            "value": f"практика {len(row['fallowed']['fallowing'])}, оптимум {len(row['fallowed']['capped'])}",
            "source": MODEL,
        },
        "checks_incomplete": {
            "value": incomplete, "of": len(checks), "dates": n_dates,
            "note": "массив лежит в одном тайле MGRS, поэтому ряд полный у всех чеков",
            "source": SENTINEL,
        },
        "yield_base": {
            "uniform": num(row["yield_tons"]["uniform"], 1),
            "fallowing": num(row["yield_tons"]["fallowing"], 1),
            "optimal": num(row["yield_tons"]["capped"], 1),
            "full": num(row["yield_tons"]["full"], 1),
            "unit": "т", "source": MODEL,
        },
        "et0": {
            "value": num(et0_ours, 1), "unit": "мм", "source": FAO,
            "ref": num(et0_ref, 1), "ref_source": ERA5,
            "diff_pct": num(100.0 * (et0_ours / et0_ref - 1.0), 2) if et0_ref else None,
        },
        "etm": {"value": num(etm_mm, 1), "unit": "мм", "source": FAO},
        "percolation": {"value": num(perc_mm, 1), "unit": "мм", "source": MODEL},
        "flooding": {"value": int(engine.INITIAL_MM), "unit": "мм", "source": MODEL},
        "norm": {
            "min": int(min(norms)), "mean": int(sum(norms) / len(norms)), "max": int(max(norms)),
            "unit": "м³/га", "source": MODEL,
        },
        "kpd": {
            "min": round(min(c.kpd for c in checks), 3), "max": round(max(c.kpd for c in checks), 3),
            "source": MODEL,
        },
        "elev": {
            "min": num(min(elevs), 2), "mean": num(sum(elevs) / len(elevs), 2), "max": num(max(elevs), 2),
            "unit": "м", "source": "публичные Terrain Tiles z=13, систематическая ошибка не измерена",
        },
        "season": {"start": engine.SEASON_START, "days": engine.SEASON_DAYS, "source": SCENARIO},
        "montecarlo": {
            "sigmas": mc_out, "base": mc.get("base", ""),
            "zero_sigma": num(zero_sigma, 2) if zero_sigma else None,
            "value": (f"при точности отметок хуже {num(zero_sigma, 1)} м эффект исчезает"
                      if zero_sigma else "порог точности отметок не найден"),
            "source": MODEL,
        },
        "disclaimer": {
            "value": "модельный массив, сезон 2025, полевых измерений нет",
            "source": "—",
        },
    }
    return content


def to_md(c: dict) -> str:
    def row(name, field, unit=""):
        d = c[field]
        val = d["value"]
        if unit or d.get("unit"):
            val = f"{val} {unit or d.get('unit', '')}".strip()
        note = d.get("note") or d.get("base") or ""
        return f"| {name} | {val} | {d['source']}" + (f" | {note} |" if note else " | |")

    lines = [
        "# content.md — единственный источник чисел",
        "",
        f"Собрано: {c['_meta']['generated']}. Файл создан `backend/app/content.py` из данных пайплайна.",
        "В вёрстке нет ни одной цифры, набранной руками: интерфейс читает `content.json`.",
        "",
        "| Поле | Значение | Источник | Примечание |",
        "|---|---|---|---|",
        row("Массив: район, чеков, га", "massif"),
        row("Дата снимка подложки", "image_date"),
        row("Даты контроля (затопление/полог/сброс)", "control_dates"),
        row("Лимит (базовый сценарий)", "limit_base"),
        row("Эффект к практике, %", "effect_pct"),
        row("Эффект к практике, ₽/га", "effect_rub_ha"),
        row("Эффект на массив, млн ₽", "effect_rub_array"),
        row("Чеков выведено из севооборота", "checks_fallowed"),
        row("Вода: полная потребность", "water_full"),
        row("Вода: подача при лимите", "water_supply"),
        row("Вода: недоподача к полной потребности", "water_shortage"),
        row("Вода: разница к практике вывода чеков", "water_vs_practice"),
        row("Обратная задача: вода под урожай практики", "inverse"),
        row("Экономия воды при равном урожае, млн м³", "water_saving"),
        row("Порог точности отметок", "montecarlo"),
        row("Число чеков с неполным рядом", "checks_incomplete"),
        f"| Урожай: без лимита / уравнительно / вывод чеков / оптимум | "
        f"{c['yield_base']['full']} / {c['yield_base']['uniform']} / {c['yield_base']['fallowing']} / "
        f"{c['yield_base']['optimal']} т | {MODEL} | |",
        row("ET₀ за сезон (наш расчёт)", "et0"),
        row("ETm за сезон", "etm"),
        row("Фильтрация нетто за сезон", "percolation"),
        row("Разовое затопление", "flooding"),
        f"| Норма водоподачи, м³/га | {c['norm']['min']} / {c['norm']['mean']} / {c['norm']['max']} "
        f"(мин / средняя / макс) | {MODEL} | |",
        f"| КПД подачи | {c['kpd']['min']} … {c['kpd']['max']} | {MODEL} | |",
        f"| Отметки чеков, м | {c['elev']['min']} … {c['elev']['mean']} … {c['elev']['max']} "
        f"(мин / средняя / макс) | {c['elev']['source']} | |",
        f"| Сезон | {c['season']['start']}, {c['season']['days']} сут | {SCENARIO} | |",
        f"| ET₀ ERA5 для сверки | {c['et0']['ref']} мм | {ERA5} | расхождение с FAO-56 {c['et0']['diff_pct']} % |",
    ]
    for key, s in c["montecarlo"]["sigmas"].items():
        lines.append(f"| Монте-Карло, σ = {key} м | {s['mean']} % (5…95 %: {s['p05']}…{s['p95']}) "
                     f"| {MODEL} | {s['runs']} прогонов |")
    lines += [
        f"| Дисклеймер | {c['disclaimer']['value']} | {c['disclaimer']['source']} | |",
        "",
        "## Что здесь нельзя менять руками",
        "",
        "Любое число на экране обязано иметь `source`. Значения без источника — брак:",
        "зритель защиты не может их проверить, а значит, не обязан верить.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    payload = build()
    (ROOT / "content.md").write_text(to_md(payload), encoding="utf-8")
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    (DATA / "content.json").write_text(text, encoding="utf-8")
    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "content.json").write_text(text, encoding="utf-8")
    print(f"content.md и content.json обновлены ({len(text) // 1024} КБ)")
    print(to_md(payload))
