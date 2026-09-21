---
name: GBP Review Velocity & Sentiment Monitor
description: Pulls reviews per location from the Google Business Profile API and computes review velocity, star trend, response rate, and NLP sentiment and theme extraction, flagging velocity drops, sentiment declines, and unanswered reviews. Use when the user wants review monitoring, reputation tracking, response-rate audits, or competitor review benchmarking across locations.
category: local-seo
---

# GBP Review Velocity & Sentiment Monitor

AGENT ROLE: Autonomous reputation-monitoring agent. Pull reviews per location, compute velocity/sentiment/response metrics, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Quantify each location's review health — how fast reviews arrive, the star trend, sentiment and recurring themes, and how many reviews go unanswered — and flag deteriorations that need attention.

## INPUTS
- `account_id` + `location_ids` (REQUIRED): GBP resources (`accounts/{a}/locations/{l}`).
- `window_days` (OPTIONAL, default 90): velocity + trend window.
- `sentiment_backend` (OPTIONAL enum `lexicon|openai`): default `lexicon` (keyless); `openai` if `OPENAI_API_KEY` set.
- `benchmark` (OPTIONAL): prior run's aggregates for trend deltas.

## AUTHENTICATION (Google Business Profile API)
1. REQUIRE OAuth token, scope `https://www.googleapis.com/auth/business.manage` (`GBP_OAUTH_TOKEN` or delegated SA).
   - IF absent THEN STOP `error.code="AUTH_MISSING_TOKEN"`; `401` → STOP `error.code="AUTH_EXPIRED"`.
2. Requires Google-approved Business Profile API access. `403 SERVICE_DISABLED`/`PERMISSION_DENIED` → STOP `error.code="API_ACCESS_NOT_APPROVED"`.
3. Reviews live on the v4 endpoint: `GET https://mybusiness.googleapis.com/v4/{account_id}/locations/{loc}/reviews` (paged via `pageToken`).
4. `openai` sentiment backend: `POST https://api.openai.com/v1/chat/completions` with `OPENAI_API_KEY`.

## EXPECTED TOOL CALLS
- Run `scripts/gbp_reviews.py --account accounts/1 --locations loc1,loc2 --window 90`.
- Per location: page all reviews in the window; each review has `starRating` (ONE..FIVE), `comment`, `createTime`, and `reviewReply` (present iff answered).

## PROCEDURE (deterministic, per location)
STEP 1 — FETCH reviews within `window_days` (page until `createTime` older than the window).
STEP 2 — VELOCITY: `reviews_per_week = count / (window_days/7)`; compare first-half vs second-half of the window to detect acceleration/deceleration.
STEP 3 — STAR TREND: mean star of older half vs recent half; `star_delta`.
STEP 4 — SENTIMENT: map `starRating` to polarity, refine with text sentiment (`lexicon` or `openai`); extract recurring THEMES from negative reviews (top noun phrases / lexicon categories: service, wait, price, cleanliness, staff).
STEP 5 — RESPONSE RATE: `answered / total`; `unanswered_recent` = unanswered reviews in the last 14 days (highest priority).
STEP 6 — FLAGS: `velocity_drop` (recent < 0.5× earlier), `sentiment_decline` (`star_delta <= -0.4`), `low_response_rate` (< 0.5). EMIT per-location metrics + flags, sorted worst-first.

## RATE LIMITS & ERROR HANDLING
- GBP v4 strict QPM. `429` → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` with `partial`.
- `openai` sentiment `429` → backoff (max 4); on persistent failure fall back to `lexicon` and set `sentiment_backend="lexicon"`.
- Per-location failure → mark `status="fetch_error"`, continue.

## MISSING / INSUFFICIENT DATA
- IF a location has < 5 reviews in the window THEN report raw counts but mark trend metrics `low_confidence` (too few to trend).
- Star rating is authoritative for polarity; if a review has no `comment`, sentiment = star-derived only (note `text_available=false`).
- Never invent themes; if no negative reviews, themes = [].

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/gbp_reviews.py` — GBP v4 reviews client, velocity/star/response metrics, sentiment + theme extraction.
- `references/output.schema.json` — output contract.
