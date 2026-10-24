import re

import pytest

from lakehouse_governance.catalog import AccessDenied, Catalog, Masking


def read(warehouse, policy, role, table, columns=None, limit=1):
    catalog = Catalog(warehouse["connection"], policy, role)
    return catalog.select(table, columns, limit=limit)


def test_admin_sees_precise_coordinates(warehouse, policy):
    rows = read(warehouse, policy, "admin", "silver_activities", ["activity_id", "gps_lat"])

    assert rows
    assert len(str(rows[0]["gps_lat"]).split(".")[1]) >= 4


def test_analyst_is_denied_the_coordinate_columns(warehouse, policy):
    with pytest.raises(AccessDenied, match="may not select|no grant"):
        read(warehouse, policy, "analyst", "silver_activities", ["gps_lat", "activity_id"])


def test_analyst_gets_bucketed_health_and_a_pseudonym(warehouse, policy):
    rows = read(
        warehouse, policy, "analyst", "silver_activities",
        ["activity_id", "heart_rate_avg", "athlete_name"],
    )

    row = rows[0]
    assert row["heart_rate_avg"] % 10 == 0
    assert re.fullmatch(r"athlete-[0-9a-f]{8}", row["athlete_name"])


def test_scientist_sees_coordinates_rounded_to_about_ten_km(warehouse, policy):
    rows = read(warehouse, policy, "scientist", "silver_activities", ["gps_lat", "gps_lon"])

    row = rows[0]
    for column in ("gps_lat", "gps_lon"):
        decimals = len(str(row[column]).split(".")[1])
        assert decimals <= 1


def test_contractor_can_only_read_the_aggregates(warehouse, policy):
    assert read(warehouse, policy, "contractor", "gold_weekly_summary", ["event_id"])

    with pytest.raises(AccessDenied, match="no grant"):
        read(warehouse, policy, "contractor", "silver_activities", ["activity_id"])


def test_unknown_roles_tables_and_columns_are_refused(warehouse, policy):
    with pytest.raises(AccessDenied, match="unknown role"):
        Catalog(warehouse["connection"], policy, "intern")

    with pytest.raises(AccessDenied, match="no grant"):
        read(warehouse, policy, "admin", "not_a_table", ["activity_id"])

    with pytest.raises(AccessDenied, match="no column"):
        read(warehouse, policy, "admin", "silver_activities", ["not_a_column"])


def test_masked_columns_are_listed_for_the_audit_trail(warehouse, policy):
    analyst = Catalog(warehouse["connection"], policy, "analyst")
    admin = Catalog(warehouse["connection"], policy, "admin")

    assert analyst.masked_columns("silver_activities") == ["athlete_name", "heart_rate_avg"]
    assert admin.masked_columns("silver_activities") == []
    # the analyst's denial list removes coordinates entirely rather than masking them
    assert "gps_lat" not in analyst.masked_columns("silver_activities")


def test_gold_is_readable_unmasked_by_every_role(warehouse, policy):
    for role in ("admin", "scientist", "analyst", "contractor"):
        rows = read(warehouse, policy, role, "gold_weekly_summary", ["event_id", "total_km"])
        assert rows
        assert rows[0]["total_km"] > 0


def test_masking_expressions_are_explicit():
    assert Masking(kind="round_coordinates", detail="1").expression("gps_lat") == "ROUND(gps_lat, 1)"
    assert Masking(kind="bucket_health", detail="10").expression("hr") == "CAST(FLOOR(hr / 10) * 10 AS INT)"
    assert "MD5" in Masking(kind="pseudonymise").expression("athlete_name")
    assert Masking(kind="null").expression("anything") == "NULL"

    with pytest.raises(ValueError, match="unsupported masking kind"):
        Masking(kind="scramble").expression("x")
