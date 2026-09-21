---
name: Hreflang Network Validator
description: Crawls a set of URLs, builds the full hreflang graph across every language and region variant, and validates bidirectional return tags, self-references, locale codes, and canonical/status consistency. Use when the user runs an international/multilingual site and asks about hreflang errors, wrong-language results, or return-tag problems.
category: technical-seo
---

# Hreflang Network Validator

AGENT ROLE: Autonomous internationalization-integrity agent. Build the hreflang cluster graph from the provided URLs (or sitemap) and report every broken edge per `references/output.schema.json`. Report facts, not opinions.

## OBJECTIVE
For a cluster of alternate-language pages, verify that hreflang annotations form a valid, fully reciprocal graph: every page references every alternate, every alternate references back, all locale codes are valid, all targets are 200/canonical.

## INPUTS
- `urls` (REQUIRED string[]) OR `sitemap_url` (REQUIRED if `urls` absent): entry points for the cluster.
- `source` (OPTIONAL enum `html|http_header|sitemap`): where to read hreflang. Default `html` (also parse HTTP `Link` headers).
- `expected_locales` (OPTIONAL string[]): if given, flag any cluster missing one of these.
- `max_urls` (OPTIONAL, default 2000).

## AUTHENTICATION / RUNTIME
- No API key. Plain HTTPS GETs with a descriptive UA `seoskills-hreflang/1.0`.
- Honor `robots.txt`; IF a URL is disallowed THEN skip with reason `robots_disallow`.

## EXPECTED TOOL CALLS
- Run `scripts/hreflang_validate.py --urls a,b,c` (or `--sitemap {url}`).
- Per URL, GET the document AND read the response's `Link:` header. Parse `<link rel="alternate" hreflang="xx-YY" href="...">` from `<head>` and headers.

## PROCEDURE (deterministic)
STEP 1 — COLLECT: for each URL capture `{status, canonical, declared: [{hreflang, href}]}`.
STEP 2 — BUILD GRAPH: nodes = fetched URLs; edge A→B for each declared alternate on A pointing to B.
STEP 3 — VALIDATE each node/edge, emit an issue with a `code` for every failure:
  - `RETURN_TAG_MISSING`: A→B exists but B→A does not (non-reciprocal).
  - `SELF_REFERENCE_MISSING`: page does not include an hreflang pointing to its own URL.
  - `INVALID_LOCALE`: hreflang not a valid ISO 639-1 language, optional ISO 3166-1 region, or `x-default`.
  - `TARGET_NOT_200`: alternate href resolves to non-200 (follow ≤3 redirects; report final).
  - `NON_CANONICAL_TARGET`: alternate href differs from that target's own canonical.
  - `MIXED_ABSOLUTE_RELATIVE` / `HTTP_TARGET_ON_HTTPS`: protocol/host inconsistencies.
  - `DUPLICATE_LOCALE`: two hrefs for the same hreflang value on one page.
  - `MISSING_EXPECTED_LOCALE`: cluster lacks a locale from `expected_locales`.
STEP 4 — GROUP issues by cluster (connected component) and emit; `valid=true` only if a cluster has zero issues.

## RATE LIMITS & ERROR HANDLING
- Concurrency ≤ 5 per host; global ≤ 20. Insert 200ms jitter between requests to the same host.
- IF a host returns `429`/`503` THEN backoff `2^attempt` (max 4); IF still failing THEN mark that URL `fetch_status="unavailable"` and downgrade dependent edges to `code="TARGET_UNVERIFIABLE"` (do not assert broken).
- Timeout 15s/request; a single failed fetch never aborts the run.

## MISSING / INSUFFICIENT DATA
- IF a URL declares zero hreflang THEN issue `NO_HREFLANG_DECLARED` (info-level) unless it is genuinely single-locale.
- IF `sitemap_url` yields 0 URLs THEN `status="empty_input"`, no error.
- Never assert `RETURN_TAG_MISSING` for a target that was `TARGET_UNVERIFIABLE`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/hreflang_validate.py` — crawler + graph validation.
- `references/output.schema.json` — output contract.
