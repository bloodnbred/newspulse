"""Phase V2 tests: fact_stock_daily.

The important test here is the pandas recomputation. Everything else checks a
count; that one checks the arithmetic against an independent implementation, so
a sign error or a percentage-point/fraction mix-up cannot pass.

The fixture plants a planted outlier at a known size so the flag can be shown to
fire for the right reason rather than merely "some row is flagged".
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
import pytest

from dwm.warehouse import stock_facts


def _price_csv(rows: list[tuple[str, float, float | None]]) -> str:
    """rows: (date, close, adj_close)."""
    lines = ["date,open,high,low,close,adj_close,volume"]
    for day, close, adj in rows:
        adj = close if adj is None else adj
        lines.append(f"{day},{close},{close},{close},{close},{adj},1000")
    return "\n".join(lines) + "\n"


def _series(n: int, start: float, step: float = 1.0) -> list[tuple[str, float, float | None]]:
    """n weekday-ish dates with a linear adj_close, so returns are predictable."""
    day = dt.date(2020, 6, 1)
    out: list[tuple[str, float, float | None]] = []
    value = start
    for _ in range(n):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        out.append((day.isoformat(), value, value))
        day += dt.timedelta(days=1)
        value += step
    return out


@pytest.fixture()
def con(tmp_path):
    """A warehouse with just enough of v1+v2 for the stock fact to build."""
    c = duckdb.connect()
    c.execute(
        """
        CREATE TABLE dim_dataset (dataset_key INTEGER, dataset_code VARCHAR);
        CREATE TABLE dim_stock (
            stock_key INTEGER, symbol VARCHAR, price_source VARCHAR,
            is_survivor BOOLEAN
        );
        CREATE TABLE dim_date (date_key INTEGER, full_date DATE, is_trading_day BOOLEAN);
        CREATE TABLE stg_stocks (
            symbol VARCHAR, trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, adj_close DOUBLE, volume DOUBLE
        );
        """
    )
    c.execute("INSERT INTO dim_dataset VALUES (3, 'stocks')")
    c.execute(
        "INSERT INTO dim_stock VALUES "
        "(1, 'TCS', 'yahoo_chart_api', true), (2, 'SBIN', 'yahoo_chart_api', true)"
    )
    c.executemany(
        "INSERT INTO dim_date VALUES (?, ?, true)",
        [
            (int(d.strftime("%Y%m%d")), d)
            for d in (dt.date(2020, 6, 1) + dt.timedelta(days=i) for i in range(40))
        ],
    )
    return c


def _load(con, symbol: str, rows: list[tuple[str, float, float | None]]) -> None:
    """rows: (date, close, adj_close)."""
    con.executemany(
        "INSERT INTO stg_stocks VALUES (?, CAST(? AS DATE), ?, ?, ?, ?, ?, 1000)",
        [
            (symbol, day, close, close, close, close,
             close if adj is None else adj)
            for day, close, adj in rows
        ],
    )


def test_returns_match_an_independent_pandas_recomputation(con, tmp_path) -> None:
    """Percentage points, computed from adj_close. The pack's fractions would
    be a hundred times smaller and still look plausible."""
    rows = _series(30, start=100.0, step=0.5)
    _load(con, "TCS", rows)
    stock_facts.build_stock_daily(con)

    got = con.execute(
        """
        SELECT trade_date, return_pct, next_day_return_pct, return_5d
        FROM fact_stock_daily WHERE stock_key = 1 ORDER BY trade_date
        """
    ).fetchall()

    df = pd.DataFrame([{"d": r[0], "adj": r[2] if r[2] is not None else r[1]} for r in rows])
    expect = (df["adj"] / df["adj"].shift(1) - 1.0) * 100.0

    assert got[0][1] is None, "the first row has no previous close"
    for i in range(1, len(got)):
        assert got[i][1] == pytest.approx(expect.iloc[i], abs=1e-4), f"row {i}"

    # next_day_return is the following session's return, and the last row has
    # nothing after it.
    for i in range(len(got) - 1):
        assert got[i][2] == pytest.approx(expect.iloc[i + 1], abs=1e-4)
    assert got[-1][2] is None

    # return_5d is against five sessions back, not one. With a linear series a
    # 5-day return is 5x the 1-day return, which is what makes this meaningful.
    if len(got) > 5:
        expect_5d = (df["adj"] / df["adj"].shift(5) - 1.0) * 100.0
        assert got[5][3] == pytest.approx(expect_5d.iloc[5], abs=1e-4)
        assert got[5][3] != pytest.approx(expect.iloc[5], abs=1e-4)
    assert got[0][3] is None


def test_adj_close_is_used_not_close(con) -> None:
    """close is split-adjusted but not dividend-adjusted, so a dividend-only
    series would show a return under `close` that is not a real loss."""
    rows = [("2020-06-01", 100.0, 100.0), ("2020-06-02", 100.0, 90.0)]
    _load(con, "TCS", rows)
    stock_facts.build_stock_daily(con)
    row = con.execute(
        "SELECT return_pct, return_basis FROM fact_stock_daily "
        "WHERE stock_key = 1 ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    assert row[0] == pytest.approx(-10.0, abs=1e-4)
    assert row[1] == "adj_close"


def test_first_row_of_each_symbol_has_no_return(con) -> None:
    _load(con, "TCS", _series(5, 10.0))
    _load(con, "SBIN", _series(5, 20.0))
    stock_facts.build_stock_daily(con)
    nulls = con.execute(
        "SELECT stock_key, count(*) FROM fact_stock_daily WHERE return_pct IS NULL "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert nulls == [(1, 1), (2, 1)]


def test_date_and_stock_are_unique(con) -> None:
    _load(con, "TCS", _series(10, 10.0))
    stock_facts.build_stock_daily(con)
    dupes = con.execute(
        "SELECT count(*) FROM (SELECT stock_key, trade_date FROM fact_stock_daily "
        "GROUP BY 1,2 HAVING count(*) > 1)"
    ).fetchone()[0]
    assert dupes == 0


def test_an_outlier_is_flagged_at_the_documented_threshold(con) -> None:
    """A planted +40% day. The threshold is 25.0 percentage points."""
    rows = [("2020-06-01", 100.0, 100.0), ("2020-06-02", 140.0, 140.0)]
    _load(con, "TCS", rows)
    stock_facts.build_stock_daily(con)
    flagged = con.execute(
        "SELECT return_pct, is_outlier FROM fact_stock_daily "
        "WHERE stock_key = 1 AND return_pct IS NOT NULL"
    ).fetchall()
    assert len(flagged) == 1
    assert flagged[0][0] == pytest.approx(40.0, abs=1e-4)
    assert flagged[0][1] is True
    assert stock_facts.OUTLIER_THRESHOLD_PCT == 25.0


def test_the_clean_view_excludes_every_flagged_row(con) -> None:
    rows = [("2020-06-01", 100.0, 100.0), ("2020-06-02", 140.0, 140.0)]
    _load(con, "TCS", rows)
    stock_facts.build_stock_daily(con)
    total = con.execute("SELECT count(*) FROM fact_stock_daily").fetchone()[0]
    clean = con.execute("SELECT count(*) FROM v_stock_daily_clean").fetchone()[0]
    assert clean == total - 1
    assert con.execute(
        "SELECT count(*) FROM v_stock_daily_clean WHERE is_outlier"
    ).fetchone()[0] == 0
    # Flagged rows are KEPT in the fact, not deleted.
    assert con.execute("SELECT count(*) FROM fact_stock_daily").fetchone()[0] == total


def test_volatility_needs_a_full_window_before_it_appears(con) -> None:
    rows = _series(30, start=100.0, step=1.0)
    _load(con, "TCS", rows)
    stock_facts.build_stock_daily(con)
    got = con.execute(
        "SELECT count(volatility_20d), count(*) FROM fact_stock_daily"
    ).fetchone()
    # 30 rows, the first has no return, so 20 returns exist from row 21 onward.
    assert got[0] == 30 - stock_facts.VOLATILITY_WINDOW


def test_volatility_uses_the_same_definition_as_the_market_fact(con) -> None:
    """v1 computes it as stddev_samp over 19 PRECEDING AND CURRENT, NULL until
    20 returns. A different window here would make a stock's volatility
    incomparable with the index's during drill-across."""
    rows = _series(30, start=100.0, step=1.0)
    _load(con, "TCS", rows)
    stock_facts.build_stock_daily(con)

    df = pd.DataFrame(
        {"adj": [r[2] for r in rows]}, index=pd.to_datetime([r[0] for r in rows])
    )
    rets = (df["adj"] / df["adj"].shift(1) - 1.0) * 100.0
    got = dict(
        con.execute(
            "SELECT trade_date, volatility_20d FROM fact_stock_daily "
            "WHERE stock_key = 1"
        ).fetchall()
    )
    checked = 0
    for day, value in got.items():
        if value is None:
            continue
        window = rets.loc[:day].dropna().tail(stock_facts.VOLATILITY_WINDOW)
        if len(window) < stock_facts.VOLATILITY_WINDOW:
            continue
        assert value == pytest.approx(window.std(ddof=1), abs=1e-4), str(day)
        checked += 1
    assert checked >= 5


