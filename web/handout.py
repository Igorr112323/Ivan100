"""Раздатка для жюри: один лист A4. Только данные из content.md и проекта."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_url import SITE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TMP = Path("/tmp")

def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def shrink_map() -> Path:
    from PIL import Image
    src = Image.open(TMP / "map.png")
    src.thumbnail((1200, 1200), Image.LANCZOS)
    out = TMP / "map_small.png"
    src.convert("RGB").save(out, optimize=True)
    return out


def main() -> None:
    content = json.loads((ROOT / "data" / "content.json").read_text(encoding="utf-8"))
    checks = content["massif"]["checks"]
    area = content["massif"]["area_ha"]
    per_ha = content["effect_rub_ha"]["value"]  # 841 при −15 %
    rub30 = 7932
    mln30 = "10,26"
    area_str = f"{area:,.1f}".replace(",", " ").replace(".", ",")
    rub30_str = f"{rub30:,}".replace(",", " ")

    mp = shrink_map()
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>АКВАЧЕК — раздатка</title><style>
@page{{size:A4 portrait;margin:12mm 12mm 10mm}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Golos Text","Segoe UI",system-ui,sans-serif;color:#1B1F24;background:#fff;
  font-size:11pt;line-height:1.3}}
.mono{{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace}}
.head{{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:1px solid #000;padding-bottom:5px}}
.head h1{{font-size:26pt;font-weight:600;letter-spacing:.02em;line-height:1}}
.head .sub{{font-size:10pt}}
.head .dis{{font-size:9pt;text-align:right;max-width:96mm}}
.map{{margin:6mm 0 4mm;border:1px solid #000}}
.map img{{display:block;width:100%;height:96mm;object-fit:cover}}
.nums{{display:flex;gap:6mm;border-top:1px solid #000;border-bottom:1px solid #000;
  padding:4mm 0}}
.nums .cell{{flex:1}}
.nums .v{{font-size:20pt;font-weight:600;letter-spacing:-.01em;line-height:1.05}}
.nums .k{{font-size:9.5pt;color:#444;margin-top:2px}}
.base{{font-size:10pt;margin:3mm 0 4mm}}
.cols{{display:flex;gap:6mm}}
.lim{{flex:1}}
.lim h2{{font-size:12pt;font-weight:600;margin-bottom:2mm}}
.lim ul{{list-style:none;font-size:9.5pt;line-height:1.35}}
.lim li{{padding-left:5mm;text-indent:-5mm;margin-bottom:1.6mm}}
.lim li b{{font-weight:600}}
.qr{{width:46mm;text-align:center}}
.qr img{{width:46mm;height:46mm;display:block;border:1px solid #000}}
.qr .cap{{font-size:9pt;margin-top:2mm;line-height:1.2}}
.qr .url{{font-size:9pt;margin-top:2mm;line-height:1.25;word-break:break-all;
  text-align:left;border-top:1px solid #999;padding-top:2mm}}
.src{{margin-top:5mm;border-top:1px solid #000;padding-top:2mm;font-size:9pt;color:#333}}
</style></head><body>
<div class="head">
  <div><h1>АКВАЧЕК</h1>
    <div class="sub">контроль водного баланса рисового массива и ведомость водоподачи</div></div>
  <div class="dis mono">модельный массив · сезон 2025 · полевых измерений нет</div>
</div>
<div class="map"><img src="{data_uri(mp, 'image/png')}" alt="массив, 51 чек"></div>
<div class="nums">
  <div class="cell"><div class="v mono">{checks} чек / {area_str} га</div>
    <div class="k">Красноармейский район, ст. Полтавская</div></div>
  <div class="cell"><div class="v mono">+410 т</div>
    <div class="k">при лимите −30 % (70 %)</div></div>
  <div class="cell"><div class="v mono">{rub30_str} &#8381;/га</div>
    <div class="k">{mln30} млн ₽ на массив за сезон</div></div>
</div>
<div class="base"><b>База сравнения:</b> против практики вывода чеков из севооборота —
  того, что хозяйство делает сейчас. Не против уравнительного распределения.</div>
<div class="cols">
  <div class="lim">
    <h2>Границы</h2>
    <ul>
      <li><b>Плацебо.</b> При −15 % выигрыш 0,54 % лежит внутри случайных перестановок
        свойств чеков: медиана 1,29 %, 95-й процентиль 3,87 %. Как доказательство не годится.</li>
      <li><b>Значимость.</b> Проверено два уровня дефицита, значим один: при −30 %
        выигрыш 6,49 % выше 95-го процентиля плацебо 5,23 % (0 из 100 перестановок).</li>
      <li><b>Отметки.</b> Эффект держится при точности лучше 0,4 м. Текущий источник —
        публичные Terrain Tiles z=13 — этой точности не обеспечивает.</li>
      <li><b>Поля.</b> Полей не было: ни расхода воды, ни нивелировки. Цифры — расчёт на
        открытых снимках и открытой погоде.</li>
    </ul>
  </div>
  <div class="qr">
    <img src="{data_uri(DIST / 'qr.png', 'image/png')}" alt="QR">
    <div class="cap">откройте и подвигайте ползунок</div>
    <div class="url mono">{SITE}</div>
  </div>
</div>
<div class="src mono">источники: Sentinel-2 L2A (границы чеков и вода) · ERA5 (погода) ·
 FAO-56 (испарение) · расчёт модели. Сезон 05-10, 143 сут. Подача при лимите 85 % — 17,66 млн м³,
 полная потребность — 20,77 млн м³.</div>
</body></html>"""
    out = TMP / "handout.html"
    out.write_text(html, encoding="utf-8")
    print("handout.html собран:", out.stat().st_size // 1024, "КБ")


if __name__ == "__main__":
    main()
