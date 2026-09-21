---
name: On-Page Schema Coverage Auditor
description: Classifies each page's content type and compares the structured data it should carry against what is present and valid, flagging missing, incomplete, or invalid schema and the exact properties to add. Use when the user wants a structured-data coverage audit, rich-result eligibility check, or to find schema gaps across a site.
category: on-page-seo
---

# On-Page Schema Coverage Auditor

AGENT ROLE: Autonomous structured-data agent. Determine the schema each page SHOULD have, compare to what it HAS, validate required properties, and emit the JSON in `references/output.schema.json`. Audit coverage; do not generate the markup.

## OBJECTIVE
For each page, detect its content type, look up the recommended schema.org types and required properties in `references/content_type_schema.json`, and report exactly what structured data is missing, incomplete, or invalid — with rich-result eligibility impact.

## INPUTS
- `urls` (REQUIRED string[]) OR `sitemap_url`.
- `content_type_hints` (OPTIONAL): map `url → type` to override auto-classification.
- `max_pages` (OPTIONAL, default 2000).

## AUTHENTICATION / RUNTIME
- No API key. Keyless HTTPS GET, UA `seoskills-schema-audit/1.0`, honor robots.

## EXPECTED TOOL CALLS
- Run `scripts/schema_coverage.py --urls a,b,c`.
- Per URL: GET; parse all JSON-LD blocks and microdata; classify content type from on-page signals.

## PROCEDURE (deterministic, per page)
STEP 1 — CLASSIFY content type (article, product, faq, howto, localbusiness, recipe, event, breadcrumb-eligible, organization) from signals: URL pattern, `og:type`, presence of price/rating (product), Q&A structure (faq), step lists (howto), address/geo (local). Use `content_type_hints` when provided.
STEP 2 — RECOMMENDED: from `content_type_schema.json`, get the recommended `@type`(s) and their `required` + `recommended` properties for the classified type.
STEP 3 — PRESENT: parse existing JSON-LD/microdata; map to schema types found.
STEP 4 — DIFF + VALIDATE:
  - `SCHEMA_MISSING`: a recommended type is entirely absent.
  - `REQUIRED_PROPERTY_MISSING`: present type lacks a required property (e.g., Product without `offers`/`name`).
  - `INVALID_VALUE`: property present but malformed (e.g., `priceCurrency` not ISO-4217, `aggregateRating.ratingValue` out of range).
  - `RECOMMENDED_PROPERTY_MISSING`: info-level.
STEP 5 — ELIGIBILITY: map missing/invalid to lost rich-result types (review star, FAQ accordion, breadcrumb, sitelinks).
STEP 6 — SCORE `coverage_score` 0–100; EMIT per page with the exact properties to add.

## RATE LIMITS & ERROR HANDLING
- Crawl: ≤ 5 concurrent, ≥ 150ms per host, timeout 12s. Per-URL failure → `{url, status:"unreachable"}`.
- Malformed JSON-LD → capture as `INVALID_JSONLD` (a finding), do not crash the page.

## MISSING / INSUFFICIENT DATA
- Auto-classification can be wrong for ambiguous pages — always return `detected_type` + `classification_confidence`; low confidence → recommend Organization/WebPage only and note it.
- Never invent property values; report what is missing, not what to set them to (that is content, not audit).
- A page legitimately needing no special schema (e.g., a contact page) is `pass` with `recommended_types: []`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/schema_coverage.py` — content-type classification, JSON-LD/microdata parse, diff + validation.
- `references/content_type_schema.json` — recommended types + required/recommended properties + validators.
- `references/output.schema.json` — output contract.
