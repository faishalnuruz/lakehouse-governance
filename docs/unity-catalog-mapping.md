# How this maps onto Unity Catalog

This project implements the *mechanisms* of a governed lakehouse with tools that run locally. It is
not Databricks, and the honest mapping matters more than the resemblance. Each row below is a
mechanism in this repository and the managed feature that does the same job in Unity Catalog.

| Mechanism here | Unity Catalog equivalent | Same | Different |
| --- | --- | --- | --- |
| `ingest.py` checkpoint of seen files | Auto Loader (`cloudFiles`) with its checkpoint | Reads only new files; a re-run is safe | Auto Loader also handles schema inference/evolution, file notification at cloud scale, and rescued data columns |
| `bronze_activities`, `silver_activities`, `gold_weekly_summary` | `catalog.schema.table` three-level namespace | Layer naming and separation | Unity Catalog namespaces are governed objects with owners and grants; mine are tables in one DuckDB file |
| `MERGE INTO` for corrections | Delta `MERGE INTO` | Same SQL semantics; the redelivery replaces the original | Delta adds ACID transactions, time travel, and `OPTIMIZE`/`ZORDER`; DuckDB is ACID but has no time travel |
| `policy.yaml` roles and table grants | `GRANT SELECT ON TABLE ... TO role` | Per-table read decisions | Unity Catalog roles are real identities from your IdP; mine are strings passed on the command line |
| `deny_columns` | Column-level grants, or a column mask that returns `NULL` | A column can be withheld entirely | Column grants are enforced by the query engine for every client; mine are enforced by `catalog.py`, so a client that bypasses this code bypasses the policy |
| Masking expressions (`round_coordinates`, `bucket_health`, `pseudonymise`) | Column mask functions, e.g. `mask_gps(coord DOUBLE)` | Values are degraded at query time, storage keeps the truth | Unity Catalog masks follow the user's group membership automatically and can be audited centrally |
| `lineage.yaml` + the drift test | Unity Catalog automatic column-level lineage | Answers "where did this column come from?" | Unity Catalog derives lineage from executed queries across notebooks and jobs; mine is declared metadata, checked against the live schema by a test |
| `cli show --role analyst` | An authenticated principal via SSO, then grants | Same query, different visibility | **Not a security boundary.** Anyone can pass `--role admin`. In Unity Catalog the identity comes from the session, not from an argument |

## What is genuinely missing here

Being explicit about the gaps is the point of the table above, so to summarise the ones that matter:

- **Row-level filters.** Unity Catalog can restrict rows by attribute (region, business unit). This
  project supports table and column decisions only.
- **Real identity.** `--role` is a parameter, not authentication. A local demo cannot demonstrate
  that the platform *prevents* access; it demonstrates that the policy layer decides consistently.
- **Automatic lineage.** Metadata-based lineage catches drift because a test compares it to the
  schema, but it does not know about an ad-hoc query someone ran in a notebook.
- **Audit logs.** A production platform records who read what, when, and with which mask applied.
  `masked_columns()` is the hook for that; there is no log sink here.
- **Storage-level controls.** Unity Catalog governs external locations, credential vending, and
  cloud-storage permissions. Here everything is a local file.
- **Tags and classification propagation.** PII is classified in `lineage.yaml` as a flat list; there
  is no inheritance of classifications down a lineage path.

## Why build it locally at all

Because governance features are easy to *claim* and hard to *test*. Every mechanism here has a test
that would fail if the behaviour regressed: an analyst cannot select a coordinate column, a masked
heart rate is a multiple of ten, a pseudonym matches a pattern, a redelivery replaces the original
instead of duplicating it, and declared lineage must match the live schema. Those are the assertions
a platform team would want in a managed environment too — the difference is who enforces them.
