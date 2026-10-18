"""`python -m dwm all` builds a warehouse from nothing.

The command had been documented in `AGENTS.md` and called by both setup
scripts since the v2 phases, and it did not exist. On a machine that had
nothing but the repository - which is the only situation a new teammate is
actually in - the documented way to get a dashboard failed with
`No such command 'all'`.

These tests exist because the failure was invisible locally: the developer
already had a warehouse, so nobody ran the documented command. What is tested
here is not "the pipeline computes a right number" - the phase tests do that -
but "the documented command exists, runs its stages in dependency order, and
stops loudly at the first one that fails".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import typer

ROOT = Path(__file__).resolve().parent.parent

#: Order matters and is the dependency order: dimensions before facts, facts
#: before cubes, cubes before mining, mining before the report. Each stage
#: reads what the previous one wrote.
EXPECTED_STAGES = ["ingest", "etl", "features", "build", "link", "mine", "report"]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "dwm", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
    )


def test_the_documented_command_exists() -> None:
    """`AGENTS.md`, `start.ps1` and `start.sh` all name it. It has to be real."""
    result = run_cli("--help")
    assert result.returncode == 0, result.stderr
    assert "all" in result.stdout
    assert "No such command" not in result.stderr


def test_all_runs_the_stages_in_dependency_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Order is the whole point, so it is asserted rather than assumed.

    Driven through the CLI's own entry point with each stage replaced by a
    recorder, so the assertion is about the order the command *invokes* rather
    than about a list somebody wrote next to it.
    """
    from dwm import cli

    order: list[str] = []
    for stage in EXPECTED_STAGES:
        monkeypatch.setattr(
            cli,
            stage,
            (lambda name: lambda **_: order.append(name))(stage),
            raising=False,
        )

    cli.run_all()

    assert order == EXPECTED_STAGES, f"stages ran as {order}"


def test_all_stops_at_the_first_failing_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken stage must stop the run, not be skipped past.

    Every later stage would otherwise report a confidently wrong number: with
    the facts missing, `link` does not crash, it links nothing, and `report`
    writes a report about an empty warehouse.
    """
    from dwm import cli
    ran: list[str] = []

    def explode(**_: object) -> None:
        ran.append("build")
        raise RuntimeError("dimension build failed")

    for stage in ("ingest", "etl", "features"):
        monkeypatch.setattr(cli, stage, (lambda n: lambda **_: ran.append(n))(stage), raising=False)
    monkeypatch.setattr(cli, "build", explode, raising=False)
    for stage in ("link", "mine", "report"):
        monkeypatch.setattr(cli, stage, (lambda n: lambda **_: ran.append(n))(stage), raising=False)

    with pytest.raises(typer.Exit) as caught:
        cli.run_all()

    assert caught.value.exit_code == 1
    assert ran == ["ingest", "etl", "features", "build"], (
        f"the run continued past a failed stage: {ran}"
    )


def test_a_failed_stage_is_named_in_the_output(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """"which stage broke" is the only question a reader has at this point.

    A stage that fails 40 minutes into a first build has to say so by name, or
    the reader is left with a traceback and no idea whether the download, the
    clean or the dimension build went wrong.
    """
    from dwm import cli
    for stage in ("ingest", "etl", "features"):
        monkeypatch.setattr(cli, stage, lambda **_: None, raising=False)

    def boom(**_: object) -> None:
        raise RuntimeError("no trading calendar")

    monkeypatch.setattr(cli, "build", boom, raising=False)

    with caplog.at_level("INFO"), pytest.raises(typer.Exit):
        cli.run_all()

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "pipeline stage: build" in messages
    assert "pipeline stage 'build' failed" in messages
    assert "no trading calendar" in messages


def test_sample_is_passed_through_to_every_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--sample N` is how a new machine gets a warehouse in minutes.

    If it reached only some stages, a sample run would build full-size cubes
    from partial facts and produce numbers that look real and are not.
    """
    from dwm import cli

    seen: list[tuple[str, int | None]] = []
    for stage in EXPECTED_STAGES:
        if stage == "report":
            # `report` has no `--sample`; it renders from what mining wrote.
            monkeypatch.setattr(cli, stage, lambda **_: None, raising=False)
            continue
        monkeypatch.setattr(
            cli,
            stage,
            (lambda n: lambda **kw: seen.append((n, kw.get("sample"))))(stage),
            raising=False,
        )

    cli.run_all(sample=1234)

    for stage, sample in seen:
        assert sample == 1234, f"stage {stage} received sample={sample}"
