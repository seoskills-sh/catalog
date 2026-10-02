---
name: nap-consistency-crawler
description: Crawls the site's location pages and major citation directories to extract each location's name, address, and phone, then normalizes and diffs them to flag inconsistencies, formatting drift, and missing citations. Use when the user manages multiple locations and needs NAP consistency auditing, citation cleanup, or local-listing accuracy checks.
metadata:
  title: Multi-Location NAP Consistency Crawler
  category: local-seo
---

# Multi-Location NAP Consistency Crawler

AGENT ROLE: Autonomous NAP-integrity agent. Extract each location's Name/Address/Phone from every source, normalize, diff against the canonical record, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
For each business location, establish a canonical NAP and detect every place (own site pages, citation directories) where it differs — even by formatting — plus flag directories where the location is missing.

## INPUTS
- `locations` (REQUIRED): array of `{ id, canonical: { name, address, phone }, page_urls: string[] }`.
- `citation_urls` (OPTIONAL): map of `location_id → [directory listing URLs]` to check (Yelp, BBB, Apple Maps, industry directories).
- `strict_phone` (OPTIONAL bool, default false): if true, formatting differences in phone count as mismatches; default normalizes to digits.

## AUTHENTICATION / RUNTIME
- No API key. Keyless HTTPS GET, UA `seoskills-nap/1.0`, honor robots. Some directories block bots — treat blocks as `unverifiable`, not `mismatch`.

## EXPECTED TOOL CALLS
- Run `scripts/nap_crawl.py --locations locations.json [--citations citations.json]`.
- Per source URL: GET; extract NAP from `LocalBusiness`/`PostalAddress` JSON-LD first, then microdata, then visible-text heuristics.

## PROCEDURE (deterministic, per location)
STEP 1 — CANONICAL: normalize the supplied canonical NAP (see normalization below).
STEP 2 — For each `page_url` and `citation_url`: fetch, extract NAP (schema → microdata → regex fallback), normalize.
STEP 3 — NORMALIZE: name → lowercase, strip legal suffixes/punctuation; address → USPS-style abbreviations (Street→St, Suite→Ste), collapse whitespace; phone → digits only (unless `strict_phone`).
STEP 4 — DIFF each source's normalized NAP vs canonical; classify per field `match | mismatch | missing`. A source with any field mismatch = `inconsistent`.
STEP 5 — MISSING CITATIONS: any `citation_url` that returns no detectable listing for the location → `missing_citation`.
STEP 6 — SCORE `consistency = matched_fields / total_checked_fields` per location; EMIT inconsistencies grouped by location, each with the exact source URL and the field-level before/after.

## RATE LIMITS & ERROR HANDLING
- Politeness: ≤ 3 concurrent per host, ≥ 250ms spacing. Timeout 15s.
- IF a source returns `403`/`429`/CAPTCHA THEN mark that source `unverifiable` (NOT a mismatch) and continue — never assert inconsistency from a blocked fetch.
- Retry transient `5xx` ≤ 2.

## MISSING / INSUFFICIENT DATA
- IF no NAP can be extracted from a source THEN `extraction="failed"` for that source (distinct from a real mismatch).
- Formatting-only differences (e.g., "Suite 200" vs "Ste 200") normalize to a match unless the raw strings are requested; always return both `normalized_match` and the raw values.
- Never guess an address; only report what was extracted.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/nap_crawl.py` — multi-source NAP extraction, normalization, diff, scoring.
- `references/output.schema.json` — output contract.
