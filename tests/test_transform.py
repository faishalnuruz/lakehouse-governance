from lakehouse_governance import transform


def test_silver_holds_one_row_per_activity(warehouse):
    connection = warehouse["connection"]

    rows = connection.execute("SELECT COUNT(*) FROM silver_activities").fetchone()[0]
    distinct = connection.execute(
        "SELECT COUNT(DISTINCT activity_id) FROM silver_activities"
    ).fetchone()[0]

    assert rows == distinct
    assert rows == warehouse["source"]["stats"].activities


def test_the_corrected_delivery_wins(warehouse):
    """The redelivered activity must replace the first version, not sit beside it."""
    connection = warehouse["connection"]

    # Identify the redelivered activity from its provenance rather than guessing
    # at an id: the corrected file is the only one that carries it.
    activity = connection.execute(
        "SELECT activity_id FROM silver_activities WHERE _source_file LIKE '%corrected%'"
    ).fetchone()[0]

    corrected = connection.execute(
        """
        SELECT distance_km, heart_rate_avg, _source_file, _ingest_seq
        FROM silver_activities WHERE activity_id = ?
        """,
        [activity],
    ).fetchone()
    original = connection.execute(
        """
        SELECT distance_km, heart_rate_avg, _ingest_seq FROM bronze_activities
        WHERE activity_id = ? ORDER BY _ingest_seq ASC LIMIT 1
        """,
        [activity],
    ).fetchone()

    assert "corrected" in corrected[2]
    assert corrected[1] == original[1] + 7          # the corrected heart rate won
    assert corrected[0] > original[0]               # and the corrected distance
    assert corrected[3] > original[2]               # because it was seen later


def test_bronze_still_holds_both_versions(warehouse):
    """Silver picks a winner; Bronze keeps the evidence of the dispute."""
    connection = warehouse["connection"]

    versions = connection.execute(
        """
        SELECT COUNT(*) FROM bronze_activities
        WHERE activity_id = (
            SELECT activity_id FROM silver_activities WHERE _source_file LIKE '%corrected%'
        )
        """
    ).fetchone()[0]

    assert versions == 2


def test_provenance_columns_are_not_overwritten_by_a_correction(warehouse):
    connection = warehouse["connection"]

    sources = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT _source_file FROM silver_activities"
        ).fetchall()
    }
    corrected_rows = connection.execute(
        "SELECT COUNT(*) FROM silver_activities WHERE _source_file LIKE '%corrected%'"
    ).fetchone()[0]

    assert corrected_rows == 1  # only the redelivered activity carries that provenance
    assert any("corrected" in name for name in sources)
    assert len(sources) == warehouse["source"]["stats"].files  # every delivery is traceable


def test_silver_and_gold_are_idempotent(warehouse):
    connection = warehouse["connection"]
    before_silver = connection.execute("SELECT COUNT(*) FROM silver_activities").fetchone()[0]
    before_gold = connection.execute("SELECT COUNT(*) FROM gold_weekly_summary").fetchone()[0]

    transform.build_silver(connection)
    transform.build_gold(connection)

    assert connection.execute("SELECT COUNT(*) FROM silver_activities").fetchone()[0] == before_silver
    assert connection.execute("SELECT COUNT(*) FROM gold_weekly_summary").fetchone()[0] == before_gold


def test_gold_reconciles_with_silver(warehouse):
    connection = warehouse["connection"]

    activities = connection.execute("SELECT SUM(activities) FROM gold_weekly_summary").fetchone()[0]
    silver_rows = connection.execute("SELECT COUNT(*) FROM silver_activities").fetchone()[0]

    assert activities == silver_rows


def test_gold_totals_match_a_manual_sum(warehouse):
    connection = warehouse["connection"]

    computed = connection.execute(
        """
        SELECT ROUND(SUM(distance_km), 2)
        FROM silver_activities
        WHERE event_id = 'ugm-100k' AND DATE_TRUNC('week', started_at) = (
            SELECT MIN(DATE_TRUNC('week', started_at)) FROM silver_activities
        )
        """
    ).fetchone()[0]
    reported = connection.execute(
        """
        SELECT total_km FROM gold_weekly_summary
        WHERE event_id = 'ugm-100k'
        ORDER BY week LIMIT 1
        """
    ).fetchone()[0]

    assert reported == computed


def test_gold_has_one_row_per_event_and_week(warehouse):
    connection = warehouse["connection"]

    rows = connection.execute("SELECT COUNT(*) FROM gold_weekly_summary").fetchone()[0]
    pairs = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT event_id, DATE_TRUNC('week', started_at) AS week
            FROM silver_activities
        )
        """
    ).fetchone()[0]

    assert rows == pairs
