---
name: SERP Feature Volatility Tracker
description: Snapshots each keyword's SERP features and top rankings on a schedule and computes a volatility score from period-over-period churn, flagging keywords whose SERP composition is destabilizing before rankings move. Use when the user wants SERP monitoring, ranking stability analysis, or early warning of algorithm/feature shifts on target terms.
category: keyword-research
---

# SERP Feature Volatility Tracker

AGENT ROLE: Autonomous SERP-monitoring agent. Take a current SERP snapshot per keyword, diff it against the prior snapshot, compute volatility, and emit the JSON in `references/output.schema.json`. This skill is stateful across runs via the `previous` snapshot input.

## OBJECTIVE
Quantify how much each keyword's SERP is changing over time — both organic ranking churn and the presence/absence of SERP features — to surface destabilizing terms early.

## INPUTS
- `keywords` (REQUIRED string[]).
- `previous` (OPTIONAL): the prior run's `snapshots` array; absent on first run (establishes a baseline).
- `top_n` (OPTIONAL, default 10).
- `track_features` (OPTIONAL): subset of `["featured_snippet","people_also_ask","ai_overview","local_pack","video","image_pack","shopping","top_stories"]`. Default all.
- `location`/`hl` (OPTIONAL): default "United States"/"en".

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
2. Endpoint (SerpApi shape): `GET https://serpapi.com/search.json?engine=google&q={kw}&num={top_n}&api_key={key}`.

## EXPECTED TOOL CALLS
- Run `scripts/volatility.py --keywords keywords.json [--previous previous.json]`.
- One SERP fetch per keyword; capture ordered organic URLs + which tracked features are present.

## PROCEDURE (deterministic, per keyword)
STEP 1 — SNAPSHOT: fetch SERP; record `{ranking: [urls], features: {feature: bool}, captured_at}`.
STEP 2 — IF no `previous` snapshot for this keyword THEN `status="baseline"`, volatility `null`, and return the snapshot for next run.
STEP 3 — RANKING CHURN: compute a rank-biased overlap between prior and current top-N (weight higher positions more). `rank_volatility = 1 − RBO` in [0,1].
STEP 4 — FEATURE CHURN: `feature_changes = set of features that appeared or disappeared`; `feature_volatility = |changes| / |tracked|`.
STEP 5 — `volatility_score = round(0.7*rank_volatility + 0.3*feature_volatility, 3)`; classify `stable (<0.2) | shifting (0.2–0.5) | volatile (>0.5)`.
STEP 6 — EMIT current snapshots (for persistence) + a `changes` list sorted by volatility desc; note new/lost features explicitly.

## RATE LIMITS & ERROR HANDLING
- SERP API bills per search. `429`/quota → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` with `partial`.
- `5xx`/timeout retry ≤3 then mark keyword `status="serp_error"`; carry forward its previous snapshot unchanged so history is not lost.
- Concurrency ≤ 3.

## MISSING / INSUFFICIENT DATA
- First run for any keyword is always `baseline` (no volatility) — this is expected, not an error.
- IF the SERP returns fewer than `top_n` results THEN compare on the intersection length; note `partial_serp=true`.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshots` array MUST be persisted and passed as `previous` next run.

## FILES
- `scripts/volatility.py` — SERP snapshot, RBO ranking churn, feature-diff volatility.
- `references/output.schema.json` — output contract.
