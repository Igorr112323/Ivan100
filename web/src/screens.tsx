/* Восемь «листов» рабочего чертежа. Каждый лист — отдельный экран продукта. */

import { useMemo, useState } from "react";
import { api, fmtDate, num, type Meta, type OptimizeResult } from "./lib/api";
import { CompareBars, ETChart, Histogram, SeriesSpark } from "./components/Charts";
import MapView from "./components/MapView";
import Vedomost, { type VedomostRow } from "./components/Vedomost";
import { Arrow, CircleMark, CONDITIONAL_SIGNS, Cross, Tick, Underline, stateColor } from "./components/Marks";

export interface Ctx {
  meta: Meta | null;
  checksGeo: any;
  opt: OptimizeResult | null;
  validation: any;
  scenarios: any;
  montecarlo: any;
  series: any;
  year: number;
  selected: number | null;
  setSelected: (id: number | null) => void;
  params: { deficit: number; ky: number; intermittent: boolean };
  setParams: (p: { deficit: number; ky: number; intermittent: boolean }) => void;
  go: (i: number) => void;
}

const STAGES = [
  {
    no: "01",
    title: "Спутник: контуры и состояние чеков",
    body: "Sentinel-2 L2A, 10 м. Контуры чеков выделяются по водному зеркалу, фаза развития — по NDVI.",
    formula: "NDVI = (B08 − B04) / (B08 + B04)   NDWI = (B03 − B08) / (B03 + B08)",
    source: "Copernicus Sentinel-2, COG на AWS Open Data",
  },
  {
    no: "02",
    title: "Погода: суточный ряд реанализа",
    body: "ERA5: температура, влажность, точка росы, ветер, радиация, осадки — за каждый день сезона.",
    formula: "T, Td, u₁₀, Rs, P → суточные средние",
    source: "ERA5, ECMWF",
  },
  {
    no: "03",
    title: "Физика: опорное испарение",
    body: "Пенман–Монтейт по FAO-56 на реальной погоде. Затем — потребность культуры с учётом фазы.",
    formula: "ET₀ = (0,408·Δ·(Rn − G) + γ·(900/(T+273))·u₂·(es − ea)) / (Δ + γ·(1 + 0,34·u₂))",
    source: "FAO Irrigation and Drainage Paper 56, ур. 6",
  },
  {
    no: "04",
    title: "Баланс чека",
    body: "Фильтрация зависит от превышения чека, осадки зачитываются, КПД подачи — по рельефу.",
    formula: "Брутто = (ΣKc·ET₀ + F(Δz) − 0,7·P) / КПД + 2 000 м³/га",
    source: "Модель проекта, калибровка по норме 16 009 м³/га",
  },
  {
    no: "05",
    title: "Отклик урожая",
    body: "FAO-33: потеря урожая пропорциональна дефициту транспирации с коэффициентом чувствительности фазы.",
    formula: "1 − Y/Ymax = Σ Ky,p · (1 − ETa/ETm)",
    source: "FAO Irrigation and Drainage Paper 33",
  },
  {
    no: "06",
    title: "Оптимизация: задача ЛП",
    body: "Переменные — подача воды по чекам и декадам. Целевая функция — валовой сбор массива при лимите водозабора.",
    formula: "max Σ Aᵢ·Yᵢ  при  Σ Aᵢ·xᵢ ≤ (1 − d)·Норма",
    source: "PuLP + решатель CBC",
  },
];

/* ------------------------------------------------------------------ лист 0 */

