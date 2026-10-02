---
name: site-migration-auditor
description: Compares a pre-migration crawl against the live post-migration site to verify that every indexable old URL 301-redirects exactly once to a live, self-canonical equivalent, catching chains, loops, 302s, and 404s while diffing titles, canonicals, structured data, and indexability to score parity and rank traffic-at-risk URLs by GSC clicks. Use when the user is replatforming, launched a migration, sees post-launch traffic drops, or wants to validate redirects and SEO parity around a cutover.
metadata:
  title: Site Migration Auditor
  category: audit
---

# Site Migration Auditor

AGENT ROLE: Autonomous migration-integrity agent. Walk each old URL's redirect chain (never auto-follow), fetch the live destination, diff SEO signals against the pre-migration record, score parity, join clicks to size traffic-at-risk, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
For a replatform, prove that each old indexable URL lands on the correct new page via a single clean 301, and that the new page preserves the title, canonical, structured data, and indexability that earned its traffic. Quantify what is broken and rank fixes by clicks at stake.

## INPUTS
- `old_crawl` (REQUIRED): JSON list of pre-migration page records `{url, title, canonical, jsonld_types|structured_data, internal_links}` (a crawler export). `results`-wrapped exports are unwrapped automatically.
- `new_crawl` (OPTIONAL): a post-migration crawl in the same shape; when present the destination signals are read from it instead of re-fetched.
- `gsc` (OPTIONAL): local JSON map `url -> clicks` for traffic weighting.
- `site` (OPTIONAL): GSC property (`sc-domain:example.com` or `https://example.com/`) to fetch clicks live instead of a file.
- `start` / `end` (OPTIONAL): ISO dates for the live GSC pull. Default = last 28 complete days ending 3 days ago (GSC lag).
- `max_urls` (OPTIONAL, default 5000), `max_hops` (OPTIONAL, default 10), `risk_threshold` (OPTIONAL, default 70): parity score below which a URL is at-risk, `concurrency` (OPTIONAL, default 6), `timeout` (OPTIONAL, default 15s).

## AUTHENTICATION (Search Console API — only if fetching clicks live)
1. Crawling needs NO credentials. IF neither `--gsc` nor `--site` is given THEN run without traffic weighting (`has_traffic_data=false`).
2. IF `--site` is given THEN REQUIRE env `GSC_OAUTH_TOKEN` (an OAuth 2.0 access token with scope `https://www.googleapis.com/auth/webmasters.readonly`). IF unset THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
3. Endpoint: `POST https://searchconsole.googleapis.com/webmasters/v3/sites/{urlEncoded site}/searchAnalytics/query` with `dimensions=["page"]`, `dataState="final"`. IF `403` THEN STOP `error.code="AUTH_NO_SITE_ACCESS"`.

## EXPECTED TOOL CALLS
- Run `scripts/migration_audit.py --old-crawl old.json [--new-crawl new.json] [--gsc clicks.json | --site {property}] [--max-urls 5000] [--risk-threshold 70]`.
- Per old URL: manual per-hop HEAD walk (redirects disabled), then one GET of the final 200 URL when its signals are not already in `--new-crawl`.

## PROCEDURE (deterministic)
STEP 1 — WALK each old URL hop-by-hop with redirects DISABLED; record `{url, status, location}` per hop; resolve relative `Location`; stop on a non-3xx status, a repeat (LOOP), or `max_hops` (TOO_LONG).
STEP 2 — CLASSIFY the redirect into exactly one `redirect_class`: `OK_301` (single 301/308 → 200), `PERSISTED_200` (URL unchanged, still 200), `CHAIN` (≥2 hops), `TEMPORARY_REDIRECT` (302/303/307), `LOOP`, `BROKEN_404`, `REDIRECT_TO_404`, `REDIRECT_TO_ERROR`, `FETCH_FAILED`, `OTHER`. Note `PROTOCOL_DOWNGRADE` (https→http) as a flag.
STEP 3 — PARITY DIFF (only when the destination is 200): read the new page's title, canonical, meta robots, and JSON-LD `@type` set (from `--new-crawl` or a live GET parsed with `html.parser`). Start at 100 and subtract: fatal redirect → 0; CHAIN/TEMPORARY −15; title token-Jaccard < 0.6 → −15 (`TITLE_CHANGED`); canonical not self-referential → −15 (`CANONICAL_DRIFT`); pre-migration `@type` set not a subset of new → −15 (`STRUCTURED_DATA_LOST`); `noindex` on target → −20 (`NOINDEX_ON_TARGET`); internal links collapsed >60% → −10. Floor at 0.
STEP 4 — TRAFFIC-AT-RISK: join clicks by URL; a URL is at-risk when `parity_score < risk_threshold` OR the redirect is fatal. `clicks_at_risk = clicks` for at-risk URLs.
STEP 5 — EMIT the per-URL ledger, a redirect distribution, average parity, and the `at_risk` list sorted by `clicks_at_risk` then parity deficit.

## RATE LIMITS & ERROR HANDLING
- Per-hop HEAD (GET fallback on 405/501). On network error retry ≤3 with backoff `2^attempt`; persistent → `redirect_class="FETCH_FAILED"` for that URL, batch continues.
- Concurrency capped at `--concurrency` (default 6) worker threads across URLs.
- GSC `429`/`5xx` → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`.

## MISSING / INSUFFICIENT DATA
- No `--gsc`/`--site` → parity is still scored; `has_traffic_data=false` and every `clicks_at_risk=0` (ranking falls back to parity deficit). NEVER invent click counts.
- Destination 200 but signals unreadable → flag `TARGET_UNVERIFIABLE` (do not assume parity).
- `internal_links` diff only runs when both crawls carry integer counts; otherwise it is skipped, not guessed.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/migration_audit.py` — redirect walk, live signal fetch, parity diff, clicks join, risk ranking.
- `references/output.schema.json` — output contract.
