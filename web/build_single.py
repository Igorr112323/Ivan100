"""Сборка одного HTML-файла: данные внутри, работает офлайн.

Выход: dist/akvachek.html — один файл, без внешних запросов, ведёт себя одинаково
на проекторе и в телефоне.

Запуск: python web/build_single.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
WEB = ROOT / "web"
OUT_DIR = ROOT / "dist"
OUT = OUT_DIR / "akvachek.html"

PLACEBO_DEFAULT = {"real": 0.54, "median": 1.29, "p95": 3.87}
UNDERUSE_M3 = 80_992
WATER_LINE = "экономии воды нет; подача та же, потери видны по чекам"
ELEV_NOTE = ("эффект держится при точности отметок лучше 0,4 м; текущий источник "
             "(публичные Terrain Tiles z=13) её не обеспечивает — задача полевого этапа 2027")
DISCLAIMER = "модельный массив · сезон 2025 · полевых измерений нет"


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def headline_variant() -> tuple[str, str, dict]:
    """Вариант А, если плацебо на −30 % пройдено; иначе вариант Б."""
    path = DATA / "optimization" / "diag_wp.json"
    row = None
    if path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        for r in d.get("placebo_limits", {}).get("rows", []):
            if r.get("deficit_pct") == 30:
                row = r
    if row and row["real_gain_pct"] > row["placebo_p95_pct"]:
        grid = load("optimization/grid.json")
        lim = next(r for r in grid["rows"] if abs(r["limit"] - 0.70) < 1e-9)
        tons = round(lim["yield_tons"]["capped"] - lim["yield_tons"]["fallowing"])
        return (f"В жёсткий дефицит (−30 %) — на {tons} т риса больше, "
                f"чем при текущей практике. В обычный год эффект близок к нулю.",
                "база: практика вывода чеков из севооборота · расчёт модели", row)
    return ("Подача та же. Потери видны: 0,46 % лимита недомспользуется — "
            "81 тыс. м³ на массиве.",
            "база: практика вывода чеков из севооборота · расчёт модели",
            row or PLACEBO_DEFAULT)


def main():
    checks_gj = load("checks.geojson")
    content = load("content.json")
    modes = load("optimization/modes.json")
    grid = load("optimization/grid.json")
    aux = load("sentinel/array_rgb.json")
    content["disclaimer"]["value"] = DISCLAIMER   # точная формулировка
    proof = load("rice_proof.json")

    checks = []
    for f in checks_gj["features"]:
        p = f["properties"]
        ring = f["geometry"]["coordinates"][0]
        step = 1 if len(ring) <= 60 else 2
        checks.append({
            "id": int(p["id"]),
            "area": round(float(p["area_ha"]), 2),
            "elev": round(float(p.get("elev_m") or 0.0), 2),
            "dz": round(float(p.get("dz_m") or 0.0), 2),
            "kpd": round(float(p.get("kpd") or 0.8), 3),
            "suspect_merge": bool(p.get("suspect_merge")),
            "bank_iou": round(float(p.get("bank_iou") or 0.0), 2),
            "poly": [[round(c[0], 6), round(c[1], 6)] for c in ring[::step]],
        })

    curve = []
    for L in (100, 90, 85, 80, 75, 70):
        r = next((x for x in grid["rows"] if abs(x["limit"] * 100 - L) < 1e-6), None)
        if r:
            curve.append({
                "limit": L, "deficit": 100 - L,
                "gain": round(r["gain_pct"]["capped_vs_fallowing"], 2),
                "per_ha": round(r["economics"]["per_ha_rub"]),
            })

    rgb_b64 = ""
    jpg = DATA / "sentinel" / "array_rgb.jpg"
    if jpg.exists():
        rgb_b64 = "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()

    head, src, placebo = headline_variant()
    now = modes["limits"][[round(l["limit"] * 100) for l in modes["limits"]].index(85)]

    payload = {
        "checks": checks,
        "modes": modes,
        "content": content,
        "grid_curve": curve,
        "aux": aux,
        "rgb": rgb_b64,
        "attr": aux.get("attribution", ""),
        "headline": head,
        "headsrc": src,
        "modes_now": now,
        "underuse_m3": UNDERUSE_M3,
        "water_line": WATER_LINE,
        "elev_note": ELEV_NOTE,
        "proof_ok": proof["counts"]["rice"],
        "proof_all": proof["counts"]["checked"],
        "placebo": {
            "real": round(placebo.get("real_gain_pct", PLACEBO_DEFAULT["real"]), 2),
            "median": round(placebo.get("placebo_median_pct", PLACEBO_DEFAULT["median"]), 2),
            "p95": round(placebo.get("placebo_p95_pct", PLACEBO_DEFAULT["p95"]), 2),
        },
        "legend": [
            {"label": "зеркало воды", "style": "background:#2E6E7E"},
            {"label": "почва насыщена", "style": "background:rgba(46,110,126,.35)"},
            {"label": "сухо", "style": "background:transparent"},
            {"label": "выведен из севооборота", "style": "border:2px solid #B4341F"},
            {"label": "недолив", "style": "border:2px solid #C8A24A"},
            {"label": "нет снимка", "cls": "hatch"},
        ],
    }

    tpl = (WEB / "template.html").read_text(encoding="utf-8")
    html = tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size
    print(f"собрано {OUT} — {size/1024/1024:.2f} МБ, чеков {len(checks)}, "
          f"лимитов {len(modes['limits'])}")
    print("заголовок:", head)
    questions(payload, OUT_DIR / "questions.html")
    if size > 1_500_000:
        print("ВНИМАНИЕ: больше 1,5 МБ")




QUESTIONS = [
    ("Откуда знаете, что эффект реален?",
     "Плацебо-тест: выигрыш {real} % лежит внутри случайных перестановок свойств чеков "
     "(медиана {median} %, 95-й процентиль {p95} %) — сам по себе он ничего не доказывает.",
     "Доказуемое другое: хозяйство не добирает {underuse} м³ своего лимита ({underuse_pct} % "
     "от {water} млн м³). Это видно в данных, а не в модели.",
     "источник: расчёт модели · Sentinel-2 L2A · ERA5"),
    ("Почему числа изменились против заявки?",
     "В заявке массив был предварительной оценкой; в продукте он переоцифрован по признаку риса, "
     "пруды и лиманы исключены, границы уточнены: {checks} чека, {area} га.",
     "₽/га пересчитаны от фактического урожая на каждом лимите: при −30 % было 11 860 ₽/га, "
     "стало {p70} ({tons} т × 25 000 ₽ / {area} га).",
     "источник: расчёт модели · 25 000 ₽/т — цена, принятая в заявке"),
    ("Откуда отметки и что будет при ошибке?",
     "Отметки чеков — публичные Terrain Tiles z=13, систематическая ошибка не измерена, "
     "полевой нивелировки нет.",
     "Монте-Карло: эффект держится при точности отметок лучше 0,4 м; текущий источник её "
     "не обеспечивает — задача полевого этапа 2027.",
     "источник: Монте-Карло по массиву · Terrain Tiles z=13"),
    ("Кто платит?",
     "Платит хозяйство: лимит и договор водопользования его, система не добавляет ничего "
     "к счёту за воду.",
     "Расход на внедрение — 0 ₽ оборудования: снимки Sentinel-2 L2A и сводка ERA5 бесплатны, "
     "датчиков и обходов не нужно. Выигрыш при −30 %: {p70} ₽/га, {mln} млн ₽ на массив за сезон.",
     "источник: расчёт модели · открытые данные"),
    ("Это рис или просто поливное поле?",
     "Рис подтверждён по спектру на {proof_ok} чеках из {proof_all}: вода под пологом (NDWI > 0) "
     "на ≥ 50 % площади, NDVI > 0,55 на пике, осенью NDWI ≤ 0.",
     "{proof_bad} чека критерий не прошли — в ведомости они помечены флагом. Это честная дыра "
     "в данных, а не «весь массив — рис».",
     "источник: Sentinel-2 L2A, 4 даты сезона 2025"),
]


def questions(payload: dict, path: Path):
    grid = payload["grid_curve"]
    p70 = next(r for r in grid if r["limit"] == 70)
    vals = {
        "real": payload["placebo"]["real"], "median": payload["placebo"]["median"],
        "p95": payload["placebo"]["p95"],
        "underuse": f"{payload['underuse_m3']:,}".replace(",", " "),
        "underuse_pct": round(payload["underuse_m3"] / (payload["modes_now"]["water_opt_m3"]) * 100, 2),
        "water": round(payload["modes_now"]["water_opt_m3"] / 1e6, 2),
        "checks": payload["content"]["massif"]["checks"],
        "area": f"{payload['content']['massif']['area_ha']:.1f}",
        "p70": f"{p70['per_ha']:,}".replace(",", " "),
        "tons": round(p70["per_ha"] * payload["content"]["massif"]["area_ha"] / 25000, 1),
        "mln": round(p70["per_ha"] * payload["content"]["massif"]["area_ha"] / 1e6, 2),
        "proof_ok": payload["proof_ok"], "proof_all": payload["proof_all"],
        "proof_bad": payload["proof_all"] - payload["proof_ok"],
    }
    items = "".join(
        f"""<div class="q"><div class="h">{h}</div><p>{a.format(**vals)}</p>
      <p>{b.format(**vals)}</p><div class="src">{src}</div></div>"""
        for h, a, b, src in QUESTIONS)
    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>АКВАЧЕК — пять злых вопросов</title><style>
:root{{--paper:#EFEAE0;--ink:#1B1F24;--seal:#B4341F;--muted:#6B6558;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--paper);color:var(--ink);font-family:"Golos Text","Segoe UI",system-ui,sans-serif;
  font-size:15px;line-height:1.4;padding:32px 40px;max-width:900px;margin:0 auto}}
h1{{font-size:24px;font-weight:600;letter-spacing:.01em}}
.sub{{font-family:var(--mono);font-size:12px;color:var(--muted);margin:4px 0 20px}}
.q{{border-top:1px solid var(--ink);padding:12px 0 14px}}
.q .h{{font-size:17px;font-weight:600;margin-bottom:6px}}
.q p{{margin:0 0 4px;max-width:78ch}}
.q .src{{font-family:var(--mono);font-size:12px;color:var(--muted);margin-top:6px}}
.stamp{{margin-top:24px;border:1px solid var(--ink);border-radius:2px;padding:12px 16px;
  font-family:var(--mono);font-size:12px;color:var(--muted)}}
@media print{{@page{{size:A4 portrait;margin:14mm 14mm}}
  body{{background:#fff;padding:0;font-size:11px}}
  .q{{padding:8px 0 9px}} .q .h{{font-size:13px}} h1{{font-size:17px}}
  .q p{{max-width:none}} .q .src{{font-size:9px}} .stamp{{font-size:9px}}}}
</style></head><body>
<h1>АКВАЧЕК — пять злых вопросов</h1>
<div class="sub">модельный массив · сезон 2025 · полевых измерений нет · {vals['checks']} чека, {vals['area']} га, Красноармейский район</div>
{items}
<div class="stamp">экономии воды нет; подача та же, потери видны по чекам<br>
эффект держится при точности отметок лучше 0,4 м; текущий источник (публичные Terrain Tiles z=13) её не обеспечивает — задача полевого этапа 2027<br>
в заявке — предварительная оценка массива; в продукте массив переоцифрован по признаку риса, пруды и лиманы исключены, границы уточнены</div>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
    print(f"собрано {path} — {path.stat().st_size/1024:.0f} КБ")


if __name__ == "__main__":
    main()
