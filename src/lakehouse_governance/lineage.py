"""Lineage as a graph you can query, verify, and render.

A lineage diagram is only worth having if it is true. Two properties are
enforced here:

* every published column resolves back to a Bronze column, so "where did this
  number come from?" always has an answer;
* the declared metadata is checked against the live schema, so adding a column
  in SQL without declaring it fails the build instead of silently vanishing from
  the diagram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_LINEAGE = Path(__file__).resolve().parents[2] / "lineage.yaml"


class LineageError(RuntimeError):
    pass


@dataclass
class Lineage:
    tables: dict[str, dict] = field(default_factory=dict)
    columns: dict[str, dict] = field(default_factory=dict)
    pii: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_LINEAGE) -> Lineage:
        data = yaml.safe_load(Path(path).read_text())
        return cls(
            tables=data.get("tables", {}),
            columns=data.get("columns", {}),
            pii=data.get("pii", {}),
        )

    # -- graph -----------------------------------------------------------------

    def node(self, table: str, column: str) -> str:
        return f"{table}.{column}"

    def source_of(self, table: str, column: str) -> str | None:
        """The upstream node for a column, or None when the table is a source.

        Source tables are terminal by definition: Bronze columns are where data
        enters the platform, so they have nothing upstream of them. Any other
        table must declare every column, and an undeclared one is an error rather
        than a silent dead end.
        """
        if table not in self.tables:
            raise LineageError(f"no lineage declared for table {table!r}")
        mapping = self.columns.get(table)
        if not mapping:
            return None
        if column not in mapping:
            raise LineageError(f"{table!r} has no declared lineage for column {column!r}")
        return mapping[column]

    def trace(self, table: str, column: str) -> list[str]:
        """Full path from a column back to its Bronze origin, nearest first."""
        path = [self.node(table, column)]
        current = self.source_of(table, column)
        seen = set(path)
        while current:
            if current in seen:
                raise LineageError(f"lineage cycle detected at {current!r}")
            path.append(current)
            seen.add(current)
            source_table, source_column = current.split(".", 1)
            current = self.source_of(source_table, source_column)
        return path

    def origins(self, table: str, column: str) -> str:
        """The Bronze column a published column ultimately comes from."""
        return self.trace(table, column)[-1]

    def layer(self, table: str) -> str:
        return self.tables.get(table, {}).get("layer", "unknown")

    def edges(self) -> list[tuple[str, str]]:
        collected: list[tuple[str, str]] = []
        for table, mapping in self.columns.items():
            for column, source in mapping.items():
                collected.append((source, self.node(table, column)))
        return collected

    # -- verification ----------------------------------------------------------

    def uncovered_tables(self) -> list[str]:
        """Tables that should be mapped but declare nothing."""
        return [
            table
            for table, spec in self.tables.items()
            if spec.get("layer") != "bronze" and not self.columns.get(table)
        ]

    def missing_from_database(self, connection) -> list[str]:
        """Declared columns that do not exist, and existing columns not declared."""
        problems: list[str] = []
        for table, mapping in self.columns.items():
            actual = {row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()}
            for column in mapping:
                if column not in actual:
                    problems.append(f"{table}.{column} declared but absent from the database")
            for column in sorted(actual - set(mapping)):
                problems.append(f"{table}.{column} exists in the database but has no lineage")
        return problems

    def pii_columns(self, classification: str | None = None) -> list[str]:
        return sorted(
            node for node, kind in self.pii.items() if classification is None or kind == classification
        )

    # -- rendering -------------------------------------------------------------

    def to_mermaid(self) -> str:
        lines = ["graph LR"]
        for table, spec in self.tables.items():
            lines.append(f"  {table}[{table}<br/>{spec.get('layer', '?')}]")
        for table, spec in self.tables.items():
            for upstream in spec.get("inputs", []):
                lines.append(f"  {upstream} --> {table}")
        for source, target in self.edges():
            source_table, source_column = source.split(".", 1)
            target_table, target_column = target.split(".", 1)
            if source_table != target_table:
                lines.append(f"  {source_table} -. {source_column} --> {target_column} .- {target_table}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "tables": self.tables,
            "edges": [{"from": source, "to": target} for source, target in self.edges()],
            "pii": self.pii,
            "mermaid": self.to_mermaid(),
        }
