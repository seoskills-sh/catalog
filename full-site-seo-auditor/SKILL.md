---
name: full-site-seo-auditor
description: Crawls a website from its sitemap (or by following its links) and audits every page for crawlability, indexability, titles and meta descriptions, headings, images, content depth, structured data, HTTPS and mobile signals, plus robots.txt and the sitemap, then scores each area out of 100 and ranks the fixes. Use when the user wants a full SEO audit of a site, a technical and on-page health check, or to find out what is holding a site back in search.
metadata:
  title: Full-Site SEO Auditor
  category: audit
---

# Full-Site SEO Auditor

AGENT ROLE: Autonomous site-audit agent. Crawl the site, measure every page against the checks below, score each area, and emit the JSON in `references/output.schema.json`. Then explain the results to the user as a prioritized fix list, in plain language, starting with the critical and high items.

## OBJECTIVE
Answer "what is wrong with this site's SEO, and what should we fix first?" with measured findings rather than a checklist: every finding names the check, how many pages it affects, example URLs, and the fix.

## INPUTS
- `site` (REQUIRED via `--site`): the site root, for example `https://example.com`.
- `max_pages` (OPTIONAL, default 50): pages to audit. Raise it for bigger sites; the run takes roughly one second per four pages.
- `sitemap` (OPTIONAL): a sitemap URL to use instead of the one robots.txt lists.
- `psi` (OPTIONAL flag): add Google PageSpeed Insights (mobile) for the homepage.

## DATA SOURCES (no keys needed, except for the optional --psi)
1. The site itself: robots.txt, the XML sitemap (indexes are followed), and each page's HTML and response headers, fetched as `seoskills-full-site-auditor/1.0`.
2. `--psi` calls the PageSpeed Insights API, which needs env `PSI_API_KEY` (a free Google Cloud API key with the PageSpeed Insights API enabled; Google refuses keyless requests). IF the key is missing or the call fails THEN `psi.status="unavailable"` with the reason, and the audit continues.

## EXPECTED TOOL CALLS
- Run `scripts/full_site_audit.py --site https://example.com [--max-pages 50] [--psi]`.
- One request each for the homepage, robots.txt, the sitemap files and the `http://` homepage, then one per audited page (4 in parallel).

## PROCEDURE (deterministic)
STEP 1: FETCH the homepage, then robots.txt. Parse robots.txt with Google's matching rules (the longest matching rule wins; Allow wins a tie; `*` and `$` supported). Flag `ROBOTS_BLOCKS_SITE` when Googlebot may not fetch `/`.
STEP 2: DISCOVER pages from the sitemaps robots.txt lists (else `/sitemap.xml`), following sitemap indexes. IF there is no sitemap THEN crawl breadth-first from the homepage through internal links, and flag `SITEMAP_MISSING`.
STEP 3: AUDIT each page for status and redirect chain, robots.txt access, meta robots and `X-Robots-Tag`, canonical tags, title, meta description, H1s, images without alt, visible word count, internal links out, JSON-LD (parsed), HTTPS and mixed content, viewport, `lang`, response time and HTML size.
STEP 4: SITE-WIDE checks. Duplicate titles and meta descriptions; `http://` to `https://` redirect; pages no audited page links to (only when the whole sitemap was audited, so a partial crawl never reports false orphans).
STEP 5: SCORE seven areas with fixed weights (crawlability 20, indexability 20, on-page 25, content 15, structured data 5, security and mobile 10, performance 5). Each area loses `severity weight × share of pages affected` per issue (critical 1.0, high 0.6, medium 0.3, low 0.1; site-wide issues count as every page). The total is the sum, out of 100.
STEP 6: EMIT issues sorted by severity, then pages affected, each with its fix; up to five quick wins; and the per-page table.

## RATE LIMITS & ERROR HANDLING
- 4 requests in parallel by default (`--concurrency`, at most 8); a 0.15-second pause between pages when crawling without a sitemap; 15-second timeout per request.
- A page that times out or fails to connect is `UNREACHABLE`, not skipped. IF the homepage answers 401, 403, 429 or 503 THEN a warning says the site may block automated requests, since that would turn bot protection into false errors.
- IF the homepage cannot be reached at all THEN STOP `error.code="SITE_UNREACHABLE"`. An `--site` that is not an absolute URL STOPs `INPUT_INVALID`.

## MISSING / INSUFFICIENT DATA
- Findings cover the audited pages only; `pages_audited` and `sitemap.fully_audited` say how much of the site that was. Never extrapolate a count to pages that were not fetched.
- The checks read the HTML as served. Content that only appears after JavaScript runs is not seen; say so if the site is a client-rendered app.
- Core Web Vitals field data needs real Chrome users; for most small sites `psi.field_p75` is null. Never present lab numbers as field data.

## OUTPUT
One JSON object per `references/output.schema.json`. Then a short report for the user: the score, the three to five most important fixes with their page counts, and the quick wins.

## FILES
- `scripts/full_site_audit.py`: robots.txt matching, sitemap discovery, crawl, per-page and site-wide checks, area scoring, PageSpeed Insights.
- `references/output.schema.json`: output contract.
