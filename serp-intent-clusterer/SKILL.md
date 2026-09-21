---
name: N-gram SERP Intent Clusterer
description: Clusters a keyword list by the overlap of their live SERP results and fuses n-gram intent signals, then labels each cluster's dominant intent and its single canonical target page. Use when the user needs keyword grouping, topic clusters, intent mapping, or wants to know which keywords should share one page.
category: keyword-research
---

# N-gram SERP Intent Clusterer

AGENT ROLE: Autonomous keyword-clustering agent. Group keywords by SERP-result overlap (the only ground-truth signal for "same intent"), label intent, and emit the JSON in `references/output.schema.json`. Cluster by evidence, not by string similarity alone.

## OBJECTIVE
Partition a keyword set into clusters where Google returns substantially the same URLs (⇒ one page can rank for all), assign each cluster a dominant intent, and name the canonical target URL.

## INPUTS
- `keywords` (REQUIRED string[]).
- `overlap_threshold` (OPTIONAL, default 3): min shared top-10 URLs for two keywords to be linked.
- `top_n` (OPTIONAL, default 10): SERP depth to compare.
- `location`/`hl` (OPTIONAL): default "United States"/"en".

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
2. Endpoint (SerpApi shape): `GET https://serpapi.com/search.json?engine=google&q={kw}&num={top_n}&api_key={key}`.
3. Record `serp_provider` in output; the fetch layer is swappable for DataForSEO.

## EXPECTED TOOL CALLS
- Run `scripts/cluster_intent.py --keywords keywords.json --overlap 3`.
- One SERP fetch per keyword; extract the ordered `organic_results[].link` set.

## PROCEDURE (deterministic)
STEP 1 — FETCH the top-`top_n` organic URLs for every keyword (normalize to registrable-domain + path).
STEP 2 — BUILD an undirected graph: node = keyword; edge (a,b) IF `|urls(a) ∩ urls(b)| >= overlap_threshold`.
STEP 3 — CLUSTER = connected components (single-linkage on SERP overlap).
STEP 4 — INTENT per cluster: compute n-grams over the cluster's keywords + SERP titles; map modifier n-grams to intent via `references`-free rules — `buy|price|cheap|deal|coupon|for sale`→transactional; `best|top|review|vs|alternative`→commercial; `how|what|why|guide|tutorial|ideas`→informational; brand/domain tokens→navigational. Dominant = highest-weighted.
STEP 5 — CANONICAL TARGET: the URL appearing in the most keywords' SERPs within the cluster (modal URL); IF the user's own domain ranks, prefer it and note `owns_target=true`.
STEP 6 — EMIT clusters sorted by size desc; singletons returned as size-1 clusters.

## RATE LIMITS & ERROR HANDLING
- SERP APIs bill per search. On `429`/quota THEN backoff `2^attempt` (max 5); after that STOP `error.code="RATE_LIMITED"` with `partial` = keywords fetched. Never silently drop.
- On `5xx`/timeout retry ≤3 then mark that keyword `status="serp_error"` and exclude it from clustering (report in `skipped`).
- Concurrency ≤ 3.

## MISSING / INSUFFICIENT DATA
- IF a keyword returns < 3 organic URLs THEN it cannot be reliably clustered → return as singleton with `low_confidence=true`.
- Never merge two keywords below `overlap_threshold` even if strings look similar (string similarity ≠ intent).

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/cluster_intent.py` — SERP fetch, overlap graph, connected-component clustering, intent labeling.
- `references/output.schema.json` — output contract.
