"""Dataset download into data/raw/{code}/.

Design constraints from the blueprint:
  * never fabricate data, so a missing download is an error, not a fallback
  * the 227 MB TOI file must survive a dropped connection, so downloads
    resume via HTTP Range
  * re-running must not re-download, so a sha256 sidecar is written and
    checked before fetching
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dwm.config import DatasetSpec, ensure_dirs, raw_dir
from dwm.logging_utils import get

log = get("dwm.ingest.fetch")

CHUNK = 1 << 20  # 1 MiB

# Some publishers reject non-browser clients outright. Verified 2026-10-01:
# Harvard Dataverse answers 403 Forbidden to a project-style User-Agent and
# 206 Partial Content to a browser-style one. Override with DWM_USER_AGENT.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
USER_AGENT = os.environ.get("DWM_USER_AGENT", DEFAULT_USER_AGENT)
CHECKSUM_SUFFIX = ".sha256"
PART_SUFFIX = ".part"
MAX_ATTEMPTS = 4


class FetchError(RuntimeError):
    """Raised when a dataset cannot be obtained. Never silently substituted."""


@dataclass(slots=True)
class FetchResult:
    code: str
    path: Path
    bytes_downloaded: int
    sha256: str
    reused: bool
    attempts: int = 1

    @property
    def size_mb(self) -> float:
        return round(self.bytes_downloaded / 1_048_576, 1)


def sha256_file(path: Path, chunk: int = CHUNK) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _checksum_path(path: Path) -> Path:
    return path.with_name(path.name + CHECKSUM_SUFFIX)


def _read_sidecar(path: Path) -> str | None:
    sidecar = _checksum_path(path)
    if not sidecar.exists():
        return None
    token = sidecar.read_text(encoding="utf-8").strip().split()
    return token[0] if token else None


def _write_sidecar(path: Path, digest: str) -> None:
    _checksum_path(path).write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def _open(url: str, offset: int = 0, timeout: int = 60) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if offset:
        # The Harvard Dataverse endpoint and raw.githubusercontent.com both
        # honour Range; verified with HTTP 206.
        request.add_header("Range", f"bytes={offset}-")
    return request


def _download_attempt(url: str, destination: Path, start_offset: int, timeout: int) -> int:
    """Stream the body to destination, appending from start_offset.

    Returns the number of bytes written by this attempt. Raises on a
    non-success status, so the caller can retry.
    """
    written = 0
    with urllib.request.urlopen(_open(url, start_offset, timeout), timeout=timeout) as response:
        mode = "ab" if start_offset else "wb"
        with destination.open(mode) as out:
            while block := response.read(CHUNK):
                out.write(block)
                written += len(block)
    return written


def fetch_dataset(
    spec: DatasetSpec, *, force: bool = False, timeout: int = 60
) -> FetchResult:
    """Ensure the raw file for `spec` is on disk. Returns a FetchResult."""
    ensure_dirs()
    directory = raw_dir(spec.code)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / spec.filename
    partial = target.with_name(target.name + PART_SUFFIX)

    if target.exists() and not force:
        expected = _read_sidecar(target)
        if expected:
            actual = sha256_file(target)
            if actual == expected:
                log.info("%s: reusing %s (%.1f MB, sha256 ok)", spec.code, target.name,
                         target.stat().st_size / 1_048_576)
                return FetchResult(spec.code, target, target.stat().st_size, actual, reused=True)
            log.warning("%s: checksum mismatch, refetching", spec.code)
        else:
            # No sidecar: hash once so future runs can skip the download.
            actual = sha256_file(target)
            _write_sidecar(target, actual)
            log.info("%s: reusing %s (%.1f MB, no sidecar, wrote one)", spec.code, target.name,
                     target.stat().st_size / 1_048_576)
            return FetchResult(spec.code, target, target.stat().st_size, actual, reused=True)

    if force:
        partial.unlink(missing_ok=True)

    log.info("%s: downloading %s", spec.code, spec.url)
    offset = partial.stat().st_size if partial.exists() else 0
    attempts = 0
    last_error: Exception | None = None

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        try:
            written = _download_attempt(spec.url, partial, offset, timeout)
            offset += written
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = exc
            have = partial.stat().st_size if partial.exists() else 0
            log.warning(
                "%s: attempt %d/%d failed (%s); %s at %.1f MB",
                spec.code, attempts, MAX_ATTEMPTS, exc, "resuming" if have else "restarting",
                have / 1_048_576,
            )
            offset = have
            if attempts < MAX_ATTEMPTS:
                continue
            raise FetchError(
                f"could not download {spec.code} after {attempts} attempts: {last_error}. "
                f"Fetch it manually from {spec.url} into {directory} and re-run. "
                f"Data is never fabricated."
            ) from last_error

        expected = spec.expected_bytes
        if expected and partial.stat().st_size < expected:
            log.warning(
                "%s: incomplete (%d of %d bytes), resuming",
                spec.code, partial.stat().st_size, expected,
            )
            continue
        break

    if not partial.exists() or partial.stat().st_size == 0:
        raise FetchError(f"{spec.code}: download produced no data")

    actual_bytes = partial.stat().st_size
    if spec.expected_bytes and actual_bytes != spec.expected_bytes:
        log.warning(
            "%s: expected %d bytes, got %d. Continuing; check the source.",
            spec.code, spec.expected_bytes, actual_bytes,
        )

    shutil.move(str(partial), str(target))
    digest = sha256_file(target)
    _write_sidecar(target, digest)
    log.info("%s: saved %.1f MB sha256=%s", spec.code, actual_bytes / 1_048_576, digest[:16])
    return FetchResult(spec.code, target, actual_bytes, digest, reused=False, attempts=attempts)


def free_space_mb(directory: Path) -> float:
    usage = shutil.disk_usage(directory)
    return usage.free / 1_048_576


# ---------------------------------------------------------------------------
# v2: small API responses and provenance sidecars
# ---------------------------------------------------------------------------


def fetch_bytes(url: str, timeout: int = 30, *, accept: str = "*/*") -> bytes:
    """GET a small resource into memory.

    For the v2 JSON and CSV endpoints, which return a few hundred KB at most and
    have no resume semantics worth having. Deliberately not routed through
    fetch_dataset, which streams a large CSV to disk with Range resumption and
    would leave a .part file behind for a 40 KB reply.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def provenance_path(target: Path) -> Path:
    """The `.source.json` sidecar that sits beside a cached file."""
    return target.with_name(target.name + ".source.json")


def write_provenance(target: Path, payload: dict[str, object]) -> Path:
    """Record where a cached file came from (AGENTS.md rule 22).

    Written after the data file, and never inside it, so a corrupt cache can be
    diagnosed without trusting the cache. Includes the sha256 of the file so a
    later run can tell whether the cache was edited by hand.
    """
    record = dict(payload)
    record["fetched_at"] = datetime.now(UTC).isoformat()
    if target.exists():
        record["bytes"] = target.stat().st_size
        record["sha256"] = sha256_file(target)
    path = provenance_path(target)
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_provenance(target: Path) -> dict[str, object] | None:
    """Read the sidecar for a cached file, or None when it has never been fetched."""
    path = provenance_path(target)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("%s: unreadable provenance sidecar, ignoring it", path.name)
        return None
