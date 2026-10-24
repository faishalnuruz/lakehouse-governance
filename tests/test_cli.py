import json

import pytest

from lakehouse_governance import cli

POLICY = "policy.yaml"
LINEAGE = "lineage.yaml"


@pytest.fixture
def built(source, tmp_path):
    """Run the pipeline through the CLI so the database exists on disk."""
    database = tmp_path / "lakehouse.duckdb"
    code = cli.main([
        "run",
        "--source", str(source["dir"]),
        "--db", str(database),
        "--checkpoint", str(tmp_path / "cp.json"),
        "--policy", POLICY,
        "--lineage", LINEAGE,
    ])
    assert code == 0
    return {"db": database, "source": source, "checkpoint": tmp_path / "cp.json"}


def test_run_reports_every_layer(built, capsys):
    capsys.readouterr()  # discard the fixture's output

    code = cli.main([
        "run",
        "--source", str(built["source"]["dir"]),
        "--db", str(built["db"]),
        "--checkpoint", str(built["checkpoint"]),
        "--lineage", LINEAGE,
    ])

    output = capsys.readouterr().out
    assert code == 0
    assert "bronze" in output and "silver" in output and "gold" in output
    assert "lineage  verified" in output
    # second run: nothing new, nothing duplicated
    assert "files read 0" in output


def test_analyst_output_is_masked(built, capsys):
    code = cli.main([
        "show", "--db", str(built["db"]), "--policy", POLICY, "--role", "analyst",
        "--table", "silver_activities", "--columns", "activity_id", "heart_rate_avg", "athlete_name",
        "--limit", "2",
    ])

    output = capsys.readouterr().out
    assert code == 0
    assert "masked columns: athlete_name, heart_rate_avg" in output
    payload = json.loads(output.strip().splitlines()[-1].strip())
    assert payload["heart_rate_avg"] % 10 == 0
    assert payload["athlete_name"].startswith("athlete-")


def test_admin_output_is_not_masked(built, capsys):
    code = cli.main([
        "show", "--db", str(built["db"]), "--policy", POLICY, "--role", "admin",
        "--table", "silver_activities", "--columns", "activity_id", "gps_lat", "athlete_name",
        "--limit", "1",
    ])

    output = capsys.readouterr().out
    payload = json.loads(output.strip().splitlines()[-1].strip())
    assert code == 0
    assert not payload["athlete_name"].startswith("athlete-")  # a real name, not a pseudonym
    assert len(str(payload["gps_lat"]).split(".")[1]) >= 4


def test_denied_reads_exit_with_a_distinct_code(built, capsys):
    code = cli.main([
        "show", "--db", str(built["db"]), "--policy", POLICY, "--role", "contractor",
        "--table", "silver_activities", "--limit", "1",
    ])

    output = capsys.readouterr().out
    assert code == 2
    assert "access denied" in output


def test_roles_prints_the_matrix(capsys):
    code = cli.main(["roles", "--policy", POLICY])

    output = capsys.readouterr().out
    assert code == 0
    for role in ("admin", "scientist", "analyst", "contractor"):
        assert role in output
    assert "denied columns" in output


def test_lineage_command_writes_files_and_verifies(built, tmp_path, capsys):
    output = tmp_path / "lineage.md"
    payload = tmp_path / "lineage.json"

    code = cli.main([
        "lineage", "--lineage", LINEAGE, "--db", str(built["db"]),
        "--output", str(output), "--json", str(payload),
    ])

    printed = capsys.readouterr().out
    assert code == 0
    assert "metadata drift: none" in printed
    assert output.read_text().startswith("# Lineage")
    assert "graph LR" in output.read_text()
    assert json.loads(payload.read_text())["edges"]


def test_lineage_command_fails_when_metadata_drifts(warehouse, tmp_path, capsys):
    """The check has to be able to fail, or it is decoration."""
    import duckdb

    database = tmp_path / "drifted.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("CREATE TABLE silver_activities (activity_id VARCHAR)")
    connection.execute("CREATE TABLE gold_weekly_summary (event_id VARCHAR)")
    connection.execute("CREATE TABLE bronze_activities (activity_id VARCHAR)")
    connection.close()

    code = cli.main([
        "lineage", "--lineage", LINEAGE, "--db", str(database),
        "--output", str(tmp_path / "l.md"), "--json", str(tmp_path / "l.json"),
    ])

    output = capsys.readouterr().out
    assert code == 1
    assert "problem(s)" in output
