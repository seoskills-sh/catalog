---
name: Image SEO Auditor
description: Crawls each page's images and checks alt-text quality, descriptive filenames, next-gen formats, explicit dimensions, and lazy-loading, flagging missing or duplicate alt text and oversized or legacy-format images. Use when the user wants an image SEO or accessibility audit, to fix missing alt text at scale, or improve image performance signals.
category: on-page-seo
---

# Image SEO Auditor

AGENT ROLE: Autonomous image-audit agent. Inspect each page's `<img>`/`<picture>` elements against the image-SEO checklist and emit the JSON in `references/output.schema.json`. Audit what the markup declares; do not download image bytes unless explicitly enabled.

## OBJECTIVE
For each page, report images with missing/duplicate/poor alt text, non-descriptive filenames, legacy formats, missing dimensions, and absent lazy-loading — the on-page image signals that affect SEO, accessibility, and Core Web Vitals.

## INPUTS
- `urls` (REQUIRED string[]) OR `sitemap_url`.
- `check_bytes` (OPTIONAL bool, default false): if true, HEAD each image to get `content-length` for oversize detection (adds requests).
- `oversize_kb` (OPTIONAL, default 200): byte threshold when `check_bytes`.
- `max_pages` (OPTIONAL, default 1000).

## AUTHENTICATION / RUNTIME
- No API key. Keyless HTTPS GET (page) + optional HEAD (image), UA `seoskills-image-audit/1.0`, honor robots.

## EXPECTED TOOL CALLS
- Run `scripts/image_audit.py --urls a,b,c [--check-bytes]`.
- Per page: GET; extract each `<img>` (src/srcset/alt/width/height/loading) and `<picture><source>` formats. IF `check_bytes`: HEAD each unique image URL.

## PROCEDURE (deterministic, per image)
STEP 1 — EXTRACT `{src, alt (present? empty? text), width, height, loading, format}`; resolve `<picture>`/`srcset` to the effective format set.
STEP 2 — CHECK and collect issues per image:
  - `ALT_MISSING`: no `alt` attribute (decorative images should have `alt=""` — an explicitly empty alt is OK, a missing attribute is not).
  - `ALT_EMPTY_NON_DECORATIVE`: `alt=""` on a likely-content image (large, in-content) — info-level.
  - `ALT_GENERIC`: alt like "image", "photo", filename, or a stuffed keyword string.
  - `NON_DESCRIPTIVE_FILENAME`: filename is `IMG_1234`, a hash, or numeric-only.
  - `LEGACY_FORMAT`: JPEG/PNG with no WebP/AVIF alternative in `<picture>`/`srcset`.
  - `MISSING_DIMENSIONS`: no width/height attributes (CLS risk).
  - `NO_LAZY_LOADING`: below-the-fold images without `loading="lazy"` (best-effort; first ~2 images exempt).
  - `OVERSIZE` (only if `check_bytes`): `content-length` > `oversize_kb`.
STEP 3 — DUPLICATE ALT: within a page, flag identical non-empty alt reused across different images.
STEP 4 — SCORE `image_score` per page; EMIT per-page image issues sorted by count, with a page-level summary.

## RATE LIMITS & ERROR HANDLING
- Crawl: ≤ 5 concurrent pages, ≥ 150ms per host. IF `check_bytes`: cap image HEADs at 5 concurrent, dedupe by URL. Timeout 10s.
- Per-page failure → `{url, status:"unreachable"}`; per-image HEAD failure → mark that image `size_unknown`, do not flag OVERSIZE.

## MISSING / INSUFFICIENT DATA
- Distinguish `alt` MISSING (no attribute — a real issue) from `alt=""` (intentional decorative — acceptable). Never conflate them.
- Above/below-the-fold is heuristic (DOM order) — mark lazy-loading findings `heuristic=true`.
- Do not judge alt-text quality beyond the generic/stuffed checks; alt copywriting is the author's job.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/image_audit.py` — image extraction, per-image checks, optional byte HEAD, scoring.
- `references/output.schema.json` — output contract.
