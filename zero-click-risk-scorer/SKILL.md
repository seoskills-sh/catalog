---
name: Zero-Click Risk Scorer
description: Analyzes each keyword's live SERP for click-absorbing features and scores its zero-click risk, then reprioritizes the keyword list toward terms that still send organic clicks. Use when the user wants to filter keywords worth targeting, spot high-volume zero-click traps, or understand how AI Overviews and featured snippets erode click potential.
category: keyword-research
---

# Zero-Click Risk Scorer

AGENT ROLE: Autonomous SERP-economics agent. Score each keyword's likelihood of resolving without a click and re-rank the list by remaining click opportunity. Emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Assign each keyword a 0–100 zero-click-risk score from the click-absorbing features present on its SERP, and compute an adjusted opportunity so high-volume keywords that no longer earn clicks are deprioritized.

## INPUTS
- `keywords` (REQUIRED): array of `{ term, volume? }` (volume optional; if absent, opportunity uses volume=1).
- `weights` (OPTIONAL): override `references/feature_weights.json`.
- `location`/`hl`/`device` (OPTIONAL): default "United States"/"en"/"mobile" (mobile shows more zero-click features).

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
2. Endpoint (SerpApi shape): `GET https://serpapi.com/search.json?engine=google&q={term}&device={device}&api_key={key}`. AI Overviews may need the follow-up `engine=google_ai_overview` expansion.

## EXPECTED TOOL CALLS
- Run `scripts/zero_click.py --keywords keywords.json`.
- Per keyword: fetch SERP; detect which weighted features are present and whether the top organic result sits above or below them.

## PROCEDURE (deterministic, per keyword)
STEP 1 — FETCH SERP; detect features from `feature_weights.json` (ai_overview, featured_snippet, knowledge_panel, people_also_ask, instant_answer/answer_box, inline_videos, local_pack, shopping).
STEP 2 — SCORE: `risk = min(100, sum(weight for each present feature))`. Cap at 100.
STEP 3 — POSITION MODIFIER: IF the first organic result is pushed below ≥2 blocks (features stacked above) THEN add the configured `push_down_penalty` (still capped at 100).
STEP 4 — CLASSIFY `low (<25) | moderate (25–55) | high (>55)`.
STEP 5 — ADJUSTED OPPORTUNITY: `adjusted = round(volume * (1 − risk/100))` — the estimated click-earning potential that survives the SERP.
STEP 6 — EMIT keywords sorted by `adjusted` desc (best real opportunities first); include the present-feature list per keyword as evidence.

## RATE LIMITS & ERROR HANDLING
- SERP API bills per search. `429`/quota → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` with `partial`. Never drop keywords silently.
- `5xx`/timeout retry ≤3 then mark keyword `status="serp_error"` (excluded from ranking, listed in `skipped`).
- Concurrency ≤ 3.

## MISSING / INSUFFICIENT DATA
- IF a feature block is present but its contents are gated by the SERP tier THEN still count its presence for risk (presence is the signal).
- IF `volume` absent THEN report `risk` but mark `adjusted` as `null` (cannot rank by opportunity without volume) and sort those by `risk` asc.
- Never invent volume.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/zero_click.py` — SERP fetch, feature detection, risk + adjusted-opportunity scoring.
- `references/feature_weights.json` — per-feature zero-click weights.
- `references/output.schema.json` — output contract.
