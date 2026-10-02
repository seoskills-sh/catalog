---
name: canonicalization-auditor
description: Crawls a site to collect each URL's rel=canonical, hreflang set, redirect target, robots directives, and sitemap membership, then detects the contradictions that make Google discard a canonical hint — canonical-to-noindex, canonical-to-redirect, canonical chains, noindex-plus-canonical on one page, canonicalized-away-but-in-sitemap, duplicate clusters with no chosen canonical, and non-reciprocal or invalid hreflang. Use when the user has duplicate-content or canonical problems, hreflang errors, "Google chose a different canonical", or conflicting index signals to untangle.
metadata:
  title: Canonicalization Auditor
  category: audit
---

# Canonicalization Auditor

AGENT ROLE: Autonomous canonical-signal agent. Crawl and probe URLs, record every consolidation signal, detect cross-signal contradictions deterministically, decide the corrected canonical, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Explain, for every problematic URL, why Google would ignore its canonical hint and what the canonical should be — by reconciling the page's own canonical, robots, hreflang, redirect behaviour, and sitemap membership with the actual state of every URL those signals point to.

## INPUTS
- `start` (REQUIRED unless `--url-list`): crawl root; bounded BFS by `max_urls`/`max_depth`.
- `url_list` (OPTIONAL): a `.json` array or newline file of URLs to audit instead of crawling.
- `sitemap` (OPTIONAL): sitemap URL/file for membership checks (recurses indexes, gunzips).
- `max_urls` (OPTIONAL, default 2000), `max_depth` (OPTIONAL, default 6), `max_probe` (OPTIONAL, default 1000): budget for on-demand fetches of canonical/hreflang targets not already crawled, `timeout` (OPTIONAL, default 15s).

## AUTHENTICATION (none)
- No API key. All requests use UA `seoskills-canonical-auditor/1.0`. Canonical/hreflang **targets** are fetched with redirects DISABLED so a redirecting or non-200 target is observed as such (not silently followed).

## EXPECTED TOOL CALLS
- Run `scripts/canonical_audit.py --start https://example.com [--sitemap {url}] [--max-urls 2000]`.
- Per URL: one GET (redirects disabled) capturing status, `Location`, `X-Robots-Tag`, and the parsed `<link rel=canonical>`, `<link rel=alternate hreflang>`, `<meta robots>`, body text hash, and links. Targets are probed on demand within `max_probe`.

## PROCEDURE (deterministic)
STEP 1 — COLLECT: crawl/probe each URL into a record `{status, location, canonical(abs), robots(meta+header), hreflang[(lang,href)], content_hash, in_sitemap}`.
STEP 2 — CANONICAL CONFLICTS (only for 200 pages whose canonical ≠ self): probe the target and classify — `CANONICAL_TO_REDIRECT` (target 3xx → corrected = resolved destination), `CANONICAL_TO_NON200` (target 4xx/5xx → corrected = self), `CANONICAL_TO_NOINDEX` (target noindex → corrected = self), `CANONICAL_CHAIN` (target's own canonical differs → corrected = end of chain), `CANONICAL_TARGET_UNVERIFIED` (budget exhausted).
STEP 3 — SAME-PAGE CONTRADICTIONS: `NOINDEX_WITH_CANONICAL` (page is noindex and points canonical elsewhere); `CANONICALIZED_BUT_IN_SITEMAP` (page canonicalizes away yet is in the sitemap).
STEP 4 — HREFLANG: for each alternate, flag `MISSING_XDEFAULT`, `INVALID_LANG_CODE`, `ALTERNATE_NON_200`, `NON_RECIPROCAL` (alternate does not link back), `ALTERNATE_NON_CANONICAL` (alternate is noindex or canonicalizes elsewhere) → grouped as `HREFLANG_CONFLICT`.
STEP 5 — DUPLICATES: group 200 pages by normalised body-text hash; a cluster with ≥2 self-canonical members is `DUPLICATE_NO_CANONICAL` with a `suggested_canonical` (shortest URL). EMIT summary, conflicts (with `corrected_canonical` + `why`), and duplicate clusters.

## RATE LIMITS & ERROR HANDLING
- Sequential crawl (concurrency 1) paced 100ms/request; per-request retry ≤3 with backoff `2^attempt` on network error; a persistently failing URL is recorded with `status=0`, batch continues.
- Redirect resolution for a canonical target walks ≤5 hops through the probe cache.
- `max_probe` caps extra target fetches; when exhausted, unresolved targets are flagged `CANONICAL_TARGET_UNVERIFIED` / left un-checked rather than assumed correct.

## MISSING / INSUFFICIENT DATA
- No `--sitemap` → `CANONICALIZED_BUT_IN_SITEMAP` cannot fire and is skipped (not guessed).
- Probe budget exhausted (`probe_budget_exhausted=true`) → affected targets are reported as unverified; NEVER assume a target is 200/indexable/reciprocal without fetching it.
- A page with no canonical tag is treated as self-canonical (Google's default), not flagged as an error on its own.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/canonical_audit.py` — crawl/probe, signal collection, conflict detection, hreflang reciprocity, duplicate clustering.
- `references/output.schema.json` — output contract.
