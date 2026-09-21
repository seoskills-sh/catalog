---
name: GA4 Attribution Path Modeler
description: Reconstructs multi-touch conversion paths from the event-level GA4 BigQuery export and assigns credit across channels with last-click, position-based, and linear models to quantify organic search's assisted-conversion value. Use when the user asks about attribution, assisted conversions, the true value of organic, or "is last-click undercounting SEO".
category: analytics
---

# GA4 Attribution Path Modeler

AGENT ROLE: Autonomous attribution agent operating over the GA4 → BigQuery export. Derive per-session channels, order each converter's touchpoints, apply the credit models, and emit the JSON in `references/output.schema.json`. This skill REQUIRES the BigQuery export to be enabled; it does not use the GA4 UI attribution.

## OBJECTIVE
Quantify how much conversion value each acquisition channel earns under multiple attribution models, and surface the gap between last-click and multi-touch credit for `Organic Search` specifically.

## INPUTS
- `project` (REQUIRED): GCP project holding the export.
- `dataset` (REQUIRED): GA4 export dataset, e.g. `analytics_123456789`.
- `conversion_events` (REQUIRED): array of event names counted as conversions, e.g. `["purchase","generate_lead"]`.
- `lookback_days` (OPTIONAL, default 30): touchpoint window before each conversion.
- `value_source` (OPTIONAL, enum `event_value|purchase_revenue|count`): default `purchase_revenue` for `purchase`, else `count`.

## AUTHENTICATION (BigQuery over the GA4 export)
1. REQUIRE `GOOGLE_APPLICATION_CREDENTIALS`. IF unset THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
2. SA roles: `roles/bigquery.jobUser` on `{project}` + `roles/bigquery.dataViewer` on `{dataset}`.
   - IF `403 accessDenied` THEN STOP `error.code="AUTH_NO_BQ_ACCESS"`.
3. ALWAYS set `maximumBytesBilled = 50 GiB` and prefer partition pruning on `_TABLE_SUFFIX`.

## PRECONDITIONS (verify before querying)
- Confirm the export tables exist: `SELECT table_name FROM {dataset}.INFORMATION_SCHEMA.TABLES WHERE table_name LIKE 'events_%' LIMIT 1`.
  - IF none THEN STOP `error.code="EXPORT_NOT_ENABLED"`: "Enable BigQuery Linking in GA4 Admin; there is no historical backfill, so only post-link dates exist."
- Detect schema variant: newer exports carry `collected_traffic_source`; older ones only event-level `traffic_source`. Set `source_field` accordingly and record it in output as `schema_variant`.

## EXPECTED TOOL CALLS
- Run `scripts/attribution_paths.sql` with query params `@start`, `@end` (as `_TABLE_SUFFIX` bounds), `@conversion_events` (ARRAY<STRING>), `@lookback` (INT64). Parameterize; never concatenate.
- Dry-run first; enforce the cost guard.

## PROCEDURE
STEP 1 — SESSION CHANNEL: for every `session_start` (or first event per `user_pseudo_id`+`ga_session_id`), derive `channel` from source/medium using the mapping in `references/channel_rules.json` (organic, paid_search, direct, referral, social, email, other).
STEP 2 — CONVERSIONS: select events whose `event_name IN @conversion_events`; attach `conv_value` per `value_source`.
STEP 3 — PATHS: for each conversion, collect that user's distinct session-channels in the `@lookback` window, ordered by time, deduplicating consecutive repeats → `path`.
STEP 4 — CREDIT MODELS per conversion value:
  - `last_click`: 100% to the final channel.
  - `position_based`: 40% first, 40% last, 20% split across middle (IF single touch → 100%).
  - `linear`: equal split across touches.
STEP 5 — AGGREGATE credit by channel per model; compute `organic_assist_ratio = organic.position_based / max(organic.last_click, epsilon)`.
STEP 6 — EMIT JSON per schema.

## RATE LIMITS & ERROR HANDLING
- BigQuery 429/`rateLimitExceeded` → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`.
- Poll long jobs `jobs.get` ≤180s; IF unfinished THEN `status="timeout"` + `job_id`.
- IF `maximumBytesBilled` exceeded THEN STOP `error.code="COST_GUARD_TRIPPED"` with the dry-run estimate; instruct to shorten `lookback_days` or date span.

## MISSING / INSUFFICIENT DATA
- IF 0 conversions in range THEN `status="no_conversions"`; return zeroed model tables, no error.
- IF `collected_traffic_source` AND event-level `traffic_source` are both null for a session THEN channel = `direct` (GA4 convention) and increment `unattributed_sessions`.
- Cross-device stitching is limited to `user_id` when present; otherwise paths are `user_pseudo_id`-scoped — record `identity_basis` in output. Never overstate cross-device certainty.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/attribution_paths.sql` — parameterized BigQuery Standard SQL over the GA4 export.
- `references/channel_rules.json` — source/medium → channel mapping.
- `references/output.schema.json` — output contract.
