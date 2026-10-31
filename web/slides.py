"""Пять слайдов 16:9, минимум текста. Только числа из content.md и проекта."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_url import SITE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = Path("/tmp/slides.html")


def uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


SLIDES = [
    ("Проблема", """
      <p class="big">Когда воды не хватает, её делят поровну:<br>каждому чеку — одинаково.</p>
      <p class="big dim">А чеки разные. Одному недолив стоит трети урожая,<br>другому — ничего.</p>
    """),
    ("Что делает продукт", """
      <div class="three">
        <div class="st"><div class="n">01</div><div class="h">Видит массив</div>
          <div class="t">границы 51 чека по открытым снимкам</div></div>
        <div class="st"><div class="n">02</div><div class="h">Считает воду</div>
          <div class="t">испарение, фильтрация, затопление — по каждому чеку</div></div>
        <div class="st"><div class="n">03</div><div class="h">Выдаёт ведомость</div>
          <div class="t">кому сколько кубометров, лист на декаду</div></div>
      </div>
    """),
    ("Экран", f"""
      <div class="shot"><img src="{uri(DIST / 'screen_1440x900.png', 'image/png')}" alt="экран"></div>
    """),
    ("Числа", """
      <div class="nums">
        <div class="cell"><div class="v">+410 т</div><div class="k">риса при лимите −30 %</div></div>
        <div class="cell"><div class="v">7 932 &#8381;/га</div><div class="k">10,26 млн &#8381; на массив</div></div>
        <div class="cell"><div class="v">81 тыс. м³</div><div class="k">0,46 % лимита, который хозяйство не добирает</div></div>
      </div>
      <p class="base">база сравнения: практика вывода чеков из севооборота · расчёт модели</p>
    """),
    ("Границы и следующий шаг", f"""
      <div class="cols">
        <div class="lim">
          <div class="h">Границы</div>
          <ul>
            <li>при −15 % выигрыш 0,54 % — внутри случайности</li>
            <li>проверено два уровня дефицита, значим один</li>
            <li>отметки нужны точнее 0,4 м — их нет</li>
            <li>полевых измерений не было</li>
          </ul>
        </div>
        <div class="next">
          <div class="h">2027</div>
          <ul>
            <li>уровнемеры в чеках</li>
            <li>съёмка отметок</li>
            <li>один сезон: вода на входе, урожай на выходе</li>
          </ul>
        </div>
        <div class="qrbox">
          <img src="{uri(DIST / 'qr.png', 'image/png')}" alt="QR">
          <div class="qrcap">откройте и подвигайте ползунок</div>
          <div class="qrurl mono">{SITE}</div>
        </div>
      </div>
    """),
]


def main() -> None:
    body = "".join(
        f"""<section class="slide"><div class="pad">
      <div class="ttl"><span>{i+1:02d}</span>{title}</div>
      {inner}
    </div></section>"""
        for i, (title, inner) in enumerate(SLIDES))
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>АКВАЧЕК — слайды</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
@page{{size:1600px 900px;margin:0}}
body{{background:#EFEAE0;color:#1B1F24;font-family:"Golos Text","Segoe UI",system-ui,sans-serif}}
.slide{{width:1600px;height:900px;page-break-after:always;position:relative;overflow:hidden}}
.slide:last-child{{page-break-after:auto}}
.pad{{padding:80px 96px}}
.ttl{{font-size:26px;font-weight:600;letter-spacing:.01em;margin-bottom:56px;
  display:flex;gap:20px;align-items:baseline}}
.ttl span{{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:20px;color:#B4341F}}
.big{{font-size:56px;line-height:1.15;font-weight:600;max-width:1300px}}
p.big{{margin-top:44px}}
p.dim{{color:#444}}
.two{{display:flex;gap:48px}}
.box{{border:2px solid #1B1F24;border-radius:2px;padding:32px 40px;width:520px}}
.box .k{{font-family:"IBM Plex Mono",monospace;font-size:20px;color:#6B6558}}
.box .v{{font-family:"IBM Plex Mono",monospace;font-size:96px;font-weight:600;line-height:1.05}}
.box .t{{font-size:24px;color:#444}}
.three{{display:flex;gap:64px;margin-top:40px}}
.st{{width:400px;border-top:3px solid #1B1F24;padding-top:20px}}
.st .n{{font-family:"IBM Plex Mono",monospace;font-size:22px;color:#B4341F}}
.st .h{{font-size:40px;font-weight:600;margin:6px 0 10px}}
.st .t{{font-size:24px;color:#444;line-height:1.3}}
.shot img{{width:1408px;height:668px;object-fit:contain;border:1px solid #1B1F24}}
.nums{{display:flex;gap:64px}}
.nums .cell{{width:400px;border-left:3px solid #B4341F;padding-left:24px}}
.nums .v{{font-family:"IBM Plex Mono",monospace;font-size:76px;font-weight:600;line-height:1.05}}
.nums .k{{font-size:24px;color:#444;margin-top:8px}}
.base{{font-family:"IBM Plex Mono",monospace;font-size:20px;color:#6B6558;margin-top:56px}}
.cols{{display:flex;gap:96px}}
.cols .h{{font-size:32px;font-weight:600;margin-bottom:20px}}
.cols ul{{list-style:none;font-size:26px;line-height:1.5}}
.cols li{{padding-left:28px;text-indent:-28px;margin-bottom:12px}}
.cols li::before{{content:"— ";color:#B4341F}}
.next .h{{color:#B4341F}}
.qrbox{{width:300px;margin-left:auto}}
.qrbox img{{width:220px;height:220px;display:block;border:1px solid #1B1F24}}
.qrbox .qrcap{{font-size:20px;margin-top:12px;line-height:1.2}}
.qrbox .qrurl{{font-size:16px;margin-top:10px;line-height:1.25;word-break:break-all;color:#6B6558}}
.mono{{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace}}
</style></head><body>{body}</body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print("slides.html:", OUT.stat().st_size // 1024, "КБ")


if __name__ == "__main__":
    main()
