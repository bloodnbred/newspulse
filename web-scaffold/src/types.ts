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

/* -------------------------------------------------------------------------
 * Stock 360 and Event Lab
 * ---------------------------------------------------------------------- */

/** One session of one stock. `mentions` is joined on
 *  `effective_trading_date_key`, so a Sunday headline lands on the Monday bar
 *  rather than on a gap where there is no price. */
export interface StockSession {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  return_pct: number;
  volatility_20d: number;
  mentions: number;
  nifty_close: number;
  /** Both series start at 100 on the window's first session, computed in SQL. */
  stock_rebased: number;
  nifty_rebased: number;
}

export interface RadarAxis {
  key: string;
  label: string;
  description: string;
  value: number | null;
  cluster_mean: number | null;
  /** Bounds of this axis across the whole set, so each arm of the radar can be
   *  scaled on its own. One shared min/max across six axes of different units
   *  collapses four of them onto the centre. */
  pop_min: number | null;
  pop_max: number | null;
  /** Share of the set this stock sits above on this axis. */
  percentile: number | null;
}

export interface EventReaction {
  event_key: number;
  label: string;
  type: string;
  date: string;
  car_0_5: number;
  car_0_1: number;
  beta: number;
  est_days: number;
}

export interface Stock360 {
  profile: {
    symbol: string;
    company_name: string;
    sector: string | null;
    sector_key: number | null;
    isin: string | null;
    is_survivor: boolean;
    price_source: string | null;
    first_trade_date: string | null;
    last_trade_date: string | null;
  };
  last: StockSession | null;
  /** Rupees, not percent. The name says so because the first version called it
   *  `change_pct` and the header then printed a price difference as a change. */
  change_abs: number | null;
  series: StockSession[];
  cluster: { cluster_id: number | null; axes: RadarAxis[] };
  peers: { symbol: string; sector: string | null; beta: number; ann_volatility: number }[];
  reactions: EventReaction[];
  totals: { sessions: number; mentions: number; outliers_excluded: number };
}

export interface StockHeadline {
  headline_id: number;
  publish_date: string;
  topic: string;
  headline_text: string;
  sentiment_compound: number;
  sensational_score: number;
  is_sensational: boolean;
  is_risk_signal: boolean;
  matched_alias: string;
  is_ambiguous_alias: boolean;
}

export interface EventSummary {
  event_key: number;
  label: string;
  type: string;
  date: string;
  stocks_studied: number;
  stocks_excluded: number;
}

export interface SectorReaction {
  sector: string;
  n_stocks: number;
  mean_car_0_5: number;
  mean_car_0_1: number;
  mean_car_m1_5: number;
  mean_beta: number;
}

export interface StockReaction {
  symbol: string;
  sector: string;
  car_0_5: number;
  car_0_1: number;
  beta: number;
  r_squared: number;
  est_days: number;
}

export interface EventDetail {
  event: {
    event_key: number;
    label: string;
    type: string;
    date: string;
    session: string | null;
  };
  by_sector: SectorReaction[];
  /** The whole valid set sorted by CAR, so the leaderboard can show that most
   *  stocks did not move - not only the extremes. */
  by_stock: StockReaction[];
  excluded: { symbol: string; reason: string; est_days: number }[];
  news_around: { offset: number; date: string; news: number }[];
  headlines: {
    headline_id: number;
    publish_date: string;
    topic: string;
    headline_text: string;
    sensational_score: number;
    is_market_report: boolean;
  }[];
  /** Events within four days: two events that close cannot be separated in a
   *  five-day window, and the page has to say so rather than imply otherwise. */
  nearby_events: { event_key: number; label: string; date: string; days_apart: number }[];
}
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
