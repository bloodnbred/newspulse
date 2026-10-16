"""Conformed dimensions for the fact constellation.

Five dimensions, all shared by the fact tables (BLUEPRINT section 2):

    dim_date       one calendar day, contiguous, flags for trading days
    dim_topic      coarse topic, with a group so "where" and "what" stay apart
    dim_dataset    which source a fact row came from, with its provenance
    dim_label      REAL / FAKE / UNLABELLED
    dim_instrument traded market series

They are conformed: `date_key`, `topic_key` and `dataset_key` mean the same
thing in every fact table, which is what makes drill-across possible.
"""

from __future__ import annotations

from typing import Any

import duckdb

from dwm.config import (
    AliasRules,
    DatasetSpec,
    EventEntry,
    SectorConfig,
    TopicMap,
    resolve_sector,
)
from dwm.logging_utils import get, human_int

log = get("dwm.etl.dims")

# Topics that describe a place rather than a subject. Kept as data so the
# report can exclude or isolate them instead of hard-coding a name.
PLACE_TOPICS = {"Local"}

# What each topic is fundamentally about. RQ1 is a subject question, so
# separating the two groups is what makes that analysis meaningful.
TOPIC_GROUPS = {
    "Local": "Place",
    "Nation": "Place",
    "World": "Place",
    "Politics": "Subject",
    "Business": "Subject",
    "Sports": "Subject",
    "Entertainment": "Subject",
    "Technology": "Subject",
    "Health": "Subject",
    "Education": "Subject",
    "Automobile": "Subject",
    "Travel": "Subject",
    "Food": "Subject",
    "Lifestyle": "Subject",
    "Environment": "Subject",
    "Crime": "Subject",
    "Religion": "Subject",
    "Misinformation": "Type",
    "Other": "Other",
    "Unknown": "Unknown",
}

DDL = {
    "dim_date": """
        CREATE OR REPLACE TABLE dim_date AS
        WITH bounds AS (
            SELECT min(d) AS lo, max(d) AS hi FROM (
                SELECT {toi} AS d FROM stg_toi WHERE {toi} IS NOT NULL
                UNION ALL
                SELECT {nifty} FROM stg_nifty WHERE {nifty} IS NOT NULL
                UNION ALL
                SELECT {ifnd} FROM stg_ifnd WHERE {ifnd} IS NOT NULL
            ) WHERE d IS NOT NULL
        ),
        cal AS (
            SELECT unnest(generate_series(lo, hi, INTERVAL 1 DAY))::DATE AS full_date
            FROM bounds
        )
        SELECT
            CAST(strftime(full_date, '%Y%m%d') AS INTEGER) AS date_key,
            full_date,
            year(full_date)                                   AS year_no,
            quarter(full_date)                                AS quarter_no,
            month(full_date)                                  AS month_no,
            day(full_date)                                    AS day_no,
            dayname(full_date)                                AS day_name,
            dayofweek(full_date)                              AS day_of_week,
            weekofyear(full_date)                             AS week_of_year,
            dayofyear(full_date)                              AS day_of_year,
            strftime(full_date, '%Y-%m')                      AS year_month,
            CAST(year(full_date) * 100 + month(full_date) AS INTEGER) AS year_month_key,
            dayofweek(full_date) IN (0, 6)                    AS is_weekend,
            full_date = last_day(full_date)                   AS is_month_end,
            (month(full_date) IN (3, 6, 9, 12)
                AND full_date = last_day(full_date))          AS is_quarter_end,
            -- Uses the configured nifty formats rather than a bare cast, so
            -- a source in DD-MM-YYYY still flags trading days correctly.
            EXISTS (SELECT 1 FROM stg_nifty n
                    WHERE {nifty} = full_date)          AS is_trading_day
        FROM cal
        ORDER BY full_date
    """,
    "dim_topic": """
        CREATE OR REPLACE TABLE dim_topic (
            topic_key       INTEGER,
            topic_name      VARCHAR,
            topic_group     VARCHAR,
            is_place        BOOLEAN,
            topic_sort      INTEGER
        )
    """,
    "dim_dataset": """
        CREATE OR REPLACE TABLE dim_dataset (
            dataset_key        INTEGER,
            dataset_code       VARCHAR,
            dataset_name       VARCHAR,
            source_url         VARCHAR,
            local_path         VARCHAR,
            citation           VARCHAR,
            license            VARCHAR,
            provenance         VARCHAR,
            is_labelled        BOOLEAN,
            declared_precision VARCHAR,
            expected_rows      BIGINT,
            staged_rows        BIGINT
        )
    """,
    "dim_label": """
        CREATE OR REPLACE TABLE dim_label (
            label_key   INTEGER,
            label_code  VARCHAR,
            label_name  VARCHAR,
            label_value INTEGER,
            is_ground_truth BOOLEAN,
            description VARCHAR
        )
    """,
    "dim_instrument": """
        CREATE OR REPLACE TABLE dim_instrument (
            instrument_key   INTEGER,
            instrument_code  VARCHAR,
            instrument_name  VARCHAR,
            exchange         VARCHAR,
            currency         VARCHAR,
            source_dataset   VARCHAR
        )
    """,
}

