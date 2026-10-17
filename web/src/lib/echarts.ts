/** The one place ECharts is imported from.
 *
 *  `import * as echarts from 'echarts'` pulls the entire library - every chart
 *  type, the maps extension, the SVG and GL renderers - which measured 1.53MB of
 *  JavaScript in the first build, 515kB gzipped, against a first-paint target of
 *  under two seconds. Registering the pieces actually needed cuts that by an
 *  order of magnitude.
 *
 *  This module exists so that fix is structural rather than a thing to remember:
 *  every component imports `echarts` from HERE, so there is no second path by
 *  which the full bundle can reappear. A file that imports from `'echarts'`
 *  directly is a bug, and the build note below says what to look for.
 *
 *  To add a chart type, add it to the `use([...])` list below. Nothing else has
 *  to change.
 */
import * as echarts from 'echarts/core'

import {
  BarChart,
  CandlestickChart,
  GaugeChart,
  HeatmapChart,
  LineChart,
  PieChart,
  RadarChart,
  ScatterChart,
  TreemapChart,
} from 'echarts/charts'

import {
  AriaComponent,
  /* The calendar is a coordinate system, not a chart type: the calendar heatmap
   * is CalendarComponent supplying the axes and HeatmapChart or ScatterChart
   * supplying the marks. Importing a `CalendarChart` does not exist. */
  CalendarComponent,
  DataZoomComponent,
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'

import { LabelLayout } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'

import { THEME_NAME, echartsTheme } from './echartsTheme'

/* Registered for what docs/UI-BLUEPRINT.md §6.1 (Home) actually draws:
 * the Time Machine line, the sector race bars, the market-mood gauge, the
 * sector treemap, the calendar heatmap, and the daily return scatter on the
 * day panel.
 *
 * ECharts is not lazy about size: registering all twenty-odd types the
 * blueprint mentions in §8 produced a 1.33MB main chunk, against a first-paint
 * target of under two seconds. Each later phase adds the one or two types its
 * page needs - graph and sankey in U4, candlestick in U5 - which keeps the
 * first load paying only for what the homepage draws.
 *
 * If a chart silently fails to render, its type is missing from this list. That
 * is the intended failure mode: an empty plot with a console error, rather than
 * a megabyte of unused library. */
echarts.use([
  BarChart,
  CandlestickChart,
  GaugeChart,
  HeatmapChart,
  LineChart,
  PieChart,
  RadarChart,
  ScatterChart,
  TreemapChart,
  AriaComponent,
  CalendarComponent,
  DataZoomComponent,
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
  LabelLayout,
  CanvasRenderer,
])

echarts.registerTheme(THEME_NAME, echartsTheme)

export { echarts, THEME_NAME }
export default echarts
