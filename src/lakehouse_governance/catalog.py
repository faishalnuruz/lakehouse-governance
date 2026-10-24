"""Table, column, and role policies — the part of Unity Catalog that is testable.

A data platform's governance is only real if an unauthorised read *fails*. This
module enforces three things and nothing more:

* which roles may read which tables;
* which columns a role may see at all;
* how sensitive columns are masked when a role is allowed to see them in
  degraded form (coordinates, heart rate, names).

Every policy lives in ``policy.yaml`` so a reviewer can read the whole setup in
one screen, and mappings to Unity Catalog's equivalent mechanisms are documented
in ``docs/unity-catalog-mapping.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "policy.yaml"


class AccessDenied(PermissionError):
    """Raised when a role asks for something it may not have."""


@dataclass
class Masking:
    """How a sensitive column is degraded for a role."""

    kind: str
    detail: str = ""
    sensitivity: str = "unknown"

    def expression(self, column: str) -> str:
        """SQL that degrades the column for display, never for storage."""
        if self.kind == "round_coordinates":
            decimals = self.detail or "1"
            return f"ROUND({column}, {decimals})"
        if self.kind == "bucket_health":
            size = self.detail or "10"
            return f"CAST(FLOOR({column} / {size}) * {size} AS INT)"
        if self.kind == "pseudonymise":
            return f"'athlete-' || SUBSTR(MD5({column}), 1, 8)"
        if self.kind == "null":
            return "NULL"
        raise ValueError(f"unsupported masking kind: {self.kind}")


@dataclass
class Policy:
    roles: dict[str, dict] = field(default_factory=dict)
    columns: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_POLICY) -> Policy:
        data = yaml.safe_load(Path(path).read_text())
        return cls(roles=data.get("roles", {}), columns=data.get("columns", {}))

    def masking(self, table: str, column: str) -> Masking | None:
        entry = self.columns.get(table, {}).get(column)
        if not entry:
            return None
        return Masking(
            kind=entry["mask"],
            detail=str(entry.get("detail", "")),
            sensitivity=entry.get("sensitivity", "unknown"),
        )

    def table_access(self, role: str, table: str) -> str:
        """Return 'read', 'masked', or raise AccessDenied."""
        if role not in self.roles:
            raise AccessDenied(f"unknown role {role!r}")
        grants = self.roles[role].get("tables", {})
        if table not in grants:
            raise AccessDenied(f"role {role!r} has no grant on {table!r}")
        grant = grants[table]
        return "masked" if grant == "read:masked" else grant

    def denied_columns(self, role: str, table: str) -> set[str]:
        return set(self.roles.get(role, {}).get("deny_columns", {}).get(table, []))

    def allowed_columns(self, role: str, table: str, columns: list[str]) -> list[str]:
        """Columns the role may see at all; masked columns appear, but degraded."""
        if role not in self.roles:
            raise AccessDenied(f"unknown role {role!r}")
        denied = self.denied_columns(role, table)
        return [column for column in columns if column not in denied]


class Catalog:
    """A thin, enforcing wrapper over the warehouse tables."""

    def __init__(self, connection, policy: Policy, role: str) -> None:
        self.connection = connection
        self.policy = policy
        self.role = role
        if role not in policy.roles:
            raise AccessDenied(f"unknown role {role!r}")

    @property
    def readable_tables(self) -> list[str]:
        return sorted(self.policy.roles[self.role].get("tables", {}))

    def select(self, table: str, columns: list[str] | None = None, limit: int | None = None) -> list[dict]:
        """Read a table as this role, masking whatever the policy says to mask."""
        mode = self.policy.table_access(self.role, table)
        available = [
            row[0]
            for row in self.connection.execute(f"DESCRIBE {table}").fetchall()
        ]
        requested = columns or available
        unknown = [column for column in requested if column not in available]
        if unknown:
            raise AccessDenied(f"{table!r} has no column(s) {sorted(unknown)}")

        # Asking for a denied column by name is an error, not something to quietly
        # drop from the result: a caller that gets fewer columns than it asked for
        # can easily misread the rows that come back.
        if columns is not None:
            denied = [column for column in columns if column in self.policy.denied_columns(self.role, table)]
            if denied:
                raise AccessDenied(f"role {self.role!r} may not select {sorted(denied)} on {table!r}")

        allowed = self.policy.allowed_columns(self.role, table, requested)

        projections = []
        for column in allowed:
            masking = self.policy.masking(table, column) if mode == "masked" else None
            projections.append(
                f"{masking.expression(column)} AS {column}" if masking else column
            )
        query = f"SELECT {', '.join(projections)} FROM {table}"
        if limit:
            query += f" LIMIT {limit}"

        cursor = self.connection.execute(query)
        names = [description[0] for description in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def masked_columns(self, table: str) -> list[str]:
        """Which columns are degraded for this role — the audit answer to 'what did they see?'"""
        if self.policy.table_access(self.role, table) != "masked":
            return []
        denied = self.policy.denied_columns(self.role, table)
        return sorted(
            column for column in self.policy.columns.get(table, {}) if column not in denied
        )
