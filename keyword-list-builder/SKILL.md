---
name: keyword-list-builder
description: Builds a prioritized keyword list from seed topics. It expands each seed through Google Autocomplete, merges the user's keyword-tool export (volume, difficulty, CPC) and Search Console queries, classifies search intent, assigns a tier and a content type, decides whether to create a page, update an existing one, fix its CTR or consolidate competing pages, and groups the keywords into starter clusters. Use when the user wants keyword research, a keyword list, keywords to target, search intent mapping, or help choosing which keywords to prioritize.
metadata:
  title: Keyword List Builder
  category: keyword-research
---

# Keyword List Builder

AGENT ROLE: Autonomous keyword-research agent. Expand the seeds, merge the user's data, run the script, emit the JSON in `references/output.schema.json`, and turn it into a short, prioritized plan: what to create, what to update, and what to leave alone.

## OBJECTIVE
Answer "which keywords should we go after, and with what page?" Every keyword gets an intent, a content type, an action and a priority, and keywords the site already ranks for are routed to the page that should own them instead of a new one.

## INPUTS
- `seed` (REQUIRED unless a file is given, via `--seed`, repeatable or comma-separated, up to 10): the business category or topic, such as "crm software" or "emergency plumber". Use topics, not single long-tail phrases.
- `country`, `language` (OPTIONAL, default `us`, `en`): the market for Autocomplete.
- `volumes` (OPTIONAL via `--volumes`): an export from any keyword tool (Ahrefs, Semrush, Moz, Keyword Planner or a plain CSV, TSV or JSON) with keyword and volume columns, and optionally difficulty (KD) and CPC. Keyword Planner ranges such as "1K - 10K" use the midpoint. Its keywords join the list.
- `gsc` (OPTIONAL via `--gsc`): a Search Console queries export with clicks, impressions and position. Include a page column to find pages competing for one query.
- `brand`, `competitor` (OPTIONAL, repeatable): brand names, so navigational searches are labelled.
- `exclude` (OPTIONAL, repeatable): drop keywords containing a term (for example a stock ticker that shares the seed's name).
- `max` (OPTIONAL, default 300): keywords to return.

## DATA SOURCES
1. Google Autocomplete (`suggestqueries.google.com`, no key): for each seed, the seed alone, the seed followed by each letter a to z, and 17 question and modifier patterns (how to, what is, best, vs, for, alternatives, pricing, cost, free, examples, template, near me and others).
2. The user's keyword-tool export, for volume, difficulty and CPC. Without it there are no volumes, and priority uses how prominently Autocomplete suggests each keyword instead. Ask the user for an export before calling a list final.
3. The user's Search Console export, for current rankings.

## EXPECTED TOOL CALLS
- Run `scripts/keyword_list.py --seed "crm software" [--volumes export.csv] [--gsc queries.csv] [--brand Acme] [--competitor HubSpot]`.
- About 44 Autocomplete requests per seed, paced at 0.25 seconds (about 20 seconds per seed).

## PROCEDURE (deterministic)
STEP 1: EXPAND each seed through Autocomplete. Keep a suggestion only when it shares a word with its seed, and score its prominence as the sum of 1 / list position across every prefix that returned it.
STEP 2: MERGE the volume export and the Search Console export. Search Console positions are averaged across rows, weighted by impressions.
STEP 3: CLASSIFY intent by rules, first match wins: transactional (purchase, sign-up, hire or local), commercial (comparison, alternatives, best-of, review), informational (how-to, definition, troubleshooting, examples, question), then commercial category and audience-fit ("for small business"). Brand terms are navigational. Each sub-intent maps to a content type, such as a comparison page for "x vs y" or a tutorial for "how to".
STEP 4: TIER: with ten or more volumes, head is the top 10% by volume and body the next 30%; otherwise head has up to 2 meaningful words, body 3 and long-tail 4 or more.
STEP 5: ACTION, from Search Console:
- `consolidate`: two or more pages get impressions for the query.
- `fix CTR`: top-10 position, but click-through under half the site's own median at that position (needs 3 or more queries per position band).
- `update existing page`: position 8 to 20 with impressions. These are the quick wins.
- `maintain`: already ranking well.
- `create`: not ranking. Own-brand terms become `brand page` and competitor brand terms `comparison page`.
STEP 6: PRIORITY, 0 to 100: demand (up to 40: log-scaled volume, where 100,000 searches a month scores 40, or Autocomplete prominence without volumes) + intent value (transactional 30, commercial 25, informational 15, navigational 5) + ease (up to 20 from 100 minus difficulty, 10 when unknown) + 10 for update or CTR actions.
STEP 7: CLUSTER: group keywords whose words beyond the seed overlap by 60% or more (plurals and price words such as pricing and cost folded together). Clusters are starting points. For SERP-based grouping, cluster the final list by shared ranking URLs.

## RATE LIMITS & ERROR HANDLING
- Autocomplete is paced at 0.25 seconds per request. IF it answers 403, 429 or 503 THEN expansion stops, a note says the list is partial, and the files still merge.
- No seed and no file STOPs `INPUT_INVALID`. An unreadable file STOPs `FILE_UNREADABLE`. No keywords at all STOPs `NO_KEYWORDS`.

## MISSING / INSUFFICIENT DATA
- Autocomplete reflects the requesting location as well as `--country`. Run from the target market where possible, and use `--exclude` for off-market suggestions.
- Intent comes from wording rules, not the live search results. For high-stakes keywords, check what actually ranks before committing a page type.
- Never invent volumes or difficulty. Without an export, say that priorities rest on Autocomplete prominence.

## OUTPUT
One JSON object per `references/output.schema.json`. Then a short plan for the user: the top 10 to 20 keywords with their action and content type, the quick wins and consolidations first, and the clusters that should become pages.

## FILES
- `scripts/keyword_list.py`: Autocomplete expansion, export merging, intent rules, tiers, actions, priority and clusters.
- `references/output.schema.json`: output contract.
