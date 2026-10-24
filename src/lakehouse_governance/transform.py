"""Silver and Gold: upsert what changed, then aggregate what is trusted.

Silver is where a redelivered activity must *replace* the earlier version rather
than appear beside it — the same MERGE pattern the Olist project uses for
deduplication, but here the correction is deliberate and its effect is measured.
"""

from __future__ import annotations

SILVER_COLUMNS = """
    activity_id VARCHAR,
    event_id VARCHAR,
    event_name VARCHAR,
    athlete_name VARCHAR,
    started_at TIMESTAMP,
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

# The full Silver column list, in DDL order, for the INSERT branch of the MERGE.
INSERT_COLUMNS = (
    "activity_id",
    "event_id",
    "event_name",
    "athlete_name",
    "started_at",
    "distance_km",
    "duration_s",
    "pace_sec_per_km",
    "elevation_gain_m",
    "heart_rate_avg",
    "heart_rate_max",
    "gps_lat",
    "gps_lon",
    "_source_file",
    "_ingest_seq",
    "_ingested_at",
)

# Columns a correction is allowed to change. Provenance is never overwritten.
UPDATABLE = (
    "distance_km",
    "duration_s",
    "pace_sec_per_km",
    "elevation_gain_m",
    "heart_rate_avg",
    "heart_rate_max",
    "gps_lat",
    "gps_lon",
    "athlete_name",
)

GOLD_COLUMNS = """
    event_id VARCHAR,
    event_name VARCHAR,
    week TIMESTAMP,
    activities BIGINT,
    total_km DOUBLE,
    total_elevation_gain_m BIGINT,
    avg_heart_rate DOUBLE,
    avg_pace_sec_per_km DOUBLE
"""


def build_silver(connection, bronze_table: str = "bronze_activities") -> dict:
    """Deduplicate Bronze to the latest version of each activity using MERGE."""
    connection.execute(f"CREATE TABLE IF NOT EXISTS silver_activities ({SILVER_COLUMNS})")
    before = connection.execute("SELECT COUNT(*) FROM silver_activities").fetchone()[0]

    # Latest delivered version per activity. `rn = 1` is the correction when one exists.
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE latest_activities AS
        SELECT activity_id, event_id, event_name, athlete_name,
               CAST(started_at AS TIMESTAMP) AS started_at,
               distance_km, duration_s, pace_sec_per_km, elevation_gain_m,
               heart_rate_avg, heart_rate_max, gps_lat, gps_lon,
               _source_file, _ingest_seq, _ingested_at,
               ROW_NUMBER() OVER (
                   PARTITION BY activity_id ORDER BY _ingest_seq DESC
               ) AS rn
        FROM {bronze_table}
        """
    )

    assignments = ", ".join(f"{column} = source.{column}" for column in UPDATABLE)
    insert_columns = ", ".join(INSERT_COLUMNS)
    connection.execute(
        f"""
        MERGE INTO silver_activities AS target
        USING (SELECT * FROM latest_activities WHERE rn = 1) AS source
        ON target.activity_id = source.activity_id
        WHEN MATCHED THEN UPDATE SET {assignments}
        WHEN NOT MATCHED THEN
            INSERT ({insert_columns})
            VALUES (source.activity_id, source.event_id, source.event_name, source.athlete_name,
                    source.started_at, source.distance_km, source.duration_s, source.pace_sec_per_km,
                    source.elevation_gain_m, source.heart_rate_avg, source.heart_rate_max,
                    source.gps_lat, source.gps_lon, source._source_file, source._ingest_seq,
                    source._ingested_at)
        """
    )

    after = connection.execute("SELECT COUNT(*) FROM silver_activities").fetchone()[0]
    corrections = connection.execute(
        "SELECT COUNT(*) FROM silver_activities WHERE _source_file LIKE '%corrected%'"
    ).fetchone()[0]
    duplicates_dropped = connection.execute(
        f"SELECT COUNT(*) - COUNT(DISTINCT activity_id) FROM {bronze_table}"
    ).fetchone()[0]

    return {
        "rows_before": before,
        "rows_after": after,
        "rows_inserted": after - before,
        "corrections_applied": corrections,
        "duplicate_deliveries_resolved": duplicates_dropped,
    }


def build_gold(connection, silver_table: str = "silver_activities") -> dict:
    """Weekly per-event aggregates — the only table most people should ever query."""
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE gold_weekly_summary AS
        SELECT event_id,
               event_name,
               DATE_TRUNC('week', started_at) AS week,
               COUNT(*) AS activities,
               ROUND(SUM(distance_km), 2) AS total_km,
               SUM(elevation_gain_m)::BIGINT AS total_elevation_gain_m,
               ROUND(AVG(heart_rate_avg), 1) AS avg_heart_rate,
               ROUND(AVG(pace_sec_per_km), 1) AS avg_pace_sec_per_km
        FROM {silver_table}
        GROUP BY 1, 2, 3
        ORDER BY week, event_id
        """
    )
    rows = connection.execute(
        "SELECT COUNT(*) FROM gold_weekly_summary"
    ).fetchone()[0]
    return {"rows": rows, "columns": len(GOLD_COLUMNS.split(","))}
