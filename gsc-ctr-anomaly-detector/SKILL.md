---
name: GSC CTR Anomaly Detector
description: Pulls query-level performance from Search Console and flags queries whose actual click-through rate deviates significantly from the expected CTR for their average position, isolating title, meta, or SERP-feature causes. Use when the user asks why clicks are low despite rankings, wants CTR optimization targets, or title/meta rewrite priorities.
category: keyword-research
---

# GSC CTR Anomaly Detector

AGENT ROLE: Autonomous CTR-diagnostics agent. Compare each query's real CTR to the position-expected benchmark, flag significant deviations, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Find queries that under- or over-perform their position's expected CTR, quantify the click opportunity, and attribute likely cause — so title/meta rewrites are prioritized by impact, not guesswork.

## INPUTS
- `site_url` (REQUIRED): verified GSC property (`sc-domain:...` or URL-prefix).
- `start_date`/`end_date` (OPTIONAL): default last 28 complete days (GSC lag ~3 days; never `today`).
- `min_impressions` (OPTIONAL, default 100): demand floor to suppress noise.
- `deviation_sigma` (OPTIONAL, default 2.0): robust-z threshold on the CTR residual.
- `ctr_curve` (OPTIONAL): a property-specific position→CTR curve; default `references/ctr_curve.json`.

## AUTHENTICATION (Search Console API)
1. REQUIRE `GOOGLE_APPLICATION_CREDENTIALS` (SA) OR OAuth, scope `https://www.googleapis.com/auth/webmasters.readonly`.
   - IF absent THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
2. Identity MUST be a verified user on `site_url`. IF `403` THEN STOP `error.code="AUTH_NO_SITE_ACCESS"`.
3. Endpoint: `POST https://searchconsole.googleapis.com/webmasters/v3/sites/{urlEncoded}/searchAnalytics/query`.

## EXPECTED TOOL CALLS
- Run `scripts/ctr_anomaly.py --site {site_url} --min-impr {n} --sigma {x}`.
- Query with `dimensions=["query","page"]`, `dataState="final"`, paged via `startRow`.

## PROCEDURE (deterministic)
STEP 1 — FETCH query/page rows (paged).
STEP 2 — FILTER to `impressions >= min_impressions`.
STEP 3 — For each row: `expected_ctr = curve[round(position)]`; `residual = actual_ctr − expected_ctr`.
STEP 4 — Build the residual distribution across all rows; compute robust center (median) + MAD; `robust_z = (residual − median) / (1.4826*MAD)`.
STEP 5 — FLAG:
  - `underperformer` IF `robust_z <= −deviation_sigma` → `opportunity_clicks = round(impressions * (expected_ctr − actual_ctr))` (positive).
  - `overperformer` IF `robust_z >= +deviation_sigma` → study as a winning title/meta pattern to replicate.
STEP 6 — ATTRIBUTE (heuristic, per underperformer): fetch the page's `<title>`/meta description (≤1 GET, honor robots); IF the query's head terms are absent from the title THEN cause hint `title_mismatch`; IF position ≤ 3 but CTR low AND the SERP likely has features THEN `serp_feature_suppression`; else `weak_snippet`.
STEP 7 — EMIT underperformers sorted by `opportunity_clicks` desc; include a small `overperformers` list.

## RATE LIMITS & ERROR HANDLING
- GSC: `429`/`RESOURCE_EXHAUSTED` → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`.
- Only trust `final` `dataState` rows.
- On-page title fetch failure → `cause="unknown"` for that row; never block the run.

## MISSING / INSUFFICIENT DATA
- IF 0 rows above `min_impressions` THEN `status="no_data"`, empty arrays, no error.
- IF `position > 20` THEN clamp expected_ctr to the 20+ bucket; do not extrapolate.
- Residual stats require ≥ 30 rows for stability; below that set `confidence="low"` and widen sigma by +0.5.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/ctr_anomaly.py` — GSC client, residual + robust-z scoring, cause attribution.
- `references/ctr_curve.json` — position→expected-CTR benchmark.
- `references/output.schema.json` — output contract.
