// Shapes returned by the /ui/* endpoints. Keep in sync with docs/UI-API-CONTRACT.md.
//
// The union lists the concepts the contract names, but it is NOT closed. The
// server emits richer labels than the pack anticipated - `dwm/inference/insights.py`
// currently produces all six of these, none of which are in the list:
//
//   'z-score surprise detection'   'event study, market model'
//   'drill-across, rank correlation'  'cube, sector-month'
//   'clustering, PCA'               'classification, chronological hold-out'
//
// Narrowing the server's wording to fit a type would lose information the
// concept chip exists to show, so the union is widened instead. The `(string & {})`
// tail keeps editor autocomplete for the known list while accepting what the
// server really sends - it is not the same as plain `string`.
export type KnownConcept =
  | 'OLAP cube' | 'Roll-up / drill-down' | 'Drill-across' | 'Event study' | 'Clustering'
  | 'Association rules' | 'Classification' | 'ETL' | 'Fact constellation' | 'Text mining'
  | 'Surprise detection' | 'Cube';

export type Concept = KnownConcept | (string & {});

export interface Meta { concept: Concept; sql?: string; ms: number; rows: number; note?: string; mock?: boolean }
export interface Envelope<T> { data: T; meta: Meta }

export interface Filters {
  from: string;            // ISO date, '' = start of window
  to: string;              // ISO date, '' = end of window
  sectors: string[];
  topics: string[];
  includeMarketReports: boolean;
}

export interface Kpi { id: string; label: string; value: number; decimals?: number; unit?: string; deltaPct?: number; spark: number[]; href?: string }
export interface TapeItem { label: string; value: string; change: number }
export interface SeriesPoint { date: string; newsHeat: number; nifty: number }
export interface EventMark { date: string; label: string; type: string }
export interface SectorHeat {
  /* snake_case, because this is what `/ui/home` actually returns. The pack's
     `types.ts` declared `newsCount` / `avgReturnPct` in camelCase while every
     other field in this file and every other endpoint in the project is
     snake_case, so a camelCase interface here would type-check against nothing
     and render `undefined`. The server is the source of truth. */
  sector: string;
  news_count: number;
  heat_z: number | null;
  avg_return_pct: number;
}
export interface SurpriseDay {
  sector: string;
  session: string;
  kind: 'news_driven' | 'quiet_news_move';
  news_heat_z: number;
  abs_return_z: number;
  avg_return: number;
}
/** One calendar-heat cell. `is_trading_day` matters: 32.4% of in-window
 *  headlines fall on a day the market was closed, and a calendar that cannot
 *  tell those apart shows holidays as quiet market days. */
export interface DayCell {
  date: string;
  value: number;
  day_of_week: number;
  is_trading_day: boolean;
}
/** One row of the Time Machine series, as `/ui/home` returns it. */
export interface SeriesPointFull {
  date: string;
  headlines: number;
  sentiment: number;
  sensational: number;
  nifty: number | null;
}

/* -------------------------------------------------------------------------
 * Cube Studio. These mirror `dwm/olap/cube_studio.py`; the field ids are the
 * contract between the shelf and the engine, and a typo in either place shows up
 * as an empty zone rather than an error.
 * ---------------------------------------------------------------------- */

export type CubeUnit = 'label' | 'year' | 'count' | 'ratio' | 'pct' | 'score' | 'points';

export interface CubeField {
  id: string;
  label: string;
  kind: 'dimension' | 'measure';
  /** One sentence on what this is and, where it matters, what it is not. */
  description: string;
  unit: CubeUnit;
  /** True for a band computed from a measure rather than read from a dimension. */
  derived: boolean;
  cube: string;
}

export interface CubeShelfEntry {
  id: string;
  label: string;
  grain: string;
  is_cube: boolean;
  notes: string[];
  dimensions: CubeField[];
  measures: CubeField[];
}

export interface OperationChip {
  id: string;
  label: string;
  blurb: string;
  zones: string[];
  hint: string;
}

export interface CubeSchema {
  cubes: CubeShelfEntry[];
  operations: OperationChip[];
}

/** One cell of the result. Keys are field ids, so a cell is readable only
 *  through the shelf - which is the point, because the alternative is a
 *  `Record<string, unknown>` that silently accepts a typo. */
export type CubeRow = Record<string, string | number | null>;

export interface CubeData {
  cube: string;
  columns: string[];
  rows: CubeRow[];
  /** Computed by a second statement, never by summing the cells. */
  totals: CubeRow;
  truncated: boolean;
  spec: {
    rows: string[];
    columns: string[];
    measures: string[];
    filters: Record<string, unknown[]>;
    orderBy: string | null;
    descending: boolean;
  };
}

export interface CubeHeadline {
  headline_id: number;
  publish_date: string;
  topic: string;
  headline_text: string;
  sentiment_compound: number;
  sensational_score: number;
  is_sensational: boolean;
  is_risk_signal: boolean;
  word_count: number;
}

/** The four drop zones. Named after the operation vocabulary so the page can
 *  say "Slice uses Filters" and mean something the reader can see. */
export type CubeZone = 'rows' | 'columns' | 'values' | 'filters';

export type CubeViewKind = 'table' | 'heatmap' | 'bars' | 'treemap';
export interface InsightCard {
  id: string; title: string; text: string; concept: Concept; page: string;
  evidence: Record<string, number | string>;
}
export interface HomeSummary {
  devSample: boolean;
  window: { start: string; end: string };
  kpis: Kpi[];
  mood: { score: number; label: string; percentile: number };
  main: SeriesPoint[];
  events: EventMark[];
  sectorHeat: SectorHeat[];
  surprise: SurpriseDay[];
  insights: InsightCard[];
}
