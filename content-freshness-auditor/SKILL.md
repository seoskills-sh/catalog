---
name: content-freshness-auditor
description: Crawls content and detects staleness signals — old publish and modified dates, outdated years and statistics, deprecated references, and recency gaps versus ranking competitors — flagging pages and the exact outdated claims to update. Use when the user wants to find stale content, prioritize updates, or catch outdated facts hurting rankings.
metadata:
  title: Content Freshness Auditor
  category: content
---

# Content Freshness Auditor

AGENT ROLE: Autonomous freshness-audit agent. Detect concrete staleness signals per page, cross-check recency against the ranking set, and emit the JSON in `references/output.schema.json`. Flag specific, fixable claims — not vague "seems old".

## OBJECTIVE
Score each page's staleness from datable, verifiable signals and list the exact outdated elements (dates, years, statistics, deprecated terms) to update, prioritized by traffic and how far behind the SERP the page has fallen.

## INPUTS
- `urls` (REQUIRED string[]) OR `sitemap_url`.
- `check_serp_recency` (OPTIONAL bool, default false): compare against competitor freshness for the page's target query (needs SERP API).
- `current_year` (OPTIONAL): default the system year; used to detect stale year references.
- `traffic` (OPTIONAL): map of `url → monthly_clicks` for prioritization.

## AUTHENTICATION
- Page fetch: keyless HTTPS GET, UA `seoskills-freshness/1.0`, honor robots.
- `check_serp_recency`: REQUIRE env `SERP_API_KEY`. IF unset AND requested THEN skip that check and note `serp_recency="skipped_no_key"`.

## EXPECTED TOOL CALLS
- Run `scripts/freshness.py --urls a,b,c [--serp-recency]`.
- Per URL: GET; parse `datePublished`/`dateModified`, visible "last updated" text, and scan body for datable claims.

## PROCEDURE (deterministic, per URL)
STEP 1 — DATES: extract `datePublished`, `dateModified` (schema + visible). `age_days` from the most recent reliable date.
STEP 2 — STALE YEARS: find explicit year mentions (`\b20\d{2}\b`) especially in titles/H1/"best X 2023"; flag any year < `current_year` in a "current-year" context (title/intro) as `stale_year`.
STEP 3 — OUTDATED STATISTICS: detect numeric claims tied to a year ("as of 2022", "in 2021, X%") older than 2 years → `aging_statistic`.
STEP 4 — DEPRECATED REFERENCES: match a configurable list (e.g., "Universal Analytics", "AMP", "FLoC", named-old-versions) → `deprecated_reference`.
STEP 5 — SERP RECENCY (optional): for the page's likely query, fetch the top results and compare their freshness; IF competitors are materially newer THEN `behind_serp=true` with the median competitor age.
STEP 6 — SCORE `staleness = f(age_days, stale_year, aging_statistics, deprecated_refs, behind_serp)` → 0–100; classify `fresh (<30) | aging (30–60) | stale (>60)`.
STEP 7 — PRIORITIZE by `traffic * staleness/100`; EMIT pages sorted desc with the specific `signals` list per page.

## RATE LIMITS & ERROR HANDLING
- Crawl: ≤ 5 concurrent, ≥ 150ms per host. Per-URL fetch failure (timeout 12s, non-200, robots) → record `{url, status:"unreachable"}`, continue.
- SERP recency `429`/quota → backoff `2^attempt` (max 5); on persistent failure disable that check and set `serp_recency="rate_limited"`.

## MISSING / INSUFFICIENT DATA
- IF no reliable date is found THEN base `age_days` on `null` and rely on content signals only; set `date_confidence="low"`.
- A year reference is only `stale_year` in a currency context (title/intro/"best…2023"); a historical mention ("founded in 1998") is NOT stale — require the currency cue.
- Never claim a statistic is wrong — only that it is *aging* and should be re-verified.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/freshness.py` — page parse, staleness-signal detection, optional SERP-recency compare, scoring.
- `references/output.schema.json` — output contract.
