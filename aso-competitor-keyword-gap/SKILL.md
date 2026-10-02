---
name: aso-competitor-keyword-gap
description: Profiles a target app and its competitors on the App Store, deriving or fetching each app's indexed keyword set, category rank, and listing metadata, then diffs the term sets to expose keyword gaps, overlaps, and your unique terms. Stateful via --previous, it detects competitor title/subtitle/screenshot/description changes over time and reports the keyword rank shifts that follow. Use when the user wants competitor keyword gaps, ASO metadata-change monitoring, or to see which terms rivals rank for that you miss.
metadata:
  title: ASO Competitor Keyword Gap
  category: aso
---

# ASO Competitor Keyword Gap

AGENT ROLE: Autonomous competitive-ASO agent. Profile the target and its competitors, diff keyword sets, track metadata changes across runs, and emit the JSON in `references/output.schema.json`. Stateful via `--previous`.

## OBJECTIVE
Expose the keyword opportunities competitors capture that the target does not (gaps), the shared battleground (overlaps), and the target's differentiators (unique_to_you); and, across runs, surface each competitor's title/subtitle/screenshot/description changes together with the keyword rank movements that follow them (correlation, disclosed as such).

## INPUTS
- `target` (REQUIRED): the App Store numeric id of your app.
- `competitors` (REQUIRED): comma-separated competitor ids.
- `country` (OPTIONAL, default `us`): storefront.
- `source` (OPTIONAL enum `auto|aso|metadata`, default `auto`): keyword source strategy.
- `previous` (OPTIONAL): prior run's `snapshot` for change detection; absent on first run (baseline).
- `max_competitors` (OPTIONAL, default 15), `max_keywords` (OPTIONAL, default 100): cost guards.

## AUTHENTICATION (iTunes Lookup + ASO Keyword API)
1. Listing metadata uses the keyless iTunes Lookup API: `GET https://itunes.apple.com/lookup?id={ids}&country={c}&entity=software` (one batched call for target + competitors).
2. Indexed keyword ranks, subtitle, and category rank REQUIRE env `ASO_API_KEY`: `GET https://api.asokeyword.io/v1/app_keywords?app_id={id}&country={c}` with `Authorization: Bearer {ASO_API_KEY}`.
3. IF `--source aso` is forced and `ASO_API_KEY` is unset THEN STOP `error.code="AUTH_MISSING_ASO_KEY"`. IF the key is absent under `auto` THEN fall back to metadata-derived keyword sets (ranks unavailable). `401` → STOP `AUTH_EXPIRED`.

## EXPECTED TOOL CALLS
- Run `scripts/competitor_keyword_gap.py --target 6001112223 --competitors 111,222,333 --country us [--previous prev.json]`.
- One batched Lookup call; when `ASO_API_KEY` is set, one keyword call per app (capped by `--max-competitors`).

## PROCEDURE (deterministic)
STEP 1 — LOOKUP all app ids in one batched call; STOP if the target is not found for the storefront.
STEP 2 — PROFILE each app: title, subtitle (ASO/override only), primary genre, version, description hash, screenshot-set hash + count, category rank, and a keyword set — from the ASO API when keyed, else derived from title+description n-grams (`keyword_source` records which).
STEP 3 — UNION the competitor keyword sets, tracking how many competitors carry each term and their average rank.
STEP 4 — GAPS = competitor-union terms absent from the target, sorted by `competitors_ranking` desc then average competitor rank asc. OVERLAPS = target ∩ union. UNIQUE_TO_YOU = target − union.
STEP 5 — IF `--previous` present THEN per competitor DIFF metadata fields vs the prior snapshot (`metadata_changes`) and compute `rank_shifts` (`delta = old_rank − new_rank`, positive = improved; lost keywords get `new_rank=null`); ELSE mark each competitor `baseline`.
STEP 6 — EMIT gaps/overlaps/unique plus the full `snapshot` to persist for the next run.

## RATE LIMITS & ERROR HANDLING
- Lookup/ASO `429` → backoff `2^attempt` (max 5) then STOP `RATE_LIMITED` (Lookup) or degrade to derived keywords (ASO).
- `5xx`/timeout retry ≤3; a competitor that fails to resolve is recorded `status="not_found"` and skipped, not fabricated.
- Concurrency ≤ 1; `time.sleep(0.25)` between per-app keyword calls; competitor and keyword counts capped by the `--max-*` guards.

## MISSING / INSUFFICIENT DATA
- FIRST RUN (no `--previous`) is a baseline: `metadata_changes` and `rank_shifts` are empty and every competitor is flagged `baseline` — NEVER invent change history.
- Without `ASO_API_KEY`, keyword sets are a visible-metadata proxy (ranks `null`, subtitle absent unless supplied); this is disclosed via `keyword_source` and a warning.
- Rank shifts are reported as temporal correlation with metadata changes, never asserted as causation.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshot` MUST be persisted for the next run.

## FILES
- `scripts/competitor_keyword_gap.py` — Lookup/ASO profiler, keyword-set diff, stateful metadata-change + rank-shift detection.
- `references/output.schema.json` — output contract.
