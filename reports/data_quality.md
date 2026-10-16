# Data quality

*Generated 2026-10-16 14:40 UTC by `python -m dwm build`. Every figure below is
read from the warehouse at write time; none is typed in by hand.*

## News corpus

- Analysis window: **2015-06-30 to 2020-06-30**, derived from the last
  publish date in the corpus, not configured.
- Headlines: **3,154,916** total, of which
  **1,113,427** fall inside the window.

## Stock prices

- `fact_stock_daily`: **61,523** rows across
  **47** symbols; 61,518 rows survive
  the clean view.
- Returns are **percentage points**, computed from **adj_close**.
  On this source `close` is split-adjusted but not dividend-adjusted, so using
  `close` would understate total return.
- `turnover` is **derived** as close x volume. The price source publishes no
  turnover, so any figure derived from it is an estimate.

### Flagged moves

`is_outlier` flags `abs(return_pct) > 25.0`. That fired
**5 times**.

Distribution of daily moves:

| Move | Rows |
|---|---:|
| under 1% | 30,531 |
| 1-2% | 16,769 |
| 2-5% | 12,326 |
| 5-10% | 1,617 |
| 10-25% | 228 |
| over 25% (flagged) | 5 |

Inspecting them: they are real market events, not data errors. The rule was
written to catch unadjusted splits and bonus issues, but because returns are
computed from adjusted closes there are no split artefacts left for it to find.
It now flags genuine large moves instead. That is still useful, but it is a
different job from the one its name suggests, and the flagged rows are kept in
the table and excluded from statistics and models.

Largest flagged moves:

| Symbol | Date | Return % | Close | Adj close |
|---|---|---:|---:|---:|
| ADANIENT | 2015-06-03 | -38.75 | 59.80 | 56.32 |
| AXISBANK | 2020-03-23 | -27.91 | 308.65 | 307.13 |
| SBIN | 2017-10-25 | 27.69 | 324.90 | 294.48 |
| ADANIENT | 2019-05-20 | 27.37 | 151.95 | 150.00 |
| BAJAJFINSV | 2020-03-23 | -25.86 | 462.11 | 460.67 |

### Coverage gaps

- **3** of the index's symbols have no usable price history inside
  the window and still carry a `dim_stock` row, so a 50-stock index cannot
  silently become a 47-stock one:

  `ETERNAL, JIOFIN, MAXHEALTH`

  Each of these listed after the analysis window closed, so there is nothing
  to fetch for the period studied. They are listed in `etl_audit` as
  unmatched rather than dropped.

- **7** symbols are flagged `is_survivor = FALSE` because they
  first trade after the window starts.
  `BSE, ETERNAL, HDFCLIFE, INDIGO, JIOFIN, MAXHEALTH, SBILIFE`

### Unusual trading days

These are weekend dates the exchange **actually traded**. They must not be
treated as closures:

`2016-10-30, 2019-10-27, 2020-02-01`

Two are Diwali Muhurat sessions; 2020-02-01 is the Union Budget Saturday.

## Survivorship bias

The constituent list used here is the **current** Nifty 50 list, applied to a
historical window. Companies that were constituents in 2015-2020 and have since
been removed from the index are simply absent from this analysis.

This biases any comparison of sector or index performance across the window: the
surviving constituents are, by construction, the ones that stayed listed. Any
chart that aggregates across stocks carries this caveat.

