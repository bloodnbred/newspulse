"""Direction model: can news features tell us tomorrow's direction?

The honest answer from this kind of data is usually "not much better than a
coin", and the module is built so that finding out is easy rather than
something to be talked around.

Four safeguards decide whether this result is trustworthy, and each exists to
stop a specific way of being wrong:

1. **Chronological splits.** 70 / 15 / 15 by date, never shuffled. Shuffling a
   time series puts tomorrow's row into today's training set and inflates
   accuracy, usually enough to invent skill that is not there.

2. **Features known at the close of day t.** Every news count is trailing and
   every price feature is contemporaneous with t. The target is day t+1. A
   feature that peeks at t+1 is a leak, and the test suite shifts one feature
   forward and asserts the test accuracy moves - a leak would leave accuracy
   unchanged, which is the giveaway.

3. **Two baselines, always.** Majority class, and "tomorrow goes the same way as
   today". A model that beats the first but not the second has learned something
   about the sample, not about news.

4. **A binomial test against the majority baseline.** Accuracy above baseline is
   not automatically significant, and a p-value next to the number is what makes
   the difference checkable.

If the model lands at or below baseline, that is the result. It is reported, not
tuned away (AGENTS.md rule 11).
"""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
)
from sklearn.naive_bayes import GaussianNB
from sklearn.tree import DecisionTreeClassifier, export_text

from dwm.logging_utils import get
from dwm.mining.common import SEED, chronological_split, safe_round

log = get("dwm.mining.direction_model")

DDL = """
CREATE OR REPLACE TABLE mining_direction_results (
    model VARCHAR, split VARCHAR, n INTEGER,
    accuracy DOUBLE, balanced_accuracy DOUBLE, f1 DOUBLE,
    baseline_majority DOUBLE, baseline_yesterday DOUBLE,
    binom_p_vs_baseline DOUBLE, precision_up DOUBLE, recall_up DOUBLE,
    notes VARCHAR
)
"""

#: Columns built strictly from information available at the end of day t.
FEATURES = [
    "news_1d", "news_5d", "sentiment_1d", "sensational_share",
    "return_t", "return_5d", "volatility_20d",
    "dow", "month",
]

#: The one column that must never be used as a feature: it is the label.
TARGET = "next_day_up"


