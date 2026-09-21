---
name: Organic Share-of-Voice Tracker
description: Tracks rankings for a keyword universe and computes each domain's organic share of voice weighted by position CTR and search volume, trending share over time and attributing gains and losses to keyword clusters. Use when the user wants organic SoV, competitive visibility benchmarking, or to see who is winning share in a market.
category: competitor-analysis
---

# Organic Share-of-Voice Tracker

AGENT ROLE: Autonomous SoV agent. Compute volume- and CTR-weighted organic share of voice per domain, trend it against the prior run, and emit the JSON in `references/output.schema.json`. Stateful across runs via `previous`.

## OBJECTIVE
For a keyword universe, compute each domain's organic Share of Voice (weighted by the CTR of its position and each keyword's search volume), trend the change since the last run, and attribute movement to specific keyword clusters.

## INPUTS
- `keywords` (REQUIRED): array of `{ term, volume?, cluster? }`.
- `competitors` (OPTIONAL string[]): domains to always include in the report (others still ranked).
- `previous` (OPTIONAL): prior run's `sov` map + `snapshots`, for trend deltas.
- `top_n` (OPTIONAL, default 10).

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
2. Endpoint (SerpApi shape): `GET https://serpapi.com/search.json?engine=google&q={term}&num={top_n}&api_key={key}`.

## EXPECTED TOOL CALLS
- Run `scripts/organic_sov.py --keywords keywords.json [--previous previous.json]`.
- One SERP fetch per keyword; record domain→position for the top N.

## PROCEDURE (deterministic)
STEP 1 — For each keyword, fetch the SERP; the keyword's total weight = `volume` (or 1). Each ranking domain earns `weight * ctr(position)` from `references/ctr_curve.json`.
STEP 2 — SoV per domain = `sum(earned) / sum(total available weight)`. (Available weight per keyword = `volume`, so SoV is comparable across domains and totals ≤ 1 minus the un-captured tail.)
STEP 3 — TREND (if `previous`): `sov_delta = current − previous` per domain.
STEP 4 — ATTRIBUTION: for the biggest movers, diff per-cluster earned weight vs last run → which clusters drove the change.
STEP 5 — EMIT the SoV leaderboard, per-competitor detail, cluster attribution for movers, and the current `snapshots` (domain→position per keyword) for persistence.

## RATE LIMITS & ERROR HANDLING
- SERP `429`/quota → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`; return the partial SoV computed so far and carry forward the previous snapshots for unfetched keywords.
- `5xx`/timeout retry ≤3 then reuse the previous snapshot for that keyword (note `stale=true`) so trend continuity holds.
- Concurrency ≤ 3.

## MISSING / INSUFFICIENT DATA
- IF a keyword lacks `volume` THEN weight = 1 and note `volume_basis="uniform"` for the run (SoV then reflects position coverage, not demand).
- First run has no trend (`baseline=true`).
- Never let one huge-volume keyword silently dominate without disclosure; report the top-weighted keywords.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshots` MUST be persisted for the next run.

## FILES
- `scripts/organic_sov.py` — SERP fetch, CTR/volume-weighted SoV, trend + cluster attribution.
- `references/ctr_curve.json` — position→CTR weights.
- `references/output.schema.json` — output contract.