LABELS = [
    # key, code, name, value, ground truth, description
    (1, "REAL", "Real news", 1, True,
     "Labelled real by the IFND source. The only rows that support accuracy claims."),
    (2, "FAKE", "Fake news", 0, True,
     "Labelled fake by the IFND source. Partly produced by an LSTM augmentation "
     "algorithm, so a model trained on it is evaluated on an easier distribution "
     "than genuine fake news."),
    (3, "UNLABELLED", "Unlabelled", None, False,
     "No ground truth exists. TOI headlines and market rows live here. Measures "
     "over these rows are risk-signal rates, never fake-news rates."),
]


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_dims(
    con: duckdb.DuckDBPyConnection,
    *,
    topic_map: TopicMap,
    specs: dict[str, DatasetSpec],
    sources: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Build all five dimensions. Returns row counts by table name."""
    counts: dict[str, int] = {}

    for name in DDL:
        con.execute(f"DROP TABLE IF EXISTS {name}")

    # dim_date is generated from the data, so its own CREATE lives in its
    # builder. The other four are fixed shapes, created here then filled.
    for name in ("dim_topic", "dim_dataset", "dim_label", "dim_instrument"):
        con.execute(DDL[name])

    _build_dim_date(con)
    counts["dim_date"] = _count(con, "dim_date")

    _build_dim_topic(con, topic_map)
    counts["dim_topic"] = _count(con, "dim_topic")

    _build_dim_dataset(con, specs, sources)
    counts["dim_dataset"] = _count(con, "dim_dataset")

    _build_dim_label(con)
    counts["dim_label"] = _count(con, "dim_label")

    _build_dim_instrument(con, specs)
    counts["dim_instrument"] = _count(con, "dim_instrument")

    for name, n in counts.items():
        log.info("%s: %s rows", name, human_int(n))
    return counts


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _insert_many(
    con: duckdb.DuckDBPyConnection, sql: str, rows: list[tuple[Any, ...]]
) -> int:
    """executemany, but an empty batch is a no-op.

    DuckDB raises InvalidInputException when executemany is handed an empty
    list, which turns "no events fall inside this window" into a crash instead
    of an empty table. A dimension that matches nothing is a legitimate state
    - a narrow window, or a stage that has not run yet - so it stays empty.
    """
    if not rows:
        return 0
    con.executemany(sql, rows)
    return len(rows)


def _build_dim_date(con: duckdb.DuckDBPyConnection) -> None:
    from dwm.etl.dates import date_key_expr, ifnd_date_value_expr, nifty_date_expr, toi_date_expr

    toi = toi_date_expr()
    nifty = nifty_date_expr()
    ifnd = ifnd_date_value_expr()
    con.execute(
        DDL["dim_date"].format(toi=toi, nifty=nifty, ifnd=ifnd)
    )
    # Guarantee the surrogate key matches the fact-side expression exactly.
    con.execute(
        f"""
        UPDATE dim_date
        SET date_key = {date_key_expr('full_date')}
        WHERE date_key IS DISTINCT FROM {date_key_expr('full_date')}
        """
    )


def _build_dim_topic(con: duckdb.DuckDBPyConnection, topic_map: TopicMap) -> None:
    names = list(dict.fromkeys(topic_map.topics))
    rows = []
    for i, name in enumerate(names, start=1):
        group = TOPIC_GROUPS.get(name, "Other")
        rows.append((i, name, group, name in PLACE_TOPICS, i))
    con.executemany("INSERT INTO dim_topic VALUES (?, ?, ?, ?, ?)", rows)


def _build_dim_dataset(
    con: duckdb.DuckDBPyConnection,
    specs: dict[str, DatasetSpec],
    sources: dict[str, Any] | None = None,
) -> None:
    """Fill dim_dataset for the v1 datasets and, when present, the v2 sources.

    Keys are assigned by sorting the codes, so adding `stocks` and `meta`
    renumbers `toi` from 3 to 5. That is safe because dataset_keys() reads the
    keys back out of this table rather than hard-coding them, which is exactly
    the failure that once pointed every headline at the wrong source.
    """

    rows: list[tuple[Any, ...]] = []
    ordered: list[tuple[str, tuple[Any, ...]]] = []
    for code in sorted(specs):
        spec = specs[code]
        ordered.append(
            (code, (
                spec.name, spec.url, str(spec.local_path()),
                spec.citation, spec.license, spec.provenance,
                spec.is_labelled, spec.declared_date_precision,
                spec.expected_rows, _staged_rows(con, f"stg_{code}"),
            ))
        )
    for code in sorted(sources or {}):
        spec = (sources or {})[code]
        note = " ".join(filter(None, [spec.limitation, spec.notes]))
        ordered.append(
            (code, (
                spec.name, spec.url or spec.url_template,
                str(spec.local_dir()), spec.citation, spec.license, note,
                False, "day", None, _staged_rows(con, f"stg_{code}"),
            ))
        )
    # Keys follow the sorted order across BOTH sets together, so the key a code
    # gets depends on the full set. dataset_keys() reads them back from here.
    ordered.sort(key=lambda pair: pair[0])
    rows = [(i, code, *body) for i, (code, body) in enumerate(ordered, start=1)]
    _insert_many(
        con,
        "INSERT INTO dim_dataset VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def _staged_rows(con: duckdb.DuckDBPyConnection, table: str) -> int:
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0]:
        return 0
    return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _build_dim_label(con: duckdb.DuckDBPyConnection) -> None:
    con.executemany("INSERT INTO dim_label VALUES (?, ?, ?, ?, ?, ?)", LABELS)


def _build_dim_instrument(
    con: duckdb.DuckDBPyConnection, specs: dict[str, DatasetSpec]
) -> None:
    nifty = specs.get("nifty")
    if nifty is None:
        return
    raw = nifty.raw or {}
    con.execute(
        """
        INSERT INTO dim_instrument VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            1,
            str(raw.get("instrument") or "NIFTY50"),
            "Nifty 50 index",
            "NSE",
            "INR",
            "nifty",
        ],
    )


