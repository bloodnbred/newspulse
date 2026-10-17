// Colour-blind-safe categorical palette for sectors, plus a shared ECharts theme.
//
// The palette deliberately avoids the five SEMANTIC hues, so a sector is never
// mistaken for a series that means something:
//
//   --news  #14B8A6 teal     --price #F59E0B amber    --mining #8B5CF6 violet
//   --up    #22C55E green    --down  #EF4444 red
//
// The pack's original first three entries were exactly #14B8A6, #F59E0B and
// #8B5CF6, which handed sector one the colour that means "news" everywhere else
// on the page and sector two the colour that means "price" - so a treemap tile
// would contradict the series sitting directly above it. Every hue below is
// desaturated or far enough in lightness from those five to stay readable as
// "just a category".
export const SECTOR_COLORS = [
  '#6BAED6', // steel blue
  '#2171B5', // deep blue
  '#9ECAE1', // pale blue
  '#6D597A', // dark mauve
  '#B56576', // dusty rose
  '#8C6D31', // bronze
  '#4D4D4D', // charcoal
  '#A6761D', // ochre  (browner and far darker than --price)
  '#5B9E9E', // muted teal-grey (low chroma against --news)
  '#C4B7A6', // warm stone
];

/** The semantic hues, named, so a chart can colour a series by meaning. */
export const SEMANTIC = {
  news: '#14B8A6',
  price: '#F59E0B',
  mining: '#8B5CF6',
  up: '#22C55E',
  down: '#EF4444',
} as const;
export const THEME_NAME = 'newspulse';

export const echartsTheme = {
  color: SECTOR_COLORS,
  backgroundColor: 'transparent',
  textStyle: { color: '#E6EDF7', fontFamily: "'Inter Variable', Inter, system-ui, sans-serif" },
  legend: { textStyle: { color: '#93A4BF' } },
  categoryAxis: {
    axisLine: { lineStyle: { color: 'rgba(148,163,184,.3)' } },
    axisLabel: { color: '#93A4BF' }, splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false }, axisLabel: { color: '#93A4BF' },
    splitLine: { lineStyle: { color: 'rgba(148,163,184,.12)' } },
  },
  tooltip: {
    backgroundColor: 'rgba(11,18,32,.92)', borderColor: 'rgba(148,163,184,.25)',
    textStyle: { color: '#E6EDF7' },
  },
  animationDuration: 600,
  animationEasing: 'cubicOut',
};

/** Call once at startup: registerNewsPulseTheme(echarts) with `import * as echarts from 'echarts'`. */
export function registerNewsPulseTheme(e: { registerTheme: (name: string, theme: object) => void }): void {
  e.registerTheme(THEME_NAME, echartsTheme);
}
