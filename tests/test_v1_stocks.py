"""Phase V1 tests: stock ingest, the trading calendar, and the v2 dimensions.

Two rules shape everything here.

The tests never touch the network. A test that fetches from Yahoo is a test
that fails on a bad connection and lies about what it verified, so the fetching
layer is exercised through injected bytes and a tiny fixture CSV. The live
source is verified by `python -m dwm ingest --dataset stocks`, which is a
command, not a test.

The expectations are derived from the data, never from a hard-coded date. The
one date this file knows about - 2020-02-01, the Union Budget Saturday when
NSE actually traded - is asserted as a *fact about the calendar*, read out of
dim_date, not written into a fixture as if it were guaranteed.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json

import duckdb
import pytest

from dwm.config import (
    load_events,
    load_sector_config,
    load_sources_config,
    load_stock_aliases,
    resolve_sector,
)
from dwm.etl import calendar as calendar_mod
from dwm.etl import dims
from dwm.ingest import meta as meta_ingest
from dwm.ingest import stocks as stocks_ingest
from dwm.ingest.fetch import provenance_path, read_provenance, write_provenance

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_sources_config_declares_the_two_v2_sources() -> None:
    specs = load_sources_config()
    assert set(specs) == {"stocks", "meta"}
    assert specs["stocks"].numeric_columns == [
        "open", "high", "low", "close", "adj_close", "volume",
    ]
    assert specs["meta"].filename or specs["meta"].file_glob


def test_load_datasets_config_is_untouched_by_v2() -> None:
    """v1's three-dataset contract must survive adding two v2 sources."""
    from dwm.config import load_datasets_config

    assert set(load_datasets_config()) == {"toi", "ifnd", "nifty"}


def test_every_declared_sector_is_unique_and_non_empty() -> None:
    cfg = load_sector_config()
    assert len(cfg.sectors) == 10
    assert len(set(cfg.sectors)) == len(cfg.sectors)
    assert all(s.strip() for s in cfg.sectors)


def test_an_override_may_not_invent_a_sector(tmp_path) -> None:
    bad = tmp_path / "stocks.yaml"
    bad.write_text(
        "sectors: [IT]\noverrides:\n  TCS: {sector: Astrology}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not in sectors"):
        load_sector_config(path=bad)


@pytest.mark.parametrize(
    ("industry", "expected"),
    [
        ("Financial Services", "Financial Services"),
        ("Healthcare", "Pharma"),
        ("Automobile and Auto Components", "Automobile"),
        ("Metals & Mining", "Metals"),
        ("Information Technology", "IT"),
        ("Fast Moving Consumer Goods", "FMCG"),
        ("Oil Gas & Consumable Fuels", "Energy"),
        ("Construction Materials", "Infrastructure"),
        ("Power", "Energy"),
        ("Telecommunication", "Telecom"),
        ("Construction", "Infrastructure"),
    ],
)
def test_the_real_nse_labels_all_map_mechanically(industry: str, expected: str) -> None:
    """All 15 labels NSE actually returns must resolve without a per-symbol rule.

    `ZZZ` is deliberately not in config/stocks.yaml, so this exercises the
    industry_to_sector map alone.
    """
    entry = resolve_sector("ZZZ", industry, load_sector_config())
    assert entry.sector == expected
    assert entry.source == "meta"


def test_an_override_beats_the_industry_label() -> None:
    entry = resolve_sector("ASIANPAINT", "Consumer Durables", load_sector_config())
    assert entry.source == "override"
    assert entry.sector == "Infrastructure"


def test_an_unmappable_industry_is_unknown_not_guessed() -> None:
    entry = resolve_sector("ZZZ", "Intergalactic Freight", load_sector_config())
    assert entry.source == "unknown"
    assert entry.sector == ""


def test_events_are_deduplicated_and_ordered() -> None:
    events = load_events()
    keys = [(e.date, e.label.casefold()) for e in events]
    assert len(keys) == len(set(keys))
    assert events == sorted(events, key=lambda e: (e.date, e.label))


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------

