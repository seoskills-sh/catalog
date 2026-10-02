---
name: product-feed-seo-optimizer
description: Audits a Google Merchant product feed for title quality, attribute completeness, and GTIN/identifier validity using the real GS1 mod-10 check digit, generates recommended Product JSON-LD, cross-checks Merchant Center disapprovals through the Content API for Shopping, and rewrites titles to a demand-informed pattern. It returns a per-product diff plus schema fixes that serve both organic search and Shopping surfaces. Use when the user wants to optimize a product feed, validate GTINs, fix Merchant Center disapprovals, or rewrite product titles for organic and Shopping.
metadata:
  title: Product Feed SEO Optimizer
  category: programmatic-seo
---

# Product Feed SEO Optimizer

AGENT ROLE: Autonomous product-feed agent. Validate identifiers and attributes, score and rewrite titles against a demand-informed pattern, generate Product schema, join Merchant Center disapprovals, and emit the JSON in `references/output.schema.json`. One diff that improves organic and Shopping together.

## OBJECTIVE
For every product in a feed, return: identifier validity (real GTIN check digit), attribute completeness, a title score with a rewritten demand-informed title (diff), recommended Product JSON-LD, and any Merchant Center disapproval — so the feed ranks in Shopping and the landing pages win rich results.

## INPUTS
- `feed` (REQUIRED via `--feed`): a Google Merchant product feed as TSV/CSV (id, title, description, link, image_link, availability, price, brand, gtin, mpn, condition, color, size, ...) or JSON array of product objects. Format is auto-detected.
- `volumes` (OPTIONAL via `--volumes`): JSON map of term -> monthly volume; drives demand-informed title front-loading.
- `title_pattern` (OPTIONAL, default `"{brand} {title} {color} {size}"`): tokens `{brand} {title} {color} {size} {material} {gender} {category}`.
- `title_max` (OPTIONAL, default 150): Merchant title limit; `title_optimal` (OPTIONAL, default 70): organic visible length.
- `merchant_id` (OPTIONAL via `--merchant-id`): when set, disapprovals are pulled from the Content API for Shopping.
- `max_products` (OPTIONAL, default 100000) and `max_status_pages` (OPTIONAL, default 40): cost guards.

## AUTHENTICATION (Content API for Shopping)
1. The feed file is local and needs no credentials.
2. IF `--merchant-id` is passed THEN REQUIRE env `CONTENT_API_ACCESS_TOKEN` (OAuth bearer, `content` scope). IF unset THEN STOP `error.code="AUTH_MISSING_CONTENT_API"`. IF the token is rejected THEN STOP `error.code="AUTH_CONTENT_API_FORBIDDEN"`. Calls `productstatuses.list`.

## EXPECTED TOOL CALLS
- Run `scripts/product_feed_optimizer.py --feed feed.tsv [--volumes volumes.json] [--merchant-id 1234567] [--title-pattern "{brand} {title} {color} {size}"]`.
- Zero network calls in the offline path; paginated `productstatuses.list` (250/page) only when `--merchant-id` is set.

## PROCEDURE (deterministic, per product)
STEP 1 — IDENTIFIER: strip non-digits from `gtin`; validate length in {8,12,13,14} and the GS1 mod-10 check digit. Status = `gtin_valid` / `invalid_gtin` / `brand_mpn` / `identifier_exempt` (when `identifier_exists=false`) / `missing_identifier`.
STEP 2 — ATTRIBUTES: check the required Merchant set (id, title, description, link, image_link, availability, price, condition, gtin-or-mpn); add color/size/gender/age_group for apparel categories; compute coverage and the missing list.
STEP 3 — TITLE AUDIT: penalize over-limit length, over-optimal length (info), promotional ALL-CAPS, promotional phrases, brand not front-loaded, and keyword stuffing; produce a 0-100 title score.
STEP 4 — REWRITE: compose a title from `title_pattern`; IF `volumes` given THEN front-load the highest-volume phrase the product actually matches; truncate at a word boundary to `title_max`. Emit old vs new and `title_changed`.
STEP 5 — SCHEMA: generate recommended Product JSON-LD (name, brand, sku, mpn, valid gtin, image, description, offers{price, priceCurrency, availability, itemCondition, url}).
STEP 6 — DISAPPROVALS: IF `--merchant-id` THEN map each product id to its disapproved item-level issues from the Content API.
STEP 7 — EMIT products sorted worst-coverage-first with feed-level rollups.

## RATE LIMITS & ERROR HANDLING
- Content API `429` -> backoff `2^attempt` (max 5) then STOP `RATE_LIMITED`; `5xx` retry <=3 then STOP `REQUEST_FAILED`; pages are fetched sequentially with pacing (concurrency 1) and capped at `--max-status-pages`.
- `401`/`403` from the Content API STOP `AUTH_CONTENT_API_FORBIDDEN`.
- An empty or unparseable feed STOPs `error.code="EMPTY_FEED"`.

## MISSING / INSUFFICIENT DATA
- WITHOUT `--volumes` titles are rewritten structurally only (`demand_informed=false`, `demand_lead_keyword=null`); no volume is invented and no keyword is front-loaded on a guess.
- WITHOUT `--merchant-id` disapprovals are not fetched (`disapprovals_source="none"`); the audit never claims a product is approved or disapproved.
- `schema_status="recommended"` reflects that JSON-LD is generated from feed data; the skill does not assert the markup is live on the page unless that is separately verified.
- A GTIN that is absent is `missing_identifier` (or `brand_mpn`/`identifier_exempt`), never silently treated as valid; a present-but-wrong GTIN is `invalid_gtin`.

## OUTPUT
One JSON object per `references/output.schema.json`.

## FILES
- `scripts/product_feed_optimizer.py` — feed parsing, GS1 GTIN validation, attribute + title audit, demand-informed title rewrite, Product JSON-LD generation, Content API disapproval join.
- `references/output.schema.json` — output contract.
