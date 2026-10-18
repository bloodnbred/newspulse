"""The chronological split must always leave something in every split.

`chronological_split` has always documented "The last date is always in test."
It was not true. As soon as there were only a few distinct dates, the 70/15/15
fractions rounded so far that `validation_end` became the maximum date, so
`dates > validation_end` selected nothing and the test split came out empty.

The consequence was not subtle and it was not local. A fresh-machine build with
`--sample 20000` produced a frame whose surviving dates were few enough to
trigger it, every estimator raised

    ValueError: Found array with 0 sample(s) (shape=(0, 9)) while a minimum
    of 1 is required by DecisionTreeClassifier.

and the whole `dwm all` pipeline stopped in the mining stage - four stages of
successful work discarded, with a numpy shape error as the only clue.
"""
from __future__ import annotations

import numpy as np
import pytest

from dwm.mining.common import TRAIN_FRACTION, VALIDATION_FRACTION, chronological_split


def dates(n: int) -> np.ndarray:
    """`n` distinct trading dates, ascending."""
    return np.arange(20200101 + n * 2, dtype=np.int64)[:n]


@pytest.mark.parametrize("n", list(range(3, 40)) + [60, 120, 239, 500])
def test_every_split_is_non_empty(n: int) -> None:
    """The property that was broken, over every small size and a few large ones."""
    split = chronological_split(dates(n))
    for label in ("train", "validation", "test"):
        assert len(getattr(split, label)) >= 1, (
            f"{n} dates: the {label} split is empty"
        )


@pytest.mark.parametrize("n", list(range(3, 40)) + [60, 120, 239, 500])
def test_the_last_date_is_always_in_test(n: int) -> None:
    """What the docstring has always claimed."""
    d = dates(n)
    split = chronological_split(d)
    assert d[split.test].max() == d.max(), (
        f"{n} dates: the last date {d.max()} is not in test"
    )


@pytest.mark.parametrize("n", list(range(3, 40)))
def test_the_splits_do_not_overlap(n: int) -> None:
    """Chronological means disjoint, and the order is train < validation < test."""
    d = dates(n)
    split = chronological_split(d)
    assert set(split.train) & set(split.validation) == set()
    assert set(split.train) & set(split.test) == set()
    assert set(split.validation) & set(split.test) == set()
    assert d[split.train].max() < d[split.validation].min()
    assert d[split.validation].max() < d[split.test].min()


def test_a_generous_corpus_is_unaffected() -> None:
    """The fix must not move the boundaries on a normal corpus.

    A split that was right for 1,235 trading sessions and changed by the guard
    would silently move every reported accuracy, so the fractions are pinned
    here rather than only the invariants.
    """
    d = dates(1235)
    split = chronological_split(d)
    unique = np.sort(np.unique(d))
    n_train = int(len(unique) * TRAIN_FRACTION)
    n_valid = int(len(unique) * VALIDATION_FRACTION)
    assert split.train_end == unique[n_train]
    assert split.validation_end == unique[n_train + n_valid]


def test_two_distinct_dates_are_refused_not_silently_collapsed() -> None:
    """Three splits need three dates. Two cannot be made to work honestly."""
    with pytest.raises(ValueError, match="at least 3 distinct dates"):
        chronological_split(dates(2))


def test_one_distinct_date_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 3 distinct dates"):
        chronological_split(np.array([20200101], dtype=np.int64))


def test_the_direction_model_reports_an_empty_split_in_words() -> None:
    """A shape error names neither the stage nor the remedy.

    The second guard is what turns `Found array with 0 sample(s)` into a
    sentence telling the reader to rebuild with a larger sample.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "dwm" / "mining" / "direction_model.py"
    text = source.read_text(encoding="utf-8")
    assert "the {label} split has no complete stock-days" in text
    assert "larger `--sample`" in text
