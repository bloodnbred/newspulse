"""v2 stock prices: one daily CSV per Nifty 50 constituent.

The pack assumed these CSVs would be downloaded by hand into data/raw/stocks/.
They do not have to be. The Yahoo Finance chart endpoint returns daily OHLCV
plus a dividend-adjusted close for every `.NS` symbol, needs no key and no
account, and all 50 current constituents resolve on it (verified 2026-10-14).

Three things about that endpoint are load-bearing and easy to get wrong:

1. **Never pass `range=max` or `range=10y` on their own.** Yahoo silently
   downgrades those to *monthly* bars. The reply looks like data and is not.
   Explicit `period1`/`period2` epochs with `interval=1d` are required, which is
   what `chart_url` below does.
2. **`close` is split-adjusted but not dividend-adjusted.** For ITC in July 2020
   the two are 199.85 and 147.14. Both are cached; V2 computes returns from
   `adj_close` and records which basis it used.
3. **`turnover` is not published at all.** Any figure derived from it is derived,
   and has to be labelled that way wherever it is shown.

Symbols come from `stg_meta` (the NSE constituent list) rather than being listed
here, so the index membership is the source of truth. A symbol the price source
does not serve is recorded as unmatched in `etl_audit` and still gets a
`dim_stock` row; it is never quietly dropped.
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.error
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from dwm.config import Settings, SourceSpec, raw_dir
from dwm.ingest import audit
from dwm.ingest.fetch import fetch_bytes, write_provenance
from dwm.logging_utils import get, human_int, step

log = get("dwm.ingest.stocks")

CSV_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]
DEFAULT_TEMPLATE = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    "?period1={start}&period2={end}&interval=1d&events=div%2Csplit"
)
DEFAULT_SUFFIX = ".NS"
# Yahoo asks for a browser-like client; the stdlib default gets 429 quickly.
REQUEST_DELAY_S = 0.35


def chart_url(template: str, ticker: str, start: date, end: date) -> str:
    """Build a daily-bar URL with explicit epoch bounds.

    `end` is exclusive upstream, so it is pushed one day past the last date we
    want, otherwise the final session is silently dropped.
    """
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86_400
    return template.replace("{ticker}", ticker).replace("{start}", str(p1)).replace(
        "{end}", str(p2)
    )


@dataclass(slots=True)
class SymbolOutcome:
    symbol: str
    ticker: str
    ok: bool
    rows: int = 0
    path: Path | None = None
    reused: bool = False
    first_date: str = ""
    last_date: str = ""
    error: str = ""


@dataclass(slots=True)
class StocksOutcome:
    ok: bool
    symbols_requested: int = 0
    symbols_ok: int = 0
    rows: int = 0
    unmatched: list[dict[str, str]] = field(default_factory=list)
    audit_id: int | None = None
    error: str | None = None
    # The staging step's own row counts and table name, carried through so the
    # CLI can report them the same way it reports a v1 ingest.
    staging: dict[str, Any] = field(default_factory=dict)


def parse_chart(payload: bytes) -> list[dict[str, Any]]:
    """Turn a Yahoo chart reply into daily rows.

    Returns [] rather than raising when the symbol has no series, so one missing
    ticker cannot abort a 50-symbol run. Rows whose close is null (holidays, or
    a session the source has no quote for) are dropped, because a null close
    cannot become a return later and keeping it would only move the problem.
    """
    doc = json.loads(payload.decode("utf-8", errors="replace"))
    chart = doc.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"].get("description") or chart["error"]))
    results = chart.get("result") or []
    if not results:
        return []
    result = results[0]
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj_block = ((result.get("indicators") or {}).get("adjclose") or [{}])[0]
    adj = adj_block.get("adjclose") or []

    rows: list[dict[str, Any]] = []
    for i, stamp in enumerate(stamps):
        close = _at(quote, "close", i)
        if close is None:
            continue
        adj_close = _at({"adjclose": adj}, "adjclose", i)
        rows.append(
            {
                "date": datetime.fromtimestamp(stamp, UTC).date().isoformat(),
                "open": _at(quote, "open", i),
                "high": _at(quote, "high", i),
                "low": _at(quote, "low", i),
                "close": close,
                "adj_close": adj_close if adj_close is not None else close,
                "volume": _at(quote, "volume", i),
            }
        )
    return rows


def _at(block: dict[str, Any], name: str, i: int) -> Any:
    values = block.get(name) or []
    return values[i] if i < len(values) else None


def write_symbol_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: ("" if row.get(k) is None else row.get(k)) for k in CSV_COLUMNS}
        )
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _classify(code: int, ticker: str, start: date, end: date) -> str:
    """Turn a provider status code into a reason a human can act on.

    The two failures look identical from the outside but mean opposite things.
    Verified 2026-10-14: ETERNAL.NS, JIOFIN.NS and MAXHEALTH.NS all answer 400
    for 2015-2020 and return daily bars for 2023-2026. A 400 is the provider
    saying "nothing in that range", which for this project almost always means
    the company listed after the news window closed. A 404 means the ticker is
    not served at all, which is a genuine source gap.

    Conflating the two would let "listed after the window" be reported as a
    broken ticker, which is a different and much smaller problem.
    """
    if code == 400:
        return (
            f"HTTP 400: no daily bars in {start.isoformat()}..{end.isoformat()}. "
            "The ticker resolves (it returns data for a later window), so this "
            "stock most likely listed after the news window, not that the source "
            "is missing it."
        )
    if code == 404:
        return (
            f"HTTP 404: the provider does not serve {ticker}. "
            "Genuine source gap; needs a symbol alias in config/stock_aliases.yaml."
        )
    if code == 429:
        return "HTTP 429: rate limited; retry later with a larger delay"
    return f"HTTP {code}"


def fetch_symbol(
    symbol: str,
    directory: Path,
    start: date,
    end: date,
    *,
    template: str = DEFAULT_TEMPLATE,
    suffix: str = DEFAULT_SUFFIX,
    force: bool = False,
    timeout: int = 30,
) -> SymbolOutcome:
    """Fetch one symbol's daily bars into directory/<SYMBOL>.csv."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{symbol}.csv"
    ticker = f"{symbol}{suffix}"
    url = chart_url(template, ticker, start, end)

    if target.exists() and not force:
        rows = _count_rows(target)
        first, last = _bounds(target)
        return SymbolOutcome(
            symbol, ticker, ok=True, rows=rows, path=target, reused=True,
            first_date=first, last_date=last,
        )

    try:
        payload = fetch_bytes(url, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return SymbolOutcome(symbol, ticker, ok=False, error=_classify(exc.code, ticker, start, end))
    except (urllib.error.URLError, OSError) as exc:
        return SymbolOutcome(symbol, ticker, ok=False, error=f"{type(exc).__name__}: {exc}")

    try:
        rows = parse_chart(payload)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return SymbolOutcome(symbol, ticker, ok=False, error=f"unparseable reply: {exc}")

    if not rows:
        return SymbolOutcome(symbol, ticker, ok=False, error="no daily bars returned")

    write_symbol_csv(target, rows)
    write_provenance(
        target,
        {
            "dataset": "stocks",
            "symbol": symbol,
            "provider_ticker": ticker,
            "source_url": url,
            "rows": len(rows),
            "requested_from": start.isoformat(),
            "requested_to": end.isoformat(),
            "return_basis": "adj_close",
            "turnover": "not published by this source; derive close * volume and label it derived",
        },
    )
    return SymbolOutcome(
        symbol, ticker, ok=True, rows=len(rows), path=target,
        first_date=rows[0]["date"], last_date=rows[-1]["date"],
    )


def _count_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def _bounds(path: Path) -> tuple[str, str]:
    dates: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            value = (row.get("date") or "").strip()
            if value:
                dates.append(value)
    return (dates[0], dates[-1]) if dates else ("", "")


def resolve_fetch_range(
    con: duckdb.DuckDBPyConnection, settings: Settings
) -> tuple[date, date]:
    """Fetch bounds, derived from the data rather than hard-coded (rule 4).

    These are the in-window headline dates, the same span v1 analyses. Asking
    for the whole 2001-2020 corpus instead would roughly double the cache for
    rows V2 would filter out anyway. When no in-window headlines exist yet, the
    full corpus span is used and V2 still filters.
    """
    if con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'fact_headline'"
    ).fetchone()[0]:
        row = con.execute(
            "SELECT min(publish_date), max(publish_date) "
            "FROM fact_headline WHERE in_window"
        ).fetchone()
        if row and row[1] is not None:
            log.info("stocks: fetching the analysis window %s..%s", row[0], row[1])
            return row[0], row[1]

    for table in ("fact_headline", "cln_headline"):
        if not con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]:
            continue
        row = con.execute(f"SELECT min(publish_date), max(publish_date) FROM {table}").fetchone()
        if row and row[1] is not None:
            log.warning(
                "stocks: no in_window flag yet; fetching the full corpus span "
                "%s..%s. V2 filters to the analysis window.", row[0], row[1],
            )
            return row[0], row[1]

    log.warning(
        "stocks: no built warehouse to take the window from; requesting full history. "
        "V2 still filters to the news window."
    )
    return date(1990, 1, 1), date.today()


