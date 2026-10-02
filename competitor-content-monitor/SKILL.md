---
name: competitor-content-monitor
description: Periodically diffs each competitor's sitemap and content to detect newly published and materially updated URLs, extracting topic and dates, then surfaces content velocity and the topics competitors are investing in. Use when the user wants to monitor competitor content, spot new competitor pages, or track publishing cadence over time.
metadata:
  title: Competitor Content Cadence Monitor
  category: competitor-analysis
---

# Competitor Content Cadence Monitor

AGENT ROLE: Autonomous competitive-content agent. Diff each competitor's current sitemap/content against the prior snapshot, classify new vs updated URLs, and emit the JSON in `references/output.schema.json`. Stateful across runs via `previous`.

## OBJECTIVE
For each competitor, detect which URLs are newly published or materially updated since the last run, extract their topic and publish/modified dates, and summarize content velocity and topical focus — a stateful intelligence feed.

## INPUTS
- `competitors` (REQUIRED): array of `{ domain, sitemap_url? }` (sitemap auto-discovered from robots.txt if omitted).
- `previous` (OPTIONAL): prior run's `snapshots` (competitor→{url→lastmod}); absent on first run (baseline).
- `sample_new_content` (OPTIONAL bool, default true): fetch new URLs to extract title/topic (adds requests).
- `max_urls_per_competitor` (OPTIONAL, default 5000).

## AUTHENTICATION / RUNTIME
- No API key. Keyless HTTPS GET, UA `seoskills-content-monitor/1.0`, honor robots.

## EXPECTED TOOL CALLS
- Run `scripts/competitor_content.py --competitors competitors.json [--previous previous.json]`.
- Per competitor: fetch robots.txt → sitemap index → child sitemaps; collect `{loc, lastmod}`. IF `sample_new_content`: GET each NEW url to extract `<title>`/`datePublished`.

## PROCEDURE (deterministic, per competitor)
STEP 1 — DISCOVER + PARSE the sitemap(s); collect `{url: lastmod}` (up to the cap).
STEP 2 — IF no `previous` snapshot THEN `status="baseline"`; return the snapshot only (no diff).
STEP 3 — DIFF vs previous:
  - `NEW`: url absent from the previous snapshot.
  - `UPDATED`: url present but `lastmod` newer than before.
  - `REMOVED`: url in previous but gone now (deindexed/pruned).
STEP 4 — ENRICH new URLs (if enabled): GET title + `datePublished`; classify topic from URL path + title tokens (bounded, best-effort).
STEP 5 — VELOCITY: `new_per_week` since last run; topic distribution of new content. EMIT per-competitor diffs + velocity, and the current `snapshots` for persistence.

## RATE LIMITS & ERROR HANDLING
- Politeness: ≤ 5 concurrent per host, ≥ 150ms spacing. Sitemap fetch failure → mark competitor `sitemap_unreachable`, carry forward its previous snapshot (do not report false NEW/REMOVED).
- Content sampling `429`/`503` from a host → stop sampling that host, keep the diff (mark `enrichment="partial"`).
- HARD cap `max_urls_per_competitor` (`hit_cap=true`).

## MISSING / INSUFFICIENT DATA
- IF a sitemap lacks `lastmod` THEN `UPDATED` cannot be detected for that competitor — only NEW/REMOVED — set `update_detection=false` and note it.
- First run per competitor is always baseline — expected, not an error.
- Never assert REMOVED from a single failed sitemap fetch; require a successful fetch showing the URL absent.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshots` MUST be persisted for the next run.

## FILES
- `scripts/competitor_content.py` — sitemap discovery/diff, new-content enrichment, velocity.
- `references/output.schema.json` — output contract.