CONSTITUENTS = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n"
    "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\n"
    "Asian Paints Ltd.,Consumer Durables,ASIANPAINT,EQ,INE021A01026\n"
)


def test_constituent_header_variations_are_accepted() -> None:
    """NSE may rename columns; a header change must not silently empty dim_stock."""
    text = (
        "Symbol,Name,Sector\n"
        "TCS,Tata Consultancy Services Ltd.,Information Technology\n"
    )
    rows = meta_ingest.parse_constituents(text)
    assert rows == [
        {
            "symbol": "TCS",
            "company": "Tata Consultancy Services Ltd.",
            "industry": "Information Technology",
            "isin": "",
        }
    ]


def test_a_missing_required_column_is_loud() -> None:
    with pytest.raises(KeyError, match="industry"):
        meta_ingest.parse_constituents("Symbol,Company Name\nTCS,Tata\n")


def test_constituents_without_a_symbol_are_skipped() -> None:
    text = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n"
        ",Information Technology,,EQ,\n"
    )
    assert len(meta_ingest.parse_constituents(text)) == 1


def test_meta_staging_round_trips() -> None:
    con = duckdb.connect()
    path = _write_fixture("NSE.csv", CONSTITUENTS)
    info = meta_ingest.stage(con, path)
    assert info["reconciled"] is True
    assert info["distinct_symbols"] == 3
    assert info["distinct_industries"] == 3
    assert con.execute("SELECT count(*) FROM stg_meta").fetchone()[0] == 3


# ---------------------------------------------------------------------------
# stocks
# ---------------------------------------------------------------------------

CHART_REPLY = {
    "chart": {
        "error": None,
        "result": [
            {
                "meta": {"symbol": "TCS.NS"},
                "timestamp": [1609459200, 1609545600, 1609632000],
                "indicators": {
                    "quote": [
                        {
                            "open": [100.0, 102.0, None],
                            "high": [104.0, 105.0, 106.0],
                            "low": [99.0, 101.0, 102.0],
                            "close": [103.0, 104.5, None],
                            "volume": [1000, 2000, 3000],
                        }
                    ],
                    "adjclose": [{"adjclose": [95.0, 96.5, 97.5]}],
                },
            }
        ],
    }
}


def _write_fixture(name: str, text: str):
    import pathlib
    import tempfile

    base = pathlib.Path(tempfile.mkdtemp())
    path = base / name
    path.write_text(text, encoding="utf-8")
    return path


def test_chart_url_never_uses_range_parameters() -> None:
    """range=max and range=10y are silently downgraded to monthly bars."""
    url = stocks_ingest.chart_url(
        stocks_ingest.DEFAULT_TEMPLATE, "TCS.NS", dt.date(2015, 1, 1), dt.date(2020, 7, 3)
    )
    assert "range=" not in url
    assert "interval=1d" in url
    assert "period1=" in url and "period2=" in url


def test_chart_url_end_is_exclusive_so_the_last_session_is_kept() -> None:
    """A one-day gap here silently drops the final bar of the window.

    The window's last date must still be *inside* [period1, period2), so the
    end bound is one day past it.
    """
    day = dt.date(2020, 7, 3)
    url = stocks_ingest.chart_url(stocks_ingest.DEFAULT_TEMPLATE, "X.NS", day, day)
    query = dict(
        part.split("=", 1) for part in url.split("?", 1)[1].split("&") if "=" in part
    )
    p1, p2 = int(query["period1"]), int(query["period2"])
    assert dt.datetime.fromtimestamp(p1, dt.UTC).date() == day
    assert dt.datetime.fromtimestamp(p2, dt.UTC).date() == day + dt.timedelta(days=1)


def test_parse_chart_drops_rows_with_no_close() -> None:
    """A null close cannot become a return; keeping it only moves the problem."""
    rows = stocks_ingest.parse_chart(json.dumps(CHART_REPLY).encode())
    assert [r["date"] for r in rows] == ["2021-01-01", "2021-01-02"]
    assert rows[0]["close"] == 103.0
    assert rows[0]["adj_close"] == 95.0


