"""Shared helpers for the v2 mining modules.

Three things live here that every v2 analysis needs and that each module should
not reinvent:

  * the chronological splitter, which is the only safe way to split time series
  * a seeded, reproducible cache so repeated runs give identical numbers
  * a `claim`/`caveat` pair, so a stored result carries its own limit with it

The last one matters more than it looks. A mining result stored as a bare number
is a number that will eventually be quoted without the sentence that qualified
it. Writing the caveat into the same record makes that harder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

SEED = 42

# The chronological split. Not shuffled, ever: shuffling a time series puts
# tomorrow's information into today's training row, which inflates accuracy and
# is the single most common way a time-series model is quietly evaluated wrong.
TRAIN_FRACTION = 0.70
VALIDATION_FRACTION = 0.15

#: Minimum rows before a statistical result is worth publishing at all.
MIN_SAMPLE = {
    "estimation_window": 100,
    "news_days_for_sensitivity": 100,
    "rule_support_count": 30,
    "event_study_stocks_per_sector": 5,
}


@dataclass(slots=True)
class Split:
    """Three chronological slices, with the boundary dates that define them."""

    train: Any
    validation: Any
    test: Any
    train_end: date
    validation_end: date

    def sizes(self) -> dict[str, int]:
        return {
            "train": len(self.train),
            "validation": len(self.validation),
            "test": len(self.test),
        }


def chronological_split(
    dates: Any, *, train_fraction: float = TRAIN_FRACTION,
    validation_fraction: float = VALIDATION_FRACTION,
) -> Split:
    """Split row indices by date. The last date is always in test.

    The boundaries are derived from the data, never configured as dates, so a
    rerun on a different corpus splits at different places and still cannot leak.
    """
    dates = np.asarray(dates)
    unique = np.sort(np.unique(dates))
    if len(unique) < 3:
        raise ValueError(
            f"need at least 3 distinct dates to split chronologically, got {len(unique)}"
        )
    n_train = max(1, int(len(unique) * train_fraction))
    n_valid = max(1, int(len(unique) * validation_fraction))
    if n_train + n_valid >= len(unique):
        n_valid = 1
        n_train = len(unique) - 2

    train_end = unique[min(n_train, len(unique) - 1)]
    validation_end = unique[min(n_train + n_valid, len(unique) - 1)]

    return Split(
        train=np.flatnonzero(dates <= train_end),
        validation=np.flatnonzero((dates > train_end) & (dates <= validation_end)),
        test=np.flatnonzero(dates > validation_end),
        train_end=train_end,
        validation_end=validation_end,
    )


def ols_market_model(
    stock_returns: np.ndarray, market_returns: np.ndarray
) -> tuple[float, float, float]:
    """Fit r_stock = alpha + beta * r_market.

    Returns (alpha, beta, r_squared). Returns NaN rather than raising when the
    inputs are degenerate, because one stock with a flat series must not abort a
    run over fifty.
    """
    if len(stock_returns) < 3 or len(market_returns) < 3:
        return float("nan"), float("nan"), float("nan")
    market = np.asarray(market_returns, dtype=float)
    stock = np.asarray(stock_returns, dtype=float)
    if np.allclose(market.std(), 0):
        return float("nan"), float("nan"), float("nan")
    beta, alpha = np.polyfit(market, stock, 1)
    predicted = alpha + beta * market
    ss_res = float(((stock - predicted) ** 2).sum())
    ss_tot = float(((stock - stock.mean()) ** 2).sum())
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(alpha), float(beta), r_squared


def cumulative_abnormal_return(
    abnormal: np.ndarray, start: int, end: int
) -> float:
    """CAR over [start, end] inclusive, clamped to the series.

    Zero when the window is entirely outside the data, which is the honest
    answer for an event near the end of the window: there is no evidence, and
    returning 0.0 is not the same as claiming no reaction.
    """
    if len(abnormal) == 0:
        return float("nan")
    lo = max(0, start)
    hi = min(len(abnormal) - 1, end)
    if lo > hi:
        return float("nan")
    return float(np.sum(abnormal[lo : hi + 1]))


def rolling_z(values: np.ndarray, window: int) -> np.ndarray:
    """Trailing z-score of each value against the PRECEDING `window` values.

    Two decisions, both load-bearing:

    * The window excludes the current value. Including it means a huge move
      drags the mean and the standard deviation towards itself, which caps the
      z-score near sqrt(n) - about 7.7 for a 60-day window - however extreme the
      move actually was. A surprise detector that cannot register more than
      7.7 sigma is not measuring surprises.

    * The window is TRAILING. A centred window would score a day using later
      information, which is exactly the look-ahead rule 11 forbids.

    A window with zero variance yields NaN, not 0.0. Against a perfectly flat
    baseline there is no finite z-score, and reporting 0 would claim that an
    enormous move was entirely normal. Callers skip NaN rows.
    """
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) <= window:
        return out
    for i in range(window, len(values)):
        chunk = values[i - window : i]
        mu = chunk.mean()
        sd = chunk.std(ddof=1)
        if not np.isfinite(sd) or sd <= 0:
            continue
        out[i] = (values[i] - mu) / sd
    return out


@dataclass(slots=True)
class Finding:
    """One result, carrying the sentence that limits it."""

    id: str
    title: str
    value: Any = None
    n: int | None = None
    concept: str = ""
    caveat: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "title": self.title,
            "value": self.value,
            "n": self.n,
            "concept": self.concept,
        }
        if self.caveat:
            payload["caveat"] = self.caveat
        payload.update(self.extra)
        return payload


def seeded_rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def max_drawdown(prices: np.ndarray) -> float:
    """Largest peak-to-trough decline, as a positive fraction (0.2 = a 20% fall).

    Computed on the running maximum. A negative drawdown would be ambiguous to
    read; this returns the magnitude and the caller says "fell".
    """
    prices = np.asarray(prices, dtype=float)
    if len(prices) < 2:
        return float("nan")
    running_max = np.maximum.accumulate(prices)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = 1.0 - prices / running_max
    return float(np.nanmax(drawdowns))


def safe_round(value: Any, digits: int = 6) -> float | None:
    """Round for storage, mapping NaN/inf to None rather than to a number.

    DuckDB stores NaN happily and it then prints as `nan` in JSON, which the
    dashboard would render as text. Null is the honest representation.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(out):
        return None
    return round(out, digits)
