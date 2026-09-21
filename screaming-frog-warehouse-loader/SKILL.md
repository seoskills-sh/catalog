---
name: Screaming Frog Warehouse Loader
description: Normalizes Screaming Frog SEO Spider CSV exports (internal_all, response_codes, directives, all_inlinks) into one stable warehouse schema, computes a run-over-run diff of new, changed, and removed URLs against the prior run's row-hash snapshot, and loads only the incremental rows into BigQuery with idempotent inserts so crawl history is queryable as SQL. Use when the user wants to turn recurring Screaming Frog crawls into a queryable warehouse, track crawl trends over time, or diff one crawl against the last without re-crawling.
category: integrations
---

# Screaming Frog Warehouse Loader

AGENT ROLE: Autonomous crawl-ingestion agent. Read the Screaming Frog CSV exports from a run directory, normalize them into the stable warehouse schema, diff against the previous run's `row_hashes`, emit DDL + a MERGE plan, and (when asked) stream only the changed rows into BigQuery. Emit the JSON in `references/output.schema.json`. Stateful across runs via `row_hashes`.

## OBJECTIVE
Convert a Screaming Frog crawl into append-only warehouse rows with a per-URL `row_hash`, so each run inserts only new/changed URLs plus tombstones for removed ones, and audits query crawl history in SQL instead of re-crawling. Deterministic normalization; incremental, idempotent load.

## INPUTS
- `export-dir` (REQUIRED): directory holding the SF CSV exports. `internal_all` is mandatory; `response_codes`, `directives`, `all_inlinks` are merged when present.
- `previous` (OPTIONAL): the prior run's output JSON (its `row_hashes` map drives the diff). Absent on first run (baseline).
- `run-id` (OPTIONAL, default today's date): stamped on every row; part of the idempotent `insertId`.
- `dialect` (OPTIONAL, default `bigquery`): `bigquery` or `postgres` — shapes the emitted DDL/MERGE.
- `table` (OPTIONAL, default `seo_wh.crawl_pages`).
- `load` (OPTIONAL flag): actually stream incremental rows into BigQuery.
- `max-rows` (OPTIONAL, default 200000): size guard on rows parsed and loaded.

## AUTHENTICATION (BigQuery, only when `--load`)
Default transform-only mode needs NO credentials (parse, diff, emit DDL/MERGE).
1. IF `--load` AND `dialect=postgres` THEN STOP `error.code="LOAD_UNSUPPORTED_STDLIB"` (Postgres needs a driver; run the emitted SQL via your DB tool).
2. IF `--load` AND `dialect=bigquery` THEN REQUIRE `GCP_ACCESS_TOKEN` (a short-lived OAuth access token, e.g. `gcloud auth print-access-token`), `BQ_PROJECT`, and `BQ_DATASET`.
   - IF any is unset THEN STOP `error.code="AUTH_MISSING_WAREHOUSE_CREDS"`.
   - This reference is std-lib-only and cannot sign a service-account JWT; supply a bearer access token, not a key file.
3. IF the token is rejected THEN STOP `error.code="AUTH_INVALID_TOKEN"` (401).

## EXPECTED TOOL CALLS
- Transform + diff only: `scripts/warehouse_loader.py --export-dir ./sf_export --run-id 2026-09-15 --previous prev.json`.
- Incremental load: append `--load` (BigQuery target); the script creates the table if missing, then `tabledata.insertAll` the changed rows.

## PROCEDURE (deterministic)
STEP 1 — DISCOVER exports in `export-dir` by fuzzy basename (internal_all / response_codes / directives / all_inlinks). IF `internal_all` absent THEN STOP `error.code="REQUIRED_EXPORT_MISSING"`.
STEP 2 — NORMALIZE: merge the exports into one row per URL following priority internal_all > response_codes > directives (first non-empty value wins). Map SF headers to stable columns, coercing ints/floats. Strip URL fragments.
STEP 3 — ENRICH from `all_inlinks`: count distinct linking sources per destination and the top inbound anchors.
STEP 4 — HASH: compute `row_hash = sha1(HASH_FIELDS)` per URL (status, indexability, canonical, redirect, on-page, robots) — the ingested timestamp is excluded so unchanged pages hash identically.
STEP 5 — DIFF vs `previous.row_hashes`: partition URLs into new / changed / removed / unchanged. First run → `status="baseline"`.
STEP 6 — BUILD incremental payload = new + changed rows, plus a `present=false` tombstone row per removed URL. Emit `ddl` (partitioned by ingest day, clustered by url) and an illustrative `merge_sql` for the current-state view.
STEP 7 — IF `--load`: ensure the table (idempotent, 409 = already exists), then `insertAll` in batches of 500 with `insertId = row_hash:run_id` so re-runs never double-insert. Report loaded/failed.

## RATE LIMITS & ERROR HANDLING
- BigQuery `429`/`5xx` → exponential backoff `2^attempt` (max 5) then return the HTTP code on the batch; `401` → STOP `AUTH_INVALID_TOKEN`; `409` on table create → treat as success.
- Streaming inserts are paced (`sleep 0.2` between 500-row batches) to stay under the insert quota; effective concurrency 1.
- Partial insert errors are counted per row (`rows_failed`) rather than aborting the run.

## MISSING / INSUFFICIENT DATA
- First run (no `previous`) → `status="baseline"`; every URL is "new", nothing is diffed as changed.
- IF a merged CSV lacks a mapped column THEN that field is `null` (never fabricated); `all_inlinks` absent → `inlinks_enriched=false`.
- IF `max-rows` is hit THEN `truncated=true` and the run reports it; narrow the crawl or raise the cap rather than trusting a partial diff.
- Zero parsed URL rows → `status="no_data"` (not an error).

## OUTPUT
One JSON object per `references/output.schema.json`. `row_hashes` MUST be persisted and passed back as the next run's `--previous`.

## FILES
- `scripts/warehouse_loader.py` — CSV normalization, run diff, DDL/MERGE emission, idempotent BigQuery load.
- `references/output.schema.json` — output contract.
