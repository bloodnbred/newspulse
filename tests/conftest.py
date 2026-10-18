"""Shared pytest fixtures.

Tests never touch the real warehouse, data/raw, or reports/. Every fixture
writes to a tmp_path so the suite is safe to run at any time.

The `reports_dir` redirect below is not optional. The mining and inference
stages write their artefacts to a fixed reports directory, so without it a
test run silently replaced the real full-corpus `reports/mining.json` with
results computed from a ten-row fixture, and the report then rendered three
paired trading days as though that were the finding.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from dwm.config import Settings


@pytest.fixture(autouse=True)
def never_write_to_the_real_warehouse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse any test that opens the project's warehouse for writing.

    The suite is meant to be safe to run at any time, and `reports_dir` below
    exists for exactly that reason: a test run once replaced the real
    `reports/mining.json` with results from a ten-row fixture. The warehouse is
    the same hazard and far worse - 591 MB and an hour to rebuild.

    `dwm.db.connect` defaults to `create=True`, which opens read-write. Any test
    that constructs a bare `Settings()` and calls a pipeline function therefore
    resolves `db_path` to the real warehouse and can truncate it. One did, and
    the suite reported 24 failures that all read `Table with name fact_headline
    does not exist!` before anyone worked out that the database had been
    emptied rather than that the tests were broken.

    So the refusal is here, in one place, before anything can open the file. A
    test that genuinely needs a writable database has to build a `Settings`
    pointed at `tmp_path`, which is what every other fixture here does.
    """
    from dwm import db
    from dwm.config import default_db_path

    real = default_db_path().resolve()
    original = db.connect

    def guarded(settings=None, *, create: bool = True):
        path = (settings.db_path if settings is not None else db.default_db_path())
        try:
            resolved = Path(path).resolve()
        except OSError:
            resolved = Path(path)
        # `read_only` in the settings means the caller only ever reads, which
        # DuckDB enforces itself; those opens are fine.
        read_only = getattr(settings, "read_only", False) if settings is not None else False
        if resolved == real and (create or not read_only):
            raise RuntimeError(
                f"a test tried to open the project's warehouse for writing: {resolved}\n"
                "Tests must build a Settings() pointed at tmp_path. Fix the test, "
                "or copy the database first - do not relax this guard."
            )
        return original(settings, create=create)

    monkeypatch.setattr(db, "connect", guarded)


@pytest.fixture(autouse=True)
def reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every generated-artefact path at this test's own directory.

    Seeded from `DWM_TEST_REPORTS_SEED` when that is set. That exists because a
    redirect makes several tests/test_ui.py checks SKIP rather than fail, and a
    skipped test that was supposed to catch a broken chart name is worse than no
    test: the suite reports green while the check never runs. Seeding the temp
    directory with the real reports lets those checks execute against real
    output. Set the variable to run them; leave it unset for the fast default.
    """
    target = tmp_path / "reports"
    seed = os.environ.get("DWM_TEST_REPORTS_SEED")
    if seed and Path(seed).is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for item in Path(seed).iterdir():
            if item.is_file():
                shutil.copy2(item, target / item.name)
    monkeypatch.setenv("DWM_REPORTS_DIR", str(target))
    return target

TOI_FIXTURE = """publish_date,headline_category,headline_text
20010101,sports.wwe,"win over cena satisfying but defeating undertaker bigger roman reigns"
20010102,unknown,"Status quo will not be disturbed at Ayodhya; says Vajpayee"
20010102,unknown,"Fissures in Hurriyat over Pak visit"
20010103,business.gadgets,"New phone launch promises faster charging and better camera"
20010104,politics.parliament,"Parliament clears bill after heated debate!!!"
20010105,sports.cricket,"India win by 5 wickets in the final over"
"""

IFND_FIXTURE = """id,Statement,Image,Web,Category,Date,Label
1,"WHO praises India's Aarogya Setu app",https://example.com/a.jpg,DNAINDIA,COVID-19,Oct-20,TRUE
2,"Bihar Assembly Election 2020: Tej Pratap shifted",https://example.com/b.jpg,DNAINDIA,ELECTION,Nov 2020,TRUE
3,"Fact Check: 1938 video shared as PM performing yoga",https://example.com/c.jpg,INDIA TODAY,VIOLENCE,Oct-20,Fake
"""

NIFTY_FIXTURE = """Date,Open,High,Low,Close,Volume,Turnover
01-Jan-15,8272.8,8294.7,8248.75,8284,56560411,2321.88
02-Jan-15,8288.7,8410.6,8288.7,8395.45,101887024,4715.72
05-Jan-15,8407.95,8445.6,8363.9,8378.4,118160545,5525.52
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "test.duckdb", years=5, chunk=1000)


@pytest.fixture
def toi_csv(tmp_path: Path) -> Path:
    path = tmp_path / "india-news-headlines.csv"
    path.write_text(TOI_FIXTURE, encoding="utf-8")
    return path


@pytest.fixture
def ifnd_csv(tmp_path: Path) -> Path:
    path = tmp_path / "IFND.csv"
    path.write_text(IFND_FIXTURE, encoding="utf-8")
    return path


@pytest.fixture
def nifty_csv(tmp_path: Path) -> Path:
    path = tmp_path / "nifty50.csv"
    path.write_text(NIFTY_FIXTURE, encoding="utf-8")
    return path


