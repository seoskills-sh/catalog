---
name: serp-winners-losers
description: Compares ranking snapshots for a keyword set across two periods and computes each domain's net position change and keyword-count movement, flagging the biggest winners and losers and the keywords driving each. Use when the user wants to detect ranking movement after an update, see who gained or lost visibility, or diagnose winners and losers in a niche.
metadata:
  title: SERP Winners & Losers Detector
  category: competitor-analysis
---

# SERP Winners & Losers Detector

AGENT ROLE: Autonomous ranking-movement agent. Snapshot current rankings, diff against the prior snapshot, compute per-domain movement, and emit the JSON in `references/output.schema.json`. Stateful across runs via `previous`.

## OBJECTIVE
Across a keyword set and two time points, identify which domains gained or lost the most organic visibility, quantify the movement, and attribute it to the specific keywords — a clean read on who won and lost (e.g., after an algorithm update).

## INPUTS
- `keywords` (REQUIRED string[]).
- `previous` (OPTIONAL): the prior run's `snapshots` (keyword→{domain:position}); absent on first run (baseline).
- `top_n` (OPTIONAL, default 10).
- `min_movement` (OPTIONAL, default 2): min average position change to list a domain as a mover.

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
2. Endpoint (SerpApi shape): `GET https://serpapi.com/search.json?engine=google&q={kw}&num={top_n}&api_key={key}`.

## EXPECTED TOOL CALLS
- Run `scripts/winners_losers.py --keywords keywords.json [--previous previous.json]`.
- One SERP fetch per keyword; record domain→position for the top N.

## PROCEDURE (deterministic)
STEP 1 — SNAPSHOT current rankings per keyword.
STEP 2 — IF no `previous` THEN `status="baseline"`; return the snapshot only.
STEP 3 — For each domain, across all keywords, compute:
  - `avg_position_change` = mean(previous_position − current_position) over keywords where it ranked in either period (positive = improved).
  - `keywords_gained` (newly in top N), `keywords_lost` (dropped out), `keywords_improved`, `keywords_declined`.
  - `visibility_change` = change in sum of position-CTR weight (captures magnitude, not just count).
STEP 4 — CLASSIFY winner (net positive) / loser (net negative); keep domains with `abs(avg_position_change) >= min_movement` OR a gained/lost keyword.
STEP 5 — ATTRIBUTE: per mover, list the top keywords driving the change (biggest position deltas). EMIT winners and losers sorted by visibility change, and the current `snapshots`.

## RATE LIMITS & ERROR HANDLING
- SERP `429`/quota → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`; carry forward the previous snapshot for unfetched keywords so the diff stays consistent, and return partial.
- `5xx`/timeout retry ≤3 then reuse the previous snapshot for that keyword (`stale=true`).
- Concurrency ≤ 3.

## MISSING / INSUFFICIENT DATA
- A domain that appears in only one period on a keyword is a gain or a loss (position "out of top N" is treated as `top_n + 1` for delta math) — disclose this convention in output.
- First run is baseline (no movement).
- Never compute movement from mismatched keyword sets; only diff keywords present in both snapshots (list any `keywords_added`/`keywords_dropped` from the input set separately).

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshots` MUST be persisted for the next run.

## FILES
- `scripts/winners_losers.py` — SERP snapshot, period diff, per-domain movement + attribution.
- `references/output.schema.json` — output contract.
