---
name: internal-anchor-auditor
description: Crawls the site and aggregates the internal anchor text pointing to each destination page, classifying anchors as exact-match, partial, branded, or generic and flagging over-optimization and generic overuse. Use when the user wants an internal-linking anchor audit, to fix over-optimized internal anchors, or improve anchor relevance at scale.
metadata:
  title: Internal Anchor Text Auditor
  category: on-page-seo
---

# Internal Anchor Text Auditor

AGENT ROLE: Autonomous internal-anchor agent. Build the internal link graph with anchor text, classify the anchor distribution per destination, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
For each destination page, report the distribution of internal anchor text pointing to it, flag over-optimized exact-match concentration and generic-anchor overuse, and recommend a healthier mix — plus surface pages with too few descriptive internal anchors.

## INPUTS
- `start_url` (REQUIRED): crawl seed; host defines scope.
- `target_keywords` (OPTIONAL): map `destination_url → primary_keyword` for exact/partial classification.
- `brand_terms` (OPTIONAL string[]): to classify branded anchors.
- `max_pages` (OPTIONAL, default 2000).
- `exact_match_ceiling` (OPTIONAL, default 0.5): exact-match ratio above which a destination is over-optimized.

## AUTHENTICATION / RUNTIME
- No API key. Keyless HTTPS GET, UA `seoskills-anchor-audit/1.0`, honor robots.

## EXPECTED TOOL CALLS
- Run `scripts/internal_anchors.py --start {url}`.
- BFS crawl (same host); for each internal `<a href>` capture `(source, destination, anchor_text)`.

## PROCEDURE (deterministic)
STEP 1 — CRAWL (bounded BFS); collect every internal link with its normalized anchor text (strip whitespace, images→alt).
STEP 2 — GROUP by destination URL; count inbound internal links + distinct anchors.
STEP 3 — CLASSIFY each anchor for a destination (using `target_keywords`/`brand_terms`):
  - `exact_match`: equals the destination's primary keyword.
  - `partial_match`: contains keyword tokens but not exact.
  - `branded`: contains a brand term.
  - `generic`: in the generic set ("click here", "read more", "here", "this page", "link").
  - `url_or_empty`: the anchor is a bare URL or empty.
  - `other`: descriptive but none of the above.
STEP 4 — FLAG per destination:
  - `OVER_OPTIMIZED` IF `exact_match_ratio > exact_match_ceiling` AND inbound ≥ 5.
  - `GENERIC_OVERUSE` IF `generic_ratio > 0.4`.
  - `EMPTY_OR_URL_ANCHORS` IF any `url_or_empty` anchors exist.
  - `THIN_INTERNAL_LINKS` IF inbound internal links < 2 (under-linked).
STEP 5 — EMIT per destination: the anchor distribution, flags, and a recommended target mix; sort by risk (over-optimized first, then under-linked).

## RATE LIMITS & ERROR HANDLING
- Crawl politeness: ≤ 5 concurrent, ≥ 200ms per host, obey robots `Crawl-delay`. HARD cap at `max_pages` (`hit_cap=true`).
- Per-page fetch failure → skip that source page's links; continue.
- Ignore `rel="nofollow"` internal links for ratio math but report their count separately.

## MISSING / INSUFFICIENT DATA
- IF `target_keywords` absent THEN exact/partial classification is skipped (only branded/generic/other) and `classification="no_keywords"` — do not guess intent.
- A destination with only 1 inbound internal link is `THIN_INTERNAL_LINKS`, not over-optimized, regardless of that anchor.
- Never claim over-optimization below the inbound-count floor (5); small samples are noise.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/internal_anchors.py` — crawler, anchor extraction, per-destination classification + flags.
- `references/output.schema.json` — output contract.
