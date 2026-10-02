---
name: link-reclamation-finder
description: Searches for brand mentions that do not link back and identifies backlinks pointing to the site's 404 or redirected URLs, returning reclamation opportunities ranked by referring authority. Use when the user wants unlinked-mention outreach, broken-backlink recovery, or to reclaim lost link equity.
metadata:
  title: Link Reclamation Finder
  category: link-building
---

# Link Reclamation Finder

AGENT ROLE: Autonomous link-reclamation agent. Find two classes of easy wins — unlinked mentions and broken inbound links — and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Produce a prioritized reclamation list: (1) pages that mention the brand but do not link to it (request a link), and (2) inbound backlinks pointing to the site's 404/redirected URLs (fix, redirect, or reach out) — recovering link equity that already almost exists.

## INPUTS
- `brand` (REQUIRED): `{ name, domain, aliases?: string[] }`.
- `find_unlinked_mentions` (OPTIONAL bool, default true): SERP mention discovery.
- `find_broken_backlinks` (OPTIONAL bool, default true): backlink-API broken-target discovery.
- `max_results` (OPTIONAL, default 200).

## AUTHENTICATION
- Unlinked mentions: REQUIRE `SERP_API_KEY` (mention discovery via search). Skipped with a note if absent.
- Broken backlinks: REQUIRE `DATAFORSEO_LOGIN`+`DATAFORSEO_PASSWORD` or `BACKLINK_API_KEY`. Skipped with a note if absent.
- IF BOTH sources are unavailable THEN STOP `error.code="NO_SOURCES_AVAILABLE"`.
- Status verification of link targets: keyless HTTPS HEAD/GET, UA `seoskills-reclamation/1.0`.

## EXPECTED TOOL CALLS
- Run `scripts/link_reclamation.py --brand brand.json`.
- Mentions: SERP for `"{brand.name}" -site:{brand.domain}`; fetch each result page and check whether it links to the brand domain.
- Broken: fetch the site's inbound backlinks whose `target` URL is on the brand domain, then HEAD each unique target to find 404/3xx.

## PROCEDURE (deterministic)
STEP 1 — UNLINKED MENTIONS (if enabled): run SERP mention queries; for each result page, GET and check for any `href` to `brand.domain`. IF the brand is named in the visible text but NOT linked THEN it is an unlinked mention (record the URL + referring authority proxy).
STEP 2 — BROKEN BACKLINKS (if enabled): fetch inbound backlinks pointing to `brand.domain`; collect distinct target URLs; HEAD each. IF `404`/`410` THEN `broken_target`; IF `3xx` to a different page THEN `redirected_target` (equity dilution). Group referring pages per broken target.
STEP 3 — SCORE `priority` by referring authority (and count for broken targets); EMIT two lists — `unlinked_mentions` and `broken_backlinks` — each sorted by priority desc, with a recommended action per item.

## RATE LIMITS & ERROR HANDLING
- SERP `429`/quota → backoff `2^attempt` (max 5); after that stop mention discovery with a note, return broken-backlink results.
- Backlink provider `402`/`429` → skip broken discovery with a note.
- Target HEAD checks: ≤ 5 concurrent, dedupe by URL, timeout 10s; a failed HEAD → `status_unknown` (not counted as broken).

## MISSING / INSUFFICIENT DATA
- A mention page that links via an alias/redirect to the brand still counts as LINKED (not an opportunity) — resolve one hop before deciding.
- Only assert `broken_target` on a confirmed 404/410 for a fully-loaded response; never on a timeout.
- Deduplicate mentions and broken targets; the same referring page appears once.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/link_reclamation.py` — mention discovery + link check, broken-backlink detection, prioritization.
- `references/output.schema.json` — output contract.