@pytest.fixture
def con(settings: Settings):
    from dwm.db import connect

    connection = connect(settings)
    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# a complete warehouse, small enough to mine in seconds
# ---------------------------------------------------------------------------

# Slightly larger than the bare fixtures above, because the mining and
# inference stages need a few rows per topic and a few per class to produce
# anything at all. Still tiny: this exists to exercise the code paths, not to
# produce a meaningful answer.
MINING_TOI_ROWS = [
    "publish_date,headline_category,headline_text",
    "20160101,sports.cricket,India win the final over comfortably",
    "20160103,business.markets,Sensex gains as profits beat expectations",
    "20160106,sports.cricket,SHOCKING! India CRUSHED in final OVER!!!",
    "20160107,business.markets,Sensex falls as profits disappoint badly",
    "20160110,city.mumbai,Mumbai rains bring relief after a long dry spell",
    "20170103,business.markets,Sensex gains on budget hopes",
    "20170104,sports.cricket,India wins another final comfortably",
    "20180103,business.markets,Sensex ends year on a high note",
    "20180104,technology.gadgets,Phone camera claims to be the best ever",
    "20180105,city.bengaluru,Bengaluru traffic plan announced for metro",
]

MINING_IFND_ROWS = [
    "id,Statement,Image,Web,Category,Date,Label",
    '1,"A calm statement about policy",a.jpg,SITE,POLITICS,Oct-20,TRUE',
    '2,"SHOCKING government admits massive failure!!",b.jpg,SITE,GOVERNMENT,Oct-20,Fake',
    '3,"Third statement with a neutral tone",c.jpg,SITE,VIOLENCE,Nov 2020,FALSE',
    '4,"Fourth statement about courts",d.jpg,SITE,POLITICS,20-Sep,TRUE',
    '5,"Fifth statement with no date",e.jpg,SITE,ELECTION,,FALSE',
    '6,"Sixth calm statement",f.jpg,SITE,POLITICS,Oct-20,TRUE',
    '7,"Seventh calm statement",g.jpg,SITE,GOVERNMENT,Nov 2020,TRUE',
    '8,"Eighth calm statement",h.jpg,SITE,POLITICS,Oct-20,TRUE',
    '9,"Ninth calm statement",i.jpg,SITE,GOVERNMENT,Nov 2020,TRUE',
    '10,"Tenth calm statement",j.jpg,SITE,POLITICS,Oct-20,TRUE',
]

MINING_NIFTY_ROWS = [
    "Date,Open,High,Low,Close,Volume,Turnover",
    "04-01-2016,100,110,95,105,1000,10.5",
    "05-01-2016,105,115,100,112,1100,11.5",
    "06-01-2016,112,120,108,118,1200,12.5",
    "07-01-2016,118,125,115,124,1300,13.5",
    "03-01-2017,124,130,120,128,1500,15.5",
    "04-01-2017,128,135,125,132,1600,16.5",
    "05-01-2017,132,140,130,138,1700,17.5",
    "06-01-2017,138,145,135,142,1800,18.5",
    "03-01-2018,142,148,138,145,1900,19.5",
    "04-01-2018,145,152,142,150,2000,20.5",
    "05-01-2018,150,156,147,154,2100,21.5",
    "06-01-2018,154,160,150,158,2200,22.5",
]


@pytest.fixture
def mining_db(tmp_path: Path, settings: Settings, monkeypatch):
    """A full warehouse — staged, cleaned, featurised, built — with a fast
    mining configuration, yielding `(connection, mining_config)`.

    Shared by the mining, inference and API test modules, because all three
    need the same thing and rebuilding it per module would triple the runtime
    of the suite.
    """
    import copy

    import dwm.features.runner as runner
    from dwm.config import load_datasets_config
    from dwm.db import connect
    from dwm.etl import run_etl
    from dwm.features.runner import load_features_config, run_features
    from dwm.ingest.staging import stage_dataset
    from dwm.mining import load_mining_config
    from dwm.warehouse import run_build

    features_cfg = copy.deepcopy(load_features_config())
    features_cfg["runtime"]["use_multiprocessing"] = False
    features_cfg["keywords"]["min_doc_frequency"] = 1
    monkeypatch.setattr(runner, "load_features_config", lambda *a, **k: features_cfg)

    mining_cfg = copy.deepcopy(load_mining_config())
    # Small samples so the fixture runs in seconds, and low thresholds so the
    # sparse fixture still yields transactions.
    mining_cfg["clustering"].update(
        {"sample_size": 400, "k_min": 2, "k_max": 3, "min_df": 1,
         "svd_components": 5}
    )
    mining_cfg["rules"].update({"sample_transactions": 500, "min_support": 0.10})
    mining_cfg["rules"]["keyword_rules"]["sample_transactions"] = 200
    mining_cfg["market"]["min_observations"] = 3
    monkeypatch.setattr("dwm.mining.load_mining_config", lambda *a, **k: mining_cfg)

    specs = load_datasets_config()
    connection = connect(settings)
    for code, rows in (
        ("toi", MINING_TOI_ROWS),
        ("ifnd", MINING_IFND_ROWS),
        ("nifty", MINING_NIFTY_ROWS),
    ):
        path = tmp_path / f"{code}.csv"
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        stage_dataset(connection, specs[code], path, settings=settings)
    run_etl(settings, con=connection)
    run_features(settings, con=connection)
    run_build(settings, con=connection)
    try:
        yield connection, mining_cfg
    finally:
        connection.close()
