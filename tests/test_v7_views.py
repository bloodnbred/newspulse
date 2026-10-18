"""Phase V7 tests: the v2 read API and the reader lock that guards it.

The checks that grepped the v1 front end's JavaScript were removed with it
and checks the palette against the CSS. What is here is the part that needs the
server: that each v2 chart the views mount actually builds from real data in
both themes, that the payloads stay small enough to load, and that the
disclosures the v2 pages promise are really present.

A test that skips is a test that never ran, so the skip guards here state the
command that would make each one execute rather than hiding the gap.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = ROOT / "warehouse" / "dwm.duckdb"
MODELS = ROOT / "models" / "tfidf.joblib"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(),
    reason="warehouse not built; run `python -m dwm all` first",
)


@pytest.fixture(scope="module")
def con():
    import duckdb

    connection = duckdb.connect(str(WAREHOUSE), read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from dwm.api.app import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# the spec builder
# ---------------------------------------------------------------------------















# ---------------------------------------------------------------------------
# the drill-across payload
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------








@pytest.mark.parametrize(
    "operation",
    ["slice", "dice", "rollup", "drilldown", "pivot", "cube", "drill-across",
     "stock_profile"],
)
def test_every_sector_operation_is_reachable_and_explains_itself(client, operation: str) -> None:
    """Every whitelisted operation answers, and carries its own caveat.

    The caveat is the part that matters: an OLAP row set is a set of numbers, and
    a reader who cannot see whether it is news or sentiment, or whether the
    averages were stored, will read it as whatever they assumed.
    """
    params = {"symbol": "TCS"} if operation == "stock_profile" else {}
    response = client.get(f"/olap/sector/{operation}", params=params)
    assert response.status_code == 200, response.text[:200]
    body = response.json()
    assert body.get("caveat"), f"{operation} returns rows with no caveat"
    assert body.get("description"), f"{operation} does not say what it computed"


def test_an_unknown_sector_operation_lists_the_real_ones(client) -> None:
    response = client.get("/olap/sector/nonsense")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "slice" in detail and "drill-across" in detail, (
        "a 404 that does not say what is available is a dead end"
    )


def test_drilldown_defaults_to_the_latest_year_the_cube_holds(client) -> None:
    """The corpus ends 2020-06-30, so defaulting to the current year would
    return nothing and look like a broken endpoint."""
    response = client.get("/olap/sector/drilldown")
    assert response.status_code == 200
    assert "2020" in response.json()["description"]


# ---------------------------------------------------------------------------
# the disclosures the pages promise
# ---------------------------------------------------------------------------


def test_the_quality_endpoint_admits_the_unaudited_precision(client) -> None:
    """Mention precision is absent until a person fills in the audit sample, and
    the page says so rather than showing a blank cell."""
    response = client.get("/quality")
    assert response.status_code == 200
    body = response.json()
    assert "is_correct" in body["caveat"]
    linking = body.get("linking") or {}
    assert linking.get("precision") is None, (
        "if precision has been audited, update this test and the page copy"
    )


def test_the_stock_listing_discloses_survivorship(client) -> None:
    """Today's Nifty 50 list applied to a 2015-2020 window. Said on the
    endpoint, so it cannot be forgotten on a page."""
    body = client.get("/stocks").json()
    assert "surviv" in body["caveat"].lower()
    assert "Nifty 50" in body["caveat"]


def test_the_clusters_endpoint_reports_the_silhouette_context(client) -> None:
    """A silhouette near 0.2 is weak structure, and the endpoint says so on
    every call rather than only on the page that happens to mention it."""
    body = client.get("/mining/stockclusters").json()
    assert "silhouette" in body["caveat"].lower()
    assert body["clusters"], "no clusters returned"


def test_the_direction_endpoint_always_carries_both_baselines(client) -> None:
    """A model number without a baseline beside it is the easiest figure in this
    project to over-read."""
    body = client.get("/mining/direction").json()
    assert body["baseline_majority"] is not None
    assert body["baseline_yesterday"] is not None
    assert "beats_baseline" in body
    assert "chronological" in body["caveat"].lower()


# ---------------------------------------------------------------------------
# the JS, statically
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------




def test_a_parallel_response_is_not_built_from_another_querys_rows(client) -> None:
    """The corruption was silent, so the shape of the answer is checked.

    A response whose keys do not match the keys of the same endpoint's own
    serial response is a response zipped from the wrong query. Asserting on the
    status code alone would pass this: the failing responses above were 500s
    only because the mismatch happened to raise.
    """
    import concurrent.futures

    serial = client.get("/sectors").json()
    expected = set(serial["sectors"][0])

    def one(_: int) -> dict:
        return client.get("/sectors").json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for body in pool.map(one, range(24)):
            assert body["sectors"], "a concurrent request returned no rows"
            for row in body["sectors"]:
                assert set(row) == expected, (
                    f"a concurrent response has columns {sorted(row)} where a "
                    f"serial one has {sorted(expected)}"
                )


# ---------------------------------------------------------------------------
# percentage columns: points vs fractions
# ---------------------------------------------------------------------------


#: `(column name, a value the warehouse really produces, the digits that must
#: appear on screen)`. These are the columns the front end used to scale wrongly.
PERCENT_CASES = [
    ("mean_return_pct", 1.0669, "1.07%"),        # a 1.07% sector day
    ("mean_abs_return_pct", 23.23, "23.23%"),    # the largest clean single-stock move
    ("return_pct", 0.0427, "0.04%"),             # v1's own fact_market_daily
    ("variance_explained_pct", 7.993, "7.99%"),  # v1's regression figure
    ("risk_signal_rate", 0.034589, "3.46%"),     # a genuine fraction
    ("dominant_cluster_share", 0.9488, "94.88%"),
    ("share_of_year", 0.4302, "43.02%"),          # a fraction that does not end in _share
    ("share_of_window", 0.0064, "0.64%"),
    ("positive_rate", 0.049761, "4.98%"),
    ("min_rows_for_a_rate", 1000, "1,000"),       # a threshold, not a percentage
]




def _extract_js_function(source: str, name: str) -> str:
    """Pull one top-level `function name(...) {...}` out of the JavaScript."""
    start = source.index(f"function {name}(")
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start: i + 1]
    raise AssertionError(f"could not find the end of {name}()")


def _run_node(harness: str) -> str:
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "harness.js"
        path.write_text(harness, encoding="utf-8")
        result = subprocess.run(
            ["node", str(path)], capture_output=True, text=True, timeout=60,
        )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\n{result.stderr}")
    return result.stdout.strip()




def test_the_lock_lives_beside_the_connection_and_every_reader_takes_it() -> None:
    """Regression guard for the defect that outlived its own fix.

    Commit 1e75d21 added a lock to `dwm/api/v2.py` and called the concurrency
    bug closed. It was not. `_open_connection` is `@lru_cache`d, so v2, ui and
    the v1 store all share ONE handle - and a lock only protects the readers who
    take it. `store.py` did not, so `/health` went on interleaving and built
    `SELECT count(*) FROM 2015-06-30`: a `publish_date` value from a concurrent
    `/ui/home`, handed to `table_counts()` as a table name.

    Two assertions, because either alone would pass while the bug is live:
    the lock is in `store` (the module that owns the connection), and hammering
    every reader at once produces no error.
    """
    import threading

    from dwm.api import store, ui, v2

    assert hasattr(store, "_LOCK"), "store.py owns the connection and the lock"
    assert not hasattr(v2, "_LOCK"), (
        "v2 still holds its own lock; a second lock cannot protect a shared "
        "handle, and its existence hides that store.py is the one that matters"
    )

    errors: list[str] = []

    def hammer(name: str, fn) -> None:
        for _ in range(40):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - the point is to catch any
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

    jobs = [
        ("store.table_counts", store.table_counts),
        ("store.audit_funnel", lambda: store.audit_funnel(5)),
        ("store.topics", store.topics),
        ("v2._has", lambda: v2._has("fact_headline")),
        ("ui._has", lambda: ui._has("cube_sector_month")),
        ("v2._rows", lambda: v2._rows("SELECT 1 AS n")),
        # `ui._window()` was the one live unlocked reader. It held a
        # module-level handle from `store.connection()` and ran
        # `execute().fetchone()` on it, so it could overwrite a concurrent
        # request's result set - and the failure surfaced in THAT request, as a
        # TypeError inside a `_has` that had been correct all along. Hammering
        # only the locked readers cannot see it, which is why it survived the
        # previous two rounds of this fix.
        ("ui._window", ui._window),
    ]
    threads = [threading.Thread(target=hammer, args=job) for job in jobs for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, (
        f"{len(errors)} of {len(threads) * 40} concurrent reads failed. Every "
        f"reader of the shared handle must go through store.reading(). "
        f"First few: {errors[:3]}"
    )


def test_no_api_module_holds_an_unlocked_handle() -> None:
    """The structural half of the guard above.

    The hammer test can only prove the readers it happens to call. This proves
    the property that makes them all safe: no module keeps a connection of its
    own. `store.connection()` stays for callers outside `store`, but it hands
    back an UNLOCKED handle, and a lock that only some readers take protects
    nothing - the unlocked ones overwrite the locked ones' result sets.
    """
    import ast
    from pathlib import Path

    api = Path(__file__).resolve().parent.parent / "dwm" / "api"
    offenders: list[str] = []
    for path in sorted(api.glob("*.py")):
        if path.name == "store.py":
            continue  # the owner; `connection()` is its documented escape hatch
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # A module-level assignment whose value calls store.connection().
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            if not isinstance(getattr(node, "parent", None), ast.Module):
                # ast does not set `parent`; check the statement list instead.
                pass
            value = node.value
            if value is None:
                continue
            text = ast.unparse(value)
            if "store.connection()" in text or (
                path.stem == "v2" and "_con" in text and "duckdb.connect" in text
            ):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                name = ast.unparse(targets[0])
                offenders.append(f"{path.name}: {name} = {text}")
    assert not offenders, (
        "a module-level handle outside store.py defeats the lock: "
        + "; ".join(offenders)
    )




def _stock_rows(con) -> list[dict]:
    rows = con.execute(
        """
        SELECT trade_date, adj_close, return_pct, volatility_20d
        FROM fact_stock_daily WHERE stock_key = (
            SELECT stock_key FROM dim_stock WHERE symbol = 'TCS'
        ) ORDER BY trade_date LIMIT 30
        """
    ).fetchall()
    return [
        {"trade_date": r[0], "adj_close": r[1], "return_pct": r[2],
         "volatility_20d": r[3], "mentions": 0}
        for r in rows
    ]
