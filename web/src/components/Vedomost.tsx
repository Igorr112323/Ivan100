/* Ведомость водоподачи — выходной документ системы.
   Таблица, а не карточки: моно-цифры по разрядам, волосяные линейки, строка ИТОГО. */

import { num } from "../lib/api";
import { stateColor } from "./Marks";

export type VedomostRow = {
  id: number;
  area_ha: number;
  elev_m: number;
  kpd: number;
  season_m3: number;
  norm_full_m3: number;
  cut_pct: number;
  yield_frac: number;
  decades: { decade: number; start: string; m3: number; l_s: number }[];
};

type Props = {
  rows: VedomostRow[];
  limit: number;
  selected: number | null;
  onSelect: (id: number) => void;
  compact?: boolean;
};

export default function Vedomost({ rows, limit, selected, onSelect, compact }: Props) {
  const shown = compact ? rows.slice(0, 14) : rows;
  const total = rows.reduce((a, r) => a + r.season_m3, 0);
  const area = rows.reduce((a, r) => a + r.area_ha, 0);

  return (
    <div>
      <div className="print-only" style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>Ведомость водоподачи на сезон</div>
        <div className="mono" style={{ marginTop: 4 }}>
          АКВ-2027-КРА · лимит {num(limit, 0)} м³ · чеков {rows.length}
        </div>
      </div>
      <div className="scroll-x">
        <table className="vedomost">
          <thead>
            <tr>
              <th>Чек</th>
              <th>Площадь, га</th>
              <th>Отм., м</th>
              <th>КПД</th>
              <th>Норма, м³</th>
              <th>Назначено, м³</th>
              <th>Срез, %</th>
              <th>Урожай, доля</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr
                key={r.id}
                data-selected={selected === r.id}
                onClick={() => onSelect(r.id)}
                style={{ cursor: "pointer" }}
              >
                <td>
                  <span
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      background: stateColor(r.cut_pct),
                      marginRight: 7,
                      border: "1px solid rgba(27,31,36,.35)",
                    }}
                  />
                  {r.id}
                </td>
                <td>{num(r.area_ha, 1)}</td>
                <td>{num(r.elev_m, 2)}</td>
                <td>{num(r.kpd, 2)}</td>
                <td>{num(r.norm_full_m3, 0)}</td>
                <td>{num(r.season_m3, 0)}</td>
                <td style={{ color: r.cut_pct > 25 ? "var(--dry)" : "inherit" }}>{num(r.cut_pct, 1)}</td>
                <td>{num(r.yield_frac, 3)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td>Итого</td>
              <td>{num(area, 1)}</td>
              <td />
              <td />
              <td>{num(rows.reduce((a, r) => a + r.norm_full_m3, 0), 0)}</td>
              <td>{num(total, 0)}</td>
              <td />
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
      {compact && rows.length > 14 ? (
        <div className="src" style={{ marginTop: 8 }}>
          показаны первые 14 чеков из {rows.length} · полный список — на листе 3
        </div>
      ) : null}
    </div>
  );
}
