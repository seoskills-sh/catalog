---
name: query-fanout-expander
description: Recursively expands seed terms through Google Autocomplete, People-Also-Ask, and related searches to reconstruct the query fan-out network AI engines decompose queries into, then deduplicates and clusters by intent. Use when the user wants exhaustive query discovery, topic coverage, People-Also-Ask mining, or the sub-questions a topic must answer for AI search.
metadata:
  title: Query Fan-Out Expander
  category: keyword-research
---

# Query Fan-Out Expander

AGENT ROLE: Autonomous query-discovery agent. Expand seeds breadth-first across autocomplete + SERP question sources, dedupe, cluster, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
From a few seeds, reconstruct the broad "fan-out" of real user queries and sub-questions around a topic — the same decomposition modern AI search performs — so content can cover the whole intent surface.

## INPUTS
- `seeds` (REQUIRED string[]).
- `depth` (OPTIONAL, default 2): expansion hops (depth 1 = direct suggestions only).
- `max_queries` (OPTIONAL, default 500): hard cap.
- `sources` (OPTIONAL): subset of `["autocomplete","paa","related"]`. Default all.
- `hl`/`gl` (OPTIONAL): default "en"/"us".

## AUTHENTICATION
- `autocomplete`: Google Suggest is keyless — `GET https://suggestqueries.google.com/complete/search?client=firefox&hl={hl}&q={q}` returns `[query,[suggestions...]]`. Use UA `seoskills-fanout/1.0`; it is unofficial, so treat failures as soft.
- `paa`/`related`: REQUIRE env `SERP_API_KEY` (SerpApi `related_questions` + `related_searches`). IF unset AND those sources requested THEN drop them, keep autocomplete, and note `sources_used`.
- IF NEITHER autocomplete nor a SERP key is available THEN STOP `error.code="NO_SOURCES_AVAILABLE"`.

## EXPECTED TOOL CALLS
- Run `scripts/fanout.py --seeds seeds.json --depth 2`.
- BFS: expand each frontier query via the enabled sources; enqueue new normalized queries until `depth` or `max_queries`.

## PROCEDURE (deterministic)
STEP 1 — INIT frontier = seeds (depth 0). Maintain a `seen` set (normalized: lowercased, whitespace-collapsed).
STEP 2 — For each frontier query at depth d < `depth`: collect suggestions/questions from the enabled sources; add unseen to `seen` and to the next frontier; record each query's `source` and `parent`.
STEP 3 — STOP expanding when `depth` reached OR `len(seen) >= max_queries` (set `hit_cap=true`).
STEP 4 — CLUSTER the full set by shared head-term + question-word into intent buckets (question/comparison/commercial/transactional/other) using the same modifier rules as the intent clusterer.
STEP 5 — EMIT the deduped query list with `depth`, `source`, `parent`, plus cluster rollups sorted by size.

## RATE LIMITS & ERROR HANDLING
- Autocomplete: throttle ≤ 5 req/s with 150ms jitter; on repeated `429`/block, stop autocomplete for the run and mark `autocomplete_status="throttled"` (partial results still returned).
- SERP API: `429`/quota → backoff `2^attempt` (max 5), then stop SERP sources and continue with whatever is collected; set `serp_status="rate_limited"`.
- Every source failure is soft — the run always returns the queries gathered so far.

## MISSING / INSUFFICIENT DATA
- IF a query yields no expansions THEN it is a leaf (kept, not errored).
- Dedupe is mandatory; never emit the same normalized query twice.
- Autocomplete reflects locale — always pass `hl`/`gl`; note them in output.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/fanout.py` — BFS expander over autocomplete + SERP questions, dedupe + clustering.
- `references/output.schema.json` — output contract.
