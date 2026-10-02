---
name: striking-distance-finder
description: Surfaces "striking distance" keywords ranking in Search Console positions 11-20 with real impression demand, ranks them by projected click uplift, and maps each to its URL and likely on-page gap. Use when the user asks for quick SEO wins, page-two keywords, low-hanging fruit, or "what should I optimize next".
metadata:
  title: Striking Distance Opportunity Finder
  category: analytics
---

# Striking Distance Opportunity Finder

AGENT ROLE: Autonomous opportunity-mining agent. Pull GSC query/page data, isolate page-two opportunities, quantify the click upside of reaching page one, optionally confirm the on-page gap, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Return a prioritized list of query→URL pairs whose average position is 11–20 and whose demand makes a page-one push worthwhile, each with a projected click uplift and a concrete optimization hint.

## INPUTS
- `site_url` (REQUIRED): as verified in GSC (`sc-domain:...` or URL-prefix).
- `start_date`,`end_date` (OPTIONAL): default last 28 complete days (respect GSC's ~3-day lag; never request `today`).
- `position_band` (OPTIONAL): default `[11, 20]`.
- `min_impressions` (OPTIONAL, default 100): demand floor over the window.
- `target_position` (OPTIONAL, default 7): the realistic position used to model the upside.
- `verify_onpage` (OPTIONAL bool, default false): IF true, fetch each candidate URL and check whether the query term is present in `<title>` and `<h1>`.

## AUTHENTICATION (Search Console API)
1. REQUIRE `GOOGLE_APPLICATION_CREDENTIALS` (SA) OR OAuth token, scope `https://www.googleapis.com/auth/webmasters.readonly`.
   - IF absent THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
2. Identity MUST be a verified user on `site_url`. IF `403` THEN STOP `error.code="AUTH_NO_SITE_ACCESS"`.
3. Endpoint: `POST https://searchconsole.googleapis.com/webmasters/v3/sites/{urlEncoded}/searchAnalytics/query`.

## EXPECTED TOOL CALLS
- Run `scripts/striking_distance.py --site {site_url} --min-impr {n} --target {pos} [--verify]`.
- Query with `dimensions=["query","page"]`, `dataState="final"`, `rowLimit=25000`, paging via `startRow`.
- IF `verify_onpage` THEN issue at most 1 GET per candidate URL, concurrency ≤ 5, honoring `robots.txt`; timeout 10s each. NEVER submit forms or follow non-GET actions.

## PROCEDURE
STEP 1 — FETCH query/page rows (paged).
STEP 2 — FILTER: keep rows where `position_band[0] <= position <= position_band[1]` AND `impressions >= min_impressions`.
STEP 3 — SCORE each candidate:
  - `current_ctr` = observed `clicks / impressions` (fallback to curve[position] IF clicks == 0).
  - `target_ctr` = `curve[target_position]` from `references/ctr_curve.json`.
  - `projected_uplift_clicks = max(0, round(impressions * (target_ctr - current_ctr)))`.
  - `effort_hint`: IF position ≤ 15 THEN "low" ELSE "medium".
STEP 4 — OPTIONAL ON-PAGE GAP (verify_onpage true):
  - Fetch URL; set `gap.title_missing_term` / `gap.h1_missing_term` booleans (case-insensitive, term tokens).
  - IF fetch fails/blocked THEN `gap = null`, `gap_status = "fetch_failed"`. Do NOT block the whole run on one URL.
STEP 5 — DEDUPE by (query,page); IF a query maps to multiple pages in-band THEN keep the highest-impression page and add `also_ranking_urls`.
STEP 6 — EMIT JSON, sorted by `projected_uplift_clicks` desc, capped at `limit` (default 100).

## RATE LIMITS & ERROR HANDLING
- GSC: `429`/`RESOURCE_EXHAUSTED` → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`.
- On-page fetches: IF a host returns repeated `429/503` THEN stop verifying that host and mark remaining `gap_status="rate_limited_host"`; the GSC result still returns.
- Treat any position exactly at a band edge inclusively.

## MISSING / INSUFFICIENT DATA
- IF 0 rows in band THEN `status="no_opportunities"`; return empty list, no error (site may already rank page-one or lack impressions).
- IF clicks are all zero for a candidate THEN use curve-based `current_ctr`; mark `ctr_basis="modeled"`.
- Never claim an on-page gap without a successful fetch; absence of verification ⇒ `gap = null`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/striking_distance.py` — auth, paging, scoring, optional on-page check.
- `references/ctr_curve.json` — position→CTR lookup for uplift modeling.
- `references/output.schema.json` — output contract.
