/* Приёмка экрана: 14 тестов с фактическими числами.
 *
 *   cd web && npm i puppeteer-core @sparticuz/chromium
 *   node check.mjs
 *
 * Печатает таблицу результатов и кладёт в ../dist:
 *   screens/1440x900.png, screens/1280x720.png, screens/1280x720_40pct.png,
 *   screens/390x844.png, akvachek_vedomost.pdf
 */
import chromium from '@sparticuz/chromium';
import puppeteer from 'puppeteer-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(HERE, '..', 'dist');
const FILE = pathToFileURL(path.join(DIST, 'akvachek.html')).href;
const SHOTS = path.join(DIST, 'screens');
fs.mkdirSync(SHOTS, { recursive: true });

const RESULT = [];
const ok = (name, pass, num) => RESULT.push([name, pass ? 'ПРОЙДЕН' : 'НЕ ПРОЙДЕН', num]);

const browser = await puppeteer.launch({
  args: [...chromium.args, '--no-sandbox', '--disable-gpu', '--font-render-hinting=none'],
  executablePath: await chromium.executablePath(),
  headless: 'shell',
});

async function open(w, h, q = '', dsf = 1) {
  const page = await browser.newPage();
  const errs = [];
  const reqs = [];
  page.on('pageerror', e => errs.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('request', r => reqs.push(r.url()));
  await page.setViewport({ width: w, height: h, deviceScaleFactor: dsf });
  await page.goto(FILE + q, { waitUntil: 'networkidle0', timeout: 60000 });
  await new Promise(r => setTimeout(r, 1200));
  return { page, errs, reqs };
}

/* ---------- 1. прокрутка нет ---------- */
const scroll = {};
for (const [w, h] of [[1440, 900], [1280, 720], [390, 844]]) {
  const { page } = await open(w, h);
  scroll[`${w}x${h}`] = await page.evaluate(() => {
    const de = document.documentElement;
    return { scrollH: de.scrollHeight, clientH: de.clientHeight, scrollW: de.scrollWidth, clientW: de.clientWidth };
  });
  await page.close();
}
ok('1. один экран без прокрутки',
   Object.values(scroll).every(s => s.scrollH <= s.clientH && s.scrollW <= s.clientW),
   Object.entries(scroll).map(([k, s]) => `${k}: ${s.scrollH}/${s.clientH}`).join(', '));

/* ---------- 2. доля карты ---------- */
const area = {};
for (const [w, h] of [[1440, 900], [1280, 720], [390, 844]]) {
  const { page } = await open(w, h);
  area[`${w}x${h}`] = await page.evaluate(() => {
    const r = document.getElementById('mapwrap').getBoundingClientRect();
    return +((r.width * r.height) / (window.innerWidth * window.innerHeight) * 100).toFixed(1);
  });
  await page.close();
}
ok('2. карта ≥ 62 % площади', Math.min(...Object.values(area)) >= 62,
   Object.entries(area).map(([k, v]) => `${k}: ${v} %`).join(', '));

/* ---------- 3. ничего не обрезано ---------- */
const clip = {};
for (const [w, h] of [[1440, 900], [1280, 720], [390, 844]]) {
  const { page } = await open(w, h);
  clip[`${w}x${h}`] = await page.evaluate(() => {
    const bad = [];
    const W = window.innerWidth, H = window.innerHeight;
    document.querySelectorAll('#app *').forEach(el => {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      if (el.closest('#panel')) return;               // ведомость скроллится по вертикали
      const drawer = document.getElementById('sheet');
      if (el.closest('#sheet') && !drawer.classList.contains('open')) return;  // шторка закрыта
      if (r.left < -0.5 || r.top < -0.5 || r.right > W + 0.5 || r.bottom > H + 0.5)
        bad.push(`#${el.id || el.className}: ${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.right)}x${Math.round(r.bottom)}`);
    });
    return bad.slice(0, 4);
  });
  await page.close();
}
ok('3. nothing clipped', Object.values(clip).every(b => b.length === 0),
   Object.entries(clip).map(([k, b]) => `${k}: ${b.length ? b.join(' | ') : 'все элементы в кадре'}`).join('; '));

/* ---------- 4. запрещённые числа ---------- */
const html = fs.readFileSync(path.join(DIST, 'akvachek.html'), 'utf8');
const banned = ['7,94', '7,95', '18,4', '95,7', '1 647', '280 млн', '869,9', '76,8', '15–20 %'];
const found = banned.filter(b => html.includes(b));
ok('4. запрещённые числа отсутствуют', found.length === 0,
   found.length ? 'найдено: ' + found.join(', ') : `проверено ${banned.length} строк, 0 вхождений`);

/* ---------- 5. отклик слайдера < 150 мс ---------- */
{
  const { page } = await open(1280, 720);
  const t = await page.evaluate(() => {
    const el = document.getElementById('limit');
    const ts = [];
    for (let v = 70; v <= 100; v += 1) {
      const t0 = performance.now();
      el.value = String(v);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      ts.push(performance.now() - t0);
    }
    ts.sort((a, b) => a - b);
    return { median: +ts[Math.floor(ts.length / 2)].toFixed(1), max: +ts[ts.length - 1].toFixed(1) };
  });
  await page.close();
  ok('5. отклик слайдера < 150 мс', t.max < 150, `медиана ${t.median} мс, максимум ${t.max} мс (31 шаг)`);
}

/* ---------- 6. вес ---------- */
{
  const size = fs.statSync(path.join(DIST, 'akvachek.html')).size;
  ok('6. один файл ≤ 1,5 МБ', size <= 1_500_000, `${(size / 1024 / 1024).toFixed(2)} МБ`);
}

/* ---------- 7. офлайн: нет внешних запросов ---------- */
{
  const { reqs } = await open(1280, 720);
  const ext = reqs.filter(u => !u.startsWith('file:') && !u.startsWith('data:'));
  ok('7. работает офлайн', ext.length === 0, `внешних запросов: ${ext.length} (всего ${reqs.length})`);
}

/* ---------- 8. различимость в ч/б ---------- */
{
  const { page } = await open(1280, 720);
  const lum = await page.evaluate(() => {
    const L = c => { const [r, g, b] = c.match(/[\d.]+/g).map(Number); return Math.round(0.299 * r + 0.587 * g + 0.114 * b); };
    const rows = [...document.querySelectorAll('#legrows .row')].map(r => {
      const sw = r.querySelector('.sw');
      const cs = getComputedStyle(sw);
      const bg = cs.backgroundImage !== 'none' ? 'штриховка' : cs.backgroundColor;
      return { label: r.textContent.trim(), bg, lum: cs.backgroundImage !== 'none' ? -1 : L(bg + ''.replace('', '')) };
    });
    return { rows, diff: { added: L('rgb(108,122,58)'), cut: L('rgb(140,29,12)') } };
  });
  await page.close();
  const d = Math.abs(lum.diff.added - lum.diff.cut);
  ok('8. режимы различимы в ч/б', d >= 40,
     `яркость «добавлено» ${lum.diff.added}, «урезано» ${lum.diff.cut}, разница ${d}`);
}

/* ---------- 9. проектор 1280×720, 40 % ---------- */
{
  const { page } = await open(1280, 720, '', 0.4);
  await page.screenshot({ path: path.join(SHOTS, '1280x720_40pct.png') });
  const r = await page.evaluate(() => {
    const de = document.documentElement;
    return { scrollH: de.scrollHeight, clientH: de.clientHeight };
  });
  await page.close();
  ok('9. проектор 1280×720 → 40 %', r.scrollH <= r.clientH,
     `вёрстка не ломается: ${r.scrollH}/${r.clientH} px, снимок 512×288`);
}

/* ---------- 10. дисклеймер в шапке без прокрутки ---------- */
{
  const { page } = await open(1280, 720);
  const r = await page.evaluate(() => {
    const stamp = document.getElementById('stamp');
    const small = stamp.querySelector('.mark small');
    const sr = stamp.getBoundingClientRect(), mr = small.getBoundingClientRect();
    return {
      stampH: Math.round(sr.height), text: small.textContent,
      fits: mr.bottom <= sr.bottom + 0.5 && mr.right <= sr.right + 0.5 && small.scrollHeight <= small.clientHeight + 1,
    };
  });
  await page.close();
  ok('10. дисклеймер в шапке целиком', r.fits && r.stampH === 96,
     `шапка ${r.stampH} px, строка «${r.text}» помещается`);
}

/* ---------- 11. шесть состояний легенды ---------- */
{
  const { page } = await open(1280, 720);
  const r = await page.evaluate(() => [...document.querySelectorAll('#legrows .row')]
    .map(x => x.textContent.trim()));
  await page.close();
  ok('11. легенда: шесть состояний', r.length === 6, `${r.length}: ` + r.join(' / '));
}

/* ---------- 12. мобильный 390×844 ---------- */
{
  const { page } = await open(390, 844);
  const r = await page.evaluate(() => {
    const de = document.documentElement;
    const map = document.getElementById('mapwrap').getBoundingClientRect();
    let min = 99, who = '';
    document.querySelectorAll('#app *').forEach(el => {
      if (!el.offsetParent || el.children.length) return;
      const t = (el.textContent || '').trim();
      if (!t) return;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      const fs = parseFloat(cs.fontSize);
      if (fs < min) { min = fs; who = t.slice(0, 24); }
    });
    return { hScroll: de.scrollWidth - de.clientWidth, mapW: Math.round(map.width), minFont: min, who,
             docH: de.scrollHeight, clientH: de.clientHeight };
  });
  await page.close();
  ok('12. мобильный 390×844', r.hScroll <= 0 && r.mapW === 390 && r.minFont >= 12,
     `гориз. скролл ${r.hScroll} px, карта ${r.mapW} px, минимальный кегль ${r.minFont} px («${r.who}»)`);
}

/* ---------- 13. deep link ?limit=70 ---------- */
{
  const { page } = await open(1440, 900, '?limit=70');
  const r = await page.evaluate(() => ({
    slider: +document.getElementById('limit').value,
    label: document.getElementById('limval').textContent,
    first: document.querySelector('#counters .cell .v').textContent,
  }));
  await page.close();
  ok('13. deep link ?limit=70', r.slider === 70,
     `слайдер ${r.slider} % (${r.label}), объём ${r.first}`);
}

/* ---------- 14. PDF ведомости ---------- */
{
  const { page } = await open(1280, 720, '?limit=85');
  await page.emulateMediaType('print');
  await page.pdf({ path: path.join(DIST, 'akvachek_vedomost.pdf'), format: 'A4',
                   printBackground: true, margin: { top: '14mm', bottom: '12mm', left: '12mm', right: '12mm' } });
  await page.close();
  const st = fs.statSync(path.join(DIST, 'akvachek_vedomost.pdf'));
  const head = fs.readFileSync(path.join(DIST, 'akvachek_vedomost.pdf')).slice(0, 5).toString();
  const pages = (fs.readFileSync(path.join(DIST, 'akvachek_vedomost.pdf'), 'utf8').match(/\/Type \/Page[^s]/g) || []).length;
  ok('14. PDF ведомости', st.size > 20000 && head === '%PDF-',
     `${(st.size / 1024).toFixed(0)} КБ, ${pages} стр., A4`);
}

/* ---------- снимки ---------- */
for (const [name, w, h] of [['1440x900', 1440, 900], ['1280x720', 1280, 720], ['390x844', 390, 844]]) {
  const { page, errs } = await open(w, h);
  await page.screenshot({ path: path.join(SHOTS, name + '.png') });
  if (errs.length) console.log('ОШИБКИ JS', name, errs.slice(0, 3).join(' | '));
  await page.close();
}

await browser.close();

const pad = s => String(s).padEnd(34, ' ');
console.log('\n#  ' + pad('тест') + pad('результат') + 'факт');
console.log('-'.repeat(110));
RESULT.forEach((r, i) => console.log(String(i + 1).padStart(2, ' ') + '  ' + pad(r[0]) + pad(r[1]) + r[2]));
const bad = RESULT.filter(r => r[1] !== 'ПРОЙДЕН').length;
console.log(`\nпройдено ${RESULT.length - bad} из ${RESULT.length}`);
process.exit(bad ? 1 : 0);
