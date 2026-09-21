---
name: SERP Competitor Landscape Mapper
description: Fetches SERPs for a keyword universe and tallies each domain's ranking positions to compute SERP presence and average position, returning the true organic competitor landscape ranked by weighted SERP share. Use when the user wants to identify real organic competitors, map the SERP landscape, or find who to benchmark against.
category: competitor-analysis
---

# SERP Competitor Landscape Mapper

AGENT ROLE: Autonomous competitive-mapping agent. Aggregate domain rankings across a keyword universe and emit the JSON in `references/output.schema.json`. The organic competitor set is often different from the business's assumed rivals — report what the SERPs actually show.

## OBJECTIVE
For a keyword set, rank every domain by how much of the SERP real estate it controls (weighted by position and coverage), revealing the true organic competitors and where the target sits among them.

## INPUTS
- `keywords` (REQUIRED string[]).
- `target` (OPTIONAL): the user's domain, to locate it in the landscape.
- `top_n` (OPTIONAL, default 10): SERP depth to score.
- `location`/`hl` (OPTIONAL): default "United States"/"en".

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
2. Endpoint (SerpApi shape): `GET https://serpapi.com/search.json?engine=google&q={kw}&num={top_n}&api_key={key}`.

## EXPECTED TOOL CALLS
- Run `scripts/serp_competitors.py --keywords keywords.json [--target example.com]`.
- One SERP fetch per keyword; extract ordered `organic_results[].link` → domains.

## PROCEDURE (deterministic)
STEP 1 — FETCH the top-`top_n` organic domains for each keyword.
STEP 2 — For each domain accumulate: `keyword_coverage` (how many keywords it ranks for), `positions` list, and a `visibility_weight = sum(position_ctr(pos) * 1)` using a position→weight curve (higher positions worth more).
STEP 3 — SERP SHARE: `share = domain_visibility / total_visibility` across all domains.
STEP 4 — LOCATE TARGET (if provided): its rank in the landscape, coverage, and average position; identify keywords where a competitor outranks it.
STEP 5 — EMIT the competitor leaderboard sorted by SERP share, plus per-domain coverage and average position; mark which are "true competitors" (coverage over a threshold) vs incidental.

## RATE LIMITS & ERROR HANDLING
- SERP APIs bill per search. `429`/quota → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` with `partial` (keywords fetched) and the landscape built so far.
- `5xx`/timeout retry ≤3 then mark that keyword `skipped`; continue.
- Concurrency ≤ 3.

## MISSING / INSUFFICIENT DATA
- IF a keyword returns no organic results THEN skip it (note in `skipped`), do not zero-weight domains.
- Aggregators (wikipedia, amazon, youtube) can dominate — flag them `is_aggregator=true` so the user can filter; do not silently drop them.
- Coverage below the `true_competitor` threshold is reported but labeled `incidental`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/serp_competitors.py` — SERP fetch, visibility weighting, share computation, target location.
- `references/output.schema.json` — output contract.
