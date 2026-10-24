# Architecture and decisions

## Flow

```text
activity files (JSON, one per batch, one redelivery)
        |
        v
  Bronze   append-only, provenance columns (_source_file, _ingest_seq, _ingested_at)
        |      checkpoint records which files were read, and the last sequence used
        v
  Silver   MERGE on activity_id: the latest delivery wins, provenance is never overwritten
        |
        v
  Gold     weekly per-event aggregates — the only table analysts and contractors read
        |
        +--> policy.yaml   who may read what, and how sensitive columns are degraded
        +--> lineage.yaml  column-level lineage, verified against the live schema
```

## Decisions worth defending

**Bronze keeps redeliveries; Silver decides the winner.** When an activity is corrected, Bronze holds
both versions, because the platform's job is to remember what arrived. Silver picks one — and the
test asserts that the *corrected* row is the survivor, that Bronze still holds two versions, and that
provenance was not overwritten by the update.

**Deduplication orders by a sequence number, not a timestamp.** `_ingest_seq` increments per file
ingested and is stored in the checkpoint. Two files can land inside the same clock tick; sequence
numbers cannot collide, so "latest delivery wins" is deterministic. This replaced an earlier version
that ordered by `_ingested_at` and was wrong for exactly that reason.

**Reading a denied column is an error, not a silent omission.** If a caller asks for `gps_lat` and
gets a result without it, it is far too easy to misread the remaining columns. Asking by name fails
loudly; selecting a whole table returns what the role may see and reports what was withheld via
`masked_columns()`.

**The access decision is a value, not a vibe.** `table_access()` returns `read`, `read:masked`, or
raises `AccessDenied`, and the CLI turns a denial into exit code 2 so a scheduler can distinguish a
policy failure from a crash.

**Lineage is declared and then verified.** Metadata-based lineage (the dbt approach) is cheap and
readable, but it drifts. `missing_from_database()` compares every declared column against the live
schema in both directions, so adding a column in SQL without declaring it fails a test instead of
quietly disappearing from the diagram.

**Source tables are terminal.** Bronze columns have nothing upstream, and that is expressed as
"table declared, no column map" rather than by inventing fake upstream nodes. Any other table must
declare every column, and an undeclared one raises instead of creating a dead end in the graph.

**Masking happens at query time, never at rest.** Storage keeps the precise coordinate and the true
heart rate; the mask is applied in the projection. A test asserts that a masked heart rate is a
multiple of ten *and* that the underlying value is unchanged.

## Failure modes

| Situation | What happens |
| --- | --- |
| Checkpoint file is corrupt | Files are re-read: duplicated rows are recoverable, skipped files are not |
| Same activity delivered twice | Silver keeps the higher `_ingest_seq`, Bronze keeps both |
| Re-running the pipeline | Bronze ingests nothing new, Silver and Gold are unchanged (tested) |
| A role asks for a denied column by name | `AccessDenied`, and the CLI exits 2 |
| A role reads a table without naming columns | Denied columns are omitted; `masked_columns()` reports what was withheld |
| A masking kind is unknown | `ValueError` at policy load, not a silent pass-through |
| Declared column missing from the database | The lineage command reports drift and exits 1 |
| Column added to a table but not declared | Same — detected in the opposite direction |
| A cycle appears in lineage | `LineageError` while tracing, rather than an infinite loop |