def test_parse_chart_falls_back_to_close_when_adjclose_is_missing() -> None:
    payload = json.loads(json.dumps(CHART_REPLY))
    del payload["chart"]["result"][0]["indicators"]["adjclose"]
    rows = stocks_ingest.parse_chart(json.dumps(payload).encode())
    assert all(r["adj_close"] == r["close"] for r in rows)


def test_parse_chart_raises_on_a_provider_error() -> None:
    payload = json.dumps({"chart": {"error": {"description": "Not Found"}}}).encode()
    with pytest.raises(ValueError, match="Not Found"):
        stocks_ingest.parse_chart(payload)


def test_parse_chart_tolerates_a_symbol_with_no_series() -> None:
    assert stocks_ingest.parse_chart(json.dumps({"chart": {"result": []}}).encode()) == []


def test_http_400_is_reported_as_listing_later_not_a_broken_ticker() -> None:
    """Verified live: ETERNAL/JIOFIN/MAXHEALTH answer 400 for 2015-2020 and
    return bars for 2023-2026. Reporting that as a broken ticker would
    overstate how much data is missing."""
    reason = stocks_ingest._classify(
        400, "ETERNAL.NS", dt.date(2015, 6, 30), dt.date(2020, 6, 30)
    )
    assert "listed after the news window" in reason

    missing = stocks_ingest._classify(
        404, "NOPE.NS", dt.date(2015, 6, 30), dt.date(2020, 6, 30)
    )
    assert "does not serve" in missing


def test_stock_staging_uses_the_file_name_as_the_symbol(tmp_path) -> None:
    body = io.StringIO()
    writer = csv.writer(body)
    writer.writerow(["date", "open", "high", "low", "close", "adj_close", "volume"])
    writer.writerow(["2015-01-01", 1, 2, 0.5, 1.5, 1.4, 100])
    writer.writerow(["2015-01-02", 2, 3, 1.5, 2.5, 2.4, 200])
    (tmp_path / "TCS.csv").write_text(body.getvalue(), encoding="utf-8")

    con = duckdb.connect()
    info = stocks_ingest.stage(con, tmp_path)
    assert info["reconciled"] is True
    assert info["distinct_symbols"] == 1
    row = con.execute(
        "SELECT symbol, trade_date, close, adj_close FROM stg_stocks ORDER BY trade_date"
    ).fetchall()
    assert [r[0] for r in row] == ["TCS", "TCS"]
    assert row[0][3] == 1.4


def test_stock_staging_reports_an_empty_directory(tmp_path) -> None:
    con = duckdb.connect()
    with pytest.raises(FileNotFoundError, match="no per-symbol price CSVs"):
        stocks_ingest.stage(con, tmp_path)


def test_symbols_come_from_stg_meta_not_a_hardcoded_list() -> None:
    con = duckdb.connect()
    with pytest.raises(FileNotFoundError, match="stg_meta"):
        stocks_ingest.constituents(con)
    meta_ingest.stage(con, _write_fixture("NSE.csv", CONSTITUENTS))
    assert stocks_ingest.constituents(con) == ["ASIANPAINT", "RELIANCE", "TCS"]


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def test_provenance_records_the_url_and_the_content_hash(tmp_path) -> None:
    path = tmp_path / "TCS.csv"
    path.write_text("date,close\n2020-01-01,1.0\n", encoding="utf-8")
    write_provenance(path, {"dataset": "stocks", "source_url": "https://example/x"})
    record = read_provenance(path)
    assert record["source_url"] == "https://example/x"
    assert record["sha256"]
    assert record["fetched_at"]
    assert provenance_path(path).exists()


def test_an_unreadable_sidecar_is_ignored_not_fatal(tmp_path) -> None:
    path = tmp_path / "TCS.csv"
    path.write_text("date\n", encoding="utf-8")
    provenance_path(path).write_text("{not json", encoding="utf-8")
    assert read_provenance(path) is None


