---
name: app-keyword-rank-tracker
description: Records an app's keyword positions across the App Store (keyless iTunes Search) and Google Play (a rank CSV exported from your ASO tool) and across locales, persisting history via --previous for velocity and volatility. Reports ranking movers, newly ranking and lost keywords, and per-keyword trend metrics — and returns an honest baseline on the first run instead of fabricated deltas. Use when the user wants daily app keyword rank tracking, ranking movers, or velocity and volatility across both stores.
metadata:
  title: App Keyword Rank Tracker
  category: aso
---

# App Keyword Rank Tracker

AGENT ROLE: Autonomous app rank-tracking agent. Snapshot keyword positions per store and locale, persist history, diff against the prior run, and emit the JSON in `references/output.schema.json`. Stateful via `--previous`.

## OBJECTIVE
Maintain a daily position history for a keyword set across the App Store and Google Play (and locales), and report what moved: improvers, decliners, newly ranking and lost keywords, plus per-keyword velocity (trend) and volatility (instability) — with a truthful baseline on the first run rather than invented movement.

## INPUTS
- `app_id` (App Store id) and/or `play_ranks` (Google Play rank CSV, below): at least one REQUIRED.
- `keywords` (REQUIRED): local JSON array of keyword strings.
- `play_ranks` (OPTIONAL via `--play-ranks`): a CSV of today's Google Play ranks exported from your ASO tool, with keyword (`keyword`/`term`/`search term`/`query`), locale (`locale`/`country`/`storefront`/`market`) and rank (`rank`/`position`/`ranking`) columns. A blank, zero or non-numeric rank means not ranked; a keyword and locale missing from the file means no data this run.
- `package` (OPTIONAL): the Play package name, reported alongside the Play ranks.
- `locales` (OPTIONAL, default `us`): comma-separated storefront/country codes.
- `stores` (OPTIONAL, default `appstore,play`).
- `previous` (OPTIONAL): the prior run's `snapshot` (history); absent on first run (baseline).
- `limit` (OPTIONAL, default 50): search depth scanned per keyword; also sets the out-of-results convention (`limit+1`).
- `min_move` (OPTIONAL, default 3): minimum position delta to list a keyword as a mover; also the volatility threshold.
- `max_keywords` (OPTIONAL, default 100), `history_cap` (OPTIONAL, default 30): cost/state guards.

## DATA SOURCES (no keys needed)
1. App Store ranks use the keyless iTunes Search API: `GET https://itunes.apple.com/search?term={kw}&country={locale}&entity=software&limit={limit}`. The app's 1-based index in the results is its rank (an approximation of App Store search order).
2. Google Play has no free rank API, so its ranks come from `--play-ranks`. IF `play` is in `--stores` without `--play-ranks` THEN Play is skipped with a warning and the App Store is still tracked. IF neither an App Store id nor a Play rank file is given THEN STOP `error.code="INPUT_MISSING"`.

## EXPECTED TOOL CALLS
- Run `scripts/app_rank_tracker.py --app-id 6001112223 --keywords keywords.json --locales us,gb [--play-ranks play_ranks.csv --package com.acme.budget] [--previous prev.json]`.
- One iTunes Search request per (keyword × locale) for the App Store, capped by `--max-keywords`; Google Play reads the CSV and makes no requests.

## PROCEDURE (deterministic)
STEP 1 — For each (keyword, store, locale) GET the current rank (App Store: scan search results for the app id; Play: read the `--play-ranks` row). `not_found` when the app is absent within `--limit` (or the CSV row has no rank).
STEP 2 — APPEND `{date, rank}` to that key's history (capped at `--history-cap`); carry forward the prior history on a fetch error and mark the key `stale` (never append a fabricated point).
STEP 3 — IF no `--previous` THEN `status="baseline"`: emit current positions only, no deltas.
STEP 4 — Else CLASSIFY per key: `newly_ranking` (was absent, now ranked), `lost` (was ranked, now not_found), `improved`/`declined` when `delta = prev_rank − cur_rank` clears `±min_move` (positive = moved up).
STEP 5 — TREND: `velocity = (oldest − newest) / (points − 1)` over history (positive = improving, not-ranked points substituted with `limit+1`); `volatility = population stddev` of the same series.
STEP 6 — EMIT movers, newly/lost lists, per-keyword positions with velocity/volatility, volatility leaders, and the full `snapshot` to persist.

## RATE LIMITS & ERROR HANDLING
- Apple limits the iTunes Search API to about 20 calls a minute and answers `403` beyond it, so App Store lookups are paced at one per 3.1 seconds (100 keywords in one locale take about 5 minutes).
- `403`/`429` → mark the key `stale`, carry its prior history, and set top-level `status="partial"`; `5xx`/timeout retry ≤3, then the same stale carry-forward. A failed fetch never becomes a "not ranked" point.
- Concurrency ≤ 1; total App Store requests bounded by `--max_keywords × locales`.

## MISSING / INSUFFICIENT DATA
- FIRST RUN is a baseline: ranks are recorded and `status="baseline"` with NO deltas — the tracker NEVER fabricates movement without a prior snapshot.
- A keyword out of the top `--limit` is `not_found` (rank null), treated as `limit+1` only for velocity/volatility continuity — disclosed via `out_of_results_convention`.
- Stale (unfetched) keys are excluded from movement classification and counted in `stale_count`; velocity/volatility need ≥2 history points or they report 0.
- A Play keyword and locale absent from `--play-ranks` is stale (no data), not lost; Play ranks are only as fresh as the export, so export them on the same day you run the tracker.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshot` MUST be persisted for the next run.

## FILES
- `scripts/app_rank_tracker.py` — App Store rank fetch, Google Play rank import, history persistence, movers/velocity/volatility, honest baseline.
- `references/output.schema.json` — output contract.
