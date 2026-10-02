---
name: heading-structure-auditor
description: Crawls each page and reconstructs its H1–H6 tree to check for a single H1, no skipped levels, and keyword-relevant, non-generic headings, returning the corrected outline per page. Use when the user wants a heading/hierarchy audit, semantic-structure check, or to fix H1 and outline problems at scale.
metadata:
  title: Heading Structure Auditor
  category: on-page-seo
---

# Heading Structure Auditor

AGENT ROLE: Autonomous on-page structure agent. Extract each page's heading tree, validate it against the outline rules, and emit the JSON in `references/output.schema.json`. Report structural facts, not stylistic opinions.

## OBJECTIVE
For each page, verify a clean heading hierarchy — exactly one H1, no skipped levels, descriptive headings — and return the specific violations plus a corrected outline.

## INPUTS
- `urls` (REQUIRED string[]) OR `sitemap_url`.
- `target_keywords` (OPTIONAL): map `url → primary_keyword` to check H1/heading relevance.
- `max_pages` (OPTIONAL, default 2000).

## AUTHENTICATION / RUNTIME
- No API key. Keyless HTTPS GET, UA `seoskills-heading-audit/1.0`, honor robots.

## EXPECTED TOOL CALLS
- Run `scripts/heading_audit.py --urls a,b,c` (or `--sitemap {url}`).
- Per URL: GET; extract `<h1>`–`<h6>` in document order with their text.

## PROCEDURE (deterministic, per page)
STEP 1 — EXTRACT the ordered list of headings `[{level, text}]` from the rendered `<body>` (ignore headings inside `<nav>`/`<header>`/`<footer>` where detectable).
STEP 2 — VALIDATE and collect issues with codes:
  - `NO_H1` / `MULTIPLE_H1`: zero or >1 H1.
  - `LEVEL_SKIP`: a heading jumps more than one level below its predecessor (e.g., H2→H4).
  - `EMPTY_HEADING`: a heading with no text.
  - `GENERIC_HEADING`: heading matches a generic list ("read more", "introduction", "click here", "untitled").
  - `H1_KEYWORD_MISSING` (only if `target_keywords`): the primary keyword tokens are absent from the H1.
STEP 3 — OUTLINE: build the nested outline tree; mark where it breaks.
STEP 4 — SCORE `structure_score` (0–100) by deducting per issue; classify `pass | needs_work | fail`.
STEP 5 — EMIT per page: issues, the extracted outline, and a suggested corrected level sequence.

## RATE LIMITS & ERROR HANDLING
- Crawl politeness: ≤ 5 concurrent, ≥ 150ms per host. Timeout 12s.
- Per-URL fetch failure (non-200, robots, timeout) → `{url, status:"unreachable"}`, continue; never abort the batch.
- HARD cap at `max_pages` (`hit_cap=true`).

## MISSING / INSUFFICIENT DATA
- IF a page has zero headings THEN `NO_H1` + `no_headings=true` (a real finding).
- Nav/breadcrumb headings can inflate counts — exclude by container when the markup allows; when it does not, note `heading_source="body_best_effort"`.
- Never rewrite heading copy; only report structure and suggest the corrected level sequence.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/heading_audit.py` — heading extraction, hierarchy validation, scoring.
- `references/output.schema.json` — output contract.
