import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Meta, type OptimizeResult } from "./lib/api";
import Stamp from "./components/Stamp";
import { Sheet0, Sheet1, Sheet2, Sheet3, Sheet4, Sheet5, Sheet6, Sheet7, type Ctx } from "./screens";

const SHEETS = [
  { key: "Титул", title: "АКВАЧЕК · система оптимизации водоподачи" },
  { key: "Карта", title: "Карта массива и результат оптимизации" },
  { key: "Метод", title: "Методика расчёта" },
  { key: "Оптимизатор", title: "Оптимизатор водоподачи" },
  { key: "Доказательства", title: "Доказательная база" },
  { key: "Экономика", title: "Экономика" },
  { key: "Внедрение", title: "Этапы внедрения и риски" },
  { key: "Материалы", title: "Материалы и источники" },
];

const MOBILE = [
  { i: 1, label: "Карта" },
  { i: 3, label: "Оптимизатор" },
  { i: 4, label: "Доказательства" },
  { i: 5, label: "Экономика" },
  { i: 7, label: "Материалы" },
];

export default function App() {
  const [sheet, setSheet] = useState(0);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [checksGeo, setChecksGeo] = useState<any>(null);
  const [opt, setOpt] = useState<OptimizeResult | null>(null);
  const [validation, setValidation] = useState<any>(null);
  const [scenarios, setScenarios] = useState<any>(null);
  const [series, setSeries] = useState<any>(null);
  const [year, setYear] = useState(2025);
  const [selected, setSelected] = useState<number | null>(null);
  const [params, setParams] = useState({ deficit: 0.15, ky: 1.1, intermittent: false });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .meta()
      .then((m) => {
        if (!alive) return;
        setMeta(m);
        setYear(m.years[m.years.length - 1]);
        return api.checks().then((g) => alive && setChecksGeo(g));
      })
      .catch((e) => alive && setError(String(e.message ?? e)));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!meta) return;
    let alive = true;
    api
      .validation(year)
      .then((v) => alive && setValidation(v))
      .catch(() => {});
    api
      .scenarios(year, params.intermittent)
      .then((s) => alive && setScenarios(s))
      .catch(() => {});
    api
      .series()
      .then((s) => alive && setSeries(s))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [meta, year, params.intermittent]);

  useEffect(() => {
    if (!meta) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setBusy(true);
      api
        .optimize(year, params.deficit, params.ky, params.intermittent)
        .then((r) => {
          setOpt(r);
          setError(null);
        })
        .catch((e) => setError(String(e.message ?? e)))
        .finally(() => setBusy(false));
    }, 260);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [meta, year, params]);

  const go = useCallback((i: number) => {
    setSheet(i);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const ctx: Ctx = {
    meta,
    checksGeo,
    opt,
    validation,
    scenarios,
    montecarlo: null,
    series,
    year,
    selected,
    setSelected,
    params,
    setParams,
    go,
  };

  return (
    <div className="app">
      <div className="sheet">
        <div className="topbar no-print">
          <div>
            <div className="wordmark">
              АКВА<span>ЧЕК</span>
            </div>
            <div className="mono" style={{ marginTop: 4 }}>
              {SHEETS[sheet].title} · {meta ? `${meta.checks.count} чеков · ${meta.checks.total_ha.toFixed(0)} га` : ""}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--s3)", flexWrap: "wrap" }}>
            {meta && meta.years.length > 1 ? (
              <div className="btn-row">
                {meta.years.slice(-4).map((y) => (
                  <button key={y} className="btn" data-active={y === year} onClick={() => setYear(y)}>
                    {y}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="sheet-nav">
              {SHEETS.map((s, i) => (
                <button key={s.key} data-active={i === sheet} onClick={() => go(i)}>
                  {String(i).padStart(2, "0")} {s.key}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && !meta ? (
          <div className="pending" style={{ padding: "var(--s5)", lineHeight: 1.7 }}>
            Данные загружаются пайплайном: снимки Sentinel-2, ряд ERA5 и контуры чеков ещё не готовы.
            <br />
            Здесь не будет ни одного выдуманного числа — как только пайплайн завершится, страница
            перезагрузится и покажет реальный расчёт.
            <br />
            <span className="src">состояние: {error}</span>
          </div>
        ) : (
          <>
            {sheet === 0 ? <Sheet0 ctx={ctx} /> : null}
            {sheet === 1 ? <Sheet1 ctx={ctx} /> : null}
            {sheet === 2 ? <Sheet2 ctx={ctx} /> : null}
            {sheet === 3 ? <Sheet3 ctx={ctx} /> : null}
            {sheet === 4 ? <Sheet4 ctx={ctx} /> : null}
            {sheet === 5 ? <Sheet5 ctx={ctx} /> : null}
            {sheet === 6 ? <Sheet6 /> : null}
            {sheet === 7 ? <Sheet7 ctx={ctx} /> : null}
            {busy ? (
              <div className="src" style={{ marginTop: "var(--s3)" }}>
                пересчёт…
              </div>
            ) : null}
          </>
        )}

        <div className="sheet-foot no-print">
          <div className="foot-note">
            Данные: Sentinel-2 (Copernicus/ESA), ERA5 (ECMWF), Copernicus DEM GLO-30. Расчёт: FAO-56, FAO-33,
            задача ЛП на решателе CBC. Контакты и персональные данные в этой версии не публикуются.
          </div>
          <Stamp title={SHEETS[sheet].key} sheet={`${String(sheet + 1).padStart(2, "0")} / 08`} />
        </div>
      </div>

      <div className="bottomnav no-print">
        {MOBILE.map((m) => (
          <button key={m.i} data-active={sheet === m.i} onClick={() => go(m.i)}>
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}
