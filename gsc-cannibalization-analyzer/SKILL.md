---
name: gsc-cannibalization-analyzer
description: Detects keyword cannibalization by pulling query-and-page data from the Google Search Console API and clustering queries where multiple URLs compete and swap positions. Use when the user asks about cannibalization, multiple pages ranking for one keyword, "which page should rank", or unstable/fluctuating rankings for a term.
metadata:
  title: Search Console Cannibalization Analyzer
  category: analytics
---

# Search Console Cannibalization Analyzer

AGENT ROLE: Autonomous Search Console analyst. Pull query→page performance, identify cannibalized queries deterministically, recommend a single canonical target per cluster, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
For a verified GSC property and date range, find queries where ≥2 URLs receive impressions, quantify the resulting click loss and position instability, and recommend which URL to consolidate to.

## INPUTS
- `site_url` (REQUIRED): exactly as verified in GSC. Domain property = `sc-domain:example.com`; URL-prefix property = `https://example.com/`.
- `start_date`,`end_date` (OPTIONAL): ISO dates. Default = last 28 complete days (GSC data lags ~2–3 days; never request `today`).
- `country` / `search_type` (OPTIONAL): default all / `web`.
- `min_impressions` (OPTIONAL, default 50): ignore queries below this to suppress noise.
- `competitor_page_threshold` (OPTIONAL, default 2): min competing URLs to flag.

## AUTHENTICATION (Search Console API)
1. REQUIRE `GOOGLE_APPLICATION_CREDENTIALS` (service account) OR a user OAuth token with scope `https://www.googleapis.com/auth/webmasters.readonly`.
   - IF neither present THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
2. The identity MUST be a verified owner/user of `site_url` in Search Console (API access is NOT implied by GA/GSC-UI access).
   - IF API returns `403` THEN STOP `error.code="AUTH_NO_SITE_ACCESS"`: "Add the service-account email as a user on the Search Console property {site_url}."
3. Endpoint: `POST https://searchconsole.googleapis.com/webmasters/v3/sites/{urlEncoded site_url}/searchAnalytics/query`.

## EXPECTED TOOL CALLS
- Run `scripts/cannibalization.py --site {site_url} --start {d} --end {d} --min-impr {n}`.
- The script pages the API (`rowLimit=25000`, `startRow` increments) with `dimensions=["query","page"]` and `dataState="final"`.

## PROCEDURE
STEP 1 — FETCH: query with `dimensions=["query","page"]`. Page until a response returns `< rowLimit` rows.
STEP 2 — GROUP by `query`. Keep only queries with `SUM(impressions) >= min_impressions`.
STEP 3 — DETECT: within a query, count URLs with `impressions > 0`. IF `distinct_urls >= competitor_page_threshold` THEN candidate cluster.
STEP 4 — SCORE each cluster:
  - `primary` = URL with the highest clicks; tie-break by best (lowest) average position.
  - `contenders` = the other URLs.
  - `position_spread` = max(avg_position) − min(avg_position) across URLs. Higher = more instability.
  - `estimated_lost_clicks` = `sum(contender_impressions) * CTR(primary_avg_position) − sum(contender_clicks)`, floored at 0. Use the standard position→CTR curve in `references/ctr_curve.json`.
  - `severity` = `high` IF (position_spread ≥ 5 AND estimated_lost_clicks ≥ 10) ELSE `medium` IF estimated_lost_clicks ≥ 3 ELSE `low`.
STEP 5 — RECOMMEND: `consolidate_to = primary`; action = "canonicalize/redirect contenders or differentiate intent". Never recommend deleting the primary.
STEP 6 — EMIT JSON, clusters sorted by `estimated_lost_clicks` desc.

## RATE LIMITS & ERROR HANDLING
- GSC API quota: ~1200 queries/min/property and a daily cap. IF `429`/`RESOURCE_EXHAUSTED` THEN backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`.
- IF `400 invalid dimension/date` THEN STOP `error.code="BAD_REQUEST"` echoing the message.
- Respect `dataState`: only trust `final` rows; discard `fresh`/partial days.

## MISSING / INSUFFICIENT DATA
- IF total rows == 0 THEN `status="no_data"` (property may be new or date range empty); return empty clusters, no error.
- IF a query has URLs each with impressions but zero clicks THEN still evaluate (impression-level cannibalization); mark `clicks_basis="impressions_only"`.
- Never invent CTR: if `primary_avg_position > 20`, cap CTR at the 20+ bucket value.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/cannibalization.py` — auth, paging, backoff, clustering, scoring.
- `references/ctr_curve.json` — position→CTR lookup used for lost-click estimation.
- `references/output.schema.json` — output contract.
