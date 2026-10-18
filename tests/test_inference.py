"""Inference and API tests (Phases 7-8).

The theme is the same as the mining tests: protect the promises rather than the
numbers.

  * the renderer performs no computation, so a figure cannot drift from its
    fact
  * every fact carries a source, and every easy-to-misread one carries a
    caution
  * the cautions actually reach the rendered document and the API response
  * the report never calls a style measure a fake-news rate
  * the gate fails loudly on a warehouse that cannot support its claims
  * the API is read-only, and unknown operations are refused
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dwm.config import Settings
from dwm.inference import load_facts, run_inference
from dwm.inference.facts import build_facts, check_guards, write_facts
from dwm.inference.report import (
    _table,
    humanise,
    num,
    pct,
    render_report,
    write_report,
)
from dwm.mining import run_mining

# ---------------------------------------------------------------------------
# formatting: the smallest thing that can quietly tell a lie
# ---------------------------------------------------------------------------


def test_pct_renders_nothing_bare() -> None:
    assert pct(0.037718) == "3.77%"
    assert pct(None) == "n/a"


def test_a_missing_rate_is_not_a_zero_rate() -> None:
    """0% and no measurement must never print the same thing."""
    assert pct(0.0) == "0.00%"
    assert pct(None) != pct(0.0)


def test_num_and_humanise_handle_none() -> None:
    assert num(None) == "n/a"
    assert num(1234.0) == "1,234"
    assert num(0.075) == "0.0750"
    assert humanise(None) == "n/a"
    assert humanise(1113427) == "1,113,427"


def test_table_handles_no_rows() -> None:
    assert "No rows" in _table(["a", "b"], [])


# ---------------------------------------------------------------------------
# a facts fixture, built from a tiny warehouse
# ---------------------------------------------------------------------------


@pytest.fixture
def facts(mining_db, monkeypatch):
    """A facts.json built from the sample warehouse.

    Written to the test's own reports directory by the autouse
    `reports_dir` fixture, so the real one is never touched.
    """
    con, _cfg = mining_db
    from dwm.mining import run_mining

    # The mining stage writes reports/mining.json, which build_facts reads.
    run_mining(Settings(db_path="unused"), con=con)
    return build_facts(con)


# ---------------------------------------------------------------------------
# fact structure
# ---------------------------------------------------------------------------


def test_every_fact_records_its_source(facts) -> None:
    """Traceability claim: a fact without a source cannot be checked."""
    for key, block in facts.items():
        fact = block.get("fact") if isinstance(block, dict) else None
        if not isinstance(fact, dict):
            continue
        assert fact.get("source"), f"{key} has a fact with no source"


def test_every_rate_fact_carries_a_caution(facts) -> None:
    """A rate without its caution is the project's main failure mode."""
    for key, block in facts.items():
        fact = block.get("fact") if isinstance(block, dict) else None
        if not isinstance(fact, dict) or not fact.get("unit"):
            continue
        assert fact.get("caution"), f"{key} reports a unit with no caution"


def test_the_fake_news_phrase_never_appears(facts) -> None:
    """Enforced over the whole payload, not just the report prose."""
    blob = json.dumps(facts).lower()
    assert "fake news rate" not in blob
    assert "fakenewsrate" not in blob
    assert "risk_signal_rate" in blob or "risk-signal rate" in blob


def test_classifier_accuracy_travels_with_its_baseline(facts) -> None:
    rq7 = facts["rq7_classifier"]
    if not rq7.get("ran"):
        pytest.skip("classifier did not run on the fixture")
    metrics = rq7["metrics"]
    assert metrics["majority_baseline_accuracy"] == rq7["baseline"]
    assert "lift_over_baseline_pp" in metrics
    assert "ground truth" in rq7["fact"]["caution"].lower()


def test_correlation_travels_with_its_p_value(facts) -> None:
    rq6 = facts["rq6_market_association"]
    if not rq6.get("ran"):
        pytest.skip("market analysis did not run on the fixture")
    for name, measure in rq6["measures"].items():
        for entry in measure["by_lag"]:
            if entry.get("pearson_r") is not None:
                assert "p_value" in entry, name
                assert "n" in entry, name


def test_partial_year_never_enters_a_comparison(facts) -> None:
    for move in facts["rq1_topic_mix"]["largest_moves"]:
        if 2015 in (move["from_year"], move["to_year"]):
            assert not move["both_comparable"]


