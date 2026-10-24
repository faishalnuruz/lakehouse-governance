import pytest

from lakehouse_governance.lineage import Lineage, LineageError

BRONZE_COLUMNS = {
    "activity_id", "event_id", "event_name", "athlete_name", "started_at", "distance_km",
    "duration_s", "pace_sec_per_km", "elevation_gain_m", "heart_rate_avg", "heart_rate_max",
    "gps_lat", "gps_lon", "_source_file", "_ingest_seq", "_ingested_at",
}


def test_every_published_column_traces_back_to_bronze(lineage):
    for table, mapping in lineage.columns.items():
        for column in mapping:
            origins = lineage.trace(table, column)
            assert origins[-1].startswith("bronze_activities.")
            assert origins[-1].split(".", 1)[1] in BRONZE_COLUMNS


def test_trace_returns_the_full_path(lineage):
    path = lineage.trace("gold_weekly_summary", "avg_heart_rate")

    assert path == [
        "gold_weekly_summary.avg_heart_rate",
        "silver_activities.heart_rate_avg",
        "bronze_activities.heart_rate_avg",
    ]


def test_origins_of_aggregates_are_still_specific_columns(lineage):
    assert lineage.origins("gold_weekly_summary", "total_km") == "bronze_activities.distance_km"
    assert lineage.origins("gold_weekly_summary", "activities") == "bronze_activities.activity_id"


def test_layers_are_declared(lineage):
    assert lineage.layer("bronze_activities") == "bronze"
    assert lineage.layer("silver_activities") == "silver"
    assert lineage.layer("gold_weekly_summary") == "gold"
    assert lineage.uncovered_tables() == []


def test_edges_and_mermaid_render_the_graph(lineage):
    edges = lineage.edges()
    mermaid = lineage.to_mermaid()

    assert ("bronze_activities.distance_km", "silver_activities.distance_km") in edges
    assert mermaid.startswith("graph LR")
    assert "bronze_activities" in mermaid and "gold_weekly_summary" in mermaid
    assert "-->" in mermaid


def test_pii_columns_are_classified(lineage):
    assert lineage.pii_columns("pii_location") == [
        "silver_activities.gps_lat",
        "silver_activities.gps_lon",
    ]
    assert "silver_activities.heart_rate_avg" in lineage.pii_columns("health")
    assert len(lineage.pii_columns()) == 5


def test_unknown_columns_are_refused(lineage):
    with pytest.raises(LineageError, match="no declared lineage"):
        lineage.source_of("silver_activities", "not_a_column")

    with pytest.raises(LineageError, match="no lineage declared"):
        lineage.source_of("not_a_table", "activity_id")


def test_cycles_are_detected():
    cyclic = Lineage(
        tables={"a": {"layer": "silver"}, "b": {"layer": "silver"}},
        columns={"a": {"x": "b.y"}, "b": {"y": "a.x"}},
    )

    with pytest.raises(LineageError, match="cycle"):
        cyclic.trace("a", "x")


def test_declared_metadata_matches_the_live_schema(warehouse, lineage):
    """Lineage that drifts from the tables is worse than no diagram at all."""
    assert lineage.missing_from_database(warehouse["connection"]) == []


def test_schema_drift_is_caught(warehouse, lineage):
    connection = warehouse["connection"]
    connection.execute("ALTER TABLE silver_activities ADD COLUMN sneaky_duck VARCHAR")

    problems = lineage.missing_from_database(connection)

    assert any("sneaky_duck" in problem for problem in problems)


def test_missing_declared_column_is_caught(warehouse, lineage):
    connection = warehouse["connection"]
    connection.execute("ALTER TABLE gold_weekly_summary DROP COLUMN avg_heart_rate")

    problems = lineage.missing_from_database(connection)

    assert any("avg_heart_rate declared but absent" in problem for problem in problems)
