---
name: Faceted Navigation Index Planner
description: Enumerates ecommerce facet and filter URL combinations from a crawl export, joins each to search demand, Googlebot crawl frequency from access logs, index status, and optional GSC clicks, then models crawl-budget waste and emits a per-facet directive of index, canonicalize, noindex, or robots-disallow with a concrete implementation rule. It turns an uncontrolled filter space into a defensible crawl and index policy that protects crawl budget. Use when the user wants to decide which faceted URLs to index, stop a filter crawl trap, or write robots and canonical rules for faceted navigation.
category: programmatic-seo
---

# Faceted Navigation Index Planner

AGENT ROLE: Autonomous faceted-navigation policy agent. Parse the facet URL space from a crawl, measure demand + crawl frequency + index status per facet, quantify crawl-budget waste, decide index/canonicalize/noindex/disallow per facet and per combination, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
For every facet parameter (single-select) and every multi-facet combination present on the site, output the correct indexation directive and the exact implementation (robots line, canonical target, or meta robots), backed by measured demand and Googlebot behavior — so valuable filters get indexed and combinatorial junk stops draining crawl budget.

## INPUTS
- `crawl` (REQUIRED via `--crawl`): a crawler CSV export (Screaming Frog shape: Address, Status Code, Indexability, Canonical). Columns are detected by header name.
- `facet_params` (REQUIRED via `--facet-params`): comma list of query params that are facets (e.g. color,size,brand,sort,page).
- `logs` (OPTIONAL via `--logs`): server access log (Combined Log Format) used to count Googlebot hits per URL.
- `demand` (OPTIONAL via `--demand`): JSON map of `param=value` (or bare value, or `p1&p2` for a combo) to monthly volume.
- `site` (OPTIONAL via `--site`): GSC property; joins clicks per faceted URL.
- `min_demand` (OPTIONAL, default 50): demand at/above which a facet earns `index`.
- `combo_explosion_cap` (OPTIONAL, default 3): a combination of this many simultaneous facet params is treated as a crawl trap and disallowed.
- `noise_params` (OPTIONAL): extra params to force to `robots-disallow` (a sensible sort/session/tracking set is built in).

## AUTHENTICATION (crawl/logs keyless + GSC)
1. The crawl and log inputs are local files and need no credentials.
2. IF `--site` is passed THEN REQUIRE env `GSC_ACCESS_TOKEN` (OAuth bearer, `webmasters.readonly`). IF unset THEN STOP `error.code="AUTH_MISSING_GSC"`. IF the token is rejected THEN STOP `error.code="AUTH_GSC_FORBIDDEN"`.

## EXPECTED TOOL CALLS
- Run `scripts/faceted_index_planner.py --crawl crawl.csv --facet-params color,size,brand,sort,page [--logs access.log] [--demand demand.json] [--site sc-domain:example.com]`.
- No paid API calls in the keyless path; at most one GSC Search Analytics query.

## PROCEDURE (deterministic)
STEP 1 — PARSE the crawl; for each URL split the query string and record which facet params (and which noise params) are present, plus index status and canonical.
STEP 2 — LOGS: IF `--logs` THEN count Googlebot requests (UA contains "googlebot") per request-target and aggregate hits to each facet param and each combination signature.
STEP 3 — JOIN demand per facet value and clicks per URL (IF `--site`).
STEP 4 — WASTE MODEL: a Googlebot hit on a faceted URL that has no demand-bearing facet value AND no GSC clicks is counted as wasted crawl budget; report `wasted_bot_hits` and `wasted_share`.
STEP 5 — SINGLE-FACET DIRECTIVE: IF the param is a noise/tracking/sort param THEN `robots-disallow`; ELIF pagination THEN `canonicalize` (series); ELIF max value demand >= `min_demand` THEN `index` (self-canonical); ELIF demand data exists but is low THEN `canonicalize` to parent; ELSE `noindex,follow`.
STEP 6 — COMBINATION DIRECTIVE (2+ params): IF param count >= `combo_explosion_cap` THEN `robots-disallow` (crawl trap); ELIF the exact combination has measured demand >= `min_demand` THEN `index` (curate a static URL); ELSE `canonicalize` to the highest-demand parent.
STEP 7 — EMIT each directive with its implementation rule and a projected crawl saving = Googlebot hits on everything disallowed.

## RATE LIMITS & ERROR HANDLING
- GSC `429`/`5xx` -> backoff `2^attempt` (max 5) then proceed WITHOUT clicks (non-fatal); `401`/`403` STOP `AUTH_GSC_FORBIDDEN`.
- Crawl parsing caps at `--max-urls` (default 200000) and log parsing at `--max-log-lines` (default 3,000,000) as cost/memory guards; both are streamed line by line.
- Concurrency: single-pass file reads plus one GSC call; effective concurrency 1.
- An empty crawl export STOPs `error.code="BAD_CRAWL"`.

## MISSING / INSUFFICIENT DATA
- WITHOUT `--logs` there is no crawl-frequency signal: `crawl_data=false`, all `googlebot_hits`/waste fields are `null`, and directives fall back to demand + index status only — crawl waste is never fabricated.
- WITHOUT `--demand` a facet has unknown demand: it is NEVER indexed on a guess; it defaults to `noindex,follow` (reason `no_demand_data`), the safe policy.
- WITHOUT `--site` clicks are `null` and do not enter the waste model.
- Multi-facet combinations without an explicit combination-demand entry default to `canonicalize`, never `index`.

## OUTPUT
One JSON object per `references/output.schema.json`.

## FILES
- `scripts/faceted_index_planner.py` — facet URL enumeration, log-based crawl frequency, demand + clicks join, crawl-budget waste model, per-facet and per-combination directive engine.
- `references/output.schema.json` — output contract.