def constituents(con: duckdb.DuckDBPyConnection, *, limit: int | None = None) -> list[str]:
    """Symbols to fetch, read from stg_meta, in a deterministic order."""
    present = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'stg_meta'"
    ).fetchone()[0]
    if not present:
        raise FileNotFoundError(
            "stg_meta is empty; run `python -m dwm ingest --dataset meta` first"
        )
    sql = "SELECT DISTINCT symbol FROM stg_meta ORDER BY symbol"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in con.execute(sql).fetchall()]


DDL = """
CREATE OR REPLACE TABLE stg_stocks (
    stg_row_id      BIGINT,
    stg_dataset_code VARCHAR,
    stg_filename    VARCHAR,
    stg_ingested_at TIMESTAMP,
    stg_row_num     BIGINT,
    symbol          VARCHAR,
    trade_date      DATE,
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    adj_close       DOUBLE,
    volume          DOUBLE
)
"""


def stage(
    con: duckdb.DuckDBPyConnection,
    directory: Path,
    *,
    limit_symbols: int | None = None,
) -> dict[str, Any]:
    """Load every cached per-symbol CSV into one stg_stocks table.

    The symbol comes from the file name, which is what `symbol_from: filename`
    declares. A per-file row count is recorded in etl_audit so a symbol that
    contributes nothing is visible rather than silently absent.
    """
    audit.ensure_audit_table(con)
    files = sorted(directory.glob("*.csv"))
    if not files:
        raise FileNotFoundError(
            f"no per-symbol price CSVs in {directory}. "
            f"Run `python -m dwm ingest --dataset stocks` first."
        )
    if limit_symbols:
        files = files[:limit_symbols]

    con.execute(DDL)
    total_read = 0
    total_loaded = 0
    row_id = 0
    per_file: list[dict[str, Any]] = []

    for path in files:
        symbol = path.stem.strip().upper()
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            batch: list[tuple[Any, ...]] = []
            read = 0
            for row in reader:
                read += 1
                day = (row.get("date") or "").strip()
                if not day:
                    continue
                row_id += 1
                batch.append(
                    (
                        row_id, "stocks", path.name, row_id, symbol, day,
                        _num(row.get("open")), _num(row.get("high")),
                        _num(row.get("low")), _num(row.get("close")),
                        _num(row.get("adj_close")), _num(row.get("volume")),
                    )
                )
            if batch:
                con.executemany(
                    "INSERT INTO stg_stocks VALUES (?, ?, ?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
            total_read += read
            total_loaded += len(batch)
            per_file.append({"symbol": symbol, "rows": len(batch)})

    staged = int(con.execute("SELECT count(*) FROM stg_stocks").fetchone()[0])
    distinct = int(
        con.execute("SELECT count(DISTINCT symbol) FROM stg_stocks").fetchone()[0]
    )
    info: dict[str, Any] = {
        "rows_read": total_read,
        "rows_loaded": staged,
        "rows_rejected": max(0, total_read - staged),
        "reconciled": staged == total_read,
        "table": "stg_stocks",
        "files": len(files),
        "distinct_symbols": distinct,
        "symbols_without_rows": sorted(
            f["symbol"] for f in per_file if f["rows"] == 0
        ),
    }
    log.info(
        "stg_stocks: %s rows from %s files (%s symbols)",
        human_int(staged), len(files), distinct,
    )
    return info


def _num(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def fetch_all(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    spec: SourceSpec | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    force: bool = False,
    skip_fetch: bool = False,
    sleep: float = REQUEST_DELAY_S,
) -> tuple[StocksOutcome, list[SymbolOutcome]]:
    """Fetch every constituent's bars. Missing symbols are recorded, not fatal."""
    if start is None or end is None:
        window_lo, window_hi = resolve_fetch_range(con, settings)
        start = start or window_lo
        end = end or window_hi
    lo, hi = start, end
    directory = spec.local_dir() if spec else raw_dir("stocks")
    # --sample caps how many symbols are fetched, not how many rows: truncating
    # rows would leave every stock with a fake start-of-series and break the
    # 20-day volatility window that V2 computes from it.
    symbols = constituents(con, limit=settings.sample)
    template = spec.url_template if spec and spec.url_template else DEFAULT_TEMPLATE
    suffix = spec.ticker_suffix if spec and spec.ticker_suffix else DEFAULT_SUFFIX

    results: list[SymbolOutcome] = []
    for i, symbol in enumerate(symbols, start=1):
        if skip_fetch:
            # Report what is actually on disk. Claiming success without reading
            # the cache would report 50/50 fetched when three symbols have no
            # file at all, which is exactly the number the audit depends on.
            cached = directory / f"{symbol}.csv"
            if cached.exists():
                first, last = _bounds(cached)
                outcome = SymbolOutcome(
                    symbol, f"{symbol}{suffix}", ok=True, reused=True,
                    rows=_count_rows(cached), path=cached,
                    first_date=first, last_date=last,
                )
            else:
                outcome = SymbolOutcome(
                    symbol, f"{symbol}{suffix}", ok=False,
                    error="no cached price file (needs a fetch, not --skip-fetch)",
                )
        else:
            outcome = fetch_symbol(
                symbol, directory, lo, hi,
                template=template, suffix=suffix, force=force,
            )
            if sleep and i < len(symbols):
                time.sleep(sleep)
        results.append(outcome)
        if not outcome.ok:
            log.warning("stocks: %s unavailable (%s)", outcome.symbol, outcome.error)

    outcome = StocksOutcome(
        ok=True,
        symbols_requested=len(symbols),
        symbols_ok=sum(1 for r in results if r.ok),
        rows=sum(r.rows for r in results),
        unmatched=[
            {"symbol": r.symbol, "ticker": r.ticker, "reason": r.error}
            for r in results if not r.ok
        ],
    )
    log.info(
        "stocks: %s of %s symbols fetched, %s rows, %s unmatched",
        outcome.symbols_ok, outcome.symbols_requested, human_int(outcome.rows),
        len(outcome.unmatched),
    )
    return outcome, results


def ingest(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    spec: SourceSpec | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    force: bool = False,
    skip_fetch: bool = False,
) -> StocksOutcome:
    """Fetch then stage, writing one audit row that records the unmatched list."""
    run_id = audit.new_run_id()
    started = datetime.now(UTC).replace(tzinfo=None)
    if start is None or end is None:
        lo, hi = resolve_fetch_range(con, settings)
        start = start or lo
        end = end or hi

    fetch, results = fetch_all(
        con, settings, spec, start=start, end=end, force=force, skip_fetch=skip_fetch
    )
    if not fetch.symbols_ok:
        fetch.error = "no symbol could be fetched"
        fetch.ok = False

    if fetch.ok:
        with step("stage stocks -> stg_stocks") as info:
            info.update(stage(con, spec.local_dir() if spec else raw_dir("stocks")))
    else:
        info = {"skipped": "no price files to stage"}
    fetch.staging = dict(info)

    detail = {
        **info,
        "symbols_requested": fetch.symbols_requested,
        "symbols_ok": fetch.symbols_ok,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "unmatched": fetch.unmatched,
    }
    fetch.audit_id = audit.record(
        con, run_id=run_id, dataset_code="stocks", step="ingest.stocks",
        info=detail, status="ok" if fetch.ok else "failed", started_at=started,
    )
    return fetch
