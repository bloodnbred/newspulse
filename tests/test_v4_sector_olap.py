"""Phase V4 tests: the sector cube and the sector OLAP operations.

The theme is reconciliation. A cube or an OLAP operation that disagrees with the
facts it summarises still renders a plausible table, so these tests recompute
the same quantity a second way and demand the numbers agree.

The fixture is tiny and hand-built rather than sampled from the corpus, so a
failure points at the code rather than at the data.
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from dwm.olap import sector_ops as S
from dwm.warehouse.cubes import build_cubes


def _date(day: dt.date) -> int:
    return int(day.strftime("%Y%m%d"))


@pytest.fixture()
def con():
    """Two sectors, three months, a couple of stocks, and a trading calendar."""
    c = duckdb.connect()
    c.execute(
        """
        CREATE TABLE dim_sector (sector_key INTEGER, sector_name VARCHAR);
        CREATE TABLE dim_stock (
            stock_key INTEGER, symbol VARCHAR, sector_key INTEGER,
            price_source VARCHAR, is_survivor BOOLEAN
        );
        CREATE TABLE dim_date (date_key INTEGER, full_date DATE, is_trading_day BOOLEAN);
        CREATE TABLE dim_topic (topic_key INTEGER, topic_name VARCHAR);
        CREATE TABLE fact_headline (
            headline_id BIGINT, date_key INTEGER, publish_date DATE,
            in_window BOOLEAN, topic_key INTEGER, headline_text VARCHAR,
            sentiment_compound DOUBLE, is_sensational BOOLEAN,
            is_market_report BOOLEAN
        );
        CREATE TABLE fact_news_sector_daily (
            date_key INTEGER, effective_trading_date_key INTEGER, sector_key INTEGER,
            news_count BIGINT, news_count_non_market BIGINT,
            sum_sentiment DOUBLE, high_sensational_count BIGINT, source VARCHAR
        );
        CREATE TABLE fact_news_stock_mention (
            headline_id BIGINT, stock_key INTEGER, date_key INTEGER,
            effective_trading_date_key INTEGER, matched_alias VARCHAR,
            match_method VARCHAR, is_ambiguous_alias BOOLEAN, is_market_report BOOLEAN
        );
        CREATE TABLE fact_stock_daily (
            date_key INTEGER, stock_key INTEGER, trade_date DATE, close DOUBLE,
            adj_close DOUBLE, return_pct DOUBLE, abs_return_pct DOUBLE,
            volatility_20d DOUBLE, is_outlier BOOLEAN
        );
        """
    )
    c.executemany(
        "INSERT INTO dim_sector VALUES (?, ?)", [(1, "Energy"), (2, "IT")]
    )
    c.executemany(
        "INSERT INTO dim_stock VALUES (?,?,?,?,?)",
        [(1, "ONGC", 1, "yahoo_chart_api", True),
         (2, "TCS", 2, "yahoo_chart_api", True),
         (3, "GHOST", 1, None, False)],
    )
    c.execute("INSERT INTO dim_topic VALUES (1, 'Business')")

    # Four sessions across two months, plus one Saturday.
    days = [
        (dt.date(2018, 1, 5), True),    # Fri
        (dt.date(2018, 1, 6), False),   # Sat
        (dt.date(2018, 1, 8), True),    # Mon
        (dt.date(2018, 2, 5), True),    # Mon
    ]
    c.executemany(
        "INSERT INTO dim_date VALUES (?, ?, ?)",
        [(_date(d), d, t) for d, t in days],
    )
    c.executemany(
        "INSERT INTO fact_stock_daily VALUES (?,?,?,?,?,?,?,?,false)",
        [
            (_date(dt.date(2018, 1, 5)), 1, dt.date(2018, 1, 5), 100.0, 100.0, 1.0, 1.0, 1.2),
            (_date(dt.date(2018, 1, 8)), 1, dt.date(2018, 1, 8), 102.0, 102.0, 2.0, 2.0, 1.4),
            (_date(dt.date(2018, 2, 5)), 1, dt.date(2018, 2, 5), 101.0, 101.0, -1.0, 1.0, 1.1),
            (_date(dt.date(2018, 1, 5)), 2, dt.date(2018, 1, 5), 50.0, 50.0, 4.0, 4.0, 0.9),
            (_date(dt.date(2018, 1, 8)), 2, dt.date(2018, 1, 8), 52.0, 52.0, 4.0, 4.0, 1.0),
            # An outlier, which the clean view must exclude.
            (_date(dt.date(2018, 2, 5)), 2, dt.date(2018, 2, 5), 70.0, 70.0, 34.0, 34.0, 9.9),
        ],
    )
    # Mark the last row as the outlier rather than creating a second table.
    c.execute(
        "UPDATE fact_stock_daily SET is_outlier = true "
        "WHERE stock_key = 2 AND trade_date = DATE '2018-02-05'"
    )
    c.execute(
        "CREATE VIEW v_stock_daily_clean AS SELECT * FROM fact_stock_daily "
        "WHERE is_outlier IS NOT TRUE"
    )

    c.executemany(
        "INSERT INTO fact_news_sector_daily VALUES (?,?,?,?,?,?,?,?)",
        [
            (_date(dt.date(2018, 1, 5)), _date(dt.date(2018, 1, 5)), 1, 10, 9, 2.0, 1, "keyword"),
            # Saturday news belongs to the Monday session.
            (_date(dt.date(2018, 1, 6)), _date(dt.date(2018, 1, 8)), 1, 5, 5, -1.0, 0, "keyword"),
            (_date(dt.date(2018, 1, 8)), _date(dt.date(2018, 1, 8)), 2, 7, 7, 0.0, 2, "mention"),
            (_date(dt.date(2018, 2, 5)), _date(dt.date(2018, 2, 5)), 1, 3, 3, 1.5, 0, "keyword"),
        ],
    )
    c.execute(
        "CREATE TABLE dim_trading_calendar AS "
        "SELECT date_key, is_trading_day, date_key AS effective_trading_date_key "
        "FROM dim_date"
    )
    c.execute(
        "INSERT INTO fact_headline VALUES "
        "(1, 20180106, DATE '2018-01-06', true, 1, 'ONGC and Tata Motors in news', "
        " 0.4, false, false), "
        "(2, 20180108, DATE '2018-01-08', true, 1, 'TCS wins a contract', "
        " 0.1, true, false)"
    )
    c.execute(
        "INSERT INTO fact_news_stock_mention VALUES "
        "(1, 1, 20180106, 20180108, 'ONGC', 'exact', false, false), "
        "(2, 2, 20180108, 20180108, 'TCS', 'ticker', false, false)"
    )
    return c


# ---------------------------------------------------------------------------
# the cube
# ---------------------------------------------------------------------------


def test_the_sector_cube_matches_the_same_query_on_the_facts(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    cube = con.execute(
        "SELECT year, month, sector_key, news_count FROM cube_sector_month "
        "ORDER BY 1,2,3"
    ).fetchall()
    direct = con.execute(
        """
        SELECT effective_trading_date_key // 10000,
               (effective_trading_date_key // 100) % 100,
               sector_key, sum(news_count)
        FROM fact_news_sector_daily
        GROUP BY 1,2,3 ORDER BY 1,2,3
        """
    ).fetchall()
    assert cube == direct


def test_weekend_news_lands_on_the_monday_session_in_the_cube(con) -> None:
    """The Saturday's 5 headlines must be counted in January, not dropped."""
    build_cubes(con, only=["cube_sector_month"])
    january = con.execute(
        "SELECT sum(news_count) FROM cube_sector_month WHERE year = 2018 AND month = 1"
    ).fetchone()[0]
    assert january == 22, "10 + 5 (Saturday, folded into Monday) + 7"


def test_the_cube_stores_sums_and_counts_not_averages(con) -> None:
    """An average cannot be rolled up. Mean return must be derived at read time."""
    build_cubes(con, only=["cube_sector_month"])
    row = con.execute(
        "SELECT sum_return, sum_abs_return, stock_obs FROM cube_sector_month "
        "WHERE year = 2018 AND month = 1 AND sector_key = 1"
    ).fetchone()
    sum_return, sum_abs, obs = row
    assert obs == 2
    assert sum_return == 3.0          # 1.0 + 2.0
    assert sum_abs == 3.0
    # The mean is recoverable; an average would have thrown the counts away.
    assert sum_return / obs == pytest.approx(1.5)


def test_the_cube_excludes_outliers_through_the_clean_view(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    obs = con.execute(
        "SELECT sum(stock_obs) FROM cube_sector_month WHERE sector_key = 2"
    ).fetchone()[0]
    assert obs == 2, "the +34% outlier row must not be counted"


def test_the_cube_is_skipped_when_its_sources_are_missing(con) -> None:
    """`dwm build` has not created the sector facts yet, so the cube must be
    skipped with a reason rather than failing the whole build."""
    con.execute("DROP TABLE fact_news_sector_daily")
    result = build_cubes(con, only=["cube_sector_month"])
    assert result.get("cube_sector_month") is None
    assert "fact_news_sector_daily" in result["_skipped"]["cube_sector_month"]


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


def test_roll_up_totals_equal_the_sum_of_the_months(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    monthly = {
        r["period"]: r["news_count"] for r in S.run_operation(con, "sector_rollup", level="month")["rows"]
    }
    yearly = {
        r["period"]: r["news_count"] for r in S.run_operation(con, "sector_rollup", level="year")["rows"]
    }
    assert sum(monthly.values()) == sum(yearly.values())


def test_roll_up_by_quarter_reconciles_with_the_year(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    quarters = S.run_operation(con, "sector_rollup", level="quarter")["rows"]
    years = S.run_operation(con, "sector_rollup", level="year")["rows"]
    assert sum(r["news_count"] for r in quarters) == sum(
        r["news_count"] for r in years
    )


def test_a_derived_measure_is_a_ratio_of_sums(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    rows = S.run_operation(con, "sector_slice", sector="Energy", year=2018,
                           measure="mean_return")["rows"]
    assert rows
    raw = con.execute(
        "SELECT sum(sum_return), sum(stock_obs) FROM cube_sector_month "
        "WHERE year = 2018 AND sector_key = 1"
    ).fetchone()
    expected = raw[0] / raw[1]
    assert rows[0]["measure_value"] == pytest.approx(expected)


def test_slice_holds_one_dimension_fixed(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    rows = S.run_operation(con, "sector_slice", sector="Energy", year=2018)["rows"]
    assert {r["sector"] for r in rows} == {"Energy"}
    assert {r["year"] for r in rows} == {2018}


def test_dice_holds_both_fixed(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    rows = S.run_operation(con, "sector_dice", sectors=["Energy", "IT"],
                           years=[2018])["rows"]
    assert {r["sector"] for r in rows} <= {"Energy", "IT"}


def test_drilldown_is_capped_and_shows_the_stocks_behind_the_month(con) -> None:
    rows = S.run_operation(con, "sector_drilldown", year=2018, month=1,
                           sector="Energy")["rows"]
    assert 0 < len(rows) <= S.MAX_HEADLINES
    assert rows[0]["stocks"], "the headline list must carry its stock tags"


def test_drill_across_row_count_equals_sessions_present_on_both_sides(con) -> None:
    """Every returned row must have BOTH news and a price on that session.

    The grain is (session, sector): with no sector filter, two sectors active on
    one date legitimately produce two rows. That is not a duplicated session,
    but it is only harmless if the grain is stated, so it is asserted here.
    """
    build_cubes(con, only=["cube_sector_month"])
    result = S.run_operation(con, "drill_across_sector")
    rows = result["rows"]
    assert rows
    for row in rows:
        assert row["news_count"] and row["news_count"] > 0
        assert row["stock_obs"] and row["stock_obs"] > 0
        assert row["sector"]
    pairs = {(r["session"], r["sector"]) for r in rows}
    assert len(pairs) == len(rows), "one row per (session, sector)"
    assert result["meta"]["grain"] == "session x sector"
    assert result["meta"]["sessions"] == len({r["session"] for r in rows})


def test_drill_across_market_reports_are_excluded_by_default(con) -> None:
    """A headline that reports a move that already happened must not be
    allowed to manufacture a news/price correlation."""
    default = S.run_operation(con, "drill_across_sector")["rows"]
    included = S.run_operation(con, "drill_across_sector",
                               include_market_reports=True)["rows"]
    assert sum(r["news_count"] for r in default) <= sum(
        r["news_count"] for r in included
    )
    assert S.run_operation(con, "drill_across_sector")["meta"][
        "include_market_reports"
    ] is False


def test_drill_across_lag_shifts_the_price_side(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    zero = {r["session"] for r in S.run_operation(con, "drill_across_sector", lag=0)["rows"]}
    plus = {r["session"] for r in S.run_operation(con, "drill_across_sector", lag=1)["rows"]}
    assert zero != plus, "a lag must actually move the price side"


def test_stock_profile_reports_returns_and_mentions(con) -> None:
    result = S.run_operation(con, "stock_profile", symbol="ONGC")
    assert result["meta"]["sessions"] == 3
    assert result["meta"]["mentions"] >= 1
    assert result["rows"][0]["return_pct"] is not None


def test_pivot_puts_years_in_columns(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    result = S.run_operation(con, "sector_pivot")
    assert any(c.startswith("p20") for c in result["columns"])
    assert all(str(c).startswith("p20") for c in result["columns"][1:])


def test_the_cube_operation_totals_reconcile(con) -> None:
    build_cubes(con, only=["cube_sector_month"])
    result = S.run_operation(con, "sector_cube")
    by_year = sum(r["news_count"] for r in result["rows"] if r["year"])
    totals = sum(r["news_count"] for r in result["rows"] if not r["year"])
    assert by_year == totals
    assert by_year == result["meta"]["total_news"]


# ---------------------------------------------------------------------------
# whitelisting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "params", "message"),
    [
        ("sector_slice", {"sector": "Astrology"}, "unknown sector"),
        ("sector_slice", {"measure": "vibes"}, "unknown measure"),
        ("sector_dice", {"sectors": ["Astrology"]}, "unknown sector"),
        ("sector_rollup", {"level": "fortnight"}, "unknown roll-up level"),
        ("sector_drilldown", {"year": 2018, "quarter": 9}, "quarter must be"),
        ("sector_drilldown", {"year": 2018, "month": 44}, "month must be"),
        ("stock_profile", {"symbol": "NOPE"}, "unknown symbol"),
        ("drill_across_sector", {"lag": 99}, "lag must be"),
        ("drill_across_sector", {"sector": "Astrology"}, "unknown sector"),
    ],
)
def test_unknown_names_are_rejected_not_interpolated(
    con, op: str, params: dict, message: str
) -> None:
    """Values are parameterised and identifiers whitelisted, so an unexpected
    name raises instead of reaching the SQL."""
    with pytest.raises(ValueError, match=message):
        S.run_operation(con, op, **params)


def test_an_unknown_operation_lists_the_known_ones(con) -> None:
    with pytest.raises(ValueError, match="Known"):
        S.run_operation(con, "not_an_operation")


def test_every_registered_operation_has_default_params(con) -> None:
    for name in S.OPERATIONS:
        assert name in S.DEFAULT_PARAMS, f"{name} has no documented default"


def test_every_operation_reports_that_it_is_associative(con) -> None:
    """No operation may describe itself as causal."""
    build_cubes(con, only=["cube_sector_month"])
    for name in S.OPERATIONS:
        result = S.run_operation(con, name, **S.DEFAULT_PARAMS[name])
        blob = f"{result['description']} {result['meta']}".lower()
        for banned in ("caused", "causes", "predicts", "leads to", "proves"):
            assert banned not in blob, f"{name} claims {banned!r}"
