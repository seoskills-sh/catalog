---
name: on-page-seo-fixer
description: Audits a page for everything on the page itself that affects search (title, meta description, H1 and heading order, URL, canonical, robots directives, language and hreflang, Open Graph tags, content depth and keyword use, internal and external links, images and structured data) and returns each fix as the exact tag or change to make, filled in from the page, with a score per area. Optionally checks internal links for breakage and images for file size. Use when the user wants an on-page SEO audit of a URL, to optimize a page for a keyword, to fix meta tags, or a single-page SEO review.
metadata:
  title: On-Page SEO Fixer
  category: on-page-seo
---

# On-Page SEO Fixer

AGENT ROLE: Autonomous on-page SEO agent. Audit the page, emit the JSON in `references/output.schema.json`, then write the fixes: the new title and description, heading changes, missing tags and alt text, ready to paste.

## OBJECTIVE
Make one page as clear as possible to search engines and searchers for its target keyword, and leave nothing technical on the page that blocks it from ranking.

## INPUTS
- `url` (REQUIRED via `--url`, repeatable or comma-separated, up to 10): the pages.
- `keyword` (OPTIONAL via `--keyword`): one target keyword for all pages, or one per `--url` in the same order. Without it, the keyword checks are skipped.
- `check_links` (OPTIONAL flag): request up to 50 internal links per page to find broken ones.
- `check_images` (OPTIONAL flag): request up to 20 images per page to find files over 200 KB.

## DATA SOURCES (no keys needed)
The page's HTML and headers, fetched as `seoskills-on-page-fixer/1.0`, plus HEAD requests for links and images when asked.

## EXPECTED TOOL CALLS
- Run `scripts/on_page_fixer.py --url https://example.com/page --keyword "target keyword" [--check-links] [--check-images]`.

## PROCEDURE (deterministic)
STEP 1: FETCH the page (following redirects) and read the head, the main content (article or main element, without navigation, header and footer), links, images and JSON-LD.
STEP 2: CHECK six areas, each finding with a severity, the evidence and the fix (with a ready snippet where one fits):
- Metadata (30 points):
  - title: missing, more than one, over 60 or under 25 characters, keyword missing;
  - meta description: missing, over 160 or under 70 characters, opening by repeating the title, keyword missing.
- Headings (15): no H1, several H1s, keyword not in the H1, empty headings, skipped levels in the content, a long page with no H2s.
- Content (20):
  - under 300 words of main content;
  - keyword not in the first 100 words;
  - the exact keyword making up over 4% of the words with 8 or more uses, flagged as stuffing risk (Google's spam policies name keyword stuffing; density is not a target).
  - The Flesch reading ease score is reported for information.
- Links (15): under 3 internal links in the body of a 300-plus-word page, generic anchors ("click here"), links with no text, nofollow on internal links, and broken internal links with `--check-links` (404, 410, 5xx or no response; a 403 is confirmed with a GET first).
- Images (10): no alt attribute (the fix lists each image), no width and height, mostly JPEG or PNG, a lazy-loaded first image, and files over 200 KB with `--check-images`.
- Technical (10): noindex, nosnippet, canonical missing, multiple or pointing elsewhere, redirects, no `lang`, no viewport, hreflang without a self-reference or x-default, mixed content, a long or parameterized URL, incomplete Open Graph tags, JSON-LD that does not parse or is absent.
STEP 3: SCORE: each area loses part of its points per issue (critical 1.0, high 0.6, medium 0.3, low 0.1, so 1.5 empties an area), for a total out of 100.
STEP 4: WRITE THE FIXES for the user, in severity order:
- the replacement title (50 to 60 characters, keyword early, brand last);
- the meta description (120 to 155 characters, the answer or offer, keyword once);
- the H1 if it changes;
- alt text for each listed image, describing what it shows;
- the snippets from the output (canonical, viewport, Open Graph);
- the internal links to add, with anchor text that names the destination's topic.

## RATE LIMITS & ERROR HANDLING
- 20-second timeout for the page and 10 seconds for each link or image check. Pages are checked one at a time.
- A page that does not return HTML with status 200 is listed as `unreachable` with its status. IF none can be fetched THEN STOP `PAGES_UNREACHABLE`. A relative URL STOPs `INPUT_INVALID`.

## MISSING / INSUFFICIENT DATA
- The page is read as served. Content, links or tags added by JavaScript are not seen, so check client-rendered pages in a browser or with a rendering tool.
- Character limits are guides. Google cuts titles by pixel width and may rewrite titles and descriptions, so the goal is a clear, accurate snippet, not an exact count.
- Rankings depend on far more than on-page factors. Never promise a ranking change from these fixes, and never pad content to reach a word count.

## OUTPUT
One JSON object per `references/output.schema.json`, then the fix list with the ready-to-paste changes.

## FILES
- `scripts/on_page_fixer.py`: page parsing, the six check areas, link and image checks, scoring and snippets.
- `references/output.schema.json`: output contract.
