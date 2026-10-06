---
name: site-crawl-data-extractor
description: Crawls a website (from start URLs, its sitemap, or a URL list), politely and within robots.txt, and extracts the same fields from every page into one CSV or JSON Lines table. Built-in fields cover title, meta description, H1, canonical, robots, dates, author, word count, schema types, link and image counts and a text excerpt. Custom fields can be a regular expression, a JSON-LD property path, a meta tag, or the first element with a given tag, class or id, plus the page's HTML tables. Use when the user wants to scrape or crawl a site for structured data, extract product, article or listing details across many pages, build a content inventory, or pull competitor page data into a spreadsheet.
metadata:
  title: Site Crawl Data Extractor
  category: integrations
---

# Site Crawl Data Extractor

AGENT ROLE: Autonomous extraction agent. Turn the user's question into field definitions, crawl the right pages, emit the JSON in `references/output.schema.json` plus the full table file, then answer from the data.

## OBJECTIVE
Get the same facts out of many pages into one table, reproducibly, without a paid crawler: a content inventory, product prices from JSON-LD, article dates and authors, or any value a pattern can find.

## INPUTS
- `start` (REQUIRED unless `--urls-file`, via `--start`, repeatable): start URLs. The first one's host is the site crawled.
- `sitemap` (OPTIONAL flag): also seed from the site's sitemaps (robots.txt first, then `/sitemap.xml`).
- `urls_file` (OPTIONAL): extract exactly the listed URLs, one per line, without following links.
- `include`, `exclude` (OPTIONAL, repeatable): regular expressions on the URL path, such as `--include ^/blog/` or `--exclude /tag/`.
- `field` (OPTIONAL, repeatable): `name=kind:argument`:
  - `regex:PATTERN`: the first match (group 1 if present) in the page's visible text;
  - `rawregex:PATTERN`: the same, in the raw HTML;
  - `jsonld:Type.path`: a JSON-LD property, for example `Product.offers.price` or `*.datePublished`;
  - `meta:name`: a meta tag's content, by name or property;
  - `tag:h2`, `class:price`, `id:sku`: the text of the first matching element.
- `tables` (OPTIONAL flag): up to 5 HTML tables per page, up to 50 rows each (in JSON Lines output).
- `max_pages` (OPTIONAL, default 200), `delay` (OPTIONAL, default 0.5 seconds), `text_chars` (OPTIONAL, default 600), `keep_query` (OPTIONAL).
- `out` (OPTIONAL): a `.csv` or `.jsonl` file for every row. The JSON on stdout shows the first 25.

## DATA SOURCES (no keys needed)
The site's pages, robots.txt and sitemaps, fetched as `seoskills-crawl-extractor/1.0`.

## EXPECTED TOOL CALLS
- Run `scripts/crawl_extract.py --start https://example.com/blog/ --include ^/blog/ --field "author=jsonld:Article.author.name" --out articles.csv`.
- One request per page, one after another, at least `delay` apart.

## PROCEDURE (deterministic)
STEP 1: DESIGN the fields from the user's question. Prefer `jsonld` paths and `meta` tags, which are stable, over `regex` on text. Test them on two or three pages with `--urls-file` before a big crawl.
STEP 2: CRAWL breadth-first from the start URLs (and sitemap URLs with `--sitemap`).
- Stay on the start host, apply the include and exclude patterns, and skip files and query-string URLs (unless `--keep-query`).
- Check every URL against its host's robots.txt.
- Wait `delay` seconds between requests, or the robots.txt `Crawl-delay` when that is longer.
STEP 3: EXTRACT each page:
- the built-in fields: title, meta description, H1, canonical, robots (meta and `X-Robots-Tag`), lang, published and modified dates (JSON-LD or meta), author, words in the main content, schema types, internal and external links, images, and an excerpt;
- the custom fields and tables.
A page that is not HTML or not 200 is kept as a row with its status.
STEP 4: REPORT the fill rate of each field (the share of pages where it was found) so a broken pattern shows at once, and write the table.
STEP 5: ANSWER the user's question from the table: counts, lists, gaps or comparisons. Quote values as extracted.

## RATE LIMITS & ERROR HANDLING
- One request at a time, at least 0.5 seconds apart (a larger robots.txt `Crawl-delay` wins), 20-second timeout per page.
- A bad `--field`, a regex that does not compile, a relative start URL or an `--out` that is not `.csv` or `.jsonl` STOPs `INPUT_INVALID`. An unreadable URL file STOPs `FILE_UNREADABLE`.
- A crawl that reaches `--max-pages` says how many URLs were still queued.

## MISSING / INSUFFICIENT DATA
- The crawler does not run JavaScript. IF every page has under 50 words THEN a note says the site probably renders with JavaScript, and the values must come from a rendering tool or the site's API.
- Fill rates under 100% mean the pattern is missing or different on some pages. Inspect those rows before trusting totals.
- Respect each site's terms and robots.txt, and never collect personal data the user has no right to process. Keep crawls to what the question needs.

## OUTPUT
One JSON object per `references/output.schema.json`, the full table in `--out`, and the answer to the user's question with the numbers from the table.

## FILES
- `scripts/crawl_extract.py`: robots-aware breadth-first crawler, field extractors (regex, JSON-LD path, meta, tag, class, id, tables) and CSV or JSON Lines writer.
- `references/output.schema.json`: output contract.
