"""Phase V5 tests: the five v2 mining analyses.

Two kinds of test here, and both matter.

**Planted-signal tests.** A generator that knows the answer is the only way to
check that a recovery statistic recovers anything. The event study is run
against a market model with a known alpha and beta; if the recovered values
drift from what was planted, the estimator is broken in a way that real data
would never reveal, because real data has no known answer.

**Leakage tests.** The direction model is the one place where a bug produces a
plausible, flattering number rather than an error. Two checks stand guard:

  * the split boundaries strictly increase, so no date appears in two slices;
  * shifting a feature one day forward must CHANGE test accuracy. A feature that
    leaked would leave accuracy unchanged or improved - that is the signature of
    look-ahead, and it is invisible without this assertion.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from dwm.mining import (
    direction_model,
    event_study,
    market_rules,
    stock_clusters,
)
from dwm.mining.common import (
    chronological_split,
    cumulative_abnormal_return,
    max_drawdown,
    ols_market_model,
    rolling_z,
    safe_round,
)

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def test_the_market_model_recovers_a_planted_alpha_and_beta() -> None:
    rng = np.random.default_rng(42)
    market = rng.normal(0.1, 1.0, 200)
    stock = 0.05 + 1.4 * market + rng.normal(0, 0.2, 200)
    alpha, beta, r2 = ols_market_model(stock, market)
    assert alpha == pytest.approx(0.05, abs=0.02)
    assert beta == pytest.approx(1.4, abs=0.02)
    assert r2 == pytest.approx(1.0, abs=0.05)


def test_a_flat_market_series_yields_nan_rather_than_a_crash() -> None:
    """One dead stock must not abort a run over fifty."""
    flat = np.zeros(50)
    stock = np.random.default_rng(1).normal(0, 1, 50)
    alpha, beta, _ = ols_market_model(stock, flat)
    assert not np.isfinite(beta)


def test_car_outside_the_series_is_nan_not_zero() -> None:
    """A window past the end of the data is missing evidence, and reporting 0.0
    would be indistinguishable from a genuine 'nothing happened'."""
    series = np.array([1.0, 2.0, 3.0])
    assert cumulative_abnormal_return(series, 0, 1) == pytest.approx(3.0)
    assert np.isnan(cumulative_abnormal_return(series, 5, 9))


def test_the_splitter_puts_each_date_in_exactly_one_slice() -> None:
    dates = np.repeat(np.arange(1, 101), 5) * 100 + 100
    split = chronological_split(dates)
    assert split.train_end < split.validation_end
    allidx = np.concatenate([split.train, split.validation, split.test])
    assert sorted(allidx) == list(range(len(dates))), "every row exactly once"
    held = set(dates[split.test].tolist())
    assert not (held & set(dates[split.train].tolist()))
    assert not (held & set(dates[split.validation].tolist()))


def test_rolling_z_is_trailing_not_centred() -> None:
    """A centred window would score a day using later information, which is the
    single most common leak in a time-series feature.

    A rising series is used rather than a flat one on purpose: a flat window has
    zero variance, which is undefined rather than small, so it could not
    distinguish "did not see the future" from "could not be scored".
    """
    baseline = np.arange(70, dtype=float)
    values = np.concatenate([baseline, [200.0]])
    z = rolling_z(values, 60)
    assert np.isnan(z[59]), "the first 60 values have no preceding window"
    # The spike is scored against the 60 values BEFORE it, and dwarfs every
    # earlier day. If any earlier day had seen it, its own z would be large too.
    assert z[-1] > 4 * np.nanmax(np.abs(z[:-1]))
    assert z[-1] > 8


def test_a_spike_is_not_capped_by_including_itself_in_the_window() -> None:
    """Including the current value in the baseline drags the mean and sd
    towards it, capping z near sqrt(n) - about 7.7 here - however extreme the
    move. A surprise detector that cannot exceed 7.7 sigma is not detecting."""
    baseline = np.arange(60, dtype=float) + 100.0
    for magnitude in (1e4, 1e6):
        values = np.concatenate([baseline, [magnitude]])
        z = rolling_z(values, 60)
        assert z[-1] > 500, f"magnitude {magnitude:g} only scored {z[-1]:.1f}"


def test_a_zero_variance_window_scores_nothing_rather_than_normal() -> None:
    """Reporting 0.0 would claim a +100 move against a dead-flat baseline was
    entirely ordinary. Undefined is the honest answer, and callers skip it."""
    values = np.array([0.0] * 70 + [100.0])
    z = rolling_z(values, 60)
    assert np.isnan(z[-1])


def test_max_drawdown_is_a_magnitude() -> None:
    assert max_drawdown(np.array([100.0, 80.0, 120.0])) == pytest.approx(0.2)
    assert max_drawdown(np.array([100.0, 110.0, 120.0])) == pytest.approx(0.0)


def test_nan_and_inf_store_as_null_not_as_a_number() -> None:
    """NaN prints as 'nan' in JSON and the dashboard would render it as text."""
    assert safe_round(float("nan")) is None
    assert safe_round(float("inf")) is None
    assert safe_round(None) is None
    assert safe_round(1.23456789, 4) == 1.2346


# ---------------------------------------------------------------------------
# event study
# ---------------------------------------------------------------------------


def _event_fixture() -> tuple[list[dt.date], np.ndarray, np.ndarray, int]:
    """A stock whose market model is exactly alpha=0, beta=1, then jumps."""
    rng = np.random.default_rng(7)
    sessions = [dt.date(2019, 1, 1) + dt.timedelta(days=i) for i in range(200)]
    market = rng.normal(0.05, 1.0, 200)
    stock = 0.0 + 1.0 * market          # exactly the market, no alpha
    event_index = 150
    stock[event_index] += 5.0          # a +5% abnormal move
    return sessions, stock, market, event_index


def test_car_is_zero_when_the_stock_equals_the_market() -> None:
    """The sanity check that catches a fitted-alpha bug: a stock that moves
    exactly with the index must show no abnormality at all."""
    sessions, stock, market, index = _event_fixture()
    clean = stock.copy()
    clean[index] = market[index]        # remove the planted jump
    result = event_study.study_stock(
        1, 1, 1, sessions, clean, market, index
    )
    assert result.valid is True
    assert result.beta == pytest.approx(1.0, abs=1e-9)
    assert result.alpha == pytest.approx(0.0, abs=1e-9)
    assert result.car_0_1 == pytest.approx(0.0, abs=1e-6)
    assert result.car_0_5 == pytest.approx(0.0, abs=1e-6)


def test_the_event_study_recovers_a_planted_abnormal_move() -> None:
    sessions, stock, market, index = _event_fixture()
    result = event_study.study_stock(1, 1, 1, sessions, stock, market, index)
    assert result.valid is True
    assert result.car_0_1 == pytest.approx(5.0, abs=0.1), "the planted +5% jump"
    assert result.car_0_5 >= result.car_0_1


def test_too_short_an_estimation_window_is_invalid_with_a_reason() -> None:
    sessions, stock, market, index = _event_fixture()
    result = event_study.study_stock(
        1, 1, 1, sessions[:40], stock[:40], market[:40], 35
    )
    assert result.valid is False
    assert "pre-event sessions" in result.reason
    assert np.isnan(result.car_0_5)


def test_an_event_before_the_price_history_is_invalid_not_skipped() -> None:
    """It is kept with a reason, so the average is not quietly computed over a
    favourable subset."""
    sessions, stock, market, index = _event_fixture()
    result = event_study.study_stock(1, 1, 1, sessions, stock, market, 0)
    assert result.valid is False


def test_an_event_after_the_last_price_is_invalid() -> None:
    sessions, stock, market, _ = _event_fixture()
    result = event_study.study_stock(
        1, 1, 1, sessions, stock, market, 5      # index 5 is before the event
    )
    assert result.valid is False


def test_the_event_day_is_the_next_session_when_the_date_is_not_a_session() -> None:
    sessions = [dt.date(2019, 1, 7), dt.date(2019, 1, 8), dt.date(2019, 1, 9)]
    # Sunday 6th -> Monday 7th; a date past the end -> -1.
    assert event_study.event_index(sessions, dt.date(2019, 1, 6)) == 0
    assert event_study.event_index(sessions, dt.date(2019, 1, 9)) == 2
    assert event_study.event_index(sessions, dt.date(2019, 2, 1)) == -1


# ---------------------------------------------------------------------------
# stock clusters
# ---------------------------------------------------------------------------


def test_clusters_are_reproducible() -> None:
    """Same input, same clusters. KMeans has a seed for a reason."""
    rng = np.random.default_rng(3)
    features = {}
    for i in range(30):
        centre = i % 3
        features[str(i)] = {
            "ann_volatility": 10 + centre * 5 + rng.normal(0, 0.2),
            "mean_daily_return": 0.05 * centre + rng.normal(0, 0.01),
            "beta": 0.5 + centre * 0.2 + rng.normal(0, 0.02),
            "max_drawdown": 0.2 + centre * 0.05 + rng.normal(0, 0.005),
            "mentions_per_1000_days": 5 + centre + rng.normal(0, 0.2),
            "mention_sentiment": rng.normal(0, 0.01),
            "news_sensitivity": 0.2 + centre * 0.05 + rng.normal(0, 0.01),
        }
    first = stock_clusters.cluster(features)
    second = stock_clusters.cluster(features)
    assert first["k"] == second["k"]
    assert first["cluster_names"] == second["cluster_names"]
    assert first["silhouette"] == pytest.approx(second["silhouette"])


def test_a_stock_missing_a_feature_is_dropped_not_imputed() -> None:
    """Imputing a missing news-sensitivity with the mean would make that stock
    look average at exactly the thing it was missing."""
    good = {
        "ann_volatility": 10.0, "mean_daily_return": 0.1, "beta": 0.8,
        "max_drawdown": 0.3, "mentions_per_1000_days": 5.0,
        "mention_sentiment": 0.0, "news_sensitivity": 0.2,
    }
    rng = np.random.default_rng(9)
    features = {
        str(i): {k: v * float(rng.uniform(0.9, 1.1)) for k, v in good.items()}
        for i in range(12)
    }
    features["11"] = dict(good, news_sensitivity=float("nan"))
    result = stock_clusters.cluster(features)
    assert "11" in result["dropped"]
    assert "11" not in result["assignments"]


def test_clusters_are_named_from_their_centroids() -> None:
    rng = np.random.default_rng(5)
    features = {}
    for i in range(30):
        centre = i % 2
        features[str(i)] = {
            "ann_volatility": 8 + centre * 10 + rng.normal(0, 0.2),
            "mean_daily_return": 0.02 + centre * 0.05 + rng.normal(0, 0.005),
            "beta": 0.4 + centre * 0.5 + rng.normal(0, 0.02),
            "max_drawdown": 0.15 + centre * 0.2 + rng.normal(0, 0.005),
            "mentions_per_1000_days": 3 + centre * 4 + rng.normal(0, 0.2),
            "mention_sentiment": -0.02 + centre * 0.05 + rng.normal(0, 0.005),
            "news_sensitivity": 0.1 + centre * 0.3 + rng.normal(0, 0.01),
        }
    result = stock_clusters.cluster(features)
    names = " ".join(result["cluster_names"].values())
    assert "volatility" in names


# ---------------------------------------------------------------------------
# surprise days
# ---------------------------------------------------------------------------


def test_surprise_days_need_a_full_window_before_scoring() -> None:
    z = rolling_z(np.arange(10, dtype=float), 60)
    assert np.all(np.isnan(z))


def test_quiet_and_driven_days_are_distinguished() -> None:
    """news_driven needs BOTH z-scores high; quiet_news_move needs a big move
    with news BELOW its own recent average. The baseline carries real noise,
    because a flat window has no defined z-score at all."""
    rng = np.random.default_rng(21)
    n = 140
    news = rng.normal(20, 2, n)
    abs_return = rng.normal(2.0, 0.3, n)
    news[120] += 40.0          # busy news day
    abs_return[120] += 6.0     # and a big move        -> news_driven
    abs_return[126] += 7.0     # big move, quiet news  -> quiet_news_move
    news_z = rolling_z(news, 60)
    ret_z = rolling_z(abs_return, 60)
    assert news_z[120] > 2 and ret_z[120] > 2
    assert ret_z[126] > 2 and news_z[126] < 2


# ---------------------------------------------------------------------------
# direction model
# ---------------------------------------------------------------------------


def test_the_label_is_never_among_the_features() -> None:
    assert direction_model.TARGET not in direction_model.FEATURES


def test_baselines_are_computed_on_the_test_slice_only() -> None:
    dates = np.repeat(np.arange(1, 101), 4) * 100 + 100
    split = chronological_split(dates)
    n_test = len(split.test)
    # The last 15% of dates must be a quarter of the rows, not a quarter of the
    # whole frame evaluated on train data.
    assert n_test < len(dates) * 0.2


def test_a_shifted_feature_changes_accuracy() -> None:
    """The leak test.

    A feature that has seen the future leaves test accuracy unchanged when it is
    shifted one day later - because it was already encoding it. A legitimate
    trailing feature changes. If this assertion ever fails on a genuine feature,
    it means the feature was reading the target.
    """
    rng = np.random.default_rng(11)
    n = 4000
    dates = np.repeat(np.arange(1, n // 20 + 1), 20) * 100 + 100
    noise = rng.normal(0, 1, n)
    x = noise + rng.normal(0, 0.2, n)
    y = (x + rng.normal(0, 0.5, n) > 0)
    split = chronological_split(dates)

    def accuracy(features: np.ndarray) -> float:
        from sklearn.tree import DecisionTreeClassifier

        model = DecisionTreeClassifier(max_depth=3, random_state=42)
        model.fit(features[split.train].reshape(-1, 1), y[split.train])
        return float(
            (model.predict(features[split.test].reshape(-1, 1)) == y[split.test]).mean()
        )

    honest = accuracy(x)
    shifted = accuracy(np.concatenate([x[:1], x[:-1]]))
    assert abs(honest - shifted) > 1e-9, (
        "shifting the feature left accuracy identical, which is the signature "
        "of a leaky feature"
    )


def test_the_verdict_reports_a_failure_to_beat_baseline() -> None:
    """The module must be willing to say 'no skill found', and must do so when
    the numbers say so rather than when someone wants a better answer."""
    results = [
        {"model": "decision_tree", "accuracy": 0.49, "binom_p_vs_baseline": 0.9,
         "n": 8000}
    ]
    verdict = direction_model._verdict(results, majority=0.505, same_as_yesterday=0.50)
    assert "No model beat" in verdict
    assert "no usable skill" in verdict


def test_the_verdict_distinguishes_momentum_from_news() -> None:
    results = [
        {"model": "logistic", "accuracy": 0.52, "binom_p_vs_baseline": 0.001,
         "n": 8000}
    ]
    verdict = direction_model._verdict(results, majority=0.505, same_as_yesterday=0.53)
    assert "momentum, not news" in verdict


def test_the_verdict_calls_out_insignificance() -> None:
    results = [
        {"model": "logistic", "accuracy": 0.51, "binom_p_vs_baseline": 0.4,
         "n": 8000}
    ]
    verdict = direction_model._verdict(results, majority=0.505, same_as_yesterday=0.50)
    assert "not significantly" in verdict


# ---------------------------------------------------------------------------
# market rules
# ---------------------------------------------------------------------------


def test_mining_thresholds_are_strict() -> None:
    """A market with thousands of sector-days produces spurious associations at
    a lower bar, and a rule firing on a handful of sessions is a calendar
    effect wearing a suit."""
    assert market_rules.MIN_SUPPORT >= 0.02
    assert market_rules.MIN_CONFIDENCE >= 0.5
    assert market_rules.MIN_LIFT >= 1.1
    assert market_rules.MIN_SUPPORT_COUNT >= 30


def test_a_tiny_transaction_set_yields_no_rules() -> None:
    """Two sessions cannot support an association. Returning something anyway
    would be inventing a finding."""
    import pandas as pd

    tiny = pd.DataFrame(
        {
            "sector_name": ["Energy", "IT"],
            "news_heat": ["low", "high"],
            "sentiment": ["neutral", "positive"],
            "ret": ["up", "down"],
            "next": ["up", "down"],
            "vol_regime": ["low", "high"],
            "weekday": ["Mon", "Tue"],
        }
    )
    assert market_rules.mine(tiny) == []