def test_window_share_is_not_a_single_year_share(facts) -> None:
    """The two answer different questions and must not be conflated.

    `Local` is ~70% of the window and ~89% of 2020. Reporting the larger
    figure as "of the window" would misrepresent the corpus.
    """
    window = facts["rq1_topic_mix"]["window_shares"]
    total = window["headlines_in_window"]
    for topic in window["topics"]:
        assert 0 < topic["headline_count"] <= total
        assert topic["share_of_window"] == pytest.approx(
            topic["headline_count"] / total, abs=1e-6
        )
    biggest_year_share = max(
        (r["share_of_year"] for r in facts["rq1_topic_mix"]["rows"]), default=0
    )
    biggest_window_share = window["topics"][0]["share_of_window"]
    # They are different numbers, which is the point of storing both.
    assert biggest_window_share != pytest.approx(biggest_year_share, abs=1e-9)


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def _fake_mining(**overrides: Any) -> dict[str, Any]:
    """A mining payload shaped like the real one, for gate tests.

    Every section is non-empty, because a key that is present but hollow is
    exactly what the gate is supposed to catch.
    """
    base: dict[str, Any] = {
        "rq1_topic_mix": {"rows": [{"year": 2018}]},
        "rq2_bursts": {"count": 0, "detected": []},
        "rq3_sensationalism": {"by_topic": {"corpus_rate": 0.03}},
        "rq4_clusters": {"ran": True, "clusters": []},
        "rq5_association_rules": {"attribute_rules": {"ran": True, "apriori": {}}},
        "rq6_market_association": {
            "lagged_return_correlation": {"trading_days_paired": 500, "ran": True}
        },
        "rq7_classifier": {"ran": True, "chosen_test_metrics": {}},
    }
    base.update(overrides)
    return base


def test_gate_passes_on_a_built_warehouse(mining_db) -> None:
    """Every structural check passes; only the corpus-size floor refuses.

    The fixture holds ten headlines, far below the 1,000-row floor for a rate,
    so `headlines_in_window_sufficient` is *supposed* to fail. A gate that
    passed here would be a gate that does not guard.
    """
    con, _ = mining_db
    guards = check_guards(_fake_mining(), con)
    failed = {c["check"] for c in guards["failed"]}
    assert failed == {"headlines_in_window_sufficient"}, failed
    assert guards["passed"] is False
    # Everything structural passed.
    structural = [c for c in guards["checks"] if c["check"] !=
                  "headlines_in_window_sufficient"]
    assert all(c["passed"] for c in structural), [
        c for c in structural if not c["passed"]
    ]


def test_gate_catches_a_hollow_section(mining_db) -> None:
    """A key that is present but empty has not answered its question."""
    con, _ = mining_db
    guards = check_guards(_fake_mining(rq4_clusters={}), con)
    assert guards["passed"] is False
    assert "rq4_clusters_present" in {c["check"] for c in guards["failed"]}


def test_gate_catches_a_section_that_declined_to_run(mining_db) -> None:
    """A section that reports `ran: false` has not answered its question."""
    con, _ = mining_db
    guards = check_guards(
        _fake_mining(rq4_clusters={"ran": False, "reason": "not enough headlines"}),
        con,
    )
    failed = {c["check"] for c in guards["failed"]}
    assert "rq4_clusters_ran" in failed
    reasons = [c["detail"] for c in guards["failed"] if c["check"] == "rq4_clusters_ran"]
    assert reasons == ["not enough headlines"], "the reason must be reported"


def test_gate_fails_loudly_on_an_empty_warehouse(mining_db) -> None:
    """The blueprint requires the guard rails to fire on small data."""
    con, _ = mining_db
    con.execute("DELETE FROM fact_headline")
    guards = check_guards(
        _fake_mining(
            rq6_market_association={
                "lagged_return_correlation": {"trading_days_paired": 0}
            }
        ),
        con,
    )
    assert guards["passed"] is False
    failed = {c["check"] for c in guards["failed"]}
    assert "headlines_in_window_present" in failed
    assert "market_correlation_has_enough_observations" in failed
    # An absent section must be named, not silently skipped.
    absent = check_guards({"rq1_topic_mix": {"rows": []}}, con)
    assert "rq7_classifier_present" in {c["check"] for c in absent["failed"]}


