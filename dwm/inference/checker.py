"""Headline Checker: score a pasted headline against this corpus.

Three things are measured, and it matters that they are kept apart:

1. **Style signals** - sensationalism, sentiment, topic. These describe writing.
2. **Percentile** - where this headline's sensational score sits among the 3.15M
   in the warehouse. It answers "is this unusually hyped FOR THIS CORPUS", not
   "is this true".
3. **Neighbours** - the five most similar past headlines by TF-IDF cosine.

A headline checker is the single easiest place in this project to imply that a
style signal is a truth signal. Nothing here does. The response names the
corpus, reports the percentile against the corpus's own distribution, and says
in the body that a high score means the writing is intense, not that the claim
is false. This is exactly the distinction the project's wording rule exists to
protect, and it is why the field is called a risk signal and never a fake-news
rate.
"""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np

from dwm.config import PROJECT_ROOT
from dwm.logging_utils import get

log = get("dwm.inference.checker")

MODELS_DIR = PROJECT_ROOT / "models"
TFIDF_PATH = MODELS_DIR / "tfidf.joblib"
NEIGHBOUR_SAMPLE = 60_000
NEIGHBOURS = 5
#: Fixed so the neighbour corpus is identical on every run.
NEIGHBOUR_SEED = 42

DISCLAIMER = (
    "This scores the WRITING, not the claim. A high sensational score means the "
    "headline uses more superlatives, capitals and punctuation than most "
    "headlines in this corpus - which is not evidence that it is false, and not "
    "evidence that it is true."
)


