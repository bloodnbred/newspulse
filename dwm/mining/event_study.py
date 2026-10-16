"""Event study: how did sectors react around a named event?

The question this answers is "what happened to prices around this event", not
"what did this event cause". Those are different claims and only the first is
supportable from this data.

The method is the standard market model:

    r_stock,t = alpha + beta * r_nifty,t + e_t        (estimated pre-event)
    AR_t       = r_stock,t - (alpha + beta * r_nifty,t)
    CAR[a,b]   = sum of AR over sessions [a, b]

Estimating alpha and beta on PRE-EVENT data only is the part that matters. Fit
them on the whole window and the model has already absorbed part of the event's
own effect, which shrinks the abnormal return toward zero - the event looks like
nothing happened.

Every invalid result is KEPT with a reason rather than dropped. A stock with too
little pre-event history is a fact about the data, and silently omitting it would
make the surviving average look more confident than it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb
import numpy as np

from dwm.logging_utils import get, human_int
from dwm.mining.common import (
    MIN_SAMPLE,
    cumulative_abnormal_return,
    ols_market_model,
    safe_round,
)

log = get("dwm.mining.event_study")

# Estimation window in trading sessions, before the event.
ESTIMATION_START, ESTIMATION_END = -130, -11
MIN_ESTIMATION_DAYS = MIN_SAMPLE["estimation_window"]

#: Events closer together than this overlap, and one can contaminate the other's
#: estimation window. Flagged rather than dropped: the user asked to see it.
OVERLAP_SESSIONS = 10

DDL = """
CREATE OR REPLACE TABLE mining_event_study (
    event_key INTEGER, stock_key INTEGER, sector_key INTEGER,
    alpha DOUBLE, beta DOUBLE, r_squared DOUBLE, est_days INTEGER,
    car_0_1 DOUBLE, car_0_5 DOUBLE, car_m1_5 DOUBLE,
    event_day DATE, sessions_after INTEGER,
    valid BOOLEAN, reason VARCHAR
)
"""


@dataclass(slots=True)
class StockResult:
    event_key: int
    stock_key: int
    sector_key: int | None
    alpha: float
    beta: float
    r_squared: float
    est_days: int
    car_0_1: float
    car_0_5: float
    car_m1_5: float
    event_day: date | None
    sessions_after: int
    valid: bool
    reason: str


def event_index(sessions: list[date], start: date) -> int:
    """Index of the first trading session on or after an event's date.

    An event landing on a weekend or holiday maps to the following session,
    which is what a reader expects. Returns -1 when the event falls after the
    stock's last price, so the caller can record that as an invalid result
    rather than silently skipping the pair.

    Works off the stock's own session list rather than querying, because this
    runs once per stock per event and a query each time would mean hundreds.
    """
    for i, day in enumerate(sessions):
        if day >= start:
            return i
    return -1


def study_stock(
    stock_key: int,
    sector_key: int | None,
    event_key: int,
    sessions: list[date],
    stock_returns: np.ndarray,
    market_returns: np.ndarray,
    event_index: int,
) -> StockResult:
    """One stock's market model and cumulative abnormal returns around an event.

    `sessions` must be the trading dates this stock actually has a return for,
    ascending, and the two return arrays must align with it.
    """
    reason = ""
    lo, hi = event_index + ESTIMATION_START, event_index + ESTIMATION_END

    if event_index < 0:
        return StockResult(event_key, stock_key, sector_key, np.nan, np.nan, np.nan,
                           0, np.nan, np.nan, np.nan, None, -1, False,
                           "event date is outside this stock's price history")

    est_lo, est_hi = max(0, lo), min(len(sessions) - 1, hi)
    est_days = max(0, est_hi - est_lo + 1)
    if est_days < MIN_ESTIMATION_DAYS:
        return StockResult(
            event_key, stock_key, sector_key, np.nan, np.nan, np.nan, est_days,
            np.nan, np.nan, np.nan, sessions[event_index],
            len(sessions) - 1 - event_index, False,
            f"only {est_days} pre-event sessions, need {MIN_ESTIMATION_DAYS}",
        )

    alpha, beta, r2 = ols_market_model(
        stock_returns[est_lo : est_hi + 1], market_returns[est_lo : est_hi + 1]
    )
    if not np.isfinite(beta):
        return StockResult(event_key, stock_key, sector_key, alpha, beta, r2, est_days,
                           np.nan, np.nan, np.nan, sessions[event_index],
                           len(sessions) - 1 - event_index, False,
                           "market return had no variation in the estimation window")

    expected = alpha + beta * market_returns
    abnormal = stock_returns - expected
    # Windows are relative to the event session: CAR[-1,+5] and CAR[0,+5] both
    # start one or zero sessions after it.
    car_0_1 = cumulative_abnormal_return(abnormal, event_index, event_index + 1)
    car_0_5 = cumulative_abnormal_return(abnormal, event_index, event_index + 5)
    car_m1_5 = cumulative_abnormal_return(abnormal, event_index - 1, event_index + 5)

    if not reason:
        reason = "ok"
    return StockResult(
        event_key, stock_key, sector_key, alpha, beta, r2, est_days,
        car_0_1, car_0_5, car_m1_5, sessions[event_index],
        len(sessions) - 1 - event_index, True, reason,
    )


def run(
    con: duckdb.DuckDBPyConnection, *, event_key: int | None = None
) -> dict[str, Any]:
    """Fit the market model for every stock around every event."""
    con.execute(DDL)

    events = con.execute(
        """
        SELECT event_key, event_label, event_type, start_date
        FROM dim_event
        WHERE (? IS NULL OR event_key = ?)
        ORDER BY start_date
        """,
        [event_key, event_key],
    ).fetchall()
    if not events:
        return {"events": 0, "results": 0, "note": "no events in dim_event"}

    # Market returns, indexed by trade_date, for the whole window.
    market = {
        r[0]: float(r[1])
        for r in con.execute(
            """
            SELECT f.trade_date, m.return_pct
            FROM fact_stock_daily f
            JOIN fact_market_daily m ON m.trade_date = f.trade_date
            WHERE f.stock_key = (SELECT min(stock_key) FROM dim_stock)
              AND m.return_pct IS NOT NULL
            """
        ).fetchall()
    }
    if not market:
        return {"events": len(events), "results": 0,
                "note": "no overlapping market returns"}

    stocks = con.execute(
        """
        SELECT f.stock_key, d.sector_key, f.trade_date, f.return_pct
        FROM v_stock_daily_clean f
        JOIN dim_stock d ON d.stock_key = f.stock_key
        WHERE f.return_pct IS NOT NULL
        ORDER BY f.stock_key, f.trade_date
        """
    ).fetchall()

    by_stock: dict[int, list[tuple[date, float]]] = {}
    for stock_key, sector_key, trade_date, ret in stocks:
        by_stock.setdefault(int(stock_key), []).append((trade_date, float(ret), int(sector_key) if sector_key else None))
    sector_of = {sk: vals[0][2] for sk, vals in by_stock.items()}

    results: list[StockResult] = []
    for ek, _label, _etype, start in events:
        for stock_key, series in by_stock.items():
            sessions = [s[0] for s in series]
            returns = np.array([s[1] for s in series], dtype=float)
            market_returns = np.array(
                [market.get(d, np.nan) for d in sessions], dtype=float
            )
            # A stock can trade on a day the index series lacks; drop those days
            # from BOTH arrays so they stay aligned.
            keep = np.isfinite(market_returns)
            if keep.sum() < MIN_ESTIMATION_DAYS + 6:
                continue
            sessions = [d for d, k in zip(sessions, keep, strict=True) if k]
            returns = returns[keep]
            market_returns = market_returns[keep]

            target = event_index(sessions, start)
            if target < 0:
                continue
            results.append(
                study_stock(
                    stock_key, sector_of.get(stock_key), int(ek),
                    sessions, returns, market_returns, target,
                )
            )

    rows = [
        (
            r.event_key, r.stock_key, r.sector_key,
            safe_round(r.alpha), safe_round(r.beta), safe_round(r.r_squared),
            r.est_days,
            safe_round(r.car_0_1), safe_round(r.car_0_5), safe_round(r.car_m1_5),
            r.event_day, r.sessions_after, r.valid, r.reason,
        )
        for r in results
    ]
    con.executemany(
        "INSERT INTO mining_event_study VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )

    valid = [r for r in results if r.valid]
    summary = {
        "events": len(events),
        "results": len(results),
        "valid": len(valid),
        "invalid": len(results) - len(valid),
        "invalid_reasons": _reason_counts(results),
        "overlaps": overlapping_events(con, events),
        "by_event": aggregate(con),
    }
    log.info(
        "event study: %s valid of %s stock-event pairs across %s events",
        human_int(len(valid)), human_int(len(results)), len(events),
    )
    return summary


def _reason_counts(results: list[StockResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        if not r.valid:
            key = r.reason.split(",")[0].split(" only")[0].strip()
            counts[key] = counts.get(key, 0) + 1
    return counts


def overlapping_events(
    con: duckdb.DuckDBPyConnection, events: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    """Events whose dates are within OVERLAP_SESSIONS of each other.

    Reported, not removed. When two events collide, one event's effect is inside
    the other's estimation window, so the model is partly fitted on a period that
    contains the event it is meant to exclude.
    """
    dates = [(int(k), str(label), d) for k, label, _, d in events]
    out: list[dict[str, Any]] = []
    for i in range(len(dates)):
        for j in range(i + 1, len(dates)):
            a, b = dates[i], dates[j]
            gap = con.execute(
                """
                SELECT count(*) FROM (
                    SELECT DISTINCT trade_date FROM fact_stock_daily
                    WHERE trade_date > ? AND trade_date <= ?
                )
                """,
                [a[2], b[2]],
            ).fetchone()[0]
            if gap <= OVERLAP_SESSIONS:
                out.append(
                    {
                        "a": a[1], "b": b[1], "sessions_between": int(gap),
                        "note": (
                            "one event falls inside the other's estimation "
                            "window, so its CAR is partly contaminated"
                        ),
                    }
                )
    return out


def aggregate(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Per event and sector: mean CAR, how many stocks, and a t-statistic.

    The cross-sectional t-test is a plain one-sample test of whether the average
    stock's CAR differs from zero, across stocks on the same day. It says nothing
    about whether the event caused the move.
    """
    from scipy import stats

    out: list[dict[str, Any]] = []
    for event, sector in con.execute(
        """
        SELECT event_key, sector_key FROM mining_event_study
        WHERE valid AND sector_key IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2
        """
    ).fetchall():
        rows = con.execute(
            """
            SELECT car_0_5 FROM mining_event_study
            WHERE valid AND event_key = ? AND sector_key = ? AND car_0_5 IS NOT NULL
            """,
            [event, sector],
        ).fetchall()
        values = np.array([r[0] for r in rows], dtype=float)
        n = len(values)
        if n == 0:
            continue
        mean = float(values.mean())
        # A t-test against zero across 1 or 2 stocks is not meaningful.
        if n >= 3:
            t_stat, p_value = stats.ttest_1samp(values, 0.0)
            t_stat, p_value = float(t_stat), float(p_value)
        else:
            t_stat, p_value = None, None
        out.append(
            {
                "event_key": int(event),
                "sector_key": int(sector),
                "n_stocks": n,
                "mean_car_0_5": safe_round(mean, 4),
                "sd": safe_round(float(values.std(ddof=1)) if n > 1 else None, 4),
                "t_statistic": safe_round(t_stat, 4),
                "p_value": safe_round(p_value, 6),
                "testable": n >= 3,
            }
        )
    return out
