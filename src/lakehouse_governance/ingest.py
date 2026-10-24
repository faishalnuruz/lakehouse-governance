"""Bronze: append-only ingestion that never reads the same file twice.

This is the local stand-in for Auto Loader. The mechanism that matters is a
checkpoint of already-seen files: a scheduled run must pick up new arrivals and
ignore everything else, and re-running it must not duplicate a single row.

Bronze keeps what arrived, adding only provenance: which file it came from and
when it was ingested. Cleaning happens downstream, where it can be reviewed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

BRONZE_COLUMNS = """
    activity_id VARCHAR,
    event_id VARCHAR,
    event_name VARCHAR,
    athlete_name VARCHAR,
    started_at VARCHAR,
    distance_km DOUBLE,
    duration_s BIGINT,
    pace_sec_per_km DOUBLE,
    elevation_gain_m BIGINT,
    heart_rate_avg BIGINT,
    heart_rate_max BIGINT,
    gps_lat DOUBLE,
    gps_lon DOUBLE,
    _source_file VARCHAR,
    _ingest_seq BIGINT,
    _ingested_at TIMESTAMP
"""


@dataclass
class IngestResult:
    files_read: int = 0
    files_skipped: int = 0
    rows_added: int = 0
    rows_total: int = 0

    def as_dict(self) -> dict:
        return {
            "files_read": self.files_read,
            "files_skipped": self.files_skipped,
            "rows_added": self.rows_added,
            "rows_total": self.rows_total,
        }


def _load_checkpoint(path: Path) -> tuple[set[str], int]:
    """Return the files already read and the highest sequence number used.

    A corrupt checkpoint re-reads files, which duplicates data but is
    recoverable; skipping files would lose them permanently.
    """
    if not path.exists():
        return set(), 0
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return set(), 0
    return set(payload.get("files", [])), int(payload.get("seq", 0))


def _save_checkpoint(path: Path, seen: set[str], seq: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"files": sorted(seen), "seq": seq}, indent=2) + "\n")


def ensure_bronze(connection, table: str = "bronze_activities") -> None:
    connection.execute(f"CREATE TABLE IF NOT EXISTS {table} ({BRONZE_COLUMNS})")


def ingest(
    connection,
    source_dir: Path,
    checkpoint_path: Path,
    table: str = "bronze_activities",
) -> IngestResult:
    """Ingest files that have not been seen before, and record the new checkpoint."""
    ensure_bronze(connection, table)
    seen, seq = _load_checkpoint(checkpoint_path)
    files = sorted(source_dir.glob("*.json"))
    fresh = [path for path in files if path.name not in seen]

    if not fresh:
        total = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return IngestResult(files_read=0, files_skipped=len(files), rows_total=total)

    added = 0
    for path in fresh:
        before = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        seq += 1
        # The sequence number is what makes "latest delivery wins" deterministic:
        # two files can land inside the same clock tick, but never the same sequence.
        connection.execute(
            f"""
            INSERT INTO {table}
            SELECT *, '{path.name}' AS _source_file, {seq} AS _ingest_seq, now() AS _ingested_at
            FROM read_json_auto('{path.as_posix()}')
            """
        )
        after = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        added += after - before
        seen.add(path.name)

    _save_checkpoint(checkpoint_path, seen, seq)
    total = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return IngestResult(
        files_read=len(fresh), files_skipped=len(files) - len(fresh), rows_added=added, rows_total=total
    )