# ---------------------------------------------------------------------------
# the v2 dimensions
# ---------------------------------------------------------------------------


@pytest.fixture()
def v2_db(tmp_path):
    """A small warehouse: dim_date + fact_headline + fact_market_daily + v2 staging."""
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE dim_date (
            date_key INTEGER, full_date DATE, year_no BIGINT, quarter_no BIGINT,
            month_no BIGINT, day_no BIGINT, day_name VARCHAR, day_of_week BIGINT,
            week_of_year BIGINT, day_of_year BIGINT, year_month VARCHAR,
            year_month_key INTEGER, is_weekend BOOLEAN, is_month_end BOOLEAN,
            is_quarter_end BOOLEAN, is_trading_day BOOLEAN
        );
        CREATE TABLE fact_headline (headline_id BIGINT, date_key INTEGER, publish_date DATE, in_window BOOLEAN);
        CREATE TABLE fact_market_daily (market_id BIGINT, date_key INTEGER, trade_date DATE);
        """
    )
    # Four days: Thu 2020-06-25, Fri 26, Sat 27, Mon 29. Only Thu/Fri/Mon traded.
    dates = [
        (dt.date(2020, 6, 25), True),
        (dt.date(2020, 6, 26), True),
        (dt.date(2020, 6, 27), False),
        (dt.date(2020, 6, 29), True),
    ]
    rows = [
        (
            int(d.strftime("%Y%m%d")), d, d.year, (d.month - 1) // 3 + 1, d.month, d.day,
            d.strftime("%A"), d.weekday(), d.isocalendar()[1], d.timetuple().tm_yday,
            d.strftime("%Y-%m"), int(f"{d.year}{d.month:02d}"),
            d.weekday() >= 5, False, False, trading,
        )
        for d, trading in dates
    ]
    con.executemany(
        "INSERT INTO dim_date VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.executemany(
        "INSERT INTO fact_headline VALUES (?,?,?,?)",
        [(i, int(d.strftime("%Y%m%d")), d, True) for i, (d, _) in enumerate(dates, 1)],
    )
    con.executemany(
        "INSERT INTO fact_market_daily VALUES (?,?,?)",
        [
            (i, int(d.strftime("%Y%m%d")), d)
            for i, (d, trading) in enumerate(dates, 1)
            if trading
        ],
    )
    yield con
    con.close()


def test_window_comes_from_the_fact_table_not_the_config(v2_db) -> None:
    assert calendar_mod.window_bounds(v2_db) == ("2020-06-25", "2020-06-29")


def test_every_calendar_date_gets_a_row(v2_db) -> None:
    counts = calendar_mod.build(v2_db)
    assert counts["rows"] == 4
    assert counts["trading"] == 3
    assert counts["unresolved"] == 0


def test_a_trading_date_maps_to_itself(v2_db) -> None:
    calendar_mod.build(v2_db)
    bad = v2_db.execute(
        "SELECT count(*) FROM dim_trading_calendar "
        "WHERE is_trading_day AND effective_trading_date_key <> date_key"
    ).fetchone()[0]
    assert bad == 0


def test_a_non_trading_date_maps_forward_to_a_real_session(v2_db) -> None:
    calendar_mod.build(v2_db)
    row = v2_db.execute(
        """
        SELECT s.full_date, e.full_date
        FROM dim_trading_calendar c
        JOIN dim_date s ON s.date_key = c.date_key
        JOIN dim_date e ON e.date_key = c.effective_trading_date_key
        WHERE strftime(s.full_date, '%w') = '6'
        """
    ).fetchone()
    # Saturday 27th -> Monday 29th.
    assert row == (dt.date(2020, 6, 27), dt.date(2020, 6, 29))


def test_weekend_sessions_the_exchange_actually_traded_are_reported(v2_db) -> None:
    """A trading Saturday must map to itself, not forward, and must be visible.

    2020-06-01 was a Sunday, the Union Budget was presented, and NSE ran a
    special session. Treating such a date as a weekend closure would assert
    that the market was shut on a day it traded.
    """
    v2_db.execute(
        "INSERT INTO fact_market_daily VALUES (?, ?, ?)", (99, 20200627, dt.date(2020, 6, 27))
    )
    v2_db.execute("UPDATE dim_date SET is_trading_day = TRUE WHERE full_date = '2020-06-27'")

    calendar_mod.build(v2_db)
    assert calendar_mod.trading_weekend_sessions(v2_db) == ["2020-06-27"]

    row = v2_db.execute(
        """
        SELECT c.date_key = c.effective_trading_date_key
        FROM dim_trading_calendar c
        JOIN dim_date d ON d.date_key = c.date_key
        WHERE strftime(d.full_date, '%w') = '6'
        """
    ).fetchone()
    assert row == (True,), "a trading Saturday must map to itself"
    # This fixture has no non-trading Saturday, so the check is vacuous here
    # rather than failed; None means "nothing to check", not "broken".
    assert calendar_mod.check(v2_db)["non_trading_saturday_maps_forward"] is None


def test_calendar_check_reports_a_missing_table() -> None:
    assert calendar_mod.check(duckdb.connect()) == {"present": False}


def test_calendar_refuses_to_build_without_a_calendar(v2_db) -> None:
    v2_db.execute("DROP TABLE dim_date")
    with pytest.raises(FileNotFoundError, match="dim_date"):
        calendar_mod.build(v2_db)


def test_every_price_symbol_maps_to_exactly_one_stock_row(v2_db, tmp_path) -> None:
    body = io.StringIO()
    writer = csv.writer(body)
    writer.writerow(["date", "open", "high", "low", "close", "adj_close", "volume"])
    for day, close in (("2020-06-25", 1.0), ("2020-06-26", 1.1)):
        writer.writerow([day, close, close, close, close, close, 10])
    for symbol in ("TCS", "RELIANCE", "ASIANPAINT"):
        (tmp_path / f"{symbol}.csv").write_text(body.getvalue(), encoding="utf-8")

    meta_ingest.stage(v2_db, _write_fixture("NSE.csv", CONSTITUENTS))
    report = dims.build_stock_dims(
        v2_db,
        sector_cfg=load_sector_config(),
        alias_rules=load_stock_aliases(),
        events=load_events(),
        window=("2020-06-25", "2020-06-29"),
    )
    assert report["dim_stock"] == 3
    dupes = v2_db.execute(
        "SELECT count(*) FROM (SELECT symbol FROM dim_stock GROUP BY symbol HAVING count(*) > 1)"
    ).fetchone()[0]
    assert dupes == 0


def test_no_stock_has_a_sector_that_is_not_configured(v2_db, tmp_path) -> None:
    meta_ingest.stage(v2_db, _write_fixture("NSE.csv", CONSTITUENTS))
    report = dims.build_stock_dims(
        v2_db,
        sector_cfg=load_sector_config(),
        alias_rules=load_stock_aliases(),
        events=load_events(),
        window=("2020-06-25", "2020-06-29"),
    )
    assert report["dim_stock_unknown_sector"] == []
    assert v2_db.execute(
        "SELECT count(*) FROM dim_stock WHERE sector_key IS NULL"
    ).fetchone()[0] == 0


def test_an_index_member_without_prices_still_gets_a_row(v2_db) -> None:
    """Dropping it would quietly turn a 50-stock index into a 47-stock one."""
    meta_ingest.stage(v2_db, _write_fixture("NSE.csv", CONSTITUENTS))
    report = dims.build_stock_dims(
        v2_db,
        sector_cfg=load_sector_config(),
        alias_rules=load_stock_aliases(),
        events=load_events(),
        window=("2020-06-25", "2020-06-29"),
    )
    assert report["dim_stock"] == 3
    assert sorted(report["dim_stock_without_prices"]) == [
        "ASIANPAINT", "RELIANCE", "TCS",
    ]
    assert v2_db.execute(
        "SELECT count(*) FROM dim_stock WHERE price_source IS NOT NULL"
    ).fetchone()[0] == 0


def test_a_stock_listing_after_the_window_is_not_a_survivor(v2_db, tmp_path) -> None:
    body = io.StringIO()
    writer = csv.writer(body)
    writer.writerow(["date", "open", "high", "low", "close", "adj_close", "volume"])
    # TCS trades from the window start; RELIANCE only from the last day.
    writer.writerow(["2020-06-25", 1, 1, 1, 1, 1, 1])
    (tmp_path / "TCS.csv").write_text(body.getvalue(), encoding="utf-8")
    late = io.StringIO()
    writer2 = csv.writer(late)
    writer2.writerow(["date", "open", "high", "low", "close", "adj_close", "volume"])
    writer2.writerow(["2020-06-29", 2, 2, 2, 2, 2, 2])
    (tmp_path / "RELIANCE.csv").write_text(late.getvalue(), encoding="utf-8")

    stocks_ingest.stage(v2_db, tmp_path)
    meta_ingest.stage(v2_db, _write_fixture("NSE.csv", CONSTITUENTS))
    report = dims.build_stock_dims(
        v2_db,
        sector_cfg=load_sector_config(),
        alias_rules=load_stock_aliases(),
        events=load_events(),
        window=("2020-06-25", "2020-06-29"),
    )
    flags = dict(
        v2_db.execute("SELECT symbol, is_survivor FROM dim_stock").fetchall()
    )
    assert flags["TCS"] is True
    assert flags["RELIANCE"] is False
    assert "RELIANCE" in report["dim_stock_not_survivors"]


def test_aliases_for_non_constituents_are_reported_not_loaded(v2_db) -> None:
    """Wipro and HDFC are real headline spellings but not index members."""
    meta_ingest.stage(v2_db, _write_fixture("NSE.csv", CONSTITUENTS))
    report = dims.build_stock_dims(
        v2_db,
        sector_cfg=load_sector_config(),
        alias_rules=load_stock_aliases(),
        events=load_events(),
        window=("2020-06-25", "2020-06-29"),
    )
    orphans = {r["symbol"] for r in report["dim_stock_alias_without_stock"]}
    assert {"WIPRO", "HDFC", "YESBANK"} <= orphans
    stored = {
        r[0] for r in v2_db.execute("SELECT DISTINCT alias FROM dim_stock_alias").fetchall()
    }
    assert "Wipro" not in stored


def test_tata_motors_headlines_attach_to_tmpv(v2_db) -> None:
    """The 2025 demerger renamed the listing; the 2015-2020 news did not."""
    assert "Tata Motors" in load_stock_aliases().aliases["TMPV"]["names"]


def test_events_outside_the_window_are_excluded(v2_db) -> None:
    report = dims.build_stock_dims(
        v2_db,
        sector_cfg=load_sector_config(),
        alias_rules=load_stock_aliases(),
        events=load_events(),
        window=("2019-01-01", "2019-12-31"),   # only the 2019 events qualify
    )
    assert report["dim_event"] == 4
    labels = {
        r[0] for r in v2_db.execute("SELECT event_label FROM dim_event").fetchall()
    }
    assert "Union Budget 2019" in labels
    assert "Union Budget 2016" not in labels
    assert report["dim_event_out_of_window"]


def test_rebuilding_the_dimensions_is_idempotent(v2_db) -> None:
    meta_ingest.stage(v2_db, _write_fixture("NSE.csv", CONSTITUENTS))
    kwargs = {
        "sector_cfg": load_sector_config(),
        "alias_rules": load_stock_aliases(),
        "events": load_events(),
        "window": ("2020-06-25", "2020-06-29"),
    }
    dims.build_stock_dims(v2_db, **kwargs)
    first = v2_db.execute("SELECT count(*) FROM dim_stock").fetchone()[0]
    dims.build_stock_dims(v2_db, **kwargs)
    assert v2_db.execute("SELECT count(*) FROM dim_stock").fetchone()[0] == first
