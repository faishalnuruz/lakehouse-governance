"""Command line entry point.

    python -m lakehouse_governance.cli generate --source data/source
    python -m lakehouse_governance.cli run --source data/source --db data/lakehouse.duckdb
    python -m lakehouse_governance.cli show --db data/lakehouse.duckdb --role analyst --table silver_activities
    python -m lakehouse_governance.cli roles
    python -m lakehouse_governance.cli lineage --output docs/generated/lineage.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

from lakehouse_governance import generate as generator
from lakehouse_governance import ingest as ingestion
from lakehouse_governance import lineage as lineage_module
from lakehouse_governance import transform
from lakehouse_governance.catalog import DEFAULT_POLICY, Catalog, Policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write activity files into the source directory")
    gen.add_argument("--source", type=Path, default=Path("data/source"))
    gen.add_argument("--activities-per-event", type=int, default=6)
    gen.add_argument("--batches", type=int, default=2)
    gen.add_argument("--seed", type=int, default=31)

    run = sub.add_parser("run", help="ingest, build Silver, build Gold")
    run.add_argument("--source", type=Path, default=Path("data/source"))
    run.add_argument("--db", type=Path, default=Path("data/lakehouse.duckdb"))
    run.add_argument("--checkpoint", type=Path, default=Path("data/checkpoints/ingest.json"))
    run.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    run.add_argument("--lineage", type=Path, default=lineage_module.DEFAULT_LINEAGE)

    show = sub.add_parser("show", help="read a table as a role, with masking applied")
    show.add_argument("--db", type=Path, default=Path("data/lakehouse.duckdb"))
    show.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    show.add_argument("--role", required=True)
    show.add_argument("--table", required=True)
    show.add_argument("--columns", nargs="*", default=None)
    show.add_argument("--limit", type=int, default=3)

    roles = sub.add_parser("roles", help="print the access matrix")
    roles.add_argument("--policy", type=Path, default=DEFAULT_POLICY)

    lin = sub.add_parser("lineage", help="emit the lineage graph and verify it against the schema")
    lin.add_argument("--lineage", type=Path, default=lineage_module.DEFAULT_LINEAGE)
    lin.add_argument("--db", type=Path, default=Path("data/lakehouse.duckdb"))
    lin.add_argument("--output", type=Path, default=Path("docs/generated/lineage.md"))
    lin.add_argument("--json", type=Path, default=Path("docs/generated/lineage.json"))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "generate":
        stats = generator.generate(args.source, args.activities_per_event, args.seed, args.batches)
        print(json.dumps(stats.__dict__, indent=2))
        return 0

    if args.command == "roles":
        policy = Policy.load(args.policy)
        for role, spec in policy.roles.items():
            grants = spec.get("tables", {})
            denied = spec.get("deny_columns", {})
            print(f"{role:12} {spec.get('description', '')}")
            for table, grant in grants.items():
                columns = ", ".join(denied.get(table, [])) or "—"
                print(f"  {table:24} {grant:12} denied columns: {columns}")
        return 0

    if args.command == "lineage":
        lineage = lineage_module.Lineage.load(args.lineage)
        problems = []
        if args.db.exists():
            connection = duckdb.connect(str(args.db))
            problems = lineage.missing_from_database(connection)
            connection.close()

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "# Lineage\n\n```mermaid\n" + lineage.to_mermaid() + "\n```\n\n"
            + "\n".join(f"- {node} → {lineage.origins(*node.split('.', 1))}" for node in lineage.pii)
            + "\n"
        )
        args.json.write_text(json.dumps(lineage.to_dict(), indent=2) + "\n")

        print(f"tables      {len(lineage.tables)}")
        print(f"edges       {len(lineage.edges())}")
        print(f"pii columns {len(lineage.pii)}")
        print("metadata drift: " + ("none" if not problems else f"{len(problems)} problem(s)"))
        for problem in problems:
            print(f"  {problem}")
        print(f"\nWrote {args.output} and {args.json}")
        return 1 if problems else 0

    connection = duckdb.connect(str(args.db))

    if args.command == "run":
        ingested = ingestion.ingest(connection, args.source, args.checkpoint)
        silver = transform.build_silver(connection)
        gold = transform.build_gold(connection)
        lineage = lineage_module.Lineage.load(args.lineage)
        drift = lineage.missing_from_database(connection)

        print(f"bronze   files read {ingested.files_read}, skipped {ingested.files_skipped}, "
              f"rows added {ingested.rows_added}, total {ingested.rows_total}")
        print(f"silver   rows {silver['rows_after']}, inserted {silver['rows_inserted']}, "
              f"corrections applied {silver['corrections_applied']}, "
              f"duplicate deliveries resolved {silver['duplicate_deliveries_resolved']}")
        print(f"gold     rows {gold['rows']}")
        print("lineage  " + ("verified" if not drift else f"{len(drift)} problem(s)"))
        for problem in drift:
            print(f"  {problem}")
        connection.close()
        return 0

    if args.command == "show":
        policy = Policy.load(args.policy)
        catalog = Catalog(connection, policy, args.role)
        try:
            rows = catalog.select(args.table, args.columns, limit=args.limit)
        except PermissionError as error:
            print(f"access denied: {error}")
            connection.close()
            return 2

        mode = policy.table_access(args.role, args.table)
        masked = catalog.masked_columns(args.table)
        print(f"role {args.role} reading {args.table} ({mode})")
        if masked:
            print(f"masked columns: {', '.join(masked)}")
        for row in rows:
            print("  " + json.dumps(row))
        connection.close()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
