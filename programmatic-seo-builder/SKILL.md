---
name: programmatic-seo-builder
description: Builds a programmatic SEO page set from a spreadsheet (CSV or JSON) and one page template, generating slugs, titles, meta descriptions, conditional sections, hub pages, related links, breadcrumbs and a sitemap, and holds back every page that fails a quality gate (missing data, duplicate slugs or titles, near-duplicate records, or mostly boilerplate text) before anything ships. Use when the user wants to create pages at scale, build location, comparison, integration, template or glossary pages from data, or check a programmatic template for thin content before launch.
metadata:
  title: Programmatic SEO Builder
  category: programmatic-seo
---

# Programmatic SEO Builder

AGENT ROLE: Autonomous page-generation agent. Help the user shape the data and the template, run the build, emit the JSON in `references/output.schema.json`, and publish only what passes the gates, in batches.

## OBJECTIVE
Turn one row of data into one useful page, at scale, without shipping thin or duplicate pages. Google's spam policies name doorway pages and scaled content abuse, meaning many pages made mainly to rank rather than to help. So every page has to carry facts of its own, and the build proves that before launch instead of hoping for it.

## INPUTS
- `data` (REQUIRED via `--data`): a CSV, or a JSON list of objects (or `{"rows": [...]}`), one row per page. Column names are lowercased, with spaces and symbols turned into underscores, so `Avg Price` becomes `avg_price`.
- `template` (REQUIRED via `--template`): a Markdown, MDX or HTML file whose front matter sets `slug` and `title` (required) and optionally `description`, `h1`, `hub` and `hub_title`.
- `out` (REQUIRED unless `--dry-run`): an empty output folder. Pass `--overwrite` to reuse one.
- `base_url` (OPTIONAL via `--base-url`): the site origin, for canonicals, breadcrumbs and `sitemap.xml`.
- `priority_column` (OPTIONAL): a numeric column such as monthly search volume that orders the rollout.
- Gates (OPTIONAL): `--min-unique` (default 0.30), `--warn-unique` (0.40), `--min-completeness` (0.60), `--min-words` (250, warning only), `--related` (4 links), `--batch-size` (50).

## TEMPLATE SYNTAX
- `{column}` inserts a value. Filters: `{column|lower}`, `|upper`, `|title`, `|slug`. Values in `slug` and `hub` are always slugified.
- `{?column}...{/column}` shows the block only when the column has a value; `{!column}...{/column}` only when it is empty.
- `{related}` marks where the related links go. Without it they are added as a "Related" section at the end.
- Example front matter: `slug: /plumbers/{state}/{city}`, `title: "{service} in {city}, {state} | Brand"`, `hub: /plumbers/{state}`, `hub_title: Plumbers in {state}`.

## DATA SOURCES (no keys needed)
Local files only: the data file and the template. The script makes no network requests.

## EXPECTED TOOL CALLS
- `scripts/build_pages.py --data rows.csv --template page.md --dry-run` to measure.
- `scripts/build_pages.py --data rows.csv --template page.md --out build/ --base-url https://example.com [--priority-column volume]` to write.

## PROCEDURE (deterministic)
STEP 1: PLAN with the user: the search pattern (for example "[service] in [city]" or "[tool] vs [tool]") and one row per page. Each row needs facts that belong to that page alone, such as prices, local details, specs, reviews or measurements. A name swapped into the same paragraph is exactly what the gates hold back.
STEP 2: WRITE THE TEMPLATE so every section draws on a data column. Wrap optional sections in `{?column}` blocks, so a page with no reviews has no empty "Reviews" heading.
STEP 3: DRY RUN with `--dry-run`. Read `summary.holds_by_reason` and `uniqueness`. IF most pages are `BOILERPLATE_DOMINANT` THEN the template needs more data-driven sections, not more pages; revise it and repeat.
STEP 4: BUILD with `--out`. The script renders every row, then applies the gates:
- `MISSING_REQUIRED` (hold): a field used in the slug, title or H1 is empty.
- `SPARSE_DATA` (hold): less than `--min-completeness` of the body's fields have data.
- `DUPLICATE_SLUG`, `DUPLICATE_TITLE` (hold): an earlier row already has it; the first row keeps it.
- `NEAR_DUPLICATE_RECORD` (hold): more than 80% of the body fields match an earlier row. This runs on sets of up to 1,500 rows with at least 3 body fields.
- `BOILERPLATE_DOMINANT` (hold) and `LOW_UNIQUE_SHARE` (warning): the page's unique-text share is below `--min-unique` or `--warn-unique`. Any 3-word sequence that appears on at least half of the pages counts as boilerplate, and the share is the rest of the page's sequences. It needs at least 4 pages.
- Warnings: `SHORT_PAGE`, `TITLE_LONG` (over 60 characters), `NO_DESCRIPTION`, `DESCRIPTION_LONG` (over 160), `SLUG_LONG` (over 100).
Then it links each passing page to up to `--related` passing pages that share the most category values (the nearest rows fill any gap, so no page is a dead end), groups pages under hubs, and adds a BreadcrumbList (Home, hub, page) when `--base-url` is set.
STEP 5: REVIEW a sample of passing pages before publishing, at least 5 or about 5% of a large set, and read every held page's reason. Fix the data and rebuild, or leave those pages out.
STEP 6: ROLL OUT by `batch`, highest priority first. Publish one batch, submit its URLs, and check indexing in Search Console before releasing the next. Hubs listed in `summary.thin_hubs` have fewer than 3 pages; merge them into a broader hub or keep them out of the sitemap.

## FILES WRITTEN
`pages/` (passing pages, with front matter: title, description, h1, slug, canonical, hub, batch, breadcrumb_jsonld), `hold/` (held pages with `hold_reasons`, for review), `hubs/`, `manifest.json` (every page with its measures) and `sitemap.xml` (passing pages and hubs, when `--base-url` is set).

## RATE LIMITS & ERROR HANDLING
- No network, so no rate limits. Large sets are fine; the near-duplicate check skips sets over 1,500 rows and says so in `near_duplicate_check`.
- A template field the data does not have STOPs `TEMPLATE_UNKNOWN_FIELD` and lists the data's real columns. A missing front matter, slug or title STOPs `TEMPLATE_INVALID`.
- An unreadable file STOPs `DATA_UNREADABLE` or `TEMPLATE_UNREADABLE`; an empty one `NO_ROWS`. A non-empty `--out` without `--overwrite` STOPs `OUT_NOT_EMPTY`, so existing files are never replaced by accident.

## MISSING / INSUFFICIENT DATA
- The gates measure completeness and text overlap, not whether the data is true or the page is useful. The STEP 5 review is not optional.
- The script does not check search demand. A page nobody searches for is still a page to crawl. Validate demand first (Search Console or a keyword tool), pass it as `--priority-column`, and drop rows with no demand.
- Google publishes no numeric threshold for thin or duplicate content. The 30% and 40% unique-share defaults are deliberately cautious; explain any change to them to the user.
- Never fill missing data with invented facts to get a page through a gate. Leave it held.

## OUTPUT
One JSON object per `references/output.schema.json`, listing up to 100 pages (held pages first; `manifest.json` has them all). Then a short report for the user: pages passing and held by reason, the uniqueness range, thin hubs, and the first batch to publish.

## FILES
- `scripts/build_pages.py`: template rendering, quality gates, related links, hubs, breadcrumbs, sitemap and rollout batches.
- `references/output.schema.json`: output contract.
