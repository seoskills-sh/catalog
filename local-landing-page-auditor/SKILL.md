---
name: Multi-Location Landing Page Auditor
description: Crawls every location and store-locator page and validates LocalBusiness schema, embedded map, NAP parity with GBP, and content uniqueness across locations, flagging thin or duplicated pages, missing schema fields, and crawl issues. Use when the user has many location pages and wants a programmatic local landing-page audit at scale.
category: local-seo
---

# Multi-Location Landing Page Auditor

AGENT ROLE: Autonomous local-page-audit agent. Evaluate each location page against the local on-page checklist, measure cross-page duplication, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Score every location landing page for local SEO completeness — valid `LocalBusiness` schema, embedded map, unique content, NAP presence — and flag the pages that are thin, duplicated, or missing critical elements, prioritized by location value.

## INPUTS
- `location_pages` (REQUIRED string[]) OR `store_locator_url` (crawl to discover them).
- `gbp_nap` (OPTIONAL): map of `page_url → { name, address, phone }` (canonical GBP NAP) for parity checks.
- `page_value` (OPTIONAL): map of `page_url → weight` (e.g., revenue/traffic) for prioritization.
- `min_unique_words` (OPTIONAL, default 150): unique-content floor.

## AUTHENTICATION / RUNTIME
- No API key. Keyless HTTPS GET, UA `seoskills-local-audit/1.0`, honor robots.

## EXPECTED TOOL CALLS
- Run `scripts/local_page_audit.py --pages pages.json [--gbp gbp.json]`.
- Per page: GET; parse JSON-LD/microdata, detect map embeds, extract main text; then compute cross-page shingled duplication.

## PROCEDURE (deterministic, per page)
STEP 1 — FETCH + PARSE.
STEP 2 — SCHEMA: require a `LocalBusiness` (or subtype) JSON-LD with `name`, `address` (full `PostalAddress`), `telephone`, `geo` or `hasMap`, `openingHours`. Record each missing required field.
STEP 3 — MAP: detect an embedded map (`google.com/maps/embed`, `<iframe ... maps>`, or `geo`+static-map). `has_map = bool`.
STEP 4 — NAP PARITY (if `gbp_nap`): compare on-page NAP (schema-first) to GBP canonical → `nap_parity: match|mismatch|missing`.
STEP 5 — UNIQUENESS: compute 5-gram shingle sets per page; `max_similarity` = highest Jaccard vs any OTHER location page. `duplicate` IF `max_similarity >= 0.8`; `thin` IF unique word count < `min_unique_words`.
STEP 6 — SCORE `page_score` (0–100) from schema completeness, map, NAP parity, uniqueness; classify `pass | needs_work | fail`. Prioritize failures by `page_value`. EMIT.

## RATE LIMITS & ERROR HANDLING
- Crawl politeness: ≤ 5 concurrent, ≥ 150ms per host. Timeout 12s. HARD cap at 5000 discovered pages (`hit_cap=true`).
- Per-page fetch failure (non-200, robots) → `status="unreachable"`, continue.
- IF `store_locator_url` yields 0 links THEN STOP `error.code="NO_PAGES_FOUND"`.

## MISSING / INSUFFICIENT DATA
- IF `gbp_nap` absent THEN skip parity and set `nap_parity="not_checked"` (never a false mismatch).
- A page with no extractable main text → `thin=true` with `text_extractable=false` rather than a uniqueness score.
- Duplication is symmetric — report the specific most-similar sibling URL as evidence, not just a number.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/local_page_audit.py` — crawl/parse, schema + map + NAP checks, shingled duplication, scoring.
- `references/output.schema.json` — output contract.