def _percentile_of(con: duckdb.DuckDBPyConnection, score: float) -> dict[str, Any]:
    """Where this score sits in the warehouse's own distribution."""
    row = con.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE sensational_score <= ?),
               round(avg(sensational_score), 6),
               round(stddev_samp(sensational_score), 6)
        FROM fact_headline
        WHERE sensational_score IS NOT NULL
        """,
        [score],
    ).fetchone()
    total, at_or_below, mean, sd = row
    if not total or at_or_below is None:
        return {"percentile": None, "n_corpus": int(total or 0)}
    pct = 100.0 * float(at_or_below) / float(total)
    return {
        "percentile": round(pct, 2),
        "n_corpus": int(total),
        "corpus_mean": mean,
        "corpus_sd": sd,
        "in_words": _words(pct),
    }


def _words(pct: float) -> str:
    if pct >= 99:
        return "more hyped than 99% of the headlines in this corpus"
    if pct >= 95:
        return "more hyped than 95% of the headlines in this corpus"
    if pct >= 75:
        return "more hyped than three quarters of this corpus"
    if pct >= 25:
        return "unremarkable against this corpus"
    return "quieter than most of this corpus"


def _neighbours(
    con: duckdb.DuckDBPyConnection, text: str
) -> list[dict[str, Any]]:
    """Nearest past headlines by TF-IDF cosine, if the vectoriser has been built.

    Returns [] rather than a random sample when the model is absent. A "similar
    headlines" list built from anything other than similarity would be a lie
    about what similarity means.
    """
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer

    if not TFIDF_PATH.exists():
        log.info("no %s; /check will omit similar headlines", TFIDF_PATH.name)
        return []
    bundle = joblib.load(TFIDF_PATH)
    vectoriser: TfidfVectorizer = bundle["vectoriser"]
    matrix = bundle["matrix"]
    headlines: list[str] = bundle["headline_ids"]

    query = vectoriser.transform([text])
    scores = (matrix @ query.T).toarray().ravel()
    # `positions` index into the score vector; `headline_ids` are the matching
    # rows of the corpus. Keeping the two apart matters: they are different
    # index spaces, and conflating them either raises IndexError or, if the
    # lengths happen to line up, silently returns the wrong headlines.
    positions = np.argsort(-scores)[:NEIGHBOURS]
    if len(positions) == 0 or scores[positions[0]] <= 0:
        return []
    headline_ids = [int(headlines[p]) for p in positions]

    placeholders = ",".join("?" * len(headline_ids))
    rows = {
        int(r[0]): (str(r[1]), r[2])
        for r in con.execute(
            f"SELECT headline_id, publish_date, headline_text "
            f"FROM fact_headline WHERE headline_id IN ({placeholders})",
            headline_ids,
        ).fetchall()
    }
    # Only the headline TEXT and DATE are returned. No features, no topics, no
    # scores for the neighbours: the reader asked what else said something
    # similar, not what our model thinks of them.
    return [
        {
            "date": rows[headline_id][0],
            "text": rows[headline_id][1],
            "similarity": round(float(scores[position]), 4),
        }
        for position, headline_id in zip(positions, headline_ids, strict=True)
        if headline_id in rows
    ]


def _tags(
    con: duckdb.DuckDBPyConnection, text: str
) -> dict[str, list[str]]:
    """Stocks and sectors named in the headline, using the linking rules.

    Reports the matched alias and whether it needed context, because a bare
    "Max" and a full company name are different strengths of claim.
    """
    from dwm.config import load_stock_aliases
    from dwm.linking.aliases import build_matchers, find_matches, load_stock_keys

    rules = load_stock_aliases()
    matchers = build_matchers(rules, load_stock_keys(con))
    found = find_matches(0, text, matchers, rules)

    stocks: list[dict[str, Any]] = []
    sector_of: dict[int, str] = {}
    for match in found:
        row = con.execute(
            "SELECT s.sector_name FROM dim_stock d "
            "LEFT JOIN dim_sector s ON s.sector_key = d.sector_key "
            "WHERE d.stock_key = ?",
            [match.stock_key],
        ).fetchone()
        if row and row[0]:
            sector_of[match.stock_key] = row[0]
        stocks.append(
            {
                "symbol": match.symbol,
                "matched_alias": match.matched_alias,
                "match_method": match.match_method,
                "needs_context": match.is_ambiguous_alias,
            }
        )

    from dwm.linking.sector_news import build_keyword_patterns, keyword_sector, load_sector_keywords

    patterns = build_keyword_patterns(load_sector_keywords())
    sectors = []
    for name in keyword_sector(text, patterns):
        if name in sector_of.values():
            continue
        row = con.execute(
            "SELECT sector_name FROM dim_sector WHERE sector_name = ?", [name]
        ).fetchone()
        if row:
            sectors.append(row[0])

    return {"stocks": stocks, "sectors": sectors}


def build_response(
    con: duckdb.DuckDBPyConnection,
    text: str,
    measures: Any,
    *,
    truncated: bool = False,
) -> dict[str, Any]:
    """The full /check response."""
    score = float(getattr(measures, "sensational_score", 0.0) or 0.0)
    percentile = _percentile_of(con, score)
    tags = _tags(con, text)
    neighbours = _neighbours(con, text)

    topic = None
    row = con.execute(
        "SELECT t.topic_name FROM fact_headline f "
        "JOIN dim_topic t ON t.topic_key = f.topic_key "
        "WHERE lower(f.headline_text) = lower(?) LIMIT 1",
        [text],
    ).fetchone()
    if row:
        topic = row[0]

    return {
        "headline": text,
        "truncated": truncated,
        "is_verbatim_in_corpus": topic is not None,
        "topic": topic,
        "style": {
            "sensational_score": round(score, 6),
            "sensational": bool(getattr(measures, "is_sensational", False)),
            "risk_signal": bool(getattr(measures, "is_risk_signal", False)),
            "sentiment_compound": round(
                float(getattr(measures, "sentiment_compound", 0.0) or 0.0), 4
            ),
            "word_count": int(getattr(measures, "token_count", 0) or 0),
            "unique_tokens": int(getattr(measures, "unique_token_count", 0) or 0),
            "caps_ratio": round(float(getattr(measures, "caps_token_ratio", 0.0) or 0.0), 4),
            "superlative_count": int(getattr(measures, "superlative_count", 0) or 0),
            "urgency_count": int(getattr(measures, "urgency_count", 0) or 0),
            "exclamation_count": int(getattr(measures, "exclamation_count", 0) or 0),
        },
        "percentile": percentile,
        "neighbours": neighbours,
        "tags": tags,
        "disclaimer": DISCLAIMER,
        "metric_name": "sensational style score",
        "caveat": (
            "Percentiles are against this one corpus of Indian news headlines "
            "from one outlet over five years. They are not a measure of truth, "
            "and a score is not a verdict."
        ),
    }


def build_tfidf(con: duckdb.DuckDBPyConnection, sample: int = NEIGHBOUR_SAMPLE) -> dict[str, Any]:
    """Fit and save the TF-IDF model used for neighbours.

    Kept as its own step so `models/tfidf.joblib` is built once rather than on
    the first request, which would make the first /check call take a minute.
    """
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer

    from dwm.mining.sampling import hash_expr

    # Sampled by a deterministic hash, not by taking the first N rows in id
    # order. Headline ids ascend with time, so "first 60,000" would draw every
    # neighbour from 2015-2016 and a reader searching a 2019 headline would be
    # shown its nearest neighbours from four years earlier without being told.
    # hash_expr is seeded, so the sample is identical on every run.
    rows = con.execute(
        f"""
        SELECT headline_id, headline_text FROM (
            SELECT headline_id, headline_text,
                   {hash_expr(NEIGHBOUR_SEED, 'headline_id')} AS sample_key
            FROM fact_headline
            WHERE in_window AND headline_text IS NOT NULL
        )
        ORDER BY sample_key
        LIMIT ?
        """,
        [int(sample)],
    ).fetchall()
    if len(rows) < 100:
        return {"built": False, "reason": f"only {len(rows)} headlines; need 100"}

    vectoriser = TfidfVectorizer(
        max_features=20_000, ngram_range=(1, 2), min_df=3, stop_words="english"
    )
    matrix = vectoriser.fit_transform([r[1] for r in rows])
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"vectoriser": vectoriser, "matrix": matrix, "headline_ids": [r[0] for r in rows],
         "sample": len(rows)},
        TFIDF_PATH,
    )
    log.info("wrote %s from %s headlines", TFIDF_PATH.name, f"{len(rows):,}")
    return {
        "built": True,
        "path": str(TFIDF_PATH),
        "sample": len(rows),
        "features": int(matrix.shape[1]),
        "seed": NEIGHBOUR_SEED,
        "caveat": (
            f"A deterministic hash sample of {len(rows):,} in-window headlines, "
            f"so it spans the whole window rather than its earliest years. "
            f"Neighbours are textually similar headlines, not related stories."
        ),
    }
