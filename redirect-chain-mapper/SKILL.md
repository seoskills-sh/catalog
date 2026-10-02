---
name: redirect-chain-mapper
description: Follows every redirect hop for a list of URLs to map full chains, loops, and mixed 301/302 usage, then flags chains longer than one hop, redirects to non-200 endpoints, and conflicts between the final URL and its canonical tag. Use when the user audits redirects, site migrations, redirect chains/loops, or lost link equity.
metadata:
  title: Redirect Chain Mapper
  category: technical-seo
---

# Redirect Chain Mapper

AGENT ROLE: Autonomous redirect-integrity agent. Manually walk each URL's redirect chain (never auto-follow), record every hop, classify problems, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Produce the exact hop-by-hop redirect path for each input URL, and flag the issues that leak link equity or waste crawl: multi-hop chains, loops, temporary redirects that should be permanent, redirects ending on errors, and final-URL/canonical mismatches.

## INPUTS
- `urls` (REQUIRED string[]): URLs to trace (typically old/migrated URLs).
- `max_hops` (OPTIONAL, default 10): loop/over-length guard.
- `check_canonical` (OPTIONAL bool, default true): GET the final 200 URL and compare its canonical.
- `expected_permanent` (OPTIONAL bool, default true): treat 302/307 in a settling chain as a warning.

## AUTHENTICATION / RUNTIME
- No API key. Issue HTTP requests with redirect-following DISABLED so each hop is observed individually. UA `seoskills-redirect-mapper/1.0`.
- Prefer `HEAD`; IF a hop returns `405`/`501` for HEAD THEN retry that hop with `GET`.

## EXPECTED TOOL CALLS
- Run `scripts/redirect_map.py --urls a,b,c`.
- Per URL: loop requesting the current URL, reading `status` + `Location`, resolving relative `Location` against the current URL, appending a hop, until a non-3xx status or `max_hops`.

## PROCEDURE (deterministic, per URL)
STEP 1 — WALK: start at the input URL. At each step record `{url, status, location}`. Resolve `Location` (may be relative). Advance to it.
STEP 2 — TERMINATE when: status is non-3xx (final), OR a URL repeats (LOOP), OR hop count > `max_hops` (TOO_LONG).
STEP 3 — CLASSIFY (collect all that apply):
  - `OK_DIRECT`: 0 hops, final 200.
  - `CHAIN`: hops ≥ 2 before final 200 → equity/crawl waste; recommend pointing the source directly at the final URL.
  - `LOOP`: a URL repeats → broken.
  - `TOO_LONG`: exceeded `max_hops` → treat as loop-like.
  - `TEMPORARY_REDIRECT`: any 302/303/307 in a chain that appears permanent → should likely be 301/308.
  - `MIXED_STATUS`: chain mixes permanent and temporary codes.
  - `ENDS_NON_200`: final status ≥ 400 (redirect to a dead page).
  - `PROTOCOL_DOWNGRADE`: an https→http hop.
  - `CANONICAL_MISMATCH` (if check_canonical): final 200 URL's canonical ≠ final URL.
STEP 4 — EMIT one record per input URL with the full `hops` array, `final_url`, `final_status`, `hop_count`, and `issues`.

## RATE LIMITS & ERROR HANDLING
- Concurrency ≤ 8 across hosts; ≤ 3 per host with 150ms spacing.
- IF a hop returns `429`/`503` THEN backoff `2^attempt` (max 3) on that hop; IF persistent THEN terminate that URL's walk with `issues:["FETCH_FAILED"]` and whatever hops were collected.
- Network/timeout (10s) on a hop → record `status:0` for that hop and stop that URL; do not abort the batch.

## MISSING / INSUFFICIENT DATA
- IF `Location` header is missing on a 3xx THEN terminate with `issue="MALFORMED_REDIRECT"`.
- Never assume a chain's intent; `TEMPORARY_REDIRECT` is a warning, not an error, unless `expected_permanent=true`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/redirect_map.py` — manual per-hop tracer with classification.
- `references/output.schema.json` — output contract.
