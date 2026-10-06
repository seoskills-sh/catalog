---
name: content-brief-writer
description: Writes an editor-ready content brief for a target keyword from what the pages ranking for it actually cover. It finds the dominant format, a word-count range, the subtopics most of them share in their usual order, the angles only one covers, the questions searchers ask (People Also Ask, Autocomplete, competitor headings), the terms most pages use, their schema, media and freshness, and internal links from your own site. Use when the user wants a content brief, an SEO article outline, writing guidelines for a keyword, or to know what a page needs to cover to rank.
metadata:
  title: Content Brief Writer
  category: content
---

# Content Brief Writer

AGENT ROLE: Autonomous content-strategy agent. Collect the top results, run the script, emit the JSON in `references/output.schema.json`, then write the brief an editor or writer can use without further research.

## OBJECTIVE
Give the writer one page that answers: what does the searcher expect (format and depth), what must the piece cover to compete, what can it add that the current results lack, and how should it be titled, structured, linked and marked up.

## INPUTS
- `keyword` (REQUIRED via `--keyword`): the primary keyword.
- `urls` (REQUIRED unless `--serp`, via `--urls`, repeatable or comma-separated, 3 to 10): the top organic results for the keyword. Get them with your web search tool, skipping ads, videos, forums and the user's own site.
- `serp` (OPTIONAL flag): fetch Google's top 10, People Also Ask, related searches and SERP features from SerpApi instead. Needs env `SERP_API_KEY`.
- `site` (OPTIONAL via `--site`): the user's site, to find pages that already target the keyword and internal link candidates from its sitemap.
- `country`, `language` (OPTIONAL, default `us`, `en`).
- Ask the user for the business, the audience, and what the reader should do next (sign up, buy, call). The script cannot know these, and the brief needs them.

## DATA SOURCES
1. The competing pages themselves, fetched once each.
2. Google Autocomplete (no key) for the questions people type, using 10 question patterns.
3. With `--serp`: SerpApi's Google results (`SERP_API_KEY`).
4. With `--site`: the site's XML sitemaps (robots.txt first, then `/sitemap.xml`).

## EXPECTED TOOL CALLS
- Your web search tool, once, for the keyword's top results (unless `--serp`).
- Run `scripts/content_brief.py --keyword "local seo audit" --urls https://a.com/x,https://b.com/y,https://c.com/z [--site https://example.com]`.

## PROCEDURE (deterministic)
STEP 1: READ each competing page's main content (article or main element, without navigation and footer): title, meta description, H1, H2 and H3 headings (numbering stripped; share, newsletter, related-posts and conclusion headings dropped), word count, images, tables, lists, video embeds, schema types, newest date and author markup. Pages under 150 words or not readable as HTML are skipped.
STEP 2: FORMAT: label each page from its H1 or title (comparison, listicle, how-to, review, definition, product or category page, else guide) and take the majority. LENGTH: the middle half of the word counts (25th to 75th percentile), rounded to hundreds.
STEP 3: SUBTOPICS: group headings from all pages, across H2 and H3, when their topic words (no stop words, no generic verbs like check or review) overlap by half or more. `must_cover` means at least 40% of pages, in their usual order. `should_cover` means two or more pages. `unique_angles` means H2s only one page has.
STEP 4: QUESTIONS: merge People Also Ask, Autocomplete questions and competitors' question headings. `pages_answering` counts the pages with a heading that matches, so a question asked often but answered by few pages is a gap.
STEP 5: TERMS: two- and three-word phrases, then single words, used by at least half the pages (generic words removed). Treat them as coverage hints, not a keyword-density target.
STEP 6: SITE: sitemap URLs whose path holds all the keyword's words are `existing_pages_for_keyword`. IF any exist THEN recommend updating that page instead of writing a new one. URLs sharing a third or more of the words are internal link candidates.
STEP 7: WRITE THE BRIEF, in this order:
1. Search intent and format, in one or two sentences.
2. Target reader and the action they should take next.
3. Three title options (60 characters or fewer, keyword near the front) and one meta description (155 characters or fewer).
4. H1.
5. The outline: every `must_cover` subtopic in its usual position, the strongest `should_cover` ones, and one or two angles the others lack, with a line on what each section must say.
6. Questions to answer, gaps first.
7. Terms and entities to cover naturally.
8. The word-count range, with media (tables, images, video) where most competitors use them.
9. Internal links to add.
10. Schema: Article with author and dates.
11. E-E-A-T needs: named author, first-hand experience, cited sources, plus expert review when `ymyl` is true.
12. Freshness: when `freshness_matters` is true, show an updated date and plan a refresh.

## RATE LIMITS & ERROR HANDLING
- Pages are fetched one at a time (20-second timeout); Autocomplete at 0.2 seconds per request.
- Fewer than 3 URLs, or a relative URL, STOPs `INPUT_INVALID`. IF fewer than 2 pages are readable THEN STOP `TOO_FEW_PAGES` and list each page's status. A 403 usually means the site blocks automated requests, so swap in the next result.
- `--serp` without `SERP_API_KEY` STOPs `AUTH_MISSING_API_KEY`; a SerpApi error STOPs `SERP_FAILED`.

## MISSING / INSUFFICIENT DATA
- Google says it has no preferred word count. The range shows what current results take to cover the topic, not a target to pad toward.
- Content that loads only through JavaScript is not seen, so a page may show fewer words or headings than a browser does.
- Never invent statistics, quotes, studies or credentials for the brief. Mark them as research the writer must do, with the kind of source to cite.
- FAQPage markup no longer produces FAQ rich results in Google (stopped in May 2026). Recommend an FAQ section for readers and AI answers, not for a rich result.

## OUTPUT
One JSON object per `references/output.schema.json`, then the written brief as above.

## FILES
- `scripts/content_brief.py`: page reading, format and length, subtopic grouping, question merging, term counting, sitemap matching and SerpApi support.
- `references/output.schema.json`: output contract.