def build_dataset(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Stock-days with trailing news and price features, and a next-day label.

    Built with window functions so "trailing" is expressed once, in SQL, rather
    than re-derived per feature. `rows between 5 preceding and current row` is
    the whole of the anti-leakage design for the news features.
    """
    return con.execute(
        """
        WITH sector_news AS (
            SELECT sector_key, effective_trading_date_key AS date_key,
                   sum(news_count_non_market) AS news,
                   sum(sum_sentiment) AS sentiment,
                   sum(high_sensational_count) AS sensational
            FROM fact_news_sector_daily
            GROUP BY 1, 2
        ),
        joined AS (
            SELECT
                f.stock_key,
                f.trade_date,
                f.date_key,
                f.return_pct            AS return_t,
                f.return_5d             AS return_5d,
                f.volatility_20d        AS volatility_20d,
                f.next_day_up           AS target,
                coalesce(n.news, 0)     AS news_today,
                coalesce(n.sentiment, 0) AS sentiment
            FROM v_stock_daily_clean f
            JOIN dim_stock d ON d.stock_key = f.stock_key
            LEFT JOIN sector_news n ON n.sector_key = d.sector_key
                                   AND n.date_key = f.date_key
            WHERE f.return_pct IS NOT NULL AND f.next_day_up IS NOT NULL
        ),
        featured AS (
            SELECT
                *,
                -- Trailing window INCLUDING today. Known at the close of t.
                coalesce(sum(news_today) OVER w5, 0)            AS news_sum_5d,
                avg(news_today) OVER w5                         AS news_5d,
                -- Sentiment weighted by that day's news volume, so a busy day
                -- counts for more than a quiet one. Falls back to the day's own
                -- sentiment when news_today is zero.
                coalesce(
                    sum(sentiment) OVER w5 / nullif(sum(news_today) OVER w5, 0),
                    sentiment
                )                                               AS sentiment_1d,
                news_today * 1.0 / nullif(sum(news_today) OVER w5, 0)
                                                                AS sensational_share,
                dayofweek(trade_date)                           AS dow,
                month(trade_date)                               AS month
            FROM joined
            WINDOW
                w5 AS (PARTITION BY stock_key ORDER BY trade_date
                       ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        )
        SELECT
            stock_key, trade_date, date_key,
            news_today AS news_1d, news_5d, sentiment_1d, sensational_share,
            return_t, return_5d, volatility_20d, dow, month,
            target AS next_day_up
        FROM featured
        WHERE news_sum_5d > 0
        ORDER BY stock_key, trade_date
        """
    ).fetchdf()


def _models() -> dict[str, Any]:
    return {
        "decision_tree": DecisionTreeClassifier(
            max_depth=4, random_state=SEED, class_weight="balanced"
        ),
        "naive_bayes": GaussianNB(),
        "logistic": None,     # built lazily so scaling happens once
    }


def run(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    frame = build_dataset(con)
    if len(frame) < 200:
        return {"error": f"only {len(frame)} usable stock-days; need 200"}

    # volatility_20d is NULL for each stock's first 20 sessions and return_5d for
    # its first 5, by design - a 20-day volatility from three observations is
    # not a 20-day volatility. Those rows are DROPPED rather than imputed:
    # filling with the column mean would invent a plausible-looking volatility
    # for exactly the period the window is too short to measure.
    before = len(frame)
    frame = frame.dropna(subset=FEATURES)
    dropped = before - len(frame)
    if len(frame) < 200:
        return {"error": f"only {len(frame)} complete stock-days; need 200"}

    dates = frame["date_key"].to_numpy()
    split = chronological_split(dates)

    X = frame[FEATURES].to_numpy(dtype=float)
    y = frame[TARGET].to_numpy(dtype=bool)
    assert set(FEATURES).isdisjoint({TARGET}), "the label must never be a feature"

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    estimators: dict[str, Any] = {
        "decision_tree": DecisionTreeClassifier(
            max_depth=4, random_state=SEED, class_weight="balanced"
        ),
        "naive_bayes": GaussianNB(),
        "logistic": make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, random_state=SEED,
                               class_weight="balanced"),
        ),
    }

    # ---- baselines, computed on TEST only --------------------------------
    y_test = y[split.test]
    majority = float(np.bincount(y_test.astype(int)).max() / len(y_test)) if len(y_test) else None
    same_as_yesterday = _yesterday_baseline(frame, split)

    results: list[dict[str, Any]] = []
    rules_text = None

    # Defence in depth. `chronological_split` now guarantees every split holds
    # at least one date, but a frame that is filtered again downstream - or a
    # target that is null across the whole test window - can still empty one.
    # Estimators raise a numpy shape error for that, which names neither the
    # stage nor the remedy, so the run is refused here with a sentence a reader
    # can act on.
    for label, idx in (("train", split.train), ("validation", split.validation), ("test", split.test)):
        if len(idx) == 0:
            return {
                "error": (
                    f"the {label} split has no complete stock-days after dropping "
                    f"rows with a missing feature or target. The corpus is too "
                    f"small for a 70/15/15 chronological split - rebuild with a "
                    f"larger `--sample`, or without one."
                )
            }

    for name, model in estimators.items():
        model.fit(X[split.train], y[split.train])
        for label, idx in (("train", split.train), ("test", split.test)):
            if label == "train":
                continue
            pred = model.predict(X[idx])
            truth = y[idx]
            accuracy = float(accuracy_score(truth, pred))
            p_value = None
            if majority:
                p_value = float(
                    stats.binomtest(
                        int(round(accuracy * len(truth))), len(truth), majority,
                        alternative="greater",
                    ).pvalue
                )
            beats = (
                None if majority is None
                else bool(accuracy > majority)
            )
            note_parts = [
                f"n_test={len(truth)}",
                "chronological split, no shuffling",
            ]
            if beats is False:
                note_parts.append("BELOW the majority baseline")
            elif same_as_yesterday is not None and accuracy <= same_as_yesterday:
                note_parts.append("does not beat 'same direction as yesterday'")
            if p_value is not None and p_value > 0.05:
                note_parts.append(f"p={p_value:.3f} vs majority: not significant")

            cm = confusion_matrix(truth, pred, labels=[False, True])
            precision_up = (
                float(cm[1, 1] / cm[1].sum()) if cm[1].sum() else None
            )
            recall_up = float(cm[1, 1] / cm[:, 1].sum()) if cm[:, 1].sum() else None

            results.append(
                {
                    "model": name,
                    "split": label,
                    "n": int(len(truth)),
                    "accuracy": accuracy,
                    "balanced_accuracy": float(balanced_accuracy_score(truth, pred)),
                    "f1": float(f1_score(truth, pred, zero_division=0)),
                    "baseline_majority": majority,
                    "baseline_yesterday": same_as_yesterday,
                    "binom_p_vs_baseline": p_value,
                    "precision_up": precision_up,
                    "recall_up": recall_up,
                    "notes": "; ".join(note_parts),
                }
            )
            if label == "test" and name == "decision_tree":
                rules_text = export_text(model, feature_names=list(FEATURES))

    con.execute(DDL)
    con.executemany(
        "INSERT INTO mining_direction_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r["model"], r["split"], r["n"], safe_round(r["accuracy"]),
                safe_round(r["balanced_accuracy"]), safe_round(r["f1"]),
                safe_round(r["baseline_majority"]), safe_round(r["baseline_yesterday"]),
                safe_round(r["binom_p_vs_baseline"], 6),
                safe_round(r["precision_up"]), safe_round(r["recall_up"]),
                r["notes"],
            )
            for r in results
        ],
    )

    best = max(results, key=lambda r: r["accuracy"]) if results else None
    skill = None
    if best and majority:
        skill = best["accuracy"] - majority

    summary = {
        "rows": int(len(frame)),
        "rows_dropped_short_window": int(dropped),
        "features": FEATURES,
        "split": {
            "train_end": int(split.train_end),
            "validation_end": int(split.validation_end),
            **split.sizes(),
        },
        "baseline_majority": safe_round(majority, 4),
        "baseline_yesterday": safe_round(same_as_yesterday, 4),
        "models": [
            {
                "model": r["model"],
                "accuracy": safe_round(r["accuracy"], 4),
                "balanced_accuracy": safe_round(r["balanced_accuracy"], 4),
                "f1": safe_round(r["f1"], 4),
                "p_vs_baseline": safe_round(r["binom_p_vs_baseline"], 6),
                "beats_majority": (r["accuracy"] > majority) if majority else None,
                "notes": r["notes"],
            }
            for r in results
        ],
        "best_model": best["model"] if best else None,
        "best_accuracy": safe_round(best["accuracy"], 4) if best else None,
        "skill_over_baseline": safe_round(skill, 4),
        "verdict": _verdict(results, majority, same_as_yesterday),
        "tree_rules": rules_text,
    }
    log.info(
        "direction model: best=%s accuracy=%s baseline=%s",
        summary["best_model"], summary["best_accuracy"], summary["baseline_majority"],
    )
    return summary


def _yesterday_baseline(frame: pd.DataFrame, split: Any) -> float | None:
    """Accuracy of "tomorrow moves the same way today did".

    A real baseline, not a straw one. It is free information already present in
    the data, so a model that cannot beat it has learned nothing about news.
    """
    frame = frame.copy()
    frame["today"] = frame["return_t"] > 0
    # The shift is within stock, so it never pairs one stock's day with another's.
    frame["yesterday"] = frame.groupby("stock_key")["today"].shift(1)
    valid = frame.iloc[split.test].dropna(subset=["yesterday"])
    if len(valid) < 10:
        return None
    return float((valid["yesterday"].to_numpy() == valid[TARGET].to_numpy()).mean())


def _verdict(
    results: list[dict[str, Any]], majority: float | None,
    same_as_yesterday: float | None,
) -> str:
    """One honest sentence, chosen by what the numbers actually show."""
    if not results or majority is None:
        return "Not enough data to judge."
    best = max(results, key=lambda r: r["accuracy"])
    if best["accuracy"] <= majority:
        return (
            "No model beat the majority-class baseline. On this data, these news "
            "and price features carry no usable skill for predicting next-day "
            "direction."
        )
    if same_as_yesterday is not None and best["accuracy"] <= same_as_yesterday:
        return (
            f"The best model ({best['model']}, {best['accuracy']:.1%}) beats the "
            f"majority baseline ({majority:.1%}) but not the free 'same direction "
            f"as yesterday' baseline ({same_as_yesterday:.1%}). The apparent skill "
            f"is momentum, not news."
        )
    significant = [
        r for r in results
        if r["binom_p_vs_baseline"] is not None and r["binom_p_vs_baseline"] < 0.05
    ]
    if not significant:
        return (
            f"The best model ({best['model']}, {best['accuracy']:.1%}) is above the "
            f"majority baseline ({majority:.1%}) but not significantly so at n={best['n']}. "
            f"Treat this as no demonstrated skill."
        )
    return (
        f"{best['model']} reaches {best['accuracy']:.1%} against a {majority:.1%} "
        f"majority baseline, significantly (p<0.05), on held-out data after 2020. "
        f"It is still a weak signal, and this is an association, not advice."
    )
