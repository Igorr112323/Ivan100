/* Графики: только SVG, волосяные линии, моно-цифры, подписи осей с единицами.
   Никаких библиотек — так сохраняется характер чертежа и полный контроль над видом. */

import { num } from "../lib/api";

const INK = "#1b1f24";
const LINE = "#c9c2b4";
const SOFT = "#dbd4c5";
const WATER = "#2e6e7e";
const SEAL = "#b4341f";
const RIPE = "#c8a24a";

export function ETChart({
  series,
  phases,
}: {
  series: { date: string; et0: number; et0_ref: number }[];
  phases: { days: number; kc: number; weight: number }[];
}) {
  if (!series.length) return null;
  const W = 760;
  const H = 250;
  const padL = 34;
  const padB = 22;
  const padT = 12;
  const maxY = Math.max(9, Math.ceil(Math.max(...series.map((d) => Math.max(d.et0, d.et0_ref))) / 2) * 2);
  const x = (i: number) => padL + (i / (series.length - 1)) * (W - padL - 8);
  const y = (v: number) => padT + (1 - v / maxY) * (H - padT - padB);

  const path = (key: "et0" | "et0_ref") =>
    series.map((d, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(d[key]).toFixed(1)}`).join(" ");

  let cursor = 0;
  const bands = phases.map((p, idx) => {
    const from = cursor;
    cursor += p.days;
    return { from, to: cursor, kc: p.kc, idx };
  });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Суточная эвапотранспирация">
      <g fontFamily="IBM Plex Mono, monospace" fontSize="9" fill="#8a8880">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const v = maxY * t;
          return (
            <g key={t}>
              <line x1={padL} x2={W - 8} y1={y(v)} y2={y(v)} stroke={v === 0 ? INK : SOFT} strokeWidth={v === 0 ? 1 : 0.6} />
              <text x={padL - 6} y={y(v) + 3} textAnchor="end">
                {num(v, 0)}
              </text>
            </g>
          );
        })}
        <text x={padL - 6} y={padT - 3} textAnchor="end">
          мм/сут
        </text>
      </g>

      {bands.map((b) => (
        <g key={b.idx}>
          <rect
            x={x(b.from)}
            y={padT}
            width={x(Math.min(b.to, series.length - 1)) - x(b.from)}
            height={H - padT - padB}
            fill={b.idx === 1 ? "rgba(194,75,42,0.05)" : "rgba(108,122,58,0.05)"}
          />
          <line x1={x(b.from)} x2={x(b.from)} y1={padT} y2={H - padB} stroke={LINE} strokeWidth="0.6" strokeDasharray="2 3" />
          <text
            x={x(b.from) + 5}
            y={padT + 11}
            fontFamily="IBM Plex Mono, monospace"
            fontSize="8.5"
            fill="#5c636b"
            letterSpacing="0.06em"
          >
            Kc {b.kc.toFixed(2)}
          </text>
        </g>
      ))}

      <path d={path("et0_ref")} fill="none" stroke={LINE} strokeWidth="1.2" strokeDasharray="4 3" />
      <path d={path("et0")} fill="none" stroke={WATER} strokeWidth="1.4" />

      <g fontFamily="IBM Plex Mono, monospace" fontSize="8.5" fill="#5c636b">
        <rect x={W - 196} y={H - 18} width="8" height="2" fill={WATER} />
        <text x={W - 184} y={H - 14}>
          наш расчёт FAO-56
        </text>
        <rect x={W - 88} y={H - 18} width="8" height="2" fill={LINE} />
        <text x={W - 76} y={H - 14}>
          эталон ERA5
        </text>
      </g>

      <g fontFamily="IBM Plex Mono, monospace" fontSize="8.5" fill="#8a8880">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const i = Math.round(t * (series.length - 1));
          return (
            <text key={t} x={x(i)} y={H - 6} textAnchor={t === 0 ? "start" : t === 1 ? "end" : "middle"}>
              {series[i].date.slice(8, 10)}.{series[i].date.slice(5, 7)}
            </text>
          );
        })}
      </g>
    </svg>
  );
}

export function Histogram({
  histogram,
  mean,
  p05,
  p95,
  sigma,
}: {
  histogram: { x: number; count: number }[];
  mean: number;
  p05: number;
  p95: number;
  sigma: number;
}) {
  if (!histogram.length) return null;
  const W = 520;
  const H = 200;
  const padL = 30;
  const padB = 26;
  const maxCount = Math.max(...histogram.map((h) => h.count));
  const lo = histogram[0].x;
  const hi = histogram[histogram.length - 1].x;
  const x = (v: number) => padL + ((v - lo) / Math.max(hi - lo, 1e-9)) * (W - padL - 10);
  const bw = Math.max(3, (W - padL - 10) / histogram.length - 1.5);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Распределение эффекта">
      <g fontFamily="IBM Plex Mono, monospace" fontSize="9" fill="#8a8880">
        <line x1={padL} x2={W - 10} y1={H - padB} y2={H - padB} stroke={INK} strokeWidth="1" />
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - 10} y1={H - padB - t * (H - padB - 14)} y2={H - padB - t * (H - padB - 14)} stroke={SOFT} strokeWidth="0.6" />
            <text x={padL - 6} y={H - padB - t * (H - padB - 14) + 3} textAnchor="end">
              {num(maxCount * t, 0)}
            </text>
          </g>
        ))}
      </g>

      {histogram.map((h, i) => (
        <rect
          key={i}
          x={x(h.x) - bw / 2}
          y={H - padB - (h.count / maxCount) * (H - padB - 14)}
          width={bw}
          height={(h.count / maxCount) * (H - padB - 14)}
          fill={h.x < p05 ? RIPE : h.x > p95 ? WATER : "#5c636b"}
          opacity="0.85"
        />
      ))}

      {[
        { v: p05, label: "5 %", color: RIPE },
        { v: mean, label: "среднее", color: INK },
        { v: p95, label: "95 %", color: WATER },
      ].map((m) => (
        <g key={m.label}>
          <line x1={x(m.v)} x2={x(m.v)} y1={10} y2={H - padB} stroke={m.color} strokeWidth="1" strokeDasharray="3 2" />
          <text x={x(m.v)} y={7} textAnchor="middle" fontFamily="IBM Plex Mono, monospace" fontSize="8.5" fill={m.color}>
            {num(m.v, 2)} %
          </text>
        </g>
      ))}

      <text x={padL} y={H - 8} fontFamily="IBM Plex Mono, monospace" fontSize="8.5" fill="#8a8880">
        прирост валового сбора, %, σ = {num(sigma, 2)} м
      </text>
    </svg>
  );
}

export function CompareBars({
  rows,
  unit = "т",
}: {
  rows: { label: string; value: number; color: string; note?: string }[];
  unit?: string;
}) {
  const max = Math.max(...rows.map((r) => r.value));
  return (
    <div>
      {rows.map((r) => (
        <div key={r.label} style={{ marginBottom: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
            <span style={{ fontSize: 13.5 }}>{r.label}</span>
            <span className="num" style={{ fontSize: 15 }}>
              {num(r.value, 1)} <span style={{ fontSize: 10, color: "var(--ink-2)" }}>{unit}</span>
            </span>
          </div>
          <div style={{ height: 9, borderBottom: "1px solid var(--line)", position: "relative", marginTop: 4 }}>
            <div
              style={{
                position: "absolute",
                left: 0,
                bottom: 0,
                width: `${(r.value / max) * 100}%`,
                height: 9,
                background: r.color,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: `${(r.value / max) * 100}%`,
                bottom: -3,
                width: 1,
                height: 15,
                background: "var(--ink)",
              }}
            />
          </div>
          {r.note ? <div className="small" style={{ marginTop: 4 }}>{r.note}</div> : null}
        </div>
      ))}
    </div>
  );
}

export function SeriesSpark({ points, color = "#6c7a3a" }: { points: number[]; color?: string }) {
  if (points.length < 2) return null;
  const W = 220;
  const H = 44;
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const x = (i: number) => (i / (points.length - 1)) * (W - 2) + 1;
  const y = (v: number) => H - 3 - ((v - lo) / Math.max(hi - lo, 1e-9)) * (H - 8);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} aria-hidden="true">
      <line x1="0" x2={W} y1={H - 2} y2={H - 2} stroke={SOFT} strokeWidth="0.8" />
      <path d={points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(p).toFixed(1)}`).join(" ")} fill="none" stroke={color} strokeWidth="1.3" />
      {points.map((p, i) => (
        <circle key={i} cx={x(i)} cy={y(p)} r="1.8" fill={color} />
      ))}
    </svg>
  );
}

export { SEAL };
