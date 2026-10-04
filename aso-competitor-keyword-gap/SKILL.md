---
name: aso-competitor-keyword-gap
description: Profiles a target app and its competitors on the App Store, deriving each app's keyword set from its listing (or reading it from your ASO tool's export), measuring App Store keyword ranks and category rank from Apple's free APIs, then diffs the term sets to expose keyword gaps, overlaps, and your unique terms. Stateful via --previous, it detects competitor title/screenshot/description changes over time and reports the keyword rank shifts that follow. Use when the user wants competitor keyword gaps, ASO metadata-change monitoring, or to see which terms rivals rank for that you miss.
metadata:
  title: ASO Competitor Keyword Gap
  category: aso
---

# ASO Competitor Keyword Gap

AGENT ROLE: Autonomous competitive-ASO agent. Profile the target and its competitors, measure ranks, diff keyword sets, track metadata changes across runs, and emit the JSON in `references/output.schema.json`. Stateful via `--previous`.

## OBJECTIVE
Expose the keyword opportunities competitors capture that the target does not (gaps), the shared battleground (overlaps), and the target's differentiators (unique_to_you); and, across runs, surface each competitor's title/screenshot/description changes together with the keyword rank movements that follow them (correlation, disclosed as such).

## INPUTS
- `target` (REQUIRED): the App Store numeric id of your app.
- `competitors` (REQUIRED): comma-separated competitor ids.
- `country` (OPTIONAL, default `us`): storefront.
- `source` (OPTIONAL enum `auto|metadata`, default `auto`): `auto` adds measured App Store ranks and category ranks; `metadata` skips both (one Lookup call only).
- `keywords_file` (OPTIONAL via `--keywords-file`): a CSV exported from your ASO tool with each app's ranked keywords: app id (`app_id`/`app id`/`app`/`id`/`track id`), keyword (`keyword`/`term`/`search term`/`query`) and, optionally, rank (`rank`/`position`/`ranking`) columns. Apps in the file use its keyword sets instead of derived ones.
- `rank_terms` (OPTIONAL, default 30): terms measured with iTunes Search, about 3 seconds each; 0 skips. `rank_limit` (OPTIONAL, default 100, max 200): results scanned per term.
- `previous` (OPTIONAL): prior run's `snapshot` for change detection; absent on first run (baseline).
- `max_competitors` (OPTIONAL, default 15), `max_keywords` (OPTIONAL, default 100): cost guards.

## DATA SOURCES (no keys needed)
1. Listing metadata uses the keyless iTunes Lookup API: `GET https://itunes.apple.com/lookup?id={ids}&country={c}&entity=software` (one batched call for target + competitors).
2. Keyword ranks use the keyless iTunes Search API: `GET https://itunes.apple.com/search?term={term}&country={c}&entity=software&limit={rank_limit}`. An app's 1-based position in the results is its rank (an approximation of App Store search order).
3. Category rank uses Apple's top-chart feed for the app's primary genre: `GET https://itunes.apple.com/{c}/rss/topfreeapplications/limit=100/genre={genre_id}/json` (`toppaidapplications` for paid apps).
4. Apple's free APIs do not expose subtitles or the hidden keyword field, so `subtitle` is null and derived keyword sets come from the title and description. For full indexed keyword sets, pass `--keywords-file`.

## EXPECTED TOOL CALLS
- Run `scripts/competitor_keyword_gap.py --target 6001112223 --competitors 111,222,333 --country us [--keywords-file ranks.csv] [--previous prev.json]`.
- One batched Lookup call, one chart call per distinct genre, and up to `--rank-terms` Search calls paced at one per 3.1 seconds (30 terms take about 90 seconds).

## PROCEDURE (deterministic)
STEP 1 — LOOKUP all app ids in one batched call; STOP if the target is not found for the storefront.
STEP 2 — PROFILE each app: title, primary genre, version, description hash, screenshot-set hash + count, category rank, and a keyword set, from `--keywords-file` when the app is in it, else derived from title+description n-grams (`keyword_source` records which).
STEP 3 — MEASURE ranks for the top `--rank-terms` terms (those carried by the most competitors first) with iTunes Search. For a derived set the measurement is the truth: an app in the results gets the term with its rank, an app absent from them loses it. A file-sourced set only gains ranks it lacked. A failed search changes nothing.
STEP 4 — UNION the competitor keyword sets, tracking how many competitors carry each term and their average rank.
STEP 5 — GAPS = competitor-union terms absent from the target, sorted by `competitors_ranking` desc then average competitor rank asc. OVERLAPS = target ∩ union. UNIQUE_TO_YOU = target − union.
STEP 6 — IF `--previous` present THEN per competitor DIFF metadata fields vs the prior snapshot (`metadata_changes`) and compute `rank_shifts` (`delta = old_rank − new_rank`, positive = improved; lost keywords get `new_rank=null`); ELSE mark each competitor `baseline`.
STEP 7 — EMIT gaps/overlaps/unique plus the full `snapshot` to persist for the next run.

## RATE LIMITS & ERROR HANDLING
- Apple limits the iTunes Search API to about 20 calls a minute and answers `403` beyond it, so Search calls are paced at one per 3.1 seconds. A `403`/`429` on Lookup STOPs `RATE_LIMITED`; on Search it stops rank measurement early, keeps the ranks measured so far, and says so in `warnings`.
- `5xx`/timeout retry ≤3; a competitor that fails to resolve is recorded `status="not_found"` and skipped, not fabricated; a failed chart call leaves `category_rank` null.
- Concurrency ≤ 1; competitor and keyword counts capped by the `--max-*` guards and `--rank-terms`.

## MISSING / INSUFFICIENT DATA
- FIRST RUN (no `--previous`) is a baseline: `metadata_changes` and `rank_shifts` are empty and every competitor is flagged `baseline` — NEVER invent change history.
- Derived keyword sets are a visible-metadata proxy; terms beyond `--rank-terms` keep `null` ranks. This is disclosed via `keyword_source`, `ranks_measured` and `warnings`.
- `category_rank` is null when the app is outside its genre's top 100; `subtitle` is always null (not in Apple's free APIs).
- Rank shifts are reported as temporal correlation with metadata changes, never asserted as causation.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshot` MUST be persisted for the next run.

## FILES
- `scripts/competitor_keyword_gap.py` — Lookup profiler, keyword-file import, iTunes Search rank measurement, top-chart category rank, keyword-set diff, stateful metadata-change + rank-shift detection.
- `references/output.schema.json` — output contract.