def test_turnover_is_marked_derived(con) -> None:
    """The price source publishes no turnover. close*volume is an estimate and
    must not be allowed to look exchange-reported."""
    _load(con, "TCS", _series(3, 10.0))
    stock_facts.build_stock_daily(con)
    row = con.execute(
        "SELECT turnover, close, volume, turnover_is_derived FROM fact_stock_daily LIMIT 1"
    ).fetchone()
    assert row[2] == 1000.0
    assert row[0] == pytest.approx(row[1] * 1000.0)
    assert row[3] is True


def test_next_day_is_a_lead_and_never_a_self_reference(con) -> None:
    """A linear price series makes every 1-day return identical, which would let
    a self-reference slip through unnoticed. So this uses varying steps."""
    day = dt.date(2020, 6, 1)
    rows: list[tuple[str, float, float | None]] = []
    value = 100.0
    for step in (1.0, 5.0, 0.5, 9.0, 2.0, 7.0, 0.25, 4.0, 3.0, 6.0):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        rows.append((day.isoformat(), value, value))
        day += dt.timedelta(days=1)
        value += step
    _load(con, "TCS", rows)
    stock_facts.build_stock_daily(con)

    got = con.execute(
        "SELECT trade_date, return_pct, next_day_return_pct FROM fact_stock_daily "
        "WHERE stock_key = 1 ORDER BY trade_date"
    ).fetchall()
    assert len({round(r[1], 6) for r in got if r[1] is not None}) > 1, "fixture is not varying"

    # next_day_return_pct at row i must equal return_pct at row i+1.
    for i in range(len(got) - 1):
        assert got[i][2] == pytest.approx(got[i + 1][1], abs=1e-6), str(got[i][0])
    assert got[-1][2] is None

    # next_day_up agrees with the sign of next_day_return_pct.
    mismatch = con.execute(
        "SELECT count(*) FROM fact_stock_daily "
        "WHERE next_day_return_pct IS NOT NULL AND next_day_up IS DISTINCT FROM "
        "(next_day_return_pct > 0)"
    ).fetchone()[0]
    assert mismatch == 0


