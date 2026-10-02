---
name: pseo-page-inventory-planner
description: Demand-validates every {modifier} x {entity} combination before you generate programmatic pages by fetching search volume and competition from DataForSEO and joining Search Console to spot entities you already rank for, then scores each candidate for viability and projects its traffic. Returns a ranked build / skip / defer / exists inventory that prunes zero-demand combos so a template cannot spray thin pages into the index. Use when the user wants to plan a pSEO page set, validate modifier-entity demand, or decide which programmatic pages are worth building.
metadata:
  title: Programmatic Page Inventory Planner
  category: programmatic-seo
---

# Programmatic Page Inventory Planner

AGENT ROLE: Autonomous pSEO inventory agent. Expand modifiers x entities into candidate keywords, validate real demand and competition, mark what already ranks, project traffic per page, and emit the JSON in `references/output.schema.json`. Never green-light a page without measured demand.

## OBJECTIVE
Turn a combinatorial page idea ({modifier} x {entity}, e.g. "cheap x flights to {city}") into a demand-validated inventory: which combinations deserve a page, which are pruned as zero-demand index bloat, which are deferred as too competitive for the reward, and which already rank — with a projected monthly-clicks number per build candidate.

## INPUTS
- `modifiers` (REQUIRED string[] via `--modifiers` file): the qualifier set (e.g. best, cheap, near me).
- `entities` (REQUIRED string[] via `--entities` file): the head-noun set (e.g. cities, products, categories).
- `pattern` (OPTIONAL, default `"{modifier} {entity}"`): how a candidate keyword is composed; must use only `{modifier}` and `{entity}`.
- `min_volume` (OPTIONAL, default 20): combinations below this monthly volume are pruned (`skip`).
- `high_comp` (OPTIONAL, default 85) and `high_comp_min_volume` (OPTIONAL, default 500): a combo at/above this competition index with volume under the floor is `defer`red.
- `location_code` / `language_code` (OPTIONAL, defaults 2840 / `en`): DataForSEO geo/lang.
- `site` (OPTIONAL): a GSC property; when set, queries already earning impressions are marked `exists`.
- `max_combos` (OPTIONAL, default 1000): cost guard on the candidate set.

## AUTHENTICATION (DataForSEO + GSC)
1. REQUIRE env `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD`. IF either unset THEN STOP `error.code="AUTH_MISSING_DATAFORSEO"`. Sent as HTTP Basic to `keywords_data/google_ads/search_volume/live`.
2. IF DataForSEO returns 401 THEN STOP `error.code="AUTH_INVALID_DATAFORSEO"`.
3. IF `--site` is passed THEN REQUIRE env `GSC_ACCESS_TOKEN` (OAuth bearer, `webmasters.readonly`). IF unset THEN STOP `error.code="AUTH_MISSING_GSC"`. IF the token is rejected THEN STOP `error.code="AUTH_GSC_FORBIDDEN"`.

## EXPECTED TOOL CALLS
- Run `scripts/page_inventory_planner.py --modifiers modifiers.json --entities entities.json --pattern "{modifier} {entity}" [--site sc-domain:example.com] [--min-volume 20]`.
- One batched DataForSEO Search Volume call per 700-keyword chunk; at most one GSC Search Analytics query.

## PROCEDURE (deterministic)
STEP 1 — EXPAND: build the Cartesian product of modifiers x entities through `pattern`; normalize whitespace and case; dedupe. IF the set exceeds `max_combos` THEN cap it and set `combinations_truncated=true`.
STEP 2 — DEMAND: chunk the candidate keywords (<=700 each) and POST to DataForSEO Search Volume; record `search_volume`, `competition_index` (0-100), `competition`, `cpc` per keyword.
STEP 3 — EXISTS SIGNAL: IF `--site` THEN pull GSC query impressions once; a candidate whose exact query already earns impressions is flagged `already_ranking`.
STEP 4 — SCORE each candidate: `viability = 100 * (0.6*demand + 0.25*ease + 0.15*commercial)` where demand = log10(volume+1)/4 (saturates ~10k), ease = 1 - competition_index/100, commercial = min(cpc/8, 1).
STEP 5 — PROJECT: estimate a rank band from competition (index <=33 -> pos 4, <=66 -> pos 8, else pos 15) and project monthly clicks = volume x CTR(band).
STEP 6 — DECIDE per combo: IF no volume data THEN `skip` (`no_volume_data`); ELIF volume < `min_volume` THEN `skip` (index-bloat prune); ELIF already ranking THEN `exists`; ELIF competition_index >= `high_comp` AND volume < `high_comp_min_volume` THEN `defer`; ELSE `build`. EMIT the inventory sorted build-first then by viability, with tallies and total projected clicks.

## RATE LIMITS & ERROR HANDLING
- DataForSEO `429` -> backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`; `5xx`/timeout retry <=3 then STOP `REQUEST_FAILED`.
- A DataForSEO task-level error (non-2000x `status_code`) STOPs `REQUEST_FAILED` with the task message.
- GSC `429`/`5xx` -> backoff (max 5) then proceed WITHOUT the exists-signal (non-fatal); `401`/`403` STOP `AUTH_GSC_FORBIDDEN`.
- Concurrency: batched chunks issued sequentially with a pacing sleep; effective concurrency 1.

## MISSING / INSUFFICIENT DATA
- A keyword DataForSEO returns no row for has `search_volume=null`; it is counted in `no_volume_data` and decided `skip` — never invented as zero-real-demand vs missing.
- Without `--site` the exists-signal is absent (`exists_source="none"`); nothing is claimed to already rank.
- Projected clicks are an explicit CTR-band estimate (`projected_position` is disclosed), not a promise; deferred/skipped/exists rows project 0.
- Competition index may be null (thin Ads data); ease defaults to 0.5 and the row is still scored honestly.

## OUTPUT
One JSON object per `references/output.schema.json`.

## FILES
- `scripts/page_inventory_planner.py` — combo expansion, DataForSEO demand + GSC exists join, viability scoring, traffic projection, build/skip/defer/exists decision.
- `references/output.schema.json` — output contract.
