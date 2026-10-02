---
name: app-keyword-rank-tracker
description: Records an app's keyword positions across the App Store (keyless iTunes Search) and Google Play (ASO rank API) and across locales, persisting history via --previous for velocity and volatility. Reports ranking movers, newly ranking and lost keywords, and per-keyword trend metrics — and returns an honest baseline on the first run instead of fabricated deltas. Use when the user wants daily app keyword rank tracking, ranking movers, or velocity and volatility across both stores.
metadata:
  title: App Keyword Rank Tracker
  category: aso
---

# App Keyword Rank Tracker

AGENT ROLE: Autonomous app rank-tracking agent. Snapshot keyword positions per store and locale, persist history, diff against the prior run, and emit the JSON in `references/output.schema.json`. Stateful via `--previous`.

## OBJECTIVE
Maintain a daily position history for a keyword set across the App Store and Google Play (and locales), and report what moved: improvers, decliners, newly ranking and lost keywords, plus per-keyword velocity (trend) and volatility (instability) — with a truthful baseline on the first run rather than invented movement.

## INPUTS
- `app_id` (App Store id) and/or `package` (Play package) — at least one REQUIRED.
- `keywords` (REQUIRED): local JSON array of keyword strings.
- `locales` (OPTIONAL, default `us`): comma-separated storefront/country codes.
- `stores` (OPTIONAL, default `appstore,play`).
- `previous` (OPTIONAL): the prior run's `snapshot` (history); absent on first run (baseline).
- `limit` (OPTIONAL, default 50): search depth scanned per keyword; also sets the out-of-results convention (`limit+1`).
- `min_move` (OPTIONAL, default 3): minimum position delta to list a keyword as a mover; also the volatility threshold.
- `max_keywords` (OPTIONAL, default 100), `history_cap` (OPTIONAL, default 30): cost/state guards.

## AUTHENTICATION (iTunes Search + ASO Rank API)
1. App Store ranks use the keyless iTunes Search API: `GET https://itunes.apple.com/search?term={kw}&country={locale}&entity=software&limit={limit}` — the app's 1-based index in the results is its rank.
2. Google Play ranks REQUIRE env `ASO_API_KEY`: `GET https://api.asokeyword.io/v1/rank?package={pkg}&term={kw}&country={locale}` with `Authorization: Bearer {ASO_API_KEY}`. IF `play` is requested with a `--package` and the key is unset THEN STOP `error.code="AUTH_MISSING_ASO_KEY"`. `401` → STOP `AUTH_EXPIRED`.

## EXPECTED TOOL CALLS
- Run `scripts/app_rank_tracker.py --app-id 6001112223 --package com.acme.budget --keywords keywords.json --locales us,gb [--previous prev.json]`.
- One request per (keyword × store × locale), capped by `--max-keywords`.

## PROCEDURE (deterministic)
STEP 1 — For each (keyword, store, locale) FETCH the current rank (App Store: scan search results for the app id; Play: ASO rank API). `not_found` when the app is absent within `--limit`.
STEP 2 — APPEND `{date, rank}` to that key's history (capped at `--history-cap`); carry forward the prior history on a fetch error and mark the key `stale` (never append a fabricated point).
STEP 3 — IF no `--previous` THEN `status="baseline"`: emit current positions only, no deltas.
STEP 4 — Else CLASSIFY per key: `newly_ranking` (was absent, now ranked), `lost` (was ranked, now not_found), `improved`/`declined` when `delta = prev_rank − cur_rank` clears `±min_move` (positive = moved up).
STEP 5 — TREND: `velocity = (oldest − newest) / (points − 1)` over history (positive = improving, not-ranked points substituted with `limit+1`); `volatility = population stddev` of the same series.
STEP 6 — EMIT movers, newly/lost lists, per-keyword positions with velocity/volatility, volatility leaders, and the full `snapshot` to persist.

## RATE LIMITS & ERROR HANDLING
- `429` on either store → backoff `2^attempt` (max 5); on exhaustion mark affected keys `stale`, carry prior history, and set top-level `status="partial"`.
- `5xx`/timeout retry ≤3 then treat as a fetch error (stale carry-forward).
- Concurrency ≤ 1, `time.sleep(0.3)` between fetches; total requests bounded by `--max_keywords × stores × locales`.

## MISSING / INSUFFICIENT DATA
- FIRST RUN is a baseline: ranks are recorded and `status="baseline"` with NO deltas — the tracker NEVER fabricates movement without a prior snapshot.
- A keyword out of the top `--limit` is `not_found` (rank null), treated as `limit+1` only for velocity/volatility continuity — disclosed via `out_of_results_convention`.
- Stale (unfetched) keys are excluded from movement classification and counted in `stale_count`; velocity/volatility need ≥2 history points or they report 0.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshot` MUST be persisted for the next run.

## FILES
- `scripts/app_rank_tracker.py` — dual-store rank fetch, history persistence, movers/velocity/volatility, honest baseline.
- `references/output.schema.json` — output contract.