def test_every_guard_check_states_its_detail(mining_db) -> None:
    con, _ = mining_db
    guards = check_guards({}, con)
    assert guards["checks"], "the gate must run some checks"
    for check in guards["checks"]:
        assert check["detail"], check["check"]
        assert isinstance(check["passed"], bool)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_report_renders_every_section(facts) -> None:
    text = render_report(facts)
    for heading in (
        "## 1. The data", "## 2. RQ1", "## 3. RQ2", "## 4. RQ3",
        "## 5. RQ4", "## 6. RQ5", "## 7. RQ6", "## 8. RQ7",
        "## 9. Method", "## 10. What this study concludes",
    ):
        assert heading in text, heading


def test_report_states_there_is_no_language_model(facts) -> None:
    """The traceability claim depends on it being true."""
    text = render_report(facts)
    assert "no language model" in text.lower()


def test_report_carries_every_caution(facts) -> None:
    """A caution in facts.json that never reaches report.md is a dropped caveat."""
    text = render_report(facts)
    for key, block in facts.items():
        fact = block.get("fact") if isinstance(block, dict) else None
        if not isinstance(fact, dict) or not fact.get("caution"):
            continue
        # The first sentence is enough to prove it travelled.
        sentence = fact["caution"].split(".")[0][:60]
        assert sentence in text, f"{key}'s caution did not reach the report"


def test_report_contains_no_causal_claims(facts) -> None:
    text = render_report(facts).lower()
    for banned in ("news causes", "caused the market", "leads to a fall",
                   "impact on the nifty", "predicts the market"):
        assert banned not in text, banned
    assert "association only" in text or "not causation" in text


def test_report_states_the_partial_year(facts) -> None:
    assert "partial year" in render_report(facts).lower()


def test_report_states_the_risk_signal_rule(facts) -> None:
    text = render_report(facts)
    assert "risk-signal rate" in text
    assert "fake-news rate" in text  # only ever in a denial
    for line in text.splitlines():
        if "fake-news rate" in line.lower():
            assert "never" in line.lower() or "not a" in line.lower(), line


def test_report_numbers_come_from_the_facts(facts) -> None:
    """The renderer must not compute. Spot-check a figure it would be easy to
    recompute wrongly."""
    text = render_report(facts)
    corpus = facts["corpus"]["analysis_window"]["headlines_in_window"]
    assert humanise(corpus) in text
    rq7 = facts["rq7_classifier"]
    if rq7.get("ran"):
        assert f"{rq7['metrics']['accuracy']:.4f}" in text
        assert f"{rq7['baseline']:.4f}" in text


