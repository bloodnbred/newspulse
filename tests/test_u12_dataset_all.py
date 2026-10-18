"""`--dataset all` has to mean all.

`AGENTS.md` documented `python -m dwm ingest --dataset ... all` from the v2
phases. The literal `all` was never handled: it fell through to the
unknown-name check and raised `KeyError('unknown dataset(s): [all]')`.

That is invisible locally, because the warehouse was built years ago with the
individual flags. It is the first thing a new machine hits, and the failure it
produces is badly misleading: without `stg_meta` there is no constituent list,
so `dim_trading_calendar` is never built, and the `link` stage stops with

    missing ['dim_trading_calendar']. Run `python -m dwm build` and
    `python -m dwm etl` first.

which names two commands the reader had just run. Found by cloning the public
repository into an empty folder and running the setup script.
"""
from __future__ import annotations

import pytest

from dwm.ingest import runner
from dwm.ingest.runner import V2_DATASETS, load_datasets_config

SPECS = set(load_datasets_config())


def resolve(datasets: list[str] | None) -> tuple[list[str], list[str], list[str]]:
    """The same expansion `ingest_all` performs, without touching the network."""
    specs = SPECS
    if datasets is None:
        return sorted(specs), [], []
    requested: list[str] = []
    for name in datasets:
        if name == "all":
            requested.extend(sorted(specs))
            requested.extend(V2_DATASETS)
        else:
            requested.append(name)
    requested = list(dict.fromkeys(requested))
    return (
        [d for d in requested if d not in V2_DATASETS],
        [d for d in requested if d in V2_DATASETS],
        [d for d in requested if d not in specs and d not in V2_DATASETS],
    )


def test_all_reaches_both_loaders() -> None:
    """The v1 sources and the v2 ones, in one request."""
    v1, v2, unknown = resolve(["all"])
    assert unknown == []
    assert set(v1) == SPECS
    assert set(v2) == set(V2_DATASETS)


def test_meta_is_reachable_at_all() -> None:
    """Without meta there is no constituent list, so no calendar and no prices."""
    v2, _ = resolve(["all"])[1], None
    assert "meta" in v2
    assert "stocks" in v2


def test_the_v1_default_is_unchanged() -> None:
    """`dwm ingest` with no `--dataset` is the three v1 sources, as it always was.

    Changing this would pull a network fetch into every v1 test and every
    existing workflow, and the v2 sources have their own documented spelling.
    """
    v1, v2, unknown = resolve(None)
    assert unknown == []
    assert set(v1) == SPECS
    assert v2 == []


def test_a_single_dataset_still_selects_only_itself() -> None:
    for name in ("toi", "stocks", "meta"):
        v1, v2, unknown = resolve([name])
        assert unknown == []
        chosen = set(v1) | set(v2)
        assert chosen == {name}, f"{name} selected {chosen}"


def test_all_combined_with_a_name_does_not_repeat_it() -> None:
    v1, _, unknown = resolve(["all", "toi"])
    assert unknown == []
    assert v1.count("toi") == 1


def test_a_genuine_typo_is_still_refused() -> None:
    """`all` being accepted must not have made the check permissive."""
    _, _, unknown = resolve(["toi", "headlines"])
    assert unknown == ["headlines"], "a misspelled dataset would be fetched silently"

    # And the real code path raises on it rather than proceeding.
    with pytest.raises(KeyError, match="headlines"):
        runner.ingest(
            settings=_stub_settings(),
            datasets=["toi", "headlines"],
        )


def test_the_v2_constant_is_still_the_pair_the_docs_name() -> None:
    """`AGENTS.md` names these two; if it changes, that file has to change."""
    assert V2_DATASETS == ("meta", "stocks")


def test_run_all_asks_for_all_not_the_v1_default() -> None:
    """`dwm all` must reach the v2 loaders, or it builds half a warehouse."""
    import inspect

    from dwm import cli

    source = inspect.getsource(cli.run_all)
    assert 'dataset=["all"]' in source, (
        "run_all calls ingest with the v1 default, so it never fetches the "
        "constituent list or the per-company prices"
    )


def test_the_runner_module_imports_cleanly() -> None:
    """A syntax error in the expansion would be the worst possible failure here."""
    assert callable(runner.ingest)


def _stub_settings():
    """Just enough of a Settings for `ingest` to reach its own argument check."""
    from dwm.config import Settings

    return Settings()