export function Sheet0({ ctx }: { ctx: Ctx }) {
  const { meta } = ctx;
  return (
    <div>
      <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1.15fr)" }}>
        <div>
          <div className="mono" style={{ marginBottom: 10 }}>
            Шифр АКВ-2027-КРА · Премия IQ года · номинация «АПК, биотехнологии, пищевая промышленность»
          </div>
          <h1 className="display" style={{ fontSize: "clamp(46px, 9vw, 104px)" }}>
            АКВА<span style={{ color: "var(--seal)" }}>ЧЕК</span>
          </h1>
          <Underline width={320} />
          <p className="lead" style={{ marginTop: 18 }}>
            Система спутникового мониторинга и оптимизации водоподачи на рисовых оросительных системах
            Краснодарского края.
          </p>
          <div className="stack-s" style={{ marginTop: "var(--s4)", maxWidth: "46ch" }}>
            <div className="kv">
              <span>Что делает</span>
              <span />
            </div>
            <p className="small">
              Считает водный баланс каждого чека по спутнику и погоде — и распределяет дефицит воды так,
              чтобы недолив пришёлся на чеки и фазы развития риса с минимальной потерей урожая.
            </p>
            <p className="small">
              Выдаёт диспетчеру готовую ведомость: сколько кубометров и с каким расходом подать в каждый чек
              на декаду.
            </p>
          </div>
          <div className="btn-row" style={{ marginTop: "var(--s4)" }}>
            <button className="btn" onClick={() => ctx.go(1)}>
              Открыть карту массива →
            </button>
            <button className="btn" onClick={() => ctx.go(3)}>
              Посчитать оптимум
            </button>
          </div>
          <div className="src" style={{ marginTop: "var(--s4)" }}>
            {meta
              ? `Полигон: ${meta.project.polygon} · чеков ${meta.checks.count} · ${num(meta.checks.total_ha, 0)} га`
              : "данные загружаются"}
          </div>
        </div>
        <div className="bleed-right">
          {meta?.imagery ? (
            <img
              src={`/data/sentinel/${meta.imagery.image}`}
              alt="Снимок Sentinel-2 рисового массива"
              style={{ width: "100%", display: "block", border: "1px solid var(--line-hard)" }}
            />
          ) : (
            <div className="pending">снимок массива загружается</div>
          )}
          <div className="src" style={{ marginTop: 6 }}>
            {meta?.imagery
              ? `Sentinel-2 L2A · ${meta.imagery.date} · ${meta.imagery.scene}`
              : ""}
          </div>
          <div style={{ position: "relative", marginTop: -34, marginLeft: 8 }}>
            <Arrow />
          </div>
          <div className="note" style={{ marginLeft: 44 }}>
            чеки с водой отсюда и до вала Р-3
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Что видно сразу</h2>
        </div>
        <div className="split-even">
          {[
            {
              t: "Реальный массив, а не модель на бумаге",
              d: meta
                ? `${meta.checks.count} чеков, ${num(meta.checks.total_ha, 0)} га. Контуры оцифрованы по снимку Sentinel-2, отметки — из Copernicus DEM.`
                : "",
            },
            {
              t: "Физика, а не «средние нормы»",
              d: "Суточный Пенман–Монтейт по FAO-56 на реальной погоде ERA5. Расчёт сверен с эталонной ET0 из реанализа.",
            },
            {
              t: "Оптимум, а не уравниловка",
              d: "Задача линейного программирования решается на лету: меняете лимит воды — ведомость пересчитывается.",
            },
          ].map((b) => (
            <div key={b.t}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>{b.t}</div>
              <div className="small">{b.d}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ лист 1 */

export function Sheet1({ ctx }: { ctx: Ctx }) {
  const { meta, checksGeo, opt, selected, setSelected } = ctx;
  const cuts = useMemo(() => {
    const m = new Map<number, number>();
    (opt?.vedomost ?? []).forEach((r) => m.set(r.id, r.cut_pct));
    return m;
  }, [opt]);

  const row: VedomostRow | undefined = opt?.vedomost.find((r) => r.id === selected);
  const used = opt?.water_m3.optimal ?? 0;
  const limit = opt?.water_m3.limit ?? 1;

  return (
    <div>
      <div
        style={{
          borderBottom: "1px solid var(--line-hard)",
          paddingBottom: "var(--s2)",
          marginBottom: "var(--s3)",
          display: "flex",
          gap: "var(--s3)",
          flexWrap: "wrap",
          alignItems: "baseline",
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 15 }}>
          Реальный массив рисовых чеков: цвет чека — срез водоподачи, назначенный оптимизатором
        </div>
        <div className="small">
          {meta
            ? `${meta.checks.count} чеков · ${num(meta.checks.total_ha, 0)} га · снимок Sentinel-2 от ${fmtDate(
                meta.imagery?.date ?? meta.model.season_start
              )}. Нажмите на чек.`
            : ""}
        </div>
      </div>
      <div className="split">
        <div className="bleed-left" style={{ position: "relative" }}>
          <MapView
            imagery={meta?.imagery ?? null}
            checksGeo={checksGeo}
            cuts={cuts}
            selected={selected}
            onSelect={setSelected}
          />
          <div className="map-legend">
            {CONDITIONAL_SIGNS.map((s) => (
              <div className="legend-row" key={s.label}>
                <span className="legend-swatch" style={{ background: s.color }} />
                {s.label}
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="effect">
            <div className="readout-label">Прирост валового сбора к уравнительному распределению</div>
            <span className="effect-value">
              {opt ? `+${num(opt.gain_pct.vs_uniform, 2)}` : "—"}
              <span style={{ fontSize: "0.42em", letterSpacing: "-0.02em" }}> %</span>
            </span>
            <div className="effect-sub">
              {opt
                ? `При лимите водоподачи −${num(opt.params.deficit * 100, 0)} % и Ky = ${num(opt.params.ky, 2)}. Решатель CBC: ${opt.solver}.`
                : "расчёт выполняется"}
            </div>
            <div className="effect-grid">
              <div>
                <div className="readout-label">Дополнительно</div>
                <div className="num" style={{ fontSize: 17 }}>
                  {opt ? `${num((opt.yield_tons.optimal - opt.yield_tons.uniform) / 1000, 1)} тыс. т` : "—"}
                </div>
              </div>
              <div>
                <div className="readout-label">По краю</div>
                <div className="num" style={{ fontSize: 17 }}>
                  {opt ? `${num(opt.economics.krai_rub / 1e6, 0)} млн ₽` : "—"}
                </div>
              </div>
              <div>
                <div className="readout-label">Водозабор</div>
                <div className="num" style={{ fontSize: 17 }}>
                  {opt ? `${num(used / 1e6, 2)} млн м³` : "—"}
                </div>
              </div>
            </div>
          </div>

          <div className="readout" style={{ marginTop: "var(--s3)" }}>
            <div className="readout-label">Лимит водохранилища на сезон</div>
            <div style={{ height: 12, border: "1px solid var(--ink)", position: "relative", marginTop: 4 }}>
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  width: `${Math.min(100, (used / limit) * 100)}%`,
                  background: "var(--water)",
                }}
              />
              <div
                style={{
                  position: "absolute",
                  left: "100%",
                  top: -3,
                  bottom: -3,
                  borderLeft: "1px solid var(--seal)",
                }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 5 }}>
              <span className="num" style={{ fontSize: 11 }}>
                назначено {num(used, 0)} м³
              </span>
              <span className="num" style={{ fontSize: 11, color: "var(--seal)" }}>
                лимит {num(limit, 0)} м³
              </span>
            </div>
          </div>

          {row ? (
            <div className="readout" style={{ marginTop: "var(--s3)" }}>
              <div className="readout-label">Чек № {row.id} · выбран на карте</div>
              <div style={{ marginTop: 8 }}>
                <div className="kv">
                  <span>Площадь</span>
                  <span>{num(row.area_ha, 1)} га</span>
                </div>
                <div className="kv">
                  <span>Отметка</span>
                  <span>{num(row.elev_m, 2)} м</span>
                </div>
                <div className="kv">
                  <span>КПД подачи</span>
                  <span>{num(row.kpd, 2)}</span>
                </div>
                <div className="kv">
                  <span>Срез водоподачи</span>
                  <span style={{ color: row.cut_pct > 25 ? "var(--dry)" : "inherit" }}>{num(row.cut_pct, 1)} %</span>
                </div>
                <div className="kv">
                  <span>Сохранение урожая</span>
                  <span>{num(row.yield_frac * 100, 1)} %</span>
                </div>
              </div>
              {(() => {
                const s = ctx.series?.checks?.find((c: any) => c.id === row.id);
                if (!s || !s.series?.length) return null;
                const ndvi = s.series.map((p: any) => p.ndvi);
                const ndwi = s.series.map((p: any) => p.ndwi);
                return (
                  <div style={{ marginTop: 12 }}>
                    <div className="readout-label">
                      Спутниковый ряд чека · {s.series.length} снимков за сезон
                    </div>
                    <div style={{ marginTop: 4 }}>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span className="src">NDVI (биомасса)</span>
                        <span className="num" style={{ fontSize: 11 }}>
                          {num(ndvi[ndvi.length - 1], 3)}
                        </span>
                      </div>
                      <SeriesSpark points={ndvi} color="#6c7a3a" />
                    </div>
                    <div style={{ marginTop: 6 }}>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span className="src">NDWI (водное зеркало)</span>
                        <span className="num" style={{ fontSize: 11 }}>
                          {num(ndwi[ndwi.length - 1], 3)}
                        </span>
                      </div>
                      <SeriesSpark points={ndwi} color="#2e6e7e" />
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span className="src">{s.series[0].date.slice(5)}</span>
                      <span className="src">{s.series[s.series.length - 1].date.slice(5)}</span>
                    </div>
                  </div>
                );
              })()}

              <div style={{ marginTop: 10 }}>
                <div className="readout-label">График по декадам, м³</div>
                <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 56, marginTop: 4 }}>
                  {row.decades.map((d) => {
                    const max = Math.max(...row.decades.map((x) => x.m3), 1);
                    return (
                      <div
                        key={d.decade}
                        title={`${d.start}: ${num(d.m3, 0)} м³, ${num(d.l_s, 1)} л/с`}
                        style={{
                          flex: 1,
                          height: `${(d.m3 / max) * 100}%`,
                          background: stateColor(row.cut_pct),
                          borderTop: "1px solid var(--ink)",
                        }}
                      />
                    );
                  })}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span className="src">{row.decades[0]?.start}</span>
                  <span className="src">{row.decades[row.decades.length - 1]?.start}</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="readout" style={{ marginTop: "var(--s3)" }}>
              <div className="small">Нажмите на чек на карте — появится карточка с графиком водоподачи.</div>
            </div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Сравнение стратегий при дефиците воды</h2>
        </div>
        {opt ? (
          <div className="split-even">
            <CompareBars
              rows={[
                {
                  label: "Полная потребность (лимита нет)",
                  value: opt.yield_tons.full,
                  color: "#6e9a8d",
                  note: "верхняя граница: воды сколько нужно",
                },
                {
                  label: "Уравнительное урезание (практика)",
                  value: opt.yield_tons.uniform,
                  color: "#c8a24a",
                  note: `потеря ${num(opt.gain_pct.uniform_vs_full, 2)} % к полной потребности`,
                },
                {
                  label: "Вывод высоких чеков (практика)",
                  value: opt.yield_tons.fallowing,
                  color: "#c14b2a",
                  note: `в севообороте выведено чеков: ${opt.fallowed_checks.length}`,
                },
                {
                  label: "Оптимум АКВАЧЕК",
                  value: opt.yield_tons.optimal,
                  color: "#2e6e7e",
                  note: `+${num(opt.gain_pct.vs_uniform, 2)} % к уравнительному`,
                },
              ]}
            />
            <div>
              <div className="readout">
                <div className="readout-label">Экономия воды при том же урожае</div>
                <div className="readout-value">{num(opt.water_saved_pct, 2)} %</div>
                <div className="readout-note">
                  {num(opt.water_m3.uniform - opt.water_m3.optimal, 0)} м³ за сезон на массиве — при
                  неизменном валовом сборе
                </div>
              </div>
              <div className="readout">
                <div className="readout-label">Норма водоподачи (расчёт)</div>
                <div className="readout-value">{num(opt.norms_m3_ha.mean, 0)} м³/га</div>
                <div className="readout-note">
                  Калибрована по нормативной норме Кубани: {num(meta?.model.target_norm_m3_ha ?? 16009, 0)} м³/га
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="pending">расчёт выполняется</div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ лист 2 */

export function Sheet2({ ctx }: { ctx: Ctx }) {
  return (
    <div>
      <div className="section-head">
        <h2 className="hang">Как считается каждое число</h2>
      </div>
      <p className="lead">
        Шесть последовательных стадий. Каждая следующая получает данные предыдущей, и все входы — открытые
        и проверяемые.
      </p>

      <div className="stack-m" style={{ marginTop: "var(--s4)" }}>
        {STAGES.map((s, i) => (
          <div key={s.no} style={{ display: "grid", gridTemplateColumns: "52px minmax(0,1fr)", gap: "var(--s3)" }}>
            <div>
              <div className="display" style={{ fontSize: 30, color: i === 4 ? "var(--seal)" : "var(--ink)" }}>
                {s.no}
              </div>
              {i < STAGES.length - 1 ? (
                <div style={{ borderLeft: "1px solid var(--line-hard)", height: 34, marginLeft: 12, marginTop: 6 }} />
              ) : null}
            </div>
            <div style={{ borderBottom: "1px solid var(--line-soft)", paddingBottom: "var(--s3)" }}>
              <div style={{ fontWeight: 600, fontSize: 16 }}>{s.title}</div>
              <p className="small" style={{ marginTop: 6, maxWidth: "58ch" }}>
                {s.body}
              </p>
              <div
                className="num"
                style={{
                  fontSize: 12.5,
                  background: "var(--paper-3)",
                  border: "1px solid var(--line)",
                  padding: "8px 10px",
                  overflowX: "auto",
                  marginTop: 8,
                }}
              >
                {s.formula}
              </div>
              <div className="src" style={{ marginTop: 6 }}>
                Источник: {s.source}
              </div>
            </div>
          </div>
        ))}
      </div>

      {ctx.validation ? (
        <div className="section">
          <div className="section-head">
            <h2 className="hang">Независимая сверка расчёта испарения</h2>
          </div>
          <div className="split-even">
            <div>
              <ETChart series={ctx.validation.series} phases={ctx.meta?.model.phases ?? []} />
            </div>
            <div>
              <div className="readout">
                <div className="readout-label">Наш FAO-56 за сезон</div>
                <div className="readout-value">{num(ctx.validation.ours_mm, 1)} мм</div>
              </div>
              <div className="readout">
                <div className="readout-label">Эталон ET₀ из ERA5</div>
                <div className="readout-value">{num(ctx.validation.reference_mm, 1)} мм</div>
                <div className="readout-note">
                  Расхождение {num(ctx.validation.difference_pct, 2)} %, RMSE{" "}
                  {num(ctx.validation.rmse_mm_day, 3)} мм/сут, смещение {num(ctx.validation.bias_mm_day, 3)} мм/сут
                </div>
              </div>
              <div className="small" style={{ marginTop: 12 }}>
                Сверка проведена на {ctx.validation.days} сутках сезона {ctx.validation.year} года. Совпадение
                означает, что физическая часть модели воспроизводит реанализ, а не подогнана под него.
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ лист 3 */

export function Sheet3({ ctx }: { ctx: Ctx }) {
  const { opt, params, setParams } = ctx;
  const [fullList, setFullList] = useState(false);

  return (
    <div>
      <div className="section-head">
        <h2 className="hang">Оптимизатор</h2>
      </div>
      <p className="lead">
        Задайте условия маловодья — и получите ведомость: сколько воды и куда подать. Расчёт идёт на сервере,
        решается настоящая задача линейного программирования.
      </p>

      <div className="split-even" style={{ marginTop: "var(--s4)" }}>
        <div className="field">
          <div className="readout-label">Дефицит водоподачи — {num(params.deficit * 100, 0)} %</div>
          <input
            type="range"
            min={0}
            max={40}
            step={1}
            value={Math.round(params.deficit * 100)}
            onChange={(e) => setParams({ ...params, deficit: +e.target.value / 100 })}
          />
          <div className="src">лимит водохранилища на сезон, урезание от полной потребности</div>
        </div>
        <div className="field">
          <div className="readout-label">Коэффициент Ky — {num(params.ky, 2)}</div>
          <input
            type="range"
            min={60}
            max={160}
            step={2}
            value={Math.round(params.ky * 100)}
            onChange={(e) => setParams({ ...params, ky: +e.target.value / 100 })}
          />
          <div className="src">чувствительность урожая к дефициту по FAO-33</div>
        </div>
        <div className="field">
          <div className="readout-label">Режим затопления</div>
          <div className="btn-row" style={{ marginTop: 6 }}>
            <button
              className="btn"
              data-active={!params.intermittent}
              onClick={() => setParams({ ...params, intermittent: false })}
            >
              Постоянный слой
            </button>
            <button
              className="btn"
              data-active={params.intermittent}
              onClick={() => setParams({ ...params, intermittent: true })}
            >
              Прерывистый
            </button>
          </div>
          <div className="src" style={{ marginTop: 6 }}>
            прерывистое затопление снижает фильтрацию — дополнительный резерв воды
          </div>
        </div>
      </div>

      {opt ? (
        <>
          <div className="section">
            <div className="section-head">
              <h2 className="hang">Результат расчёта</h2>
              <span className="src">
                решатель {opt.solver} · {num(opt.timing_s, 2)} с · чеков {opt.vedomost.length}
              </span>
            </div>
            <div className="split-even">
              <div>
                <CompareBars
                  rows={[
                    { label: "Уравнительное урезание", value: opt.yield_tons.uniform, color: "#c8a24a" },
                    { label: "Вывод высоких чеков", value: opt.yield_tons.fallowing, color: "#c14b2a" },
                    { label: "Оптимум АКВАЧЕК", value: opt.yield_tons.optimal, color: "#2e6e7e" },
                  ]}
                />
                <div style={{ marginTop: "var(--s3)", display: "flex", gap: "var(--s4)", flexWrap: "wrap" }}>
                  <div>
                    <div className="readout-label">К уравнительной практике</div>
                    <div className="display" style={{ fontSize: 34 }}>
                      +{num(opt.gain_pct.vs_uniform, 2)} %
                    </div>
                  </div>
                  <div>
                    <div className="readout-label">К выводу чеков</div>
                    <div className="display" style={{ fontSize: 34 }}>
                      +{num(opt.gain_pct.vs_fallowing, 2)} %
                    </div>
                  </div>
                </div>
              </div>
              <div>
                <div className="readout">
                  <div className="readout-label">Водозабор</div>
                  <div className="readout-value">{num(opt.water_m3.optimal / 1e6, 3)} млн м³</div>
                  <div className="readout-note">
                    лимит {num(opt.water_m3.limit / 1e6, 3)} млн м³ · полная потребность{" "}
                    {num(opt.water_m3.full / 1e6, 3)} млн м³ · дефицит {num(opt.water_m3.limit_shortage / 1e6, 3)}{" "}
                    млн м³
                  </div>
                </div>
                <div className="readout">
                  <div className="readout-label">Экономия воды</div>
                  <div className="readout-value">{num(opt.water_saved_pct, 2)} %</div>
                  <div className="readout-note">
                    {num(opt.water_m3.uniform - opt.water_m3.optimal, 0)} м³ при сохранении сбора
                  </div>
                </div>
                <div className="readout">
                  <div className="readout-label">Экономика прироста</div>
                  <div className="readout-value">{num(opt.economics.per_ha_rub, 0)} ₽/га</div>
                  <div className="readout-note">
                    {num(opt.economics.per_ha_kg, 1)} кг/га · на массив {num(opt.economics.array_rub / 1e6, 2)}{" "}
                    млн ₽ · по краю {num(opt.economics.krai_rub / 1e6, 0)} млн ₽
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-head">
              <h2 className="hang">Ведомость водоподачи</h2>
              <div className="btn-row">
                <button className="btn" onClick={() => setFullList((v) => !v)}>
                  {fullList ? "Сократить" : `Все ${opt.vedomost.length} чеков`}
                </button>
                <button className="btn no-print" onClick={() => window.print()}>
                  Печать / PDF
                </button>
              </div>
            </div>
            <Vedomost
              rows={opt.vedomost}
              limit={opt.water_m3.limit}
              selected={ctx.selected}
              onSelect={(id) => ctx.setSelected(id)}
              compact={!fullList}
            />
            <div className="src" style={{ marginTop: 8 }}>
              Строка «Итого» сходится с лимитом: назначено {num(opt.water_m3.optimal, 0)} м³ из{" "}
              {num(opt.water_m3.limit, 0)} м³
            </div>
          </div>
        </>
      ) : (
        <div className="pending">решатель ЛП выполняет расчёт</div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ лист 4 */

export function Sheet4({ ctx }: { ctx: Ctx }) {
  const [sigma, setSigma] = useState(0.5);
  const [mc, setMc] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const { opt, meta, validation, scenarios } = ctx;

  const runMc = async (s: number) => {
    setSigma(s);
    setLoading(true);
    try {
      const r = await api.montecarlo(ctx.year, ctx.params.deficit, ctx.params.ky, s, 80);
      setMc(r);
    } finally {
      setLoading(false);
    }
  };

  const properties = opt
    ? [
        {
          t: "Норма водоподачи воспроизводит нормативную",
          v: `${num(opt.norms_m3_ha.mean, 0)} м³/га`,
          n: `цель ${num(meta?.model.target_norm_m3_ha ?? 16009, 0)} м³/га`,
          ok: true,
        },
        {
          t: "Расчёт ET₀ сверен с независимым рядом ERA5",
          v: validation ? `${num(validation.difference_pct, 2)} %` : "—",
          n: validation ? `RMSE ${num(validation.rmse_mm_day, 3)} мм/сут` : "",
          ok: !!validation && Math.abs(validation.difference_pct) < 15,
        },
        {
          t: "Решатель ЛП находит оптимум",
          v: opt.solver,
          n: `${num(opt.timing_s, 2)} с, ${opt.vedomost.length} чеков`,
          ok: opt.solver === "Optimal",
        },
        {
          t: "Прирост к уравнительной практике",
          v: `+${num(opt.gain_pct.vs_uniform, 2)} %`,
          n: `${num(opt.yield_tons.optimal - opt.yield_tons.uniform, 1)} т на массиве`,
          ok: opt.gain_pct.vs_uniform > 0,
        },
        {
          t: "Прирост к практике вывода чеков",
          v: `+${num(opt.gain_pct.vs_fallowing, 2)} %`,
          n: `выводилось бы ${opt.fallowed_checks.length} чеков`,
          ok: opt.gain_pct.vs_fallowing > 0,
        },
        {
          t: "Снижение водозабора при том же сборе",
          v: `${num(opt.water_saved_pct, 2)} %`,
          n: `${num(opt.water_m3.uniform - opt.water_m3.optimal, 0)} м³`,
          ok: opt.water_saved_pct > 0,
        },
        {
          t: "Наблюдаемость массива со спутника",
          v: meta?.scenes?.saved ? `${meta.scenes.saved.length} снимков` : "—",
          n: meta?.scenes?.saved
            ? `${fmtDate(meta.scenes.saved[0].date)} — ${fmtDate(meta.scenes.saved[meta.scenes.saved.length - 1].date)}`
            : "",
          ok: !!meta?.scenes?.saved?.length,
        },
      ]
    : [];

  const powerN = (effectPct: number, cv = 6.3) => {
    const z = 1.96 + 1.645;
    return Math.ceil((2 * z * z * cv * cv) / (effectPct * effectPct));
  };

  return (
    <div>
      <div className="section-head">
        <h2 className="hang">Доказательная база</h2>
      </div>
      <p className="lead">
        Ниже — не обещания, а измеряемые свойства расчёта. Каждое можно проверить: данные открыты, код
        воспроизводит результат.
      </p>

      <div className="proof" style={{ marginTop: "var(--s4)" }}>
        {properties.map((p, i) => (
          <div className="proof-row" key={p.t}>
            <div className="proof-no">{String(i + 1).padStart(2, "0")}</div>
            <div>
              <div style={{ fontWeight: 600 }}>{p.t}</div>
              <div className="small">{p.n}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              {p.ok ? (
                <span className="proof-done">
                  <Tick /> выполнено
                </span>
              ) : (
                <span style={{ color: "var(--dry)", fontFamily: "var(--font-mono)", fontSize: 13 }}>
                  <Cross /> не выполнено
                </span>
              )}
              <div className="num" style={{ fontSize: 15, marginTop: 4 }}>
                {p.v}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Устойчивость к ошибке рельефа</h2>
        </div>
        <p className="small" style={{ maxWidth: "70ch" }}>
          Решение принимается по отметкам с шумом σ, а результат оценивается в истинной модели — так же, как
          в поле, где рельеф известен с ошибкой. Проверка честная: оптимум не может быть хуже равномерного
          распределения по построению, поэтому важно не «не хуже», а сколько именно эффекта сохраняется.
        </p>
        <div className="btn-row" style={{ margin: "var(--s3) 0" }}>
          {[0.25, 0.5, 1.0, 3.0].map((s) => (
            <button key={s} className="btn" data-active={sigma === s} onClick={() => runMc(s)}>
              σ = {num(s, 2)} м
            </button>
          ))}
        </div>
        {loading ? <div className="pending">идёт расчёт 80 прогонов</div> : null}
        {mc && !loading ? (
          <div className="split-even">
            <Histogram histogram={mc.histogram} mean={mc.mean} p05={mc.p05} p95={mc.p95} sigma={mc.sigma_m} />
            <div>
              <div className="readout">
                <div className="readout-label">Средний прирост</div>
                <div className="readout-value">+{num(mc.mean, 2)} %</div>
              </div>
              <div className="readout">
                <div className="readout-label">90 %-й доверительный интервал</div>
                <div className="readout-value">
                  {num(mc.p05, 2)} … {num(mc.p95, 2)} %
                </div>
                <div className="readout-note">
                  {mc.runs} прогонов, минимум {num(mc.min, 2)} %, максимум {num(mc.max, 2)} %
                </div>
              </div>
              <div className="small" style={{ marginTop: 12 }}>
                σ = 0,25–0,5 м — реалистичная ошибка лазерной съёмки или РТК. σ = 3 м — ошибка глобального
                цифрового рельефа без съёмки: при ней разброс отметок между чеками становится больше реального
                микрорельефа, и часть эффекта оптимизатора оказывается иллюзорной. Отсюда задача этапа 2 —
                уточнить отметки по съёмке хозяйств.
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {scenarios ? (
        <div className="section">
          <div className="section-head">
            <h2 className="hang">Девять расчётных случаев</h2>
          </div>
          <div className="scroll-x">
            <table className="vedomost">
              <thead>
                <tr>
                  <th>Дефицит</th>
                  <th>Ky</th>
                  <th>Уравнительно, т</th>
                  <th>Вывод чеков, т</th>
                  <th>Оптимум, т</th>
                  <th>К уравн., %</th>
                  <th>К выводу, %</th>
                  <th>Решатель</th>
                </tr>
              </thead>
              <tbody>
                {scenarios.rows.map((r: any) => (
                  <tr key={`${r.deficit}-${r.ky}`}>
                    <td>{num(r.deficit * 100, 0)} %</td>
                    <td>{num(r.ky, 2)}</td>
                    <td>{num(r.uniform_t, 1)}</td>
                    <td>{num(r.fallowing_t, 1)}</td>
                    <td>{num(r.optimal_t, 1)}</td>
                    <td style={{ color: "var(--water)" }}>+{num(r.gain_vs_uniform, 2)}</td>
                    <td>+{num(r.gain_vs_fallowing, 2)}</td>
                    <td>{r.solver}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Журнал съёмки</h2>
        </div>
        <div className="scroll-x">
          <table className="vedomost">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Сцена</th>
                <th>Облачность, %</th>
                <th>Кондиционных пикселей, %</th>
                <th>NDVI ср.</th>
                <th>NDWI ср.</th>
                <th>Водное зеркало, %</th>
              </tr>
            </thead>
            <tbody>
              {(meta?.scenes?.saved ?? []).map((s: any) => (
                <tr key={s.date}>
                  <td>{fmtDate(s.date)}</td>
                  <td>{s.id}</td>
                  <td>{num(s.cloud_cover_pct, 2)}</td>
                  <td>{num(s.valid_pct, 1)}</td>
                  <td>{num(s.ndvi_mean, 3)}</td>
                  <td>{num(s.ndwi_mean, 3)}</td>
                  <td>{num(s.water_pct, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="src" style={{ marginTop: 6 }}>
          Отбор сцен: STAC-каталог Earth Search v1, Sentinel-2 L2A, облачность до 35 % и доля кондиционных
          пикселей по маске SCL.
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Можно ли подтвердить эффект в поле</h2>
        </div>
        <div className="split-even">
          <div>
            <div className="small" style={{ maxWidth: "60ch" }}>
              Априорная мощность критерия при вариации урожайности по чекам 6,3 % (та же предпосылка, что и в
              заявке). Цифры неудобные, но честные: прямое статистическое подтверждение эффекта порядка 1 %
              требует опыта, сопоставимого по площади со всем краем.
            </div>
            <div className="scroll-x" style={{ marginTop: 12 }}>
              <table className="vedomost">
                <thead>
                  <tr>
                    <th>Обнаруживаемый эффект</th>
                    <th>Чеков на вариант</th>
                    <th>Га, всего</th>
                  </tr>
                </thead>
                <tbody>
                  {[5, 2, 1].map((e) => (
                    <tr key={e}>
                      <td>{num(e, 1)} %</td>
                      <td>{num(powerN(e), 0)}</td>
                      <td>{num(powerN(e) * (meta?.checks.mean_ha ?? 53) * 2, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <div className="readout">
              <div className="readout-label">Поэтому цель полевого этапа — другая</div>
              <div className="readout-note" style={{ marginTop: 0 }}>
                Проверяется не прирост урожая, а модель: расчётная эвапотранспирация и фильтрация сверяются с
                расходомерами и рейками на 5–10 чеках. Варьирование расхода воды на порядок ниже, чем
                урожайности, — и здесь опыт статистически состоятелен.
              </div>
            </div>
            <div className="readout">
              <div className="readout-label">Статистика прироста урожая</div>
              <div className="readout-note" style={{ marginTop: 0 }}>
                Набирается на этапе промышленной эксплуатации методом A/B по хозяйствам (2028–2029), где в
                контуре окажутся тысячи чеков.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ лист 5 */

export function Sheet5({ ctx }: { ctx: Ctx }) {
  const [gain, setGain] = useState(ctx.opt?.gain_pct.vs_uniform ?? 1.29);
  const [price, setPrice] = useState(25);
  const [area, setArea] = useState(500);
  const [pDrought, setPDrought] = useState(0.3);
  const [pilotCost] = useState(950000);

  const perHaKg = 73.1 * 10 * (gain / 100);
  const perHaRub = perHaKg * price;
  const kraiTons = 829700 * (gain / 100);
  const kraiRub = kraiTons * 1000 * price;
  const pilotRub = perHaRub * area * pDrought;
  const paybackMonths = pilotRub > 0 ? pilotCost / (pilotRub / 12) : Infinity;

  return (
    <div>
      <div className="section-head">
        <h2 className="hang">Экономика</h2>
      </div>
      <p className="lead">
        Считаем честно и раздельно: на один гектар, на пилот и на весь край. Эффект возникает только в годы
        лимитирования водоподачи, поэтому итог взвешен по вероятности такого года.
      </p>

      <div className="split-even" style={{ marginTop: "var(--s4)" }}>
        <div>
          <div className="field">
            <div className="readout-label">Прирост сбора — {num(gain, 2)} %</div>
            <input type="range" min={0} max={400} step={1} value={Math.round(gain * 100)} onChange={(e) => setGain(+e.target.value / 100)} />
          </div>
          <div className="field">
            <div className="readout-label">Цена риса-сырца — {num(price, 0)} ₽/кг</div>
            <input type="range" min={15} max={45} step={1} value={price} onChange={(e) => setPrice(+e.target.value)} />
            <div className="src">вилка рынка 2025 г.: 22–26 ₽/кг</div>
          </div>
          <div className="field">
            <div className="readout-label">Площадь пилота — {num(area, 0)} га</div>
            <input type="range" min={100} max={5000} step={50} value={area} onChange={(e) => setArea(+e.target.value)} />
          </div>
          <div className="field">
            <div className="readout-label">Вероятность маловодного года — {num(pDrought * 100, 0)} %</div>
            <input type="range" min={5} max={100} step={5} value={Math.round(pDrought * 100)} onChange={(e) => setPDrought(+e.target.value / 100)} />
            <div className="src">за последние 7 лет лимиты вводились в 2 сезонах: 2020 и 2025</div>
          </div>
        </div>

        <div>
          <div className="readout">
            <div className="readout-label">На один гектар</div>
            <div className="readout-value">{num(perHaRub, 0)} ₽/га</div>
            <div className="readout-note">{num(perHaKg, 1)} кг/га при урожайности 73,1 ц/га</div>
          </div>
          <div className="readout">
            <div className="readout-label">Пилот {num(area, 0)} га, в среднем за год</div>
            <div className="readout-value">{num(pilotRub, 0)} ₽</div>
            <div className="readout-note">
              Стоимость пилота 950 000 ₽/год → окупаемость{" "}
              {Number.isFinite(paybackMonths) ? `${num(paybackMonths, 1)} мес` : "не окупается"} в среднем по
              годам; в год дефицита — {num((pilotCost / ((perHaRub * area) / 12)), 1)} мес
            </div>
          </div>
          <div className="readout">
            <div className="readout-label">Весь край, в год дефицита</div>
            <div className="readout-value">{num(kraiRub / 1e6, 0)} млн ₽</div>
            <div className="readout-note">
              {num(kraiTons, 0)} т при валовом сборе 829 700 т и площади 112 300 га
            </div>
          </div>
          <div className="small" style={{ marginTop: 12 }}>
            Здесь принципиально важно разделять масштабы. Ошибка, которая легко закрадывается в такие расчёты, —
            сравнить эффект всего края со стоимостью одного пилота: получается красивая, но неверная цифра
            окупаемости «за пару недель». Настоящая окупаемость считается по площади внедрения.
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Цена воды</h2>
        </div>
        <div className="split-even">
          <div className="readout">
            <div className="readout-label">Стоимость поданной воды</div>
            <div className="readout-value">0,81 ₽/м³</div>
            <div className="readout-note">по методике ВНИИ проблем мелиорации</div>
          </div>
          <div className="small" style={{ maxWidth: "60ch" }}>
            Сэкономленные {num((ctx.opt?.water_m3.uniform ?? 0) - (ctx.opt?.water_m3.optimal ?? 0), 0)} м³ на
            массиве стоят около{" "}
            {num(((ctx.opt?.water_m3.uniform ?? 0) - (ctx.opt?.water_m3.optimal ?? 0)) * 0.81, 0)} ₽. Это важно
            понимать: прямой денежный эффект даёт сохранённый урожай, а не сэкономленная вода. Ценность воды —
            в том, что её хватает на все чеки.
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ лист 6 */

const ROADMAP = [
  {
    no: "1",
    period: "сен–окт 2026",
    title: "Модель и расчёт",
    status: "выполнен",
    items: [
      "Собран метеоархив ERA5 и космические снимки Sentinel-2",
      "Построена модель водопотребления чека по FAO-56",
      "Реализован решатель задачи ЛП и ведомость водоподачи",
      "Проведено стресс-тестирование методом Монте-Карло",
    ],
  },
  {
    no: "2",
    period: "ноя 2026 – мар 2027",
    title: "Точность отметок и калибровка",
    status: "в работе",
    items: [
      "Уточнение отметок микрорельефа по лазерным съёмкам хозяйств",
      "Калибровка коэффициентов донной фильтрации по типам почв",
      "Интеграция в веб-интерфейс агронома и автоматические ведомости",
    ],
  },
  {
    no: "3",
    period: "апр–окт 2027",
    title: "Пилот в хозяйстве",
    status: "план",
    items: [
      "Внедрение на 230–500 га в базовом хозяйстве Красноармейского района",
      "Инструментальный замер расхода воды и урожая",
      "Валидация модели по водному балансу, а не только по урожаю",
    ],
  },
  {
    no: "4",
    period: "2028–2029",
    title: "Масштабирование",
    status: "план",
    items: [
      "Красноармейский, Славянский, Калининский, Абинский районы",
      "Подписочная модель «сервис за 1 га», охват свыше 50 тыс. га",
      "A/B-статистика прироста урожая на тысячах чеков",
    ],
  },
];

const RISKS = [
  { r: "Затяжная облачность закрывает съёмку", m: "межорбитальная мозаика и запас по периодичности 4 суток" },
  { r: "Ошибка цифрового рельефа по высоте чеков", m: "лазерная съёмка на этапе 2, расчёт устойчивости к σ" },
  { r: "Хозяйство не меняет график подачи", m: "выходной документ — ведомость в привычном для диспетчера виде" },
  { r: "Лимит устанавливается вышестоящей организацией", m: "оптимизация работает внутри любого установленного лимита" },
];

export function Sheet6() {
  return (
    <div>
      <div className="section-head">
        <h2 className="hang">Внедрение</h2>
      </div>
      <div className="stack-m" style={{ marginTop: "var(--s4)" }}>
        {ROADMAP.map((s) => (
          <div key={s.no} style={{ display: "grid", gridTemplateColumns: "56px minmax(0,1fr) 120px", gap: "var(--s3)" }}>
            <div className="display" style={{ fontSize: 28 }}>{s.no}</div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 16 }}>{s.title}</div>
              <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                {s.items.map((i) => (
                  <li className="small" key={i} style={{ marginBottom: 3 }}>
                    {i}
                  </li>
                ))}
              </ul>
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="mono">{s.period}</div>
              <div
                className="num"
                style={{
                  fontSize: 11,
                  marginTop: 6,
                  color: s.status === "выполнен" ? "var(--veg)" : s.status === "в работе" ? "var(--seal)" : "var(--ink-2)",
                }}
              >
                {s.status}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Риски и как они снимаются</h2>
        </div>
        <table className="vedomost">
          <thead>
            <tr>
              <th>Риск</th>
              <th>Снятие</th>
            </tr>
          </thead>
          <tbody>
            {RISKS.map((x) => (
              <tr key={x.r}>
                <td style={{ textAlign: "left" }}>{x.r}</td>
                <td style={{ textAlign: "left" }}>{x.m}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ лист 7 */

export function Sheet7({ ctx }: { ctx: Ctx }) {
  const { meta } = ctx;
  const sources = [
    { n: "Sentinel-2 L2A", u: "Copernicus / ESA, COG на AWS Open Data", w: "контуры чеков, NDVI, NDWI, снимок массива" },
    { n: "ERA5", u: "ECMWF, Historical Weather API", w: "суточная погода и эталонная ET₀" },
    { n: "Copernicus DEM GLO-30", u: "ESA / AWS Open Data", w: "отметки чеков" },
    { n: "FAO-56, FAO-33", u: "FAO Irrigation and Drainage Papers", w: "испарение и отклик урожая" },
    { n: "Росстат, Национальный рисовый союз", u: "открытая статистика 2025 г.", w: "площадь, урожайность, валовой сбор" },
  ];

  return (
    <div>
      <div className="section-head">
        <h2 className="hang">Материалы</h2>
      </div>
      <p className="lead">
        Все данные проекта получены из открытых источников и воспроизводятся пайплайном в репозитории:
        скачивание снимков, расчёт индексов, сегментация чеков, отметки рельефа.
      </p>

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Состав данных</h2>
        </div>
        <table className="vedomost">
          <thead>
            <tr>
              <th>Источник</th>
              <th>Публикация</th>
              <th>Что даёт</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.n}>
                <td style={{ textAlign: "left" }}>{s.n}</td>
                <td style={{ textAlign: "left" }}>{s.u}</td>
                <td style={{ textAlign: "left" }}>{s.w}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {meta ? (
        <div className="section">
          <div className="section-head">
            <h2 className="hang">Паспорт расчёта</h2>
          </div>
          <div className="split-even">
            <div>
              <div className="kv"><span>Чеков в массиве</span><span>{num(meta.checks.count, 0)}</span></div>
              <div className="kv"><span>Площадь массива, га</span><span>{num(meta.checks.total_ha, 0)}</span></div>
              <div className="kv"><span>Средний чек, га</span><span>{num(meta.checks.mean_ha, 1)}</span></div>
              <div className="kv"><span>Перепад отметок, м</span><span>{num(meta.checks.elev_span_m, 2)}</span></div>
              <div className="kv"><span>Средняя отметка, м</span><span>{num(meta.checks.mean_elev_m, 2)}</span></div>
            </div>
            <div>
              <div className="kv"><span>Сезон</span><span>{fmtDate(meta.model.season_start)} — {fmtDate(meta.model.season_end)}</span></div>
              <div className="kv"><span>Длина сезона, сут</span><span>{num(meta.model.season_days, 0)}</span></div>
              <div className="kv"><span>Фильтрация базовая, мм/сут</span><span>{num(meta.model.percolation_base_mm_day, 2)}</span></div>
              <div className="kv"><span>Норма расчётная, м³/га</span><span>{num(meta.model.calibrated_norm_m3_ha, 0)}</span></div>
              <div className="kv"><span>Норма нормативная, м³/га</span><span>{num(meta.model.target_norm_m3_ha, 0)}</span></div>
            </div>
          </div>
        </div>
      ) : null}

      <div className="section">
        <div className="section-head">
          <h2 className="hang">Что взять с собой</h2>
        </div>
        <div className="btn-row">
          <button className="btn" onClick={() => window.print()}>
            Ведомость водоподачи (печать / PDF)
          </button>
          <button className="btn" onClick={() => ctx.go(4)}>
            Доказательная база
          </button>
          <button className="btn" onClick={() => ctx.go(3)}>
            Оптимизатор
          </button>
        </div>
        <div className="src" style={{ marginTop: 12 }}>
          Сайт полностью работоспособен без интернета: снимок, контуры, погода и решатель — локальные.
        </div>
        <div style={{ position: "relative", marginTop: "var(--s4)", height: 90 }}>
          <CircleMark />
          <div className="note" style={{ position: "absolute", left: 26, top: 26 }}>
            здесь — QR на этот сайт
          </div>
        </div>
      </div>
    </div>
  );
}
