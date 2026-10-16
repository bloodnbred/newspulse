"""Project configuration: paths, run options, and dataset/topic config loading.

Kept free of duckdb/pandas imports so that `pytest` can exercise configuration
and the CLI without touching the warehouse.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

# Repository root: the directory containing dwm/ and config/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = PROJECT_ROOT / "config"
DATASETS_YAML = CONFIG_DIR / "datasets.yaml"
TOPICS_YAML = CONFIG_DIR / "topics.yaml"

# v2 sources. Kept apart from DATASETS_YAML so that load_datasets_config()
# still returns exactly the three v1 datasets, which tests/test_config.py pins.
DATASETS_V2_YAML = CONFIG_DIR / "datasets_v2.yaml"
STOCKS_YAML = CONFIG_DIR / "stocks.yaml"
EVENTS_V2_YAML = CONFIG_DIR / "events_v2.yaml"
STOCK_ALIASES_YAML = CONFIG_DIR / "stock_aliases.yaml"
SECTOR_KEYWORDS_YAML = CONFIG_DIR / "sector_keywords.yaml"

DEFAULT_DB_NAME = "dwm.duckdb"


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    return PROJECT_ROOT / "data"


def raw_dir(dataset: str) -> Path:
    return data_dir() / "raw" / dataset


def warehouse_dir() -> Path:
    return PROJECT_ROOT / "warehouse"


def default_db_path() -> Path:
    return warehouse_dir() / DEFAULT_DB_NAME


def reports_dir() -> Path:
    """Where generated artefacts go.

    Overridable through `DWM_REPORTS_DIR` so a test run, a sample run or a
    second warehouse can never overwrite the real `reports/mining.json` and
    `report.md`. Without this, running the test suite silently replaced the
    full-corpus mining results with results computed from a ten-row fixture,
    and the report then rendered three paired trading days.
    """
    override = os.environ.get("DWM_REPORTS_DIR")
    return Path(override) if override else PROJECT_ROOT / "reports"


def logs_dir() -> Path:
    return PROJECT_ROOT / "logs"


def ensure_dirs() -> None:
    """Create the writable directory tree. Safe to call repeatedly."""
    for d in (data_dir() / "raw", warehouse_dir(), reports_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# run options
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Settings:
    """Resolved options for a single pipeline invocation."""

    db_path: Path = field(default_factory=default_db_path)
    sample: int | None = None
    years: int = 5
    chunk: int = 100_000
    read_only: bool = False

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        if self.sample is not None and self.sample <= 0:
            raise ValueError(f"--sample must be positive, got {self.sample}")
        if self.years <= 0:
            raise ValueError(f"--years must be positive, got {self.years}")
        if self.chunk <= 0:
            raise ValueError(f"--chunk must be positive, got {self.chunk}")

    @property
    def is_sampled(self) -> bool:
        return self.sample is not None

    def parent_dir(self) -> Path:
        return self.db_path.parent


# The window is never hard-coded (BLUEPRINT section 1, "The window"):
# it is derived from the maximum publish_date actually present in the data.
def analysis_window(max_date: date, years: int) -> tuple[date, date]:
    """Return the inclusive [start, end] window ending at `max_date`."""
    try:
        start = max_date.replace(year=max_date.year - years)
    except ValueError:  # 29 Feb -> 28 Feb on a non-leap year
        start = max_date.replace(year=max_date.year - years, day=28)
    return start, max_date


def window_start_from_iso(max_date_iso: str, years: int) -> str:
    """Same as analysis_window but for a YYYY-MM-DD string, returns ISO."""
    start, end = analysis_window(date.fromisoformat(max_date_iso), years)
    return start.isoformat()


def days_in_window(years: int) -> int:
    """Approximate day count, used only for logging and guard rails."""
    return int(timedelta(days=round(365.2425 * years)).days)


# ---------------------------------------------------------------------------
# dataset config
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DatasetSpec:
    """One dataset as declared in config/datasets.yaml."""

    code: str
    name: str
    url: str
    filename: str
    raw: dict[str, Any]
    enabled: bool = True

    # -- column access -------------------------------------------------
    def alias(self, logical: str) -> str:
        """Resolve a logical field to the source column name.

        Falls back to the logical name when the config does not declare it,
        which keeps single-column or already-normalised sources working.
        """
        cols = self.raw.get("columns") or {}
        aliases = cols.get(logical)
        if aliases is None:
            return logical
        if isinstance(aliases, str):
            return aliases
        return aliases[0]

    def alias_list(self, logical: str) -> list[str]:
        cols = self.raw.get("columns") or {}
        aliases = cols.get(logical) or [logical]
        return [aliases] if isinstance(aliases, str) else list(aliases)

    @property
    def text_column(self) -> str | None:
        """Logical name of the text field, or None for numeric-only sources.

        nifty is a price series and has no text at all, so the caller must not
        insist on one.
        """
        declared = self.raw.get("text_column", "")
        return str(declared) if declared else None

    @property
    def has_text(self) -> bool:
        return self.text_column is not None

    @property
    def category_column(self) -> str | None:
        return self.raw.get("category_column") or None

    @property
    def date_column(self) -> str | None:
        return self.raw.get("date_column") or None

    @property
    def label_column(self) -> str | None:
        return self.raw.get("label_column")

    @property
    def date_formats(self) -> list[str]:
        return list(self.raw.get("date_formats") or ["%Y-%m-%d"])

    @property
    def declared_date_precision(self) -> str:
        return str(self.raw.get("date_precision") or "day")

    @property
    def is_labelled(self) -> bool:
        return bool(self.raw.get("labelled"))

    @property
    def numeric_columns(self) -> list[str]:
        return list(self.raw.get("numeric_columns") or [])

    @property
    def citation(self) -> str:
        return " ".join(str(self.raw.get("citation") or "").split())

    @property
    def provenance(self) -> str:
        return " ".join(str(self.raw.get("provenance") or "").split())

    @property
    def license(self) -> str:
        return str(self.raw.get("license") or "unknown")

    @property
    def expected_bytes(self) -> int | None:
        value = self.raw.get("expected_bytes")
        return int(value) if value else None

    @property
    def expected_rows(self) -> int | None:
        """Row count the publisher documents, when known.

        Used by dim_dataset to show a source-side expectation next to what was
        actually staged. Never used as a pass/fail gate: the published count
        and the file's true count can legitimately differ.
        """
        value = self.raw.get("expected_rows")
        return int(value) if value else None

    @property
    def encoding(self) -> str:
        return str(self.raw.get("encoding") or "utf-8")

    @property
    def category_separator(self) -> str | None:
        value = self.raw.get("category_separator")
        return str(value) if value else None

    @property
    def label_values(self) -> dict[str, list[str]]:
        raw = self.raw.get("label_values") or {}
        out: dict[str, list[str]] = {}
        for key, values in raw.items():
            out[key] = [values] if isinstance(values, str) else list(values)
        return out

    def local_path(self) -> Path:
        return raw_dir(self.code) / self.filename


def load_datasets_config(path: Path | None = None) -> dict[str, DatasetSpec]:
    """Load and validate config/datasets.yaml."""
    path = path or DATASETS_YAML
    if not path.exists():
        raise FileNotFoundError(f"missing dataset config: {path}")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    block = doc.get("datasets") or {}
    if not block:
        raise ValueError(f"{path} declares no datasets")

    specs: dict[str, DatasetSpec] = {}
    for code, body in block.items():
        body = body or {}
        for required in ("name", "url", "filename"):
            if not body.get(required):
                raise ValueError(f"dataset '{code}' in {path} is missing '{required}'")
        specs[code] = DatasetSpec(
            code=code,
            name=str(body["name"]),
            url=str(body["url"]),
            filename=str(body["filename"]),
            raw=body,
            enabled=bool(body.get("enabled", True)),
        )
    return specs


def get_dataset(code: str, path: Path | None = None) -> DatasetSpec:
    specs = load_datasets_config(path)
    if code not in specs:
        raise KeyError(f"unknown dataset '{code}'. known: {sorted(specs)}")
    return specs[code]


def enabled_datasets(path: Path | None = None) -> list[DatasetSpec]:
    return [s for s in load_datasets_config(path).values() if s.enabled]


# ---------------------------------------------------------------------------
# v2 source config
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SourceSpec:
    """One v2 source, as declared in config/datasets_v2.yaml.

    Not a DatasetSpec: a v2 source is not always one file at one URL. `stocks`
    is a glob of one CSV per symbol fetched from a per-symbol URL template, and
    `meta` is a single CSV. Kept as its own type so load_datasets_config() and
    everything built on it stay exactly as v1 declared them.
    """

    code: str
    name: str
    raw: dict[str, Any]
    enabled: bool = True

    @property
    def url(self) -> str:
        return str(self.raw.get("url") or "")

    @property
    def filename(self) -> str:
        return str(self.raw.get("filename") or "")

    @property
    def file_glob(self) -> str:
        return str(self.raw.get("file_glob") or "")

    @property
    def columns(self) -> dict[str, list[str]]:
        cols = self.raw.get("columns") or {}
        out: dict[str, list[str]] = {}
        for key, value in cols.items():
            out[str(key)] = [value] if isinstance(value, str) else list(value)
        return out

    @property
    def numeric_columns(self) -> list[str]:
        declared = self.raw.get("numeric_columns")
        if declared:
            return [declared] if isinstance(declared, str) else list(declared)
        # Fall back to the OHLCV-style set when the config lists none, since
        # every price-shaped source needs exactly these.
        return [k for k in ("open", "high", "low", "close", "volume", "turnover")
                if k in self.columns]

    @property
    def symbol_from(self) -> str:
        return str(self.raw.get("symbol_from") or "")

    @property
    def url_template(self) -> str:
        return str(self.raw.get("url_template") or "")

    @property
    def ticker_suffix(self) -> str:
        return str(self.raw.get("ticker_suffix") or "")

    @property
    def notes(self) -> str:
        return " ".join(str(self.raw.get("notes") or "").split())

    @property
    def industry_to_sector(self) -> dict[str, str]:
        return {str(k).strip().lower(): str(v)
                for k, v in (self.raw.get("industry_to_sector") or {}).items()}

    @property
    def limitation(self) -> str:
        return " ".join(str(self.raw.get("limitation") or "").split())

    @property
    def citation(self) -> str:
        return " ".join(str(self.raw.get("citation") or "").split())

    @property
    def license(self) -> str:
        return str(self.raw.get("license") or "unknown")

    @property
    def encoding(self) -> str:
        return str(self.raw.get("encoding") or "utf-8")

    def alias_list(self, logical: str) -> list[str]:
        return self.columns.get(logical) or [logical]

    def local_dir(self) -> Path:
        return raw_dir(self.code)


def load_sources_config(path: Path | None = None) -> dict[str, SourceSpec]:
    """Load config/datasets_v2.yaml into SourceSpec objects."""
    path = path or DATASETS_V2_YAML
    if not path.exists():
        raise FileNotFoundError(f"missing v2 dataset config: {path}")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not doc:
        raise ValueError(f"{path} declares no sources")
    specs: dict[str, SourceSpec] = {}
    for code, body in doc.items():
        body = body or {}
        if not body.get("name"):
            raise ValueError(f"source '{code}' in {path} is missing 'name'")
        specs[str(code)] = SourceSpec(
            code=str(code), name=str(body["name"]), raw=body,
            enabled=bool(body.get("enabled", True)),
        )
    return specs


# ---------------------------------------------------------------------------
# v2 stock / sector / event / alias config
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SectorConfig:
    """config/stocks.yaml: the ten sectors, per-symbol fallbacks, overrides.

    `overrides` is the honest part. NSE's industry labels are the primary source
    but they are not always right (Asian Paints is filed under "Consumer
    Durables", which is not what it makes), so a symbol listed here wins over
    the label-derived sector and `sector_source` records that it did.
    """

    sectors: list[str] = field(default_factory=list)
    stocks: dict[str, dict[str, str]] = field(default_factory=dict)
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    industry_to_sector: dict[str, str] = field(default_factory=dict)
    fallback_file: str = ""

    def sector_for_label(self, industry: str | None) -> str | None:
        if not industry:
            return None
        return self.industry_to_sector.get(str(industry).strip().lower())


@dataclass(slots=True)
class StockEntry:
    company: str
    sector: str
    source: str          # 'meta' | 'config' | 'override' | 'unknown'
    industry: str | None = None


def load_sector_config(
    path: Path | None = None, sources: dict[str, SourceSpec] | None = None
) -> SectorConfig:
    """Merge config/stocks.yaml with the industry_to_sector map from v2 config.

    Precedence, highest first:
      1. `overrides` in stocks.yaml   - a symbol NSE files wrongly
      2. `stocks` in stocks.yaml      - the pack's own per-symbol assignment
      3. `industry_to_sector`         - mechanical map of the feed's label
      4. nothing                      - recorded as sector_source='unknown'
    """
    path = path or STOCKS_YAML
    if not path.exists():
        raise FileNotFoundError(f"missing stock config: {path}")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    sectors = [str(s) for s in (doc.get("sectors") or [])]
    if not sectors:
        raise ValueError(f"{path} declares no sectors")

    def entries(block: Any) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for symbol, body in (block or {}).items():
            body = body or {}
            out[str(symbol).strip().upper()] = {
                str(k): str(v) for k, v in body.items() if v is not None
            }
        return out

    stocks = entries(doc.get("stocks"))
    overrides = entries(doc.get("overrides"))
    for symbol, body in overrides.items():
        # An override must not invent a sector outside the declared list.
        sector = body.get("sector")
        if sector and sector not in sectors:
            raise ValueError(
                f"{path}: override for {symbol} names sector {sector!r}, "
                f"which is not in sectors {sectors}"
            )

    industry_map: dict[str, str] = {}
    for spec in (sources or load_sources_config()).values():
        industry_map.update(spec.industry_to_sector)

    return SectorConfig(
        sectors=sectors,
        stocks=stocks,
        overrides=overrides,
        industry_to_sector=industry_map,
        fallback_file=path.name,
    )


def resolve_sector(symbol: str, industry: str | None, cfg: SectorConfig) -> StockEntry:
    """Resolve one symbol to a company and sector, recording where it came from."""
    symbol = symbol.strip().upper()
    override = cfg.overrides.get(symbol)
    if override:
        return StockEntry(
            company=override.get("company", ""), sector=override["sector"],
            source="override", industry=industry,
        )
    configured = cfg.stocks.get(symbol)
    if configured:
        return StockEntry(
            company=configured.get("company", ""), sector=configured["sector"],
            source="config", industry=industry,
        )
    mapped = cfg.sector_for_label(industry)
    if mapped:
        return StockEntry(company="", sector=mapped, source="meta", industry=industry)
    return StockEntry(company="", sector="", source="unknown", industry=industry)


@dataclass(slots=True)
class EventEntry:
    date: date
    label: str
    event_type: str


def load_events(path: Path | None = None) -> list[EventEntry]:
    """config/events_v2.yaml -> event list, de-duplicated on (date, label)."""
    path = path or EVENTS_V2_YAML
    if not path.exists():
        raise FileNotFoundError(f"missing event config: {path}")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out: dict[tuple[str, str], EventEntry] = {}
    for body in doc.get("events_v2") or []:
        body = body or {}
        raw_date = str(body.get("date") or "")
        label = str(body.get("label") or "").strip()
        if not raw_date or not label:
            continue
        parsed = date.fromisoformat(raw_date)
        key = (raw_date, label.casefold())
        if key in out:
            continue
        out[key] = EventEntry(
            date=parsed, label=label,
            event_type=str(body.get("type") or "other").strip(),
        )
    return sorted(out.values(), key=lambda e: (e.date, e.label))


@dataclass(slots=True)
class AliasRules:
    """config/stock_aliases.yaml: how headlines spell company names."""

    context_words: list[str] = field(default_factory=list)
    use_topic_as_context: str = ""
    aliases: dict[str, dict[str, list[str] | list[str]]] = field(default_factory=dict)


def load_stock_aliases(path: Path | None = None) -> AliasRules:
    path = path or STOCK_ALIASES_YAML
    if not path.exists():
        raise FileNotFoundError(f"missing alias config: {path}")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    def strings(value: Any) -> list[str]:
        if value is None:
            return []
        return [value] if isinstance(value, str) else [str(v) for v in value]

    aliases: dict[str, dict[str, list[str]]] = {}
    for symbol, body in (doc.get("aliases") or {}).items():
        body = body or {}
        entry = {
            "names": strings(body.get("names")),
            "ambiguous": strings(body.get("ambiguous")),
            "case_sensitive": strings(body.get("case_sensitive")),
        }
        # An alias that is only ever mentioned as a bare ticker is case
        # sensitive by definition; `ITC` and `HDFC` are ordinary words.
        for tick in entry["case_sensitive"]:
            if tick not in entry["names"]:
                entry["names"].append(tick)
        for tick in entry["ambiguous"]:
            if tick not in entry["names"]:
                entry["names"].append(tick)
        aliases[str(symbol).strip().upper()] = entry
    return AliasRules(
        context_words=[str(w).lower() for w in (doc.get("context_words") or [])],
        use_topic_as_context=str(doc.get("use_topic_as_context") or ""),
        aliases=aliases,
    )


# ---------------------------------------------------------------------------
# topic config
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TopicMap:
    """TOI category prefix -> coarse topic lookup."""

    topics: list[str] = field(default_factory=list)
    prefix_map: dict[str, str] = field(default_factory=dict)
    prefix_patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=list)
    keyword_rules: list[dict[str, Any]] = field(default_factory=list)
    unknown_labels: set[str] = field(default_factory=set)
    fallback_topic: str = "Other"
    ifnd_category_map: dict[str, str] = field(default_factory=dict)

    def lookup(self, category: str | None) -> str:
        """Coarse topic for a raw source category.

        Resolution order, most specific first:
          1. unknown-label check
          2. the full category, then progressively shorter dotted prefixes,
             so "life-style.health-fitness.diet" beats "life-style"
          3. ordered regex patterns
          4. keyword rules
          5. fallback

        Never raises, never returns None, so the Phase 2 gate that every raw
        category maps to a topic holds by construction.
        """
        if category is None:
            return "Unknown"
        raw = str(category).strip()
        if raw.lower() in self.unknown_labels:
            return "Unknown"

        for prefix in self._prefix_candidates(raw):
            topic = self.prefix_map.get(prefix)
            if topic:
                return topic

        lowered = raw.lower()
        for pattern, topic in self.prefix_patterns:
            if pattern.search(lowered):
                return topic

        for rule in self.keyword_rules:
            topic = str(rule.get("topic") or self.fallback_topic)
            needles = rule.get("match") or []
            if isinstance(needles, str):
                needles = [needles]
            for needle in needles:
                if str(needle).lower() in lowered:
                    return topic

        return self.fallback_topic

    @staticmethod
    def _prefix_candidates(raw: str) -> list[str]:
        """Longest-to-shortest dotted prefixes of a category.

        "a.b.c" -> ["a.b.c", "a.b", "a"]. Slashes are treated as separators
        too, since some TOI categories use them.
        """
        normalised = raw.strip().lower().replace("/", ".")
        parts = [p for p in normalised.split(".") if p]
        if not parts:
            return [normalised] if normalised else []
        return [".".join(parts[: i + 1]) for i in range(len(parts) - 1, -1, -1)]


def load_topic_map(path: Path | None = None) -> TopicMap:
    path = path or TOPICS_YAML
    if not path.exists():
        raise FileNotFoundError(f"missing topic config: {path}")
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    prefix_map = {str(k).lower(): str(v) for k, v in (doc.get("prefix_map") or {}).items()}
    fallback = doc.get("fallback") or {}
    topics = [str(t) for t in (doc.get("topics") or [])]
    fallback_topic = str(fallback.get("topic") or "Other")

    # Ordered regex rules, for families too large to enumerate such as the
    # Times city editions (delhi-times, bombay-times, ...).
    patterns: list[tuple[re.Pattern[str], str]] = []
    for rule in doc.get("prefix_patterns") or []:
        topic = str(rule.get("topic") or fallback_topic)
        # `match` may be a single pattern or a list of them; normalise so a
        # scalar is not iterated character by character.
        raw_patterns = rule.get("match") or []
        if isinstance(raw_patterns, str):
            raw_patterns = [raw_patterns]
        for pattern in raw_patterns:
            try:
                compiled = re.compile(str(pattern), re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"bad prefix_pattern regex {pattern!r}: {exc}") from exc
            patterns.append((compiled, topic))

    # Every topic referenced by the map must exist in the topics list, or
    # dim_topic would gain rows nothing points at.
    referenced = set(prefix_map.values()) | {fallback_topic}
    referenced.update(topic for _, topic in patterns)
    for rule in doc.get("keyword_rules") or []:
        if rule.get("topic"):
            referenced.add(str(rule["topic"]))
    referenced.update((doc.get("ifnd_category_map") or {}).values())
    for topic in sorted(referenced):
        if topic not in topics:
            topics.append(topic)

    return TopicMap(
        topics=topics,
        prefix_map=prefix_map,
        prefix_patterns=patterns,
        keyword_rules=list(doc.get("keyword_rules") or []),
        unknown_labels={str(x).lower() for x in (fallback.get("unknown_labels") or [])},
        fallback_topic=fallback_topic,
        ifnd_category_map={
            str(k).lower(): str(v) for k, v in (doc.get("ifnd_category_map") or {}).items()
        },
    )


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------


def threads() -> int:
    """DuckDB thread count, overridable for constrained laptops."""
    value = os.environ.get("DWM_THREADS")
    if value and value.isdigit():
        return int(value)
    return max(1, (os.cpu_count() or 4) - 1)


def memory_limit() -> str:
    return os.environ.get("DWM_MEMORY_LIMIT", "4GB")
