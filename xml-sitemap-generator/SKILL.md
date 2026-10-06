---
name: xml-sitemap-generator
description: Generates and audits XML sitemaps. Audit mode finds a site's sitemaps (robots.txt, then common paths), follows sitemap indexes, checks every file against the sitemap protocol (50,000 URLs, 50 MB, namespace, URL format, duplicates, lastmod accuracy) and fetches a sample of listed URLs to catch errors, redirects, noindex, non-canonical and robots-blocked pages. Generate mode builds clean sitemap files from a crawl or a URL list, keeping only indexable, self-canonical pages. Use when the user wants an XML sitemap created, checked or fixed, sitemap errors from Search Console explained, or a sitemap index for a large site.
metadata:
  title: XML Sitemap Generator and Auditor
  category: technical-seo
---

# XML Sitemap Generator and Auditor

AGENT ROLE: Autonomous sitemap agent. Audit the existing sitemaps, or build new ones, emit the JSON in `references/output.schema.json`, and give the user the exact files and robots.txt line to ship.

## OBJECTIVE
A sitemap should list every page the site wants in search results and nothing else. Answer two questions: is the current sitemap valid and clean, and if not, produce one that is.

## INPUTS
- `mode` (REQUIRED, first argument): `audit` or `generate`.
- `site` (REQUIRED via `--site`): the site origin, for example `https://example.com`.
- audit: `sitemap` (OPTIONAL, check this URL instead of discovering), `sample` (OPTIONAL, default 200 listed URLs fetched), `max_files` (OPTIONAL, default 50).
- generate:
  - `out` (REQUIRED): an empty output folder; pass `--overwrite` to reuse one.
  - `urls` (OPTIONAL): a file of URLs, one per line, optionally followed by a comma and a lastmod date. Without it the site is crawled from the homepage.
  - `max_pages` (OPTIONAL, default 500).
  - `keep_query` (OPTIONAL): keep URLs with query strings.
  - `gzip` (OPTIONAL): write `.xml.gz` files.
  - `base` (OPTIONAL): the public folder URL the files will live in, if not the site root.

## DATA SOURCES (no keys needed)
The site's robots.txt, its sitemap files (gzip handled), and its pages, fetched as `seoskills-sitemap-tool/1.0`.

## EXPECTED TOOL CALLS
- `scripts/sitemap_tool.py audit --site https://example.com [--sample 200]`
- `scripts/sitemap_tool.py generate --site https://example.com --out sitemaps/ [--urls urls.txt] [--gzip]`

## PROCEDURE (deterministic)
STEP 1: DISCOVER. Read robots.txt `Sitemap:` lines; if there are none, try `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap-index.xml`, `/wp-sitemap.xml` and `/sitemap.xml.gz`.
STEP 2: AUDIT EVERY FILE: status (`SITEMAP_UNREACHABLE`), well-formed XML (`NOT_XML`), the sitemaps.org namespace (`WRONG_NAMESPACE`), at most 50,000 URLs and 50 MB uncompressed (`TOO_MANY_URLS`, `TOO_LARGE`), empty files, an index inside an index (`NESTED_INDEX`), and whether robots.txt lists the sitemap (`NOT_IN_ROBOTS`).
STEP 3: AUDIT EVERY LISTED URL without fetching it:
- absolute URL (`INVALID_URL`), same host (`CROSS_HOST`), same scheme (`SCHEME_MISMATCH`);
- under the sitemap's folder (`OUTSIDE_SITEMAP_PATH`: Google says a sitemap covers only URLs under its own folder unless it is submitted in Search Console);
- duplicates, length over 2,048 characters, unescaped characters;
- lastmod in W3C datetime format, not in the future, and not identical on every URL (`LASTMOD_ALL_SAME`, which usually means the generation time; Google uses lastmod only when it is consistently and verifiably accurate);
- priority and changefreq, reported as information because Google ignores them.
STEP 4: SAMPLE listed URLs evenly across the list and fetch them: `NON_200`, `NO_RESPONSE` (no answer even on a retry), `REDIRECTED`, `NOINDEX` (meta robots or `X-Robots-Tag`), `NON_CANONICAL` (the page names another URL as canonical) and `BLOCKED_BY_ROBOTS` (Googlebot rules). The clean share of the sample estimates how many listed URLs are clean. Report it as an estimate, never as a count.
STEP 5: GENERATE (generate mode):
- Crawl same-host links from the homepage, four at a time, obeying robots.txt and skipping files and query strings, or check the given URL list.
- Keep only URLs that return 200 directly, are not noindex, are self-canonical and are allowed by robots.txt.
- Set lastmod only from the page's own dateModified, article:modified_time, the list file, or a Last-Modified header more than a day old, since a current one is just the render time. Never invent a date.
- Split at 50,000 URLs with a sitemap index, and print the robots.txt line to add.

## RATE LIMITS & ERROR HANDLING
- 4 parallel requests, a 20-second timeout, and one retry for timeouts, 429 and 5xx before a URL counts as failed.
- audit with no sitemap found STOPs `NO_SITEMAP`. Suggest generate.
- generate without `--out` STOPs `INPUT_INVALID`, a non-empty folder `OUT_NOT_EMPTY`, an empty URL file `NO_URLS`, and no URL passing the checks `NOTHING_TO_INCLUDE` (with reasons).

## MISSING / INSUFFICIENT DATA
- A crawl finds only pages linked in the served HTML. Orphan pages and links added by JavaScript are missed, so a URL list from the CMS or database is more complete for large sites.
- The sample shows the share of problems, not every problem. Raise `--sample` (or set it to the URL count) before removing URLs in bulk.
- A page blocked to this tool but not to Googlebot shows as `NON_200`. Check a few in a browser first.

## OUTPUT
One JSON object per `references/output.schema.json`. Then, for an audit: the problems by severity with examples and the fix for each. For a build: the files written, URLs included and excluded by reason, and the robots.txt line. Remind the user to submit the sitemap or index in Search Console and Bing Webmaster Tools.

## FILES
- `scripts/sitemap_tool.py`: discovery, protocol checks, sampling, crawling, robots.txt matching, lastmod rules and file writing.
- `references/output.schema.json`: output contract.
