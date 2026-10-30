const BASE = "";

export const num = (v: number, digits = 0) =>
  new Intl.NumberFormat("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(
    Number.isFinite(v) ? v : 0
  );

export const numSpaced = (v: number, digits = 1) => num(v, digits).replace(/ /g, " ");

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal });
  if (!res.ok) {
    throw new Error(`${res.status} ${path}`);
  }
  return (await res.json()) as T;
}

export interface Meta {
  project: { name: string; full: string; polygon: string };
  years: number[];
  checks: {
    count: number;
    total_ha: number;
    min_ha: number;
    max_ha: number;
    mean_ha: number;
    mean_elev_m: number;
    elev_span_m: number;
    digitised: Record<string, unknown>;
  };
  model: {
    percolation_base_mm_day: number;
    kf: number;
    kpd_base: number;
    kpd_slope: number;
    initial_mm: number;
    target_norm_m3_ha: number;
    calibrated_norm_m3_ha: number;
    season_days: number;
    season_start: string;
    season_end: string;
    phases: { days: number; kc: number; weight: number }[];
  };
  imagery: { image: string; bounds: [[number, number], [number, number]]; date: string; scene: string; attribution: string } | null;
  scenes: { catalogue: any[]; saved: any[] };
  weather_manifest: any;
}

export interface OptimizeResult {
  params: { year: number; deficit: number; ky: number; intermittent: boolean };
  timing_s: number;
  solver: string;
  areas_ha: number;
  yield_tons: { full: number; uniform: number; fallowing: number; optimal: number };
  gain_pct: { vs_uniform: number; vs_fallowing: number; uniform_vs_full: number };
  water_m3: { full: number; uniform: number; optimal: number; limit: number; limit_shortage: number };
  water_saved_pct: number;
  fallowed_checks: number[];
  per_check: { id: number; gross_m3: number; deficit_pct: number; yield_frac: number }[];
  vedomost: {
    id: number;
    area_ha: number;
    elev_m: number;
    kpd: number;
    season_m3: number;
    norm_full_m3: number;
    cut_pct: number;
    yield_frac: number;
    decades: { decade: number; start: string; m3: number; l_s: number }[];
  }[];
  economics: any;
  economics_fallowing: any;
  norms_m3_ha: { mean: number };
}

export const api = {
  meta: (signal?: AbortSignal) => get<Meta>("/api/meta", signal),
  checks: (signal?: AbortSignal) => get<any>("/api/checks", signal),
  weather: (year: number) => get<{ year: number; days: { date: string; et0: number; et0_ref: number; precip: number }[] }>(`/api/weather?year=${year}`),
  validation: (year: number) => get<{
    year: number;
    days: number;
    ours_mm: number;
    reference_mm: number;
    difference_pct: number;
    rmse_mm_day: number;
    bias_mm_day: number;
    series: { date: string; ours: number; ref: number }[];
  }>(`/api/validation?year=${year}`),
  optimize: (year: number, deficit: number, ky: number, intermittent: boolean) =>
    get<OptimizeResult>(`/api/optimize?year=${year}&deficit=${deficit}&ky=${ky}&intermittent=${intermittent ? 1 : 0}`),
  scenarios: (year: number, intermittent: boolean) =>
    get<{ rows: any[]; year: number; intermittent: boolean }>(`/api/scenarios?year=${year}&intermittent=${intermittent ? 1 : 0}`),
  montecarlo: (year: number, deficit: number, ky: number, sigma: number, runs: number) =>
    get<any>(`/api/montecarlo?year=${year}&deficit=${deficit}&ky=${ky}&sigma=${sigma}&runs=${runs}`),
  series: () => get<any>("/api/series?limit=40"),
};

export const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

export const fmtDate = (iso: string) => {
  const [y, m, d] = iso.split("-");
  return `${d} ${MONTHS[+m - 1]} ${y}`;
};

export const fmtDateShort = (iso: string) => {
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
};
