---
name: Index Bloat Pruning Auditor
description: Scores every indexed URL on search value (GSC clicks/impressions/position), engagement (GA4), internal links (crawl), and crawl cost (Googlebot log hits) to isolate the zero-value and redundant pages inflating the index, then recommends a per-URL action — keep, consolidate, noindex, or remove-410 — with the consolidation target and the projected crawl-efficiency gain. Use when the user asks about index bloat, thin or zero-traffic pages, crawl-budget waste, pruning or content consolidation, or "which pages should I noindex/delete".
category: audit
---

# Index Bloat Pruning Auditor

AGENT ROLE: Autonomous index-hygiene agent. Pull value, engagement, link, and crawl-cost signals, score each indexed URL, decide a single pruning action deterministically, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Find the pages that dilute the index and waste crawl budget without earning anything, and return a defensible per-URL decision (keep / consolidate → target / noindex / remove-410) plus the crawl-efficiency gain pruning them would recover.

## INPUTS
- `site` (REQUIRED unless `--gsc-file`): GSC property for the live value pull (`sc-domain:example.com` or URL-prefix).
- `gsc_file` (REQUIRED unless `--site`): local JSON map `url -> {clicks, impressions, position}`.
- `ga4` (OPTIONAL flag) / `ga4_file` (OPTIONAL): engagement via GA4 Data API (needs `GA4_PROPERTY_ID`) or a local map `path|url -> {sessions, engaged_sessions, conversions}`.
- `crawl` (OPTIONAL): map or list with `internal_links`, `status`, `title` per URL (title powers duplicate clustering; status catches already-dead pages).
- `logs` (OPTIONAL): access-log file; verified-by-UA Googlebot hits per path become the real crawl-cost weight.
- `window_days` (OPTIONAL, default 90), `min_impr` (OPTIONAL, default 10), `stale_days` (OPTIONAL, default 180).

## AUTHENTICATION (Search Console + GA4)
1. GSC value signal is REQUIRED. IF `--site` is used THEN REQUIRE env `GSC_OAUTH_TOKEN` (scope `https://www.googleapis.com/auth/webmasters.readonly`). IF neither `--gsc-file` nor `--site` THEN STOP `error.code="INPUT_INVALID"`; IF `--site` but no token THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
2. GA4 is optional. IF `--ga4` THEN REQUIRE `GA4_PROPERTY_ID` + `GA4_OAUTH_TOKEN` (scope `analytics.readonly`); missing token → GA4 skipped, not fatal.
3. Endpoints: GSC `.../searchAnalytics/query` (`dimensions=["page"]`, `dataState="final"`); GA4 `.../properties/{id}:runReport` (`landingPagePlusQueryString`). `403` → `AUTH_NO_SITE_ACCESS` / `AUTH_NO_PROPERTY_ACCESS`.

## EXPECTED TOOL CALLS
- Run `scripts/index_bloat_audit.py --site {property} [--ga4] [--crawl crawl.json] [--logs access.log] [--window-days 90] [--min-impr 10]`.
- The script pages GSC by page, optionally pages GA4 by landing page, parses Googlebot log hits with a regex, and joins all signals on the normalised URL/path.

## PROCEDURE (deterministic)
STEP 1 — GATHER value (GSC), engagement (GA4 by path), inlinks/title/status (crawl), crawl cost (Googlebot log hits per path; uniform 1.0 proxy when no logs).
STEP 2 — CLUSTER duplicates by normalised `<title>`; the cluster `primary` = highest clicks (tie: impressions); the rest map to it as consolidation targets.
STEP 3 — SCORE `value_score` in [0,1] = weighted, renormalised sum of clicks(.40), impressions(.15), inlinks(.10), and — when GA4 present — engagement_rate(.10) + conversions(.25), each min-max normalised across the set.
STEP 4 — DECIDE one action: `already_gone` (status 404/410); `consolidate`→primary (duplicate with ≤20% of primary's clicks); `remove_410` (0 clicks + 0 impressions + ≤1 inlink + still crawled); `noindex` (0 clicks, negligible impressions, or linked-but-valueless); `keep` (has clicks, or impressions ≥ `min_impr` with a "fix CTR" note).
STEP 5 — `bloat_score = crawl_cost * (1 - value_score)`; sort desc. `projected_crawl_savings_pct = crawl_cost(pruned) / crawl_cost(all)`. EMIT summary, clusters, and per-URL results.

## RATE LIMITS & ERROR HANDLING
- GSC/GA4 `429`/`5xx` → backoff `min(60, 2^attempt)` (max 5) then STOP `error.code="RATE_LIMITED"`; `400` → `BAD_REQUEST`. Both APIs paged (`rowLimit`/`limit` + offset). No more than the two APIs are hit concurrently (sequential paging).
- Log parsing is local and streamed line-by-line (no full-file load into memory beyond the counter).

## MISSING / INSUFFICIENT DATA
- No logs → `crawl_cost_source="uniform_proxy"` (every indexed URL costs 1.0); savings are then a page-count proxy, disclosed as such — NEVER presented as measured crawl volume.
- No GA4 → engagement/conversion weights are dropped and the value formula renormalises over the remaining signals (`ga4_source="off"`).
- No crawl → duplicate clustering and inlink scoring are skipped; `remove_410` still fires only on the click/impression/cost evidence, never on assumptions.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/index_bloat_audit.py` — GSC/GA4 pulls, log parsing, value scoring, duplicate clustering, action decision.
- `references/output.schema.json` — output contract.
