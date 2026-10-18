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
/* -------------------------------------------------------------------------
 * Mining Lab
 * ---------------------------------------------------------------------- */

export interface ClusterPoint {
  symbol: string;
  sector: string | null;
  cluster_id: number | null;
  ann_volatility: number;
  beta: number;
  max_drawdown: number;
  mentions_per_1000_days: number;
  mention_sentiment: number;
  news_sensitivity: number;
  pc1: number;
  pc2: number;
  n_sessions: number;
}

/** The name is derived from the cluster's rank among its peers, not chosen by
 *  anyone. The `most_volatile` family says which end it sits at, so the page can
 *  label the scatter without inventing a character for it. */
export interface ClusterProfile {
  cluster_id: number;
  name: string;
  auto_named: boolean;
  n_stocks: number;
  median_volatility: number;
  median_beta: number;
  median_drawdown: number;
  median_mentions: number;
  median_sentiment: number;
  median_news_sensitivity: number;
  sectors: string;
  most_volatile: boolean;
  least_volatile: boolean;
  most_market_linked: boolean;
}

export interface ClusterData {
  points: ClusterPoint[];
  profiles: ClusterProfile[];
  silhouette: number | null;
  silhouette_is_strong: boolean | null;
  /** One cluster holds most of the sample, which is what makes a high silhouette
   *  mean less than the number looks like. */
  dominant_cluster_share: number | null;
  concentration_note: string | null;
  chosen_k: number | null;
  k_note: string | null;
  cluster_note: string | null;
}

export interface MarketRule {
  rule_id: number;
  sector_name: string | null;
  antecedents: string;
  consequents: string;
  support: number;
  confidence: number;
  lift: number;
  support_count: number;
  n_transactions: number;
}

export interface RuleData {
  rules: MarketRule[];
  matched: number;
  total: number;
  thresholds: { min_support: number; min_confidence: number; min_lift: number };
}

export interface DirectionRow {
  model: string;
  split: string;
  n: number;
  accuracy: number;
  balanced_accuracy: number;
  f1: number;
  baseline_majority: number;
  baseline_yesterday: number | null;
  p_vs_baseline: number | null;
  precision_up: number;
  recall_up: number;
  notes: string | null;
  /** Computed from the numbers beside it, so it cannot drift from them. */
  beats_baseline: boolean;
}

export interface ClassifierModel {
  metrics: {
    accuracy: number;
    majority_baseline_accuracy: number;
    lift_over_baseline_pp: number;
    beats_baseline: boolean;
    per_class: Record<string, { precision: number; recall: number; f1: number; support: number }>;
    confusion_matrix: { labels: string[]; rows_are_actual: string[]; matrix: number[][] };
  };
  top_terms: Record<string, { fake: number; real: number }> | unknown[];
  classification_report: unknown;
}

export interface ClassifierPayload {
  ran: boolean;
  labelled_statements: number;
  class_counts: Record<string, number>;
  majority_baseline_accuracy: number;
  test_count: number;
  chosen_on_train_cv: string;
  models: Record<string, ClassifierModel>;
  honesty_note: string;
  upper_bound_caveat: string;
}

export interface ModelData {
  direction: DirectionRow[];
  classifier: ClassifierPayload | null;
  /** The fitted tree's rule text is not persisted between runs, so the page says
   *  so and names the command that prints it. */
  tree_rules_available: boolean;
}

export interface CoMention {
  a: number;
  b: number;
  a_symbol: string;
  b_symbol: string;
  a_sector: string | null;
  b_sector: string | null;
  together: number;
  first_seen: string;
  last_seen: string;
}

export interface CoMentionData {
  nodes: { stock_key: number; symbol: string; sector: string | null; mentions: number }[];
  links: CoMention[];
  total_pairs: number;
}

/* -------------------------------------------------------------------------
 * Headline Lab
 * ---------------------------------------------------------------------- */

export interface CheckTag {
  kind: string;
  label: string;
  href?: string;
  confidence?: number;
}

/**
 * The percentile is an OBJECT, not a number.
 *
 * The first version typed it as `number | null` and rendered `NaN` into the
 * gauge. It carries the corpus mean and standard deviation alongside the rank,
 * because a percentile of 99 is not interpretable without knowing what it is a
 * percentile *of* - and `in_words` is the sentence written for a reader, which
 * is better than one the front end assembles from the rank.
 */
export interface Percentile {
  percentile: number;
  n_corpus: number;
  corpus_mean: number;
  corpus_sd: number;
  in_words: string;
}

export interface CheckResult {
  headline: string;
  topic: { name: string | null; confidence: number | null } | null;
  /** Absent when the scorer could not rank the text against the corpus. */
  percentile?: Percentile | null;
  style: Record<string, number>;
  tags: { stocks: CheckTag[]; sectors: CheckTag[] };
  /** Keys are `date` / `text` / `similarity`, not the column names in
   *  fact_headline - `dwm.inference.checker` returns the scorer's own shape. */
  neighbours: { date: string; text: string; similarity: number }[];
  metric_name: string;
  is_verbatim_in_corpus: boolean;
  truncated: boolean;
  caveat: string;
  disclaimer: string;
}

export interface SampleSet {
  hype: { headline: string; publish_date: string; sensational_score: number; topic: string }[];
  plain: { headline: string; publish_date: string; sensational_score: number; topic: string }[];
}

export interface SchemaColumn {
  name: string;
  type: string;
}

/** One row per table, read from DuckDB's own catalogue. */
export interface SchemaTable {
  name: string;
  column_count: number;
  rows: number;
  columns: SchemaColumn[];
}

export interface EtlStep {
  run_id: string;
  dataset_code: string;
  step: string;
  status: string;
  rows_read: number | null;
  rows_loaded: number | null;
  rows_rejected: number | null;
  detail: string | null;
}

export interface StockHit {
  symbol: string;
  company_name: string;
  sector: string | null;
}

export interface SectorHit {
  sector_name: string;
}

export interface InsightCard {
  id: string;
  title: string;
  /** The card's whole explanation, written into `caution` by the builder
   *  because it is a caveat - what the figure does NOT show. There is no `text`
   *  field, and the first version rendered `undefined` in the card body. */
  caution: string;
  concept: string;
  page: string;
  /** Values are not all scalars: `surprise_days` carries a list of rows and a
   *  thresholds object, so anything reading this has to handle all three. */
  evidence: Record<string, unknown>;
}

export interface InsightsPayload {
  cards: InsightCard[];
  /** Anything the builder declined to state, and why. */
  withheld: { id?: string; reason: string }[];
  count: number;
  of: number;
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
