---
name: ASO Keyword Field Optimizer
description: Tokenizes an iOS app's title, subtitle, and 100-character keyword field, strips cross-field and stop-word waste, and greedily packs the highest-opportunity terms without repeating any word across fields or localizations. Returns an optimized keyword field per locale (≤100 chars, comma-separated, no spaces) plus the projected keyword-combination coverage gained. Use when the user wants to maximize iOS keyword coverage, fix wasted keyword-field characters, or expand indexable terms across localizations.
category: aso
---

# ASO Keyword Field Optimizer

AGENT ROLE: Autonomous iOS metadata optimizer. Tokenize the listing, remove indexing waste, pack the 100-char keyword field per locale without cross-field or cross-localization duplication, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Maximize the number of unique indexable words (and the search-phrase combinations Apple forms from them) across an app's title, subtitle, and hidden keyword field, for each localization, subject to the hard 100-character keyword-field limit and Apple's indexing rules (stop words ignored, duplicates wasted, words auto-combined across fields and localizations serving the same storefront).

## INPUTS
- `metadata` (REQUIRED): local JSON with `app_id` and `locales[]`, each `{locale, priority, title, subtitle, current_keywords, candidates[]}`; every candidate is `{term, volume?, difficulty?}`.
- `country` (OPTIONAL, default `us`): storefront used for `--enrich` volume lookups.
- `enrich` (OPTIONAL flag): look up missing `volume`/`difficulty` via the ASO keyword API.
- `max_lookups` (OPTIONAL, default 60): cap on billed enrichment calls across all locales.

## AUTHENTICATION (ASO Keyword Data API)
1. Offline tokenization/packing needs NO key; run it directly from `--metadata`.
2. `--enrich` REQUIRES env `ASO_API_KEY`. IF `--enrich` is set and `ASO_API_KEY` is unset THEN STOP `error.code="AUTH_MISSING_ASO_KEY"`.
3. Enrichment endpoint: `GET https://api.asokeyword.io/v1/keywords?term={term}&country={country}` with header `Authorization: Bearer {ASO_API_KEY}`, returning `{volume, difficulty}`.

## EXPECTED TOOL CALLS
- Run `scripts/keyword_field_optimizer.py --metadata listing.json [--enrich --country us --max-lookups 60]`.
- Offline: zero network calls. With `--enrich`: at most one lookup per candidate lacking a volume, capped by `--max-lookups`.

## PROCEDURE (deterministic)
STEP 1 — TOKENIZE each locale's title + subtitle into a `covered` set (lowercase, non-alphanumeric split, naive singularization, stop words removed). These words are already indexed; repeating them anywhere is waste.
STEP 2 — BUILD the candidate token pool from `candidates[]` plus the current keyword field (so existing terms are not silently lost). Assign each token the best `opportunity = volume / (difficulty + 10)` of any term it appears in; unknown-volume tokens keep `opportunity=null`.
STEP 3 — RANK tokens by opportunity desc, then first-seen order.
STEP 4 — PACK per locale in priority order (1 first). For each ranked token DROP it with a reason IF it is a stop word (`stopword`), already in title/subtitle (`in_title_subtitle`), or already placed in an earlier locale (`duplicate_across_locale`); ELSE place it IF `len(field) + len(token) + 1` ≤ 100, joining with commas and NO spaces; ELSE DROP `over_budget`.
STEP 5 — DEDUPE across localizations via a shared placed-set, so the second and third localizations extend coverage instead of repeating it.
STEP 6 — PROJECT coverage: `indexable = covered ∪ placed`; estimate combinations as `n + C(n,2)` (unigrams + unordered bigrams); report per-locale and listing-wide `combinations_gained`.

## RATE LIMITS & ERROR HANDLING
- Enrichment `429` → backoff `2^attempt` (max 5) then treat that term as unknown-volume and continue.
- `5xx`/timeout retry ≤3 then treat as unknown-volume.
- Concurrency ≤ 1 (serial lookups, `time.sleep(0.2)` pacing); enrichment calls capped by `--max_lookups` as a cost guard.

## MISSING / INSUFFICIENT DATA
- IF no candidate has volume data anywhere (and `--enrich` produced none) THEN `status="insufficient"`: tokens are still packed, but by input order, and a warning states ranking is not opportunity-driven — NEVER invent volumes.
- Brand/title words are reported in `brand_tokens_excluded` and never placed into the keyword field (already indexed by the title).
- The keyword field is always emitted with no spaces after commas; a locale whose field would exceed 100 chars is impossible by construction and is surfaced as a warning if it ever occurs.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/keyword_field_optimizer.py` — tokenizer, cross-field/locale dedupe, greedy 100-char packer, coverage projection.
- `references/output.schema.json` — output contract.
