import json

import duckdb

from lakehouse_governance import ingest


def test_first_run_reads_every_file(source):
    connection = duckdb.connect(":memory:")
    checkpoint = source["tmp"] / "cp.json"

    result = ingest.ingest(connection, source["dir"], checkpoint)

    expected_rows = source["stats"].activities + source["stats"].corrected
    assert result.files_read == source["stats"].files
    assert result.files_skipped == 0
    # Bronze keeps the redelivery too: making it Silver's job to pick a winner.
    assert result.rows_added == expected_rows
    assert result.rows_total == expected_rows


def test_second_run_reads_nothing_and_adds_nothing(source):
    connection = duckdb.connect(":memory:")
    checkpoint = source["tmp"] / "cp.json"
    ingest.ingest(connection, source["dir"], checkpoint)

    again = ingest.ingest(connection, source["dir"], checkpoint)

    expected_rows = source["stats"].activities + source["stats"].corrected
    assert again.files_read == 0
    assert again.files_skipped == source["stats"].files
    assert again.rows_added == 0
    assert again.rows_total == expected_rows


def test_a_new_file_is_picked_up_and_old_ones_are_not(source):
    connection = duckdb.connect(":memory:")
    checkpoint = source["tmp"] / "cp.json"
    ingest.ingest(connection, source["dir"], checkpoint)

    arrival = source["dir"] / "activities-100.json"
    arrival.write_text(json.dumps({
        "activity_id": "late-001", "event_id": "ugm-100k", "event_name": "UGM Trail 100K",
        "athlete_name": "new runner", "started_at": "2026-10-26T06:00:00Z",
        "distance_km": 100.0, "duration_s": 43000, "pace_sec_per_km": 430.0,
        "elevation_gain_m": 4000, "heart_rate_avg": 142, "heart_rate_max": 180,
        "gps_lat": -7.61, "gps_lon": 110.41,
    }) + "\n")

    result = ingest.ingest(connection, source["dir"], checkpoint)

    assert result.files_read == 1
    assert result.rows_added == 1
    assert result.rows_total == source["stats"].activities + source["stats"].corrected + 1


def test_every_row_records_where_it_came_from(source):
    connection = duckdb.connect(":memory:")
    ingest.ingest(connection, source["dir"], source["tmp"] / "cp.json")

    rows = connection.execute(
        "SELECT DISTINCT _source_file FROM bronze_activities ORDER BY 1"
    ).fetchall()
    ingested = connection.execute(
        "SELECT COUNT(*) FROM bronze_activities WHERE _ingested_at IS NULL"
    ).fetchone()[0]

    assert len(rows) == source["stats"].files
    assert ingested == 0


def test_a_corrupt_checkpoint_re_reads_rather_than_skipping(source):
    """Re-reading duplicates data, which is recoverable; skipping loses it forever."""
    connection = duckdb.connect(":memory:")
    checkpoint = source["tmp"] / "cp.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("{not json")

    result = ingest.ingest(connection, source["dir"], checkpoint)

    assert result.files_read == source["stats"].files
    assert json.loads(checkpoint.read_text())["files"]


def test_each_file_gets_an_increasing_sequence_number(source):
    """Clock ticks can collide; sequence numbers cannot, which is what dedupe needs."""
    connection = duckdb.connect(":memory:")
    ingest.ingest(connection, source["dir"], source["tmp"] / "cp.json")

    sequences = connection.execute(
        """
        SELECT _source_file, MIN(_ingest_seq) AS seq
        FROM bronze_activities GROUP BY 1 ORDER BY seq
        """
    ).fetchall()
    values = [seq for _, seq in sequences]
    corrected = next(seq for name, seq in sequences if "corrected" in name)

    assert values == sorted(values)
    assert corrected == max(values)  # the redelivery is seen as the newest
    assert connection.execute(
        "SELECT COUNT(*) FROM bronze_activities WHERE _ingest_seq IS NULL"
    ).fetchone()[0] == 0


def test_bronze_keeps_redeliveries_rather_than_overwriting(source):
    """Bronze is the record of what arrived; Silver decides which version wins."""
    connection = duckdb.connect(":memory:")
    ingest.ingest(connection, source["dir"], source["tmp"] / "cp.json")

    duplicated = connection.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT activity_id) FROM bronze_activities"
    ).fetchone()[0]

    assert duplicated == source["stats"].corrected