def dim_date_bounds(con: duckdb.DuckDBPyConnection) -> tuple[str, str] | None:
    row = con.execute("SELECT min(full_date), max(full_date) FROM dim_date").fetchone()
    if not row or row[0] is None:
        return None
    return row[0].isoformat(), row[1].isoformat()


# ---------------------------------------------------------------------------
# v2 dimensions
# ---------------------------------------------------------------------------

STOCK_DIM_TABLES = (
    "dim_sector",
    "dim_stock",
    "dim_stock_alias",
    "dim_event",
)


def build_stock_dims(
    con: duckdb.DuckDBPyConnection,
    *,
    sector_cfg: SectorConfig,
    alias_rules: AliasRules,
    events: list[EventEntry],
    window: tuple[str, str],
) -> dict[str, Any]:
    """Build dim_sector, dim_stock, dim_stock_alias and dim_event.

    dim_stock's universe is the union of the symbols the index lists and the
    symbols we hold prices for. A symbol in the index with no usable price
    history still gets a row, because dropping it would make a 50-stock index
    silently become a 47-stock one; `dim_stock.price_source` is NULL for it and
    `is_survivor` is FALSE.
    """
    window_start, window_end = window
    report: dict[str, Any] = {}

    for name in STOCK_DIM_TABLES:
        con.execute(f"DROP TABLE IF EXISTS {name}")

    _build_dim_sector(con, sector_cfg)
    report["dim_sector"] = _count(con, "dim_sector")

    sector_keys = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT sector_name, sector_key FROM dim_sector"
        ).fetchall()
    }

    meta_rows = _read_stg_meta(con)
    price_rows = _read_stock_price_bounds(con)
    universe = sorted(set(meta_rows) | set(price_rows))

    rows, unknown, unmapped_aliases = [], [], []
    for i, symbol in enumerate(universe, start=1):
        company, industry, isin = meta_rows.get(symbol, ("", "", ""))
        first, last, n = price_rows.get(symbol, (None, None, 0))
        entry = resolve_sector(symbol, industry or None, sector_cfg)

        sector_name = entry.sector
        sector_key = sector_keys.get(sector_name)
        sector_source = entry.source
        if sector_key is None:
            # Either the symbol had no usable industry, or it named a sector
            # that is not one of the configured ten. Both are reported, and
            # neither is guessed at.
            unknown.append(
                {
                    "symbol": symbol,
                    "sector": sector_name,
                    "reason": (
                        "sector not in dim_sector" if sector_name else "no industry label"
                    ),
                }
            )
            sector_name, sector_source = "", "unknown"

        # Listed on or before the window start => it was really in the index
        # during the period we are studying. Later, or no prices at all, means
        # it only appears because we are using today's constituent list.
        is_survivor = bool(first is not None and first.isoformat() <= window_start)

        rows.append(
            (
                i, symbol,
                company or entry.company or "",
                sector_key,
                isin or None,
                symbol in meta_rows,
                first, last,
                sector_source,
                "yahoo_chart_api" if n else None,
                is_survivor,
            )
        )

    con.execute(
        """
        CREATE TABLE dim_stock (
            stock_key        INTEGER PRIMARY KEY,
            symbol           VARCHAR UNIQUE,
            company_name     VARCHAR,
            sector_key       INTEGER,
            isin             VARCHAR,
            in_nifty50       BOOLEAN,
            first_trade_date DATE,
            last_trade_date  DATE,
            sector_source    VARCHAR,
            price_source     VARCHAR,
            is_survivor      BOOLEAN
        )
        """
    )
    _insert_many(con, "INSERT INTO dim_stock VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    report["dim_stock"] = len(rows)
    report["dim_stock_unknown_sector"] = unknown
    report["dim_stock_without_prices"] = sorted(
        s for s in universe if s not in price_rows
    )
    report["dim_stock_not_survivors"] = sorted(
        r[1] for r in rows if not r[10]
    )

    _build_dim_stock_alias(con, alias_rules, universe, unmapped_aliases)
    report["dim_stock_alias"] = _count(con, "dim_stock_alias")
    report["dim_stock_alias_without_stock"] = unmapped_aliases

    _build_dim_event(con, events, window)
    report["dim_event"] = _count(con, "dim_event")
    report["dim_event_out_of_window"] = sorted(
        e.label for e in events
        if not (window_start <= e.date.isoformat() <= window_end)
    )

    for name in ("dim_sector", "dim_stock", "dim_stock_alias", "dim_event"):
        log.info("%s: %s rows", name, human_int(report[name]))
    if unknown:
        log.warning("dim_stock: %s symbols have no resolvable sector", len(unknown))
    return report


def _build_dim_sector(con: duckdb.DuckDBPyConnection, cfg: SectorConfig) -> None:
    con.execute(
        "CREATE TABLE dim_sector (sector_key INTEGER PRIMARY KEY, sector_name VARCHAR UNIQUE)"
    )
    con.executemany(
        "INSERT INTO dim_sector VALUES (?, ?)",
        [(i, name) for i, name in enumerate(cfg.sectors, start=1)],
    )


def _read_stg_meta(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[str, str, str]]:
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'stg_meta'"
    ).fetchone()[0]:
        return {}
    return {
        r[0]: (r[1] or "", r[2] or "", r[3] or "")
        for r in con.execute(
            "SELECT symbol, company_name, industry, isin FROM stg_meta"
        ).fetchall()
    }


