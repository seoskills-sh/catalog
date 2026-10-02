---
name: semrush-gsc-rank-blender
description: Joins Semrush tracked organic positions (third-party SERP scrape) with Search Console's real per-query impression, click, and position data (first-party truth), resolving the gap between the two into one blended table that flags where the tools disagree beyond a position threshold and where GSC reveals real search demand Semrush is not tracking at all. Use when the user wants to reconcile Semrush rankings against actual Search Console performance, find rank-tracking blind spots, or trust one blended position table instead of two conflicting ones.
metadata:
  title: Semrush-GSC Rank Blender
  category: integrations
---

# Semrush-GSC Rank Blender

AGENT ROLE: Autonomous rank-reconciliation agent. Fetch Semrush organic positions and the GSC search-analytics query/page table, join on the normalized keyword, and classify each keyword as agreement, disagreement, untracked demand, or Semrush-only. Emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Reconcile the third-party rank (Semrush) against the first-party rank (GSC) for one domain and window, producing a single blended truth table: where the tools agree, where they disagree by more than the threshold (with direction and traffic-weighted impact), the real demand Semrush misses, and the Semrush keywords with no GSC demand.

## INPUTS
- `site` (REQUIRED): GSC property, e.g. `sc-domain:example.com` or a URL-prefix property.
- `domain` (REQUIRED): Semrush domain, e.g. `example.com`.
- `database` (OPTIONAL, default `us`): Semrush regional database.
- `start` / `end` (OPTIONAL): GSC window; default last 28 days ending 3 days ago (data lag).
- `delta-threshold` (OPTIONAL, default 3): min |Semrush position − GSC position| to count as a disagreement.
- `min-impr` (OPTIONAL, default 20): impression floor for a GSC query to count as untracked demand.
- `max-keywords` (OPTIONAL, default 10000) / `max-rows` (OPTIONAL, default 100000): cost/size guards.

## AUTHENTICATION (Semrush + Search Console)
1. REQUIRE `SEMRUSH_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_SEMRUSH_KEY"`.
   - Endpoint: `GET https://api.semrush.com/?type=domain_organic&export_columns=Ph,Po,Nq,Cp,Ur,Tr` — returns `;`-delimited CSV (NOT JSON). A bad key returns a plain-text `ERROR` line → STOP `SEMRUSH_API_ERROR`.
2. REQUIRE `GSC_OAUTH_TOKEN` or `GCP_ACCESS_TOKEN` (bearer, scope `webmasters.readonly`). IF unset THEN STOP `error.code="AUTH_MISSING_GSC_TOKEN"`.
   - This std-lib reference uses a short-lived access token (e.g. `gcloud auth print-access-token`); it cannot sign a service-account JWT. `401` → `AUTH_INVALID_GSC_TOKEN`; `403` → `AUTH_NO_SITE_ACCESS`.

## EXPECTED TOOL CALLS
- Run `scripts/rank_blender.py --site sc-domain:example.com --domain example.com --database us`.
- One Semrush `domain_organic` pull; paginated GSC `searchAnalytics/query` with dimensions `[query, page]`.

## PROCEDURE (deterministic)
STEP 1 — FETCH Semrush organic positions; key each row by the normalized keyword (lowercased, whitespace-collapsed): position, volume, CPC, ranking URL.
STEP 2 — FETCH GSC rows (query × page), paginate at 25000; aggregate to one record per query with summed clicks/impressions, the impression-weighted average position, and the top page by clicks.
STEP 3 — JOIN on the normalized keyword. For each common keyword compute `position_delta = semrush_position − gsc_position` (positive = Semrush shows a worse rank than reality).
STEP 4 — CLASSIFY: |delta| < threshold → agreement; otherwise disagreement tagged `semrush_optimistic` (Semrush better than reality) or `semrush_pessimistic`, ranked by `impact = |delta| × impressions`. Flag `url_mismatch` when the two tools name different ranking URLs.
STEP 5 — UNTRACKED DEMAND: GSC queries with impressions ≥ `min-impr` absent from Semrush.
STEP 6 — SEMRUSH-ONLY: Semrush keywords with no GSC demand (phantom volume, wrong locale/database, or not truly ranking for this site).
STEP 7 — EMIT summary (agreement rate, median |delta|) plus the four buckets, sorted by impact/impressions/volume.

## RATE LIMITS & ERROR HANDLING
- Semrush/GSC `429` → backoff `2^attempt` (max 5) then STOP `RATE_LIMITED`; `5xx` retry ≤3.
- GSC pagination stops at `max-rows`; Semrush capped at `max-keywords` (both bill credits/rows).
- The two calls run sequentially (concurrency 1) with pacing between them.

## MISSING / INSUFFICIENT DATA
- Semrush "NOTHING FOUND" → treated as zero tracked keywords (not an error); the run still reports GSC-side untracked demand.
- Both sides empty → `status="no_data"`.
- A keyword missing a numeric position on either side is skipped from the delta math, never guessed.
- GSC position is an impression-weighted average across pages; disclosed as such — it is not a single-SERP snapshot like Semrush's.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/rank_blender.py` — Semrush + GSC fetch, keyword join, disagreement and untracked-demand classification.
- `references/output.schema.json` — output contract.
