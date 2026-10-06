---
name: ai-search-optimizer
description: Scores a page on how easily AI search engines (ChatGPT search, Perplexity, Claude, Gemini, Google AI Overviews and AI Mode, Copilot) can extract, quote and cite it, covering answer-first structure, question headings, quotable facts, authorship, freshness and entity markup, plus snippet controls and which AI crawlers robots.txt blocks, then rewrites the weak passages. Use when the user wants to optimize content for AI search, GEO or AEO, get cited by ChatGPT or Perplexity, show up in AI Overviews, or check AI crawler access.
metadata:
  title: AI Search Optimizer
  category: ai-search
---

# AI Search Optimizer

AGENT ROLE: Autonomous AI-search content agent. Measure each page with the script, emit the JSON in `references/output.schema.json`, then rewrite the passages it flags so the page is easier for AI engines to lift and cite.

## OBJECTIVE
Answer "why would an AI answer quote this page, or not, and what exactly should change?" Every finding comes from the page as served: its opening paragraph, headings, sentences, JSON-LD, robots meta and the site's robots.txt.

## INPUTS
- `url` (REQUIRED via `--url`, repeatable or comma-separated, up to 10): the pages to optimize. Use content pages such as guides, articles, product, service and FAQ pages. Homepages and listings score lower by nature.
- `query` (OPTIONAL via `--query`, repeatable, up to 10): the questions the page should be cited for. With queries, the output shows how well the title, H1, opening paragraph, headings and body use each question's words.

## DATA SOURCES (no keys needed)
1. Each page's HTML and response headers, fetched as `seoskills-ai-search-optimizer/1.0`.
2. Each domain's `/robots.txt`, checked for 13 crawler tokens: OAI-SearchBot, ChatGPT-User, GPTBot, Claude-SearchBot, Claude-User, ClaudeBot, PerplexityBot, Perplexity-User, Googlebot, Google-Extended, Bingbot, Applebot-Extended and CCBot. Each is labelled `search`, `user` or `training`, with what a block means according to the vendor's own documentation.
3. Each domain's `/llms.txt`, reported for information only. Google says AI Overviews and AI Mode need no special files or markup, so it does not change the score.

## EXPECTED TOOL CALLS
- Run `scripts/ai_search_optimizer.py --url https://example.com/guide [--url ...] [--query "..."]`.
- One request per page, plus two per domain (robots.txt and llms.txt).

## PROCEDURE (deterministic)
STEP 1: FETCH each page. Split it into text blocks and keep the main content (`<main>` or `<article>` when present), dropping navigation, site header, footer and asides.
STEP 2: ELIGIBILITY. Read meta robots, `googlebot` and `X-Robots-Tag`. `noindex` and `nosnippet` are critical, because Google applies them to AI Overviews and AI Mode too. A `max-snippet` under 160 is high.
STEP 3: SCORE five areas out of 20 each, for a total out of 100:
- Extractability: a 25 to 90 word opening paragraph under the H1 (6 points, or 2 if it exists at another length), question headings (2 each, up to 5), no paragraph over 120 words (4, or 2 if one or two are), lists and tables (1 each, up to 3), a summary or key takeaways heading (2).
- Quotability: sentences with figures such as percentages, prices, counts or timings (2 each, up to 10), one-sentence definitions (3 each, up to 6), links out to sources (4).
- Authority: an author in JSON-LD (8, or 5 if the author is only visible or in meta), Organization or publisher markup (6), outbound citations (1 each, up to 6).
- Freshness: the newest date found in JSON-LD, meta, `<time>` or a visible "Updated" line, at 180 days or less (10), 365 days or less (6) or older (2); `dateModified` in JSON-LD (5); a visible date (5).
- Entity clarity: JSON-LD for the page's own type (8, or 3 if it only describes the site), `sameAs` links (2 each, up to 6), BreadcrumbList (6).
STEP 4: QUERY FIT, only with `--query`. Measure the share of each question's words found in the title, H1, opening paragraph, best heading and body.
STEP 5: CRAWLERS. Match every page path against each crawler token with Google's robots.txt rules (the longest matching rule wins; Allow wins a tie).
STEP 6: REWRITE. For each item in `rewrite_targets`, write the replacement:
- `answer_first`: a 40 to 60 word direct answer to the page's main question, which opens with the subject and states the answer in the first sentence.
- `question_heading`: the heading rephrased as the question a searcher asks, plus the one-sentence answer that should open the section.
- `split_paragraph`: the paragraph broken into two to four short, self-contained paragraphs, or a list where it is a sequence.
Then draft the missing JSON-LD (Article or the right page type with `author`, `datePublished` and `dateModified`; Organization with `sameAs`) using only facts the page or the user provides.

## RATE LIMITS & ERROR HANDLING
- Pages are fetched one at a time, with a 15-second timeout per request.
- A page that does not return HTML with status 200 is reported as `unreachable` with its `http_status`, and the other pages continue. A 403 usually means the site blocks automated requests, not that the page is missing.
- IF no page can be fetched THEN STOP `error.code="PAGES_UNREACHABLE"`. A `--url` that is not an absolute http(s) URL STOPs `INPUT_INVALID`.

## MISSING / INSUFFICIENT DATA
- The script reads the HTML as served. Content that only appears after JavaScript runs is not seen. Say so when a page reports very few words.
- The measures count patterns (figures, definitions, question headings, dates). They do not judge whether a fact is true or a passage is good, so read the page before rewriting it.
- The score measures how citable a page is, not whether AI engines cite it today. That needs tracking real answers over time.
- Never invent statistics, sources, authors, credentials or dates for a rewrite. Where a figure would help, mark the gap `[source needed]` and ask the user for real data.
- A blocked AI crawler may be deliberate (for example a publisher keeping content out of training). Explain what the block costs, from `what_blocking_means`, and change robots.txt only if the user wants that visibility.

## OUTPUT
One JSON object per `references/output.schema.json`. Then a short report for the user: each page's score and weakest areas, any critical eligibility or crawler problems, and the rewrites as before and after pairs, followed by the JSON-LD to add.

## FILES
- `scripts/ai_search_optimizer.py`: page segmentation, eligibility, five-area scoring, query fit, robots.txt crawler matching and llms.txt check.
- `references/output.schema.json`: output contract.
