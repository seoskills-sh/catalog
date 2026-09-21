---
name: BigQuery Log File Analyzer
description: Analyzes raw server and CDN access logs stored in BigQuery to quantify Googlebot crawl behavior, surface crawl-budget waste, orphaned crawls, and under-crawled priority URLs. Use when the user has logs in BigQuery and asks about crawl budget, Googlebot activity, log-file analysis, wasted crawl, or "what is Google actually crawling".
category: analytics
---

# BigQuery Log File Analyzer

AGENT ROLE: Autonomous crawl-analytics agent. You operate over a BigQuery logs table. Resolve the schema, run the parameterized query in `scripts/googlebot_crawl_analysis.sql`, and emit the JSON contract in `references/output.schema.json`. Never SELECT `*` on a raw logs table — always project and aggregate.

## OBJECTIVE
Classify verified Googlebot requests over a window and report: crawl volume by status class, top wasted-crawl paths (non-200, non-indexable, parameterized), orphaned crawls (crawled but absent from the supplied sitemap set), and high-priority URLs under-crawled relative to their importance.

## INPUTS
- `project` (REQUIRED): GCP project id for billing/jobs.
- `logs_table` (REQUIRED): fully-qualified `project.dataset.table` holding access logs.
- `column_map` (REQUIRED): map logical fields → actual columns, e.g. `{ "ts":"timestamp", "path":"cs_uri_stem", "status":"sc_status", "ua":"cs_user_agent", "ip":"c_ip", "bytes":"sc_bytes" }`.
- `window_days` (OPTIONAL, default 30).
- `sitemap_urls` (OPTIONAL): array of indexable path strings for orphan detection.
- `priority_paths` (OPTIONAL): array of `{path_prefix, weight}` for under-crawl scoring.

## AUTHENTICATION (BigQuery)
1. REQUIRE `GOOGLE_APPLICATION_CREDENTIALS` (service-account key).
   - IF unset THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
2. Service account MUST hold `roles/bigquery.jobUser` on `{project}` AND `roles/bigquery.dataViewer` on the dataset of `{logs_table}`.
   - IF query returns `403 accessDenied` THEN STOP `error.code="AUTH_NO_BQ_ACCESS"` naming the missing role.
3. Execute via BigQuery API `jobs.query` (or `bq query --use_legacy_sql=false`). ALWAYS pass `maximumBytesBilled` = `10 * 2^30` (10 GiB) as a cost guard.
   - IF the job would exceed that THEN STOP `error.code="COST_GUARD_TRIPPED"` and report the dry-run byte estimate; instruct the user to narrow `window_days` or add a partition filter.

## GOOGLEBOT VERIFICATION (do NOT trust user-agent alone)
- Primary filter: `ua LIKE '%Googlebot%'`.
- Verification: Googlebot originates from published ranges. IF the logs table lacks a trustworthy verified-bot column THEN treat UA-only matches as `unverified` and set `verification="ua_only"` in output; DO NOT silently claim verified. IF reverse-DNS enrichment is available, keep only IPs whose PTR ends in `.googlebot.com` or `.google.com` and forward-confirms.

## EXPECTED TOOL CALLS
1. DRY RUN first: submit the query with `dryRun=true`; read `totalBytesProcessed`. Enforce the cost guard.
2. Resolve schema: `GET` table metadata (`tables.get`) to confirm every `column_map` value exists. IF a mapped column is missing THEN STOP `error.code="COLUMN_MAP_INVALID"` listing the missing keys.
3. Execute `scripts/googlebot_crawl_analysis.sql` with query parameters (`@window_days`, `@sitemap`, `@priority`). Parameterize — never string-concatenate user input into SQL.

## PROCEDURE
STEP 1 — Bind params, dry-run, cost-check, execute.
STEP 2 — From the result rollups compute:
  - `status_breakdown`: request counts by class 2xx/3xx/4xx/5xx and the wasted ratio = `(3xx+4xx+5xx)/total`.
  - `wasted_paths`: top 20 non-200 or parameterized paths by hits.
  - `orphaned`: paths with Googlebot hits whose normalized form is NOT in `sitemap_urls` (only if provided).
  - `under_crawled`: for each `priority_paths` prefix, `crawl_rate = hits / days`; flag IF `crawl_rate < 1` for weight ≥ 0.7.
STEP 3 — Emit JSON per schema, sorted by impact.

## RATE LIMITS & ERROR HANDLING
- BigQuery: 429/`rateLimitExceeded` → exponential backoff (`2^attempt`, max 5) then STOP `error.code="RATE_LIMITED"`.
- `quotaExceeded` (concurrent queries) → wait 10s, retry up to 3.
- Long jobs: poll `jobs.get` every 2s up to 120s; IF still running THEN return partial `status="timeout"` with `job_id` for later retrieval.

## MISSING / INSUFFICIENT DATA
- IF `window_days` yields 0 Googlebot rows THEN `status="no_googlebot_traffic"`; return empty arrays, do not error.
- IF `sitemap_urls` absent THEN omit `orphaned` and set `orphan_analysis="skipped_no_sitemap"`.
- IF logs lack a status or path column THEN STOP `COLUMN_MAP_INVALID`; these are mandatory.

## OUTPUT
One JSON object validating `references/output.schema.json`. No prose.

## FILES
- `scripts/googlebot_crawl_analysis.sql` — parameterized BigQuery Standard SQL.
- `references/output.schema.json` — output contract.
