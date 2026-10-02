---
name: title-meta-optimizer
description: Crawls titles and meta descriptions, measures rendered pixel width for truncation, checks primary-keyword placement, and detects site-wide duplicates, then recommends rewrites that fit the pixel budget. Use when the user wants title-tag and meta-description optimization, truncation checks, or to find duplicate or missing metadata at scale.
metadata:
  title: Title & Meta Pixel Optimizer
  category: on-page-seo
---

# Title & Meta Pixel Optimizer

AGENT ROLE: Autonomous metadata-optimization agent. Measure each page's title/meta against pixel and keyword rules, detect duplication, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Find titles and meta descriptions that truncate in the SERP, bury or omit the primary keyword, or duplicate other pages — and recommend length-safe rewrites, prioritized by search demand when GSC is connected.

## INPUTS
- `urls` (REQUIRED string[]) OR `sitemap_url`.
- `target_keywords` (OPTIONAL): map `url → primary_keyword`.
- `title_px_budget` (OPTIONAL, default 580), `meta_px_budget` (OPTIONAL, default 920).
- `gsc_site` (OPTIONAL): a verified GSC property to prioritize by impressions.

## AUTHENTICATION / RUNTIME
- Crawl: keyless HTTPS GET, UA `seoskills-title-meta/1.0`, honor robots.
- `gsc_site` prioritization: REQUIRE `GOOGLE_APPLICATION_CREDENTIALS`/OAuth (scope `webmasters.readonly`). IF absent THEN skip prioritization and note `priority_basis="none"`.

## EXPECTED TOOL CALLS
- Run `scripts/title_meta.py --urls a,b,c [--gsc sc-domain:example.com]`.
- Per URL: GET; extract `<title>` and `<meta name="description">`. IF `gsc_site` set: one `searchAnalytics` call for page-level impressions.

## PROCEDURE (deterministic, per page)
STEP 1 — EXTRACT title + meta description text.
STEP 2 — PIXEL WIDTH: estimate rendered width using the per-character width table (proportional font approximation). Flag `TITLE_TRUNCATED` IF width > `title_px_budget`; `META_TRUNCATED` IF > `meta_px_budget`; `TITLE_TOO_SHORT` IF < 200px.
STEP 3 — PRESENCE: `TITLE_MISSING` / `META_MISSING` when absent or empty.
STEP 4 — KEYWORD (if provided): `KEYWORD_NOT_IN_TITLE`; `KEYWORD_NOT_FRONT_LOADED` IF present but after ~30 pixels of other text.
STEP 5 — DUPLICATES: after crawling all pages, group identical (normalized) titles and metas → `DUPLICATE_TITLE` / `DUPLICATE_META` with the sibling URLs.
STEP 6 — PRIORITIZE: `priority = impressions` (GSC) else page order; EMIT issues per page with a length-safe rewrite suggestion (trim/rephrase to fit the budget while keeping the keyword front-loaded).

## RATE LIMITS & ERROR HANDLING
- Crawl: ≤ 5 concurrent, ≥ 150ms per host, timeout 12s. Per-URL failure → `{url, status:"unreachable"}`, continue.
- GSC `429` → backoff `2^attempt` (max 4); on persistent failure disable prioritization (`priority_basis="none"`), keep the audit.

## MISSING / INSUFFICIENT DATA
- Pixel width is an ESTIMATE (real rendering varies by font/locale) — always return both `char_count` and `estimated_px`, and label estimates as such.
- A missing meta description is a finding, not an error (Google may generate one) — severity `warning`, not `critical`.
- Never invent a rewrite that drops the primary keyword to fit the budget; if it cannot fit, flag `IRREDUCIBLE` and return the best trimmed option.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/title_meta.py` — extraction, pixel-width estimation, keyword + duplicate checks, GSC prioritization.
- `references/output.schema.json` — output contract.
