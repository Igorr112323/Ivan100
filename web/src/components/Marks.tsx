/* Пометки «от руки»: вектор с намеренно неровной линией.
   Никаких рукописных шрифтов — только обводка, стрелка, круг, подчёркивание.
   Линии проведены с лёгким дрожанием и выходом за конец, как карандашом на кальке. */

type MarkProps = { className?: string; color?: string };

export function Arrow({ className, color = "#b4341f" }: MarkProps) {
  return (
    <svg className={className} width="86" height="42" viewBox="0 0 86 42" fill="none" aria-hidden="true">
      <path
        d="M3 6 C 22 3.5, 44 9, 58 20.5 C 66 27.5, 71 33, 78 34.5"
        stroke={color}
        strokeWidth="1.4"
        strokeLinecap="square"
      />
      <path d="M78 34.5 L 66.5 32.5 M78 34.5 L 74.5 24" stroke={color} strokeWidth="1.4" strokeLinecap="square" />
    </svg>
  );
}

export function CircleMark({ className, color = "#b4341f" }: MarkProps) {
  return (
    <svg className={className} width="120" height="66" viewBox="0 0 120 66" fill="none" aria-hidden="true">
      <path
        d="M61 5 C 92 3, 114 20, 112 39 C 110 58, 84 63, 55 61 C 27 59, 7 50, 9 32 C 11 15, 33 7, 63 7.5"
        stroke={color}
        strokeWidth="1.3"
        strokeLinecap="square"
      />
    </svg>
  );
}

export function Underline({ className, color = "#b4341f", width = 190 }: MarkProps & { width?: number }) {
  return (
    <svg className={className} width={width} height="10" viewBox={`0 0 ${width} 10`} fill="none" aria-hidden="true">
      <path
        d={`M1 6.5 C ${width * 0.3} 3.5, ${width * 0.7} 8, ${width - 2} 4.5`}
        stroke={color}
        strokeWidth="1.4"
        strokeLinecap="square"
      />
    </svg>
  );
}

export function Tick({ className, color = "#1b1f24" }: MarkProps) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M1.5 7.5 L 5.4 11 L 12.5 2.6" stroke={color} strokeWidth="1.6" strokeLinecap="square" />
    </svg>
  );
}

export function Cross({ className, color = "#c14b2a" }: MarkProps) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M2 2.6 L 11.6 11.8 M11.8 2.4 L 2.4 11.6" stroke={color} strokeWidth="1.5" strokeLinecap="square" />
    </svg>
  );
}

/* Условные обозначения — как на листе карты мелиорации */
export const CONDITIONAL_SIGNS: { label: string; color: string; note: string }[] = [
  { label: "Затопление в норме", color: "#2e6e7e", note: "срез водоподачи 0–3 %" },
  { label: "Умеренное урезание", color: "#6e9a8d", note: "срез 3–12 %" },
  { label: "Существенное урезание", color: "#c8a24a", note: "срез 12–25 %" },
  { label: "Чек подсушивается", color: "#c14b2a", note: "срез более 25 %" },
];

export function stateColor(cutPct: number): string {
  if (cutPct <= 3) return "#2e6e7e";
  if (cutPct <= 12) return "#6e9a8d";
  if (cutPct <= 25) return "#c8a24a";
  return "#c14b2a";
}
