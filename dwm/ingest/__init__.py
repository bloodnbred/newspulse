"""Ingest stage: fetch raw CSVs and land them in the DuckDB staging tables."""

from dwm.ingest.runner import (
    V2_DATASETS,
    IngestOutcome,
    ingest,
    ingest_one,
    ingest_v2,
    load_raw,
)

__all__ = [
    "V2_DATASETS",
    "IngestOutcome",
    "ingest",
    "ingest_one",
    "ingest_v2",
    "load_raw",
]