def test_write_report_round_trips(facts, tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "report.md"
    monkeypatch.setattr("dwm.inference.report.report_path", lambda: target)
    write_report(render_report(facts))
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("# ")


# ---------------------------------------------------------------------------
# the stage end to end
# ---------------------------------------------------------------------------


def test_run_inference_writes_both_artefacts(mining_db) -> None:
    con, _ = mining_db
    run_mining(Settings(db_path="unused"), con=con)
    result = run_inference(Settings(db_path="unused"), con=con)
    assert Path(result["facts_path"]).exists()
    assert Path(result["report_path"]).exists()
    assert result["report_bytes"] > 0
    assert "checks_run" in result
    # The gate must report its verdict rather than assuming success.
    assert "gate_passed" in result
    assert isinstance(result["gate_passed"], bool)


def test_load_facts_reads_back_what_was_written(mining_db) -> None:
    con, _ = mining_db
    run_mining(Settings(db_path="unused"), con=con)
    run_inference(Settings(db_path="unused"), con=con)
    reloaded = load_facts()
    assert "rq7_classifier" in reloaded
    assert reloaded["guards"]["checks"]


def test_write_facts_produces_valid_json(facts, tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "facts.json"
    monkeypatch.setattr("dwm.inference.facts.facts_path", lambda: target)
    written = write_facts(facts)
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["corpus"]["row_counts"]["fact_headline"] >= 0


def test_build_facts_names_the_missing_prerequisite(mining_db, monkeypatch) -> None:
    """A report built from absent results would render as "nothing to say"."""
    import dwm.inference.facts as facts_module

    con, _ = mining_db
    monkeypatch.setattr(facts_module, "mining_path", lambda: Path("does-not-exist.json"))
    with pytest.raises(FileNotFoundError, match="mine"):
        build_facts(con, None)


def test_run_inference_names_the_missing_prerequisite(mining_db, monkeypatch) -> None:
    import dwm.inference as inference

    con, _ = mining_db
    monkeypatch.setattr(inference, "mining_path", lambda: Path("does-not-exist.json"))
    with pytest.raises(FileNotFoundError, match="mine"):
        run_inference(Settings(db_path="unused"), con=con)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(mining_db, settings, monkeypatch):
    """A TestClient wired to the fixture's warehouse and results.

    `settings` is the same object `mining_db` used, so the API opens the
    fixture's DuckDB file rather than the real 573 MB warehouse. The facts
    endpoints need `facts.json` to exist, so it is written into the test's own
    reports directory by the autouse `reports_dir` fixture.
    """
    from fastapi.testclient import TestClient

    import dwm.api.store as store
    from dwm.api.app import app

    con, _ = mining_db
    monkeypatch.setattr(store, "default_db_path", lambda: settings.db_path)
    monkeypatch.setattr(store, "_read_only_settings", lambda: settings)
    store.reload()
    # Run the real stages rather than hand-writing the files, so the API is
    # exercised against artefacts produced the way production produces them.
    run_mining(settings, con=con)
    run_inference(settings, con=con)
    store.reload()

    with TestClient(app) as test_client:
        yield test_client
    store.reload()


def test_health_reports_whether_data_exists(client) -> None:
    body = client.get("/health").json()
    assert body["status"] in {"ok", "degraded"}
    assert "warehouse" in body and "facts" in body


def test_summary_keeps_units_and_cautions(client) -> None:
    """The API must not strip the words that make a rate correct."""
    body = client.get("/summary").json()
    rq3 = body["rq3_sensationalism"]
    assert rq3["unit"]
    assert rq3["caution"]
    assert "risk-signal" in rq3["unit"]


def test_summary_never_says_fake_news_rate(client) -> None:
    blob = json.dumps(client.get("/summary").json()).lower()
    assert "fake news rate" not in blob


def test_facts_endpoint_returns_the_whole_document(client) -> None:
    body = client.get("/facts").json()
    assert "rq1_topic_mix" in body
    for key in range(1, 8):
        assert any(k.startswith(f"rq{key}_") for k in body)


def test_unknown_fact_section_is_404(client) -> None:
    assert client.get("/facts/nope").status_code == 404


def test_report_endpoint_returns_markdown(client) -> None:
    response = client.get("/report")
    assert response.status_code == 200
    assert response.text.startswith("# ")


def test_headlines_are_paged_and_bounded(client) -> None:
    body = client.get("/headlines?limit=5").json()
    assert len(body["rows"]) <= 5
    assert body["total"] > 0
    # A limit beyond the cap is refused rather than silently clamped.
    assert client.get("/headlines?limit=99999").status_code == 422
    assert client.get("/headlines?limit=0").status_code == 422


def test_headline_paging_does_not_repeat(client) -> None:
    first = client.get("/headlines?limit=3&offset=0").json()["rows"]
    second = client.get("/headlines?limit=3&offset=3").json()["rows"]
    assert [r["headline_id"] if "headline_id" in r else r["headline_text"] for r in first] != [
        r["headline_id"] if "headline_id" in r else r["headline_text"] for r in second
    ]


def test_series_returns_one_row_per_month(client) -> None:
    body = client.get("/series/monthly").json()
    months = [r["year_month"] for r in body["rows"]]
    assert months == sorted(months)
    assert len(set(months)) == len(months)


def test_series_filter_actually_filters(client) -> None:
    """A filter that changes nothing is a bug that looks like a feature."""
    everything = client.get("/series/monthly").json()["rows"]
    business = client.get("/series/monthly?topic=Business").json()["rows"]
    assert business != everything
    by_month = {r["year_month"]: r for r in everything}
    for row in business:
        assert row["headline_count"] <= by_month[row["year_month"]]["headline_count"]


def test_unknown_operation_is_refused(client) -> None:
    """The operation is never interpolated into SQL, only looked up."""
    response = client.get("/query/drop_table")
    assert response.status_code == 404
    assert "unknown operation" in response.json()["detail"]


def test_known_operations_run(client) -> None:
    assert client.get("/query/topic_mix").status_code == 200
    assert client.get("/query/roll_up").status_code == 200
    assert client.get("/query/top_months?n=3").status_code == 200


def test_operations_are_listed(client) -> None:
    body = client.get("/operations").json()
    assert len(body["operations"]) == 10
    assert all("name" in o and "default_params" in o for o in body["operations"])


def test_tables_endpoint_counts_rows(client) -> None:
    body = client.get("/tables").json()
    names = {t["table"] for t in body["tables"]}
    assert {"fact_headline", "dim_topic", "cube_month_topic"} <= names
    assert body["total_rows"] == sum(t["rows"] for t in body["tables"])


def test_reload_is_allowed_and_is_not_a_write(client) -> None:
    before = client.get("/tables").json()["total_rows"]
    assert client.post("/query/reload").status_code == 200
    assert client.get("/tables").json()["total_rows"] == before


def test_missing_results_are_503_not_500(client, monkeypatch) -> None:
    import dwm.api.store as store

    monkeypatch.setattr(store, "facts_path", lambda: Path("nope.json"))
    store.reload()
    response = client.get("/facts")
    assert response.status_code == 503
    assert "report" in response.json()["detail"]
    store.reload()


# ---------------------------------------------------------------------------
# the snapshot that lets the API and the CLI run at the same time
# ---------------------------------------------------------------------------


def test_api_serves_a_copy_not_the_live_warehouse(settings, monkeypatch) -> None:
    """DuckDB locks its file exclusively, even read-only.

    Serving the live file meant that with the API up, `dwm olap`, `dwm tables`
    and `dwm audit` all failed with "the process cannot access the file because
    it is being used by another process". For a demonstration that is fatal:
    you cannot show a CLI command without stopping the dashboard.

    So the API copies the warehouse and serves the copy. This asserts the copy
    exists, is distinct from the original, and holds the same data.
    """
    import dwm.api.store as store
    from dwm.db import connect

    # Point the snapshot machinery at the fixture, never the real 424 MB
    # warehouse: the running server holds a lock on that one.
    monkeypatch.setattr(store, "default_db_path", lambda: settings.db_path)

    build = connect(settings)
    try:
        build.execute("CREATE TABLE IF NOT EXISTS snap_probe AS SELECT 1 AS n")
        build.execute("INSERT INTO snap_probe VALUES (2)")
    finally:
        build.close()

    store.reload()
    snapshot = store.ensure_snapshot(force=True)
    try:
        assert snapshot.exists()
        assert snapshot != settings.db_path
        assert snapshot.name == "dwm.serve.duckdb"
        probe = connect(Settings(db_path=snapshot, read_only=True))
        try:
            total = probe.execute("SELECT sum(n) FROM snap_probe").fetchone()[0]
        finally:
            probe.close()
        assert total == 3, "the snapshot must hold the same rows as the original"
    finally:
        if snapshot.exists():
            snapshot.unlink()
        store.reload()


def test_snapshot_is_not_rebuilt_when_it_is_current(settings, monkeypatch) -> None:
    """Re-copying 424 MB on every start would be silly; staleness must be caught."""
    import time

    import dwm.api.store as store
    from dwm.db import connect

    monkeypatch.setattr(store, "default_db_path", lambda: settings.db_path)

    build = connect(settings)
    try:
        build.execute("CREATE TABLE IF NOT EXISTS snap_probe AS SELECT 1 AS n")
    finally:
        build.close()

    store.reload()
    try:
        first = store.ensure_snapshot(force=True)
        stamp = first.stat().st_mtime
        time.sleep(0.05)
        second = store.ensure_snapshot(force=False)
        assert second == first
        assert second.stat().st_mtime == stamp, "a current snapshot must not be re-copied"

        # Write to the source so it is newer than the snapshot. The timestamp
        # is set explicitly because a filesystem with coarse mtime granularity
        # would otherwise leave the two equal.
        later = stamp + 10
        import os

        os.utime(settings.db_path, (later, later))

        third = store.ensure_snapshot(force=False)
        assert third.stat().st_mtime > stamp, "a stale snapshot must be refreshed"
    finally:
        if store.snapshot_path().exists():
            store.snapshot_path().unlink()
        store.reload()


def test_snapshot_refuses_without_a_warehouse(tmp_path: Path, monkeypatch) -> None:
    """A missing warehouse must be an instruction, not a traceback.

    The message names the command that builds one. It used to name
    `run_all.ps1`, which no longer exists, so the one time a reader actually
    needed it the message pointed at nothing.
    """
    from dwm.api.store import DataUnavailable, ensure_snapshot

    monkeypatch.setattr("dwm.api.store.default_db_path", lambda: tmp_path / "nope.duckdb")
    with pytest.raises(DataUnavailable) as caught:
        ensure_snapshot(force=True)
    message = str(caught.value)
    assert "start.ps1" in message and "start.sh" in message
    assert "python -m dwm all" in message