def _read_stock_price_bounds(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[Any, Any, int]]:
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'stg_stocks'"
    ).fetchone()[0]:
        return {}
    return {
        r[0]: (r[1], r[2], int(r[3]))
        for r in con.execute(
            "SELECT symbol, min(trade_date), max(trade_date), count(*) "
            "FROM stg_stocks GROUP BY symbol"
        ).fetchall()
    }


def _build_dim_stock_alias(
    con: duckdb.DuckDBPyConnection,
    rules: AliasRules,
    universe: list[str],
    unmapped: list[dict[str, str]],
) -> None:
    con.execute(
        """
        CREATE TABLE dim_stock_alias (
            stock_key       INTEGER,
            alias           VARCHAR,
            alias_type      VARCHAR,
            case_sensitive  BOOLEAN,
            needs_context   BOOLEAN,
            provider_symbol VARCHAR
        )
        """
    )
    known = set(universe)
    rows: list[tuple[Any, ...]] = []
    for symbol in sorted(rules.aliases):
        if symbol not in known:
            # The alias config lists companies that are not current Nifty 50
            # constituents (Wipro, HDFC, Yes Bank...). They are legitimate
            # headline spellings but they are not stocks in this study, so they
            # are reported rather than silently loaded.
            unmapped.append({"symbol": symbol, "reason": "not a dim_stock member"})
            continue
        stock_key = int(
            con.execute("SELECT stock_key FROM dim_stock WHERE symbol = ?", [symbol])
            .fetchone()[0]
        )
        entry = rules.aliases[symbol]
        for alias in dict.fromkeys(entry.get("names", [])):
            alias = str(alias).strip()
            if not alias:
                continue
            case_sensitive = alias in entry.get("case_sensitive", [])
            needs_context = alias in entry.get("ambiguous", [])
            # A bare upper-case token that matches the listing symbol is a
            # ticker; anything with a space is a written name.
            alias_type = "ticker" if (alias == symbol or alias.isupper()) else "name"
            rows.append(
                (stock_key, alias, alias_type, case_sensitive, needs_context, None)
            )
    _insert_many(con, "INSERT INTO dim_stock_alias VALUES (?,?,?,?,?,?)", rows)


def _build_dim_event(
    con: duckdb.DuckDBPyConnection,
    events: list[EventEntry],
    window: tuple[str, str],
) -> None:
    con.execute(
        """
        CREATE TABLE dim_event (
            event_key   INTEGER PRIMARY KEY,
            event_label VARCHAR,
            event_type  VARCHAR,
            start_date  DATE,
            end_date    DATE,
            sector_key  INTEGER
        )
        """
    )
    window_start, window_end = window
    kept = [e for e in events if window_start <= e.date.isoformat() <= window_end]
    _insert_many(
        con,
        "INSERT INTO dim_event VALUES (?,?,?,?,?,?)",
        [
            (i, e.label, e.event_type, e.date, e.date, None)
            for i, e in enumerate(kept, start=1)
        ],
    )
