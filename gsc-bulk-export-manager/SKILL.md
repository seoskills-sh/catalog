---
name: gsc-bulk-export-manager
description: Validates that Search Console's daily Bulk Data Export to BigQuery is configured, then runs partitioned, parameterized queries over the full unsampled export tables (searchdata_url_impression / searchdata_site_impression) to deliver analyses the 16-month UI cannot, a complete query inventory, page-by-query click decay between two windows, and daily anomaly detection, with every query dry-run and gated by a bytes-scanned cost guard before it can bill. Use when the user has the GSC BigQuery bulk export and wants full unsampled query data, decay analysis, anomaly windows, or a cost-safe way to query the export.
metadata:
  title: GSC Bulk Export Manager
  category: integrations
---

# GSC Bulk Export Manager

AGENT ROLE: Autonomous bulk-export analytics agent. Confirm the GSC Bulk Data Export tables exist, build parameterized partitioned SQL over them, dry-run every query against a cost guard, then execute and reduce the results. Emit the JSON in `references/output.schema.json`. Never SELECT `*`; always filter on the `data_date` partition.

## OBJECTIVE
Turn the raw GSC export into three analyses the UI cannot give: the full unsampled query inventory (with anonymized-impression share disclosed), page × query click decay between a recent and a prior window, and daily click anomalies by z-score — all under a hard bytes-scanned budget.

## INPUTS
- `analysis` (OPTIONAL, default `all`): `inventory` | `decay` | `anomaly` | `all`.
- `start` / `end` (OPTIONAL): analysis window; default last 28 days ending 3 days ago.
- `prior-start` / `prior-end` (OPTIONAL): comparison window for decay; default the 28 days before `start`.
- `limit` (OPTIONAL, default 5000, capped 50000): row cap for inventory/decay.
- `max-gb-scanned` (OPTIONAL, default 5): per-query bytes-scanned guard (GiB).
- `z` (OPTIONAL, default 2.5): |z-score| threshold for a daily anomaly.

## AUTHENTICATION (BigQuery / GCP)
1. REQUIRE `GCP_ACCESS_TOKEN` (bearer) + `BQ_PROJECT` + `BQ_DATASET` (the dataset receiving the export).
   - IF any unset THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
   - This std-lib reference uses a short-lived access token (`gcloud auth print-access-token`); it cannot sign a service-account JWT.
2. The token MUST hold `bigquery.jobUser` on the project and `bigquery.dataViewer` on the dataset. `401` → `AUTH_INVALID_TOKEN`; `403` → `BQ_ACCESS_DENIED`.
3. VALIDATE via `tables.get` that `searchdata_url_impression` and `searchdata_site_impression` exist. IF missing THEN STOP `error.code="EXPORT_NOT_CONFIGURED"` with setup instructions (Settings > Bulk data export).

## EXPECTED TOOL CALLS
- Run `scripts/bulk_export_manager.py --analysis all --max-gb-scanned 5`.
- Per analysis: one `jobs.query` DRY RUN (cost check) then one real `jobs.query` with `maximumBytesBilled` set.

## PROCEDURE (deterministic)
STEP 1 — VALIDATE the export tables; STOP `EXPORT_NOT_CONFIGURED` if absent.
STEP 2 — BIND the window params (`@start`, `@end`, `@prior_start`, `@prior_end`, `@limit`) as NAMED query parameters; never concatenate input into SQL.
STEP 3 — For each requested analysis: DRY RUN → read `totalBytesProcessed`. IF it exceeds the guard THEN STOP `COST_GUARD_TRIPPED` reporting the estimate. ELSE execute with `maximumBytesBilled` = guard.
STEP 4 — INVENTORY: group `url_impression` by query over the window; `avg_position = sum_position/impressions + 1` (the export stores zero-based `sum_position`); also report the anonymized-impression share.
STEP 5 — DECAY: FULL OUTER JOIN recent vs prior window per (url, query); keep pairs that had prior clicks; sort by `clicks_delta` ascending (biggest drops first).
STEP 6 — ANOMALY: pull the daily `site_impression` totals; compute mean/std and flag days with |z| ≥ `z` as spike/drop.
STEP 7 — EMIT all requested analyses plus a `spend_report` (per-query estimated bytes/GiB and total).

## RATE LIMITS & ERROR HANDLING
- BigQuery `429`/`5xx` → backoff `2^attempt` (max 5) then STOP `RATE_LIMITED`.
- Execution-time bytes-billed breach → STOP `COST_GUARD_TRIPPED` (the guard is enforced both at dry-run and via `maximumBytesBilled`).
- Queries run sequentially (concurrency 1); each is independently cost-checked.

## MISSING / INSUFFICIENT DATA
- Anonymized queries (`is_anonymized_query = TRUE`) carry no query string; they are EXCLUDED from inventory/decay and their impression share is reported separately — never invented back.
- Anomaly needs ≥ 7 days; fewer → `stats.reason="insufficient_days"`, no anomalies emitted.
- Flat series (std = 0) → no anomalies (cannot z-score), disclosed in `stats`.
- A freshly enabled export backfills gradually; sparse early windows are reported honestly, not extrapolated.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/bulk_export_manager.py` — export validation, parameterized partitioned queries, cost guard, reductions.
- `references/output.schema.json` — output contract.
