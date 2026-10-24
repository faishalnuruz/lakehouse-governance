import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse_governance import generate, ingest, transform


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "source"
    stats = generate.generate(directory, activities_per_event=4, seed=41, batches=2)
    return {"dir": directory, "stats": stats, "tmp": tmp_path}


@pytest.fixture
def warehouse(source):
    """A built lakehouse: Bronze ingested, Silver merged, Gold aggregated."""
    connection = duckdb.connect(":memory:")
    checkpoint = source["tmp"] / "checkpoints" / "ingest.json"
    result = ingest.ingest(connection, source["dir"], checkpoint)
    silver = transform.build_silver(connection)
    gold = transform.build_gold(connection)

    return {
        "connection": connection,
        "checkpoint": checkpoint,
        "ingest": result,
        "silver": silver,
        "gold": gold,
        "source": source,
    }


@pytest.fixture
def policy():
    from lakehouse_governance.catalog import Policy

    return Policy.load(ROOT / "policy.yaml")


@pytest.fixture
def lineage():
    from lakehouse_governance.lineage import Lineage

    return Lineage.load(ROOT / "lineage.yaml")
