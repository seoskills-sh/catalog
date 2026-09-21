---
name: Indexation Coverage Auditor
description: Joins the XML sitemap set, GSC index-coverage / URL-Inspection states, a fresh crawl, and analytics traffic into one URL ledger and classifies every page (indexed-earning, submitted-not-indexed, discovered-not-indexed, excluded-crawled, orphaned-earning), then groups the indexation gaps by root cause with the single most likely fix per cluster. Use when the user asks why pages are not indexed, wants to reconcile sitemap vs GSC coverage vs crawl, sees "Discovered/Crawled - currently not indexed", or is auditing index coverage.
category: audit
---

# Indexation Coverage Auditor

AGENT ROLE: Autonomous indexation-reconciliation agent. Parse the sitemap, gather coverage states, join a crawl and traffic, build one URL ledger, classify each URL deterministically, cluster the gaps by cause, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Turn four disconnected views of a site (sitemap, GSC coverage, crawl, analytics) into a single reconciled ledger that says, for every URL, whether it is indexed and earning, and — when it is not — which specific indexation failure it belongs to and how to fix that cluster.

## INPUTS
- `sitemap` (REQUIRED): sitemap URL or local file; sitemap-index and `.gz` are followed/decompressed (bounded by `max_sitemaps`, `max_urls`).
- `site` (OPTIONAL): GSC property, required only for `--inspect` and `--live-crawl`.
- `coverage` (OPTIONAL): local JSON map `url -> {coverageState, verdict, indexingState}` (a GSC export) — avoids the API.
- `crawl` (OPTIONAL): crawl as a `url -> {status, indexable, internal_links}` map or a list of such records.
- `traffic` (OPTIONAL): local JSON map `url -> clicks|sessions`.
- `inspect` (OPTIONAL flag): call the URL Inspection API for sitemap URLs (bounded by `max_inspect`, default 200 — the API has a low daily quota).
- `live_crawl` (OPTIONAL flag): bounded BFS crawl from `site` when no `--crawl` file is given (`max_crawl` default 2000, `max_depth` default 6).

## AUTHENTICATION (Search Console URL Inspection API — only with `--inspect`)
1. No credentials are needed for sitemap parsing or crawling.
2. IF `--inspect` THEN REQUIRE env `GSC_OAUTH_TOKEN` (OAuth 2.0 access token, scope `https://www.googleapis.com/auth/webmasters.readonly`). IF unset THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
3. Endpoint: `POST https://searchconsole.googleapis.com/v1/urlInspection/index:inspect` with `{inspectionUrl, siteUrl}`. IF `403` THEN STOP `error.code="AUTH_NO_SITE_ACCESS"`.

## EXPECTED TOOL CALLS
- Run `scripts/indexation_audit.py --sitemap {url} --site {property} [--inspect --max-inspect 200] [--coverage coverage.json] [--crawl crawl.json] [--traffic traffic.json]`.
- The script parses the sitemap (recursing indexes), optionally inspects a bounded URL set, optionally crawls, then joins everything on the normalised URL.

## PROCEDURE (deterministic)
STEP 1 — SITEMAP: parse `<loc>` from `urlset`/`sitemapindex` (recurse, gunzip). IF zero URLs THEN STOP `error.code="EMPTY_SITEMAP"`.
STEP 2 — COVERAGE: from `--coverage` and/or `--inspect`, map each URL to `(in_index, cause)` via `coverageState`/`verdict` (Submitted-and-indexed → indexed; Discovered → discovered_not_indexed; Crawled-not-indexed; noindex → excluded_noindex; duplicate/canonical → excluded_canonical; redirect; soft-404).
STEP 3 — JOIN: universe = union(sitemap, crawl, traffic, coverage). Per URL record `in_sitemap`, `crawl_status`, `internal_links`, `indexable`, `in_index`, `coverage_state`, `clicks`.
STEP 4 — CLASSIFY each URL into exactly one class: `indexed_earning`, `indexed_no_traffic`, `submitted_not_indexed`, `discovered_not_indexed`, `crawled_not_indexed`, `excluded_noindex`, `excluded_canonical`, `excluded_redirect`, `orphaned_earning` (clicks>0, no inlinks, not in sitemap), `crawl_error`, `not_in_sitemap_indexable`, `coverage_unknown`.
STEP 5 — CLUSTER the non-healthy URLs by `cause`; attach the canonical `likely_fix`; sort clusters by size. EMIT summary + gaps + ledger.

## RATE LIMITS & ERROR HANDLING
- URL Inspection: `429` → backoff `2^attempt` (max 5); persistent quota → stop inspecting, set `inspect_quota_capped=true`, and classify the rest as `coverage_unknown` (never fabricate a state). `403` → `AUTH_NO_SITE_ACCESS`.
- Sitemap/crawl fetch `429`/`5xx` → backoff `2^attempt` (max 4). Crawl paces 150ms/request; concurrency 1 (sequential BFS).
- Cost guards: `max_inspect`, `max_crawl`, `max_sitemaps`, `max_urls`.

## MISSING / INSUFFICIENT DATA
- No coverage at all (`coverage_source="none"`) → `status="insufficient"`: the sitemap↔crawl↔traffic ledger is still produced (orphans, crawl errors, sitemap gaps), but index-state classes are reported as `coverage_unknown` rather than guessed.
- No traffic file → `indexed_earning` collapses into `indexed_no_traffic`; orphan detection needs both traffic and crawl inlinks and is skipped otherwise. NEVER invent index state or clicks.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/indexation_audit.py` — sitemap parse, URL Inspection, crawl, ledger join, classification, gap clustering.
- `references/output.schema.json` — output contract.