def test_rows_without_an_adj_close_are_dropped_not_zeroed(con) -> None:
    _load(con, "TCS", _series(4, 10.0))
    con.execute(
        "INSERT INTO stg_stocks VALUES ('TCS', DATE '2020-07-01', 1, 1, 1, 1, NULL, 10)"
    )
    stock_facts.build_stock_daily(con)
    assert con.execute(
        "SELECT count(*) FROM fact_stock_daily WHERE trade_date = DATE '2020-07-01'"
    ).fetchone()[0] == 0


def test_build_refuses_without_the_staging_table() -> None:
    c = duckdb.connect()
    with pytest.raises(FileNotFoundError, match="stg_stocks"):
        stock_facts.build_stock_daily(c)


def test_build_refuses_when_dim_dataset_lacks_stocks(con) -> None:
    con.execute("DELETE FROM dim_dataset")
    _load(con, "TCS", _series(3, 10.0))
    with pytest.raises(RuntimeError, match="no 'stocks' row"):
        stock_facts.build_stock_daily(con)


def test_rebuilding_is_idempotent(con) -> None:
    _load(con, "TCS", _series(8, 10.0))
    first = stock_facts.build_stock_daily(con)["rows"]
    second = stock_facts.build_stock_daily(con)["rows"]
    assert first == second


def test_check_reports_a_missing_table() -> None:
    assert stock_facts.check(duckdb.connect()) == {"present": False}


def test_check_passes_on_a_built_fact(con) -> None:
    _load(con, "TCS", _series(25, 100.0, 2.0))
    stock_facts.build_stock_daily(con)
    result = stock_facts.check(con)
    assert result["present"] is True
    assert result["duplicate_keys"] == 0
    assert result["orphan_stock_keys"] == 0
    assert result["clean_view_excludes_every_outlier"] is True


def test_coverage_reports_symbols_without_prices(con) -> None:
    con.execute("INSERT INTO dim_stock VALUES (3, 'GHOST', NULL, false)")
    _load(con, "TCS", _series(5, 10.0))
    stock_facts.build_stock_daily(con)
    cov = stock_facts.coverage(con)
    assert cov["symbols_staged"] == 1
    assert cov["symbols_in_fact"] == 1
    assert "GHOST" in cov["symbols_without_prices"]


def test_outlier_rows_name_the_stock(con) -> None:
    _load(con, "SBIN", [("2020-06-01", 100.0, 100.0), ("2020-06-02", 130.0, 130.0)])
    stock_facts.build_stock_daily(con)
    rows = stock_facts.outlier_rows(con)
    assert rows and rows[0]["symbol"] == "SBIN"
    assert rows[0]["return_pct"] == pytest.approx(30.0, abs=1e-3)
