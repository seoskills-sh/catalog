---
name: link-building-campaign-planner
description: Plans a link-building campaign from the site's own linkable assets and its competitors' backlinks. It reads the site's sitemap pages to find what can earn links (free tools, original data, templates, in-depth guides, glossaries, visuals), reads backlink exports from any tool to build a de-duplicated prospect list typed as resource pages, roundups, guest-post sites, news, directories, communities or .edu and .gov pages, ranks domains that link to several competitors but not to you first, matches each to an asset and tactic, and writes an outreach tracking sheet. Use when the user wants a link building plan, outreach targets, backlink prospects from competitors, or digital PR ideas.
metadata:
  title: Link-Building Campaign Planner
  category: link-building
---

# Link-Building Campaign Planner

AGENT ROLE: Autonomous link-building strategist. Inventory the site's assets, turn competitor backlinks into typed and scored prospects, emit the JSON in `references/output.schema.json`, then write the campaign: which asset, which prospects, which pitch, in what order.

## OBJECTIVE
Earn editorial links that a competitor already proved are available, with a reason for each site to link, instead of mass outreach with nothing to offer.

## INPUTS
- `site` (REQUIRED via `--site`): the site to build links to.
- `competitor_links` (OPTIONAL but needed for prospects, via `--competitor-links`, one file per competitor): a backlink export (CSV or TSV) from Ahrefs, Semrush, Moz, Majestic or Search Console's links report. Columns are recognized by name: referring page URL or domain, title, anchor, authority (DR, Authority Score, DA, Trust Flow), nofollow and first seen.
- `own_links` (OPTIONAL via `--own-links`): the site's own backlink export, so domains already linking are skipped.
- `topic` (OPTIONAL via `--topic`, repeatable): topic words for relevance scoring.
- `max_pages` (OPTIONAL, default 150): site pages to read for assets.
- `out` (OPTIONAL via `--out`): where to write the tracking sheet (CSV).

## DATA SOURCES
1. The site's sitemap and pages (no key), fetched as `seoskills-link-campaign-planner/1.0`. URLs that look like tools, research, guides, templates or glossaries are read first.
2. The backlink exports the user provides. No backlink API is called.

## EXPECTED TOOL CALLS
- Run `scripts/link_campaign.py --site https://example.com --competitor-links a.csv --competitor-links b.csv [--own-links ours.csv] [--topic "..."] [--out prospects.csv]`.
- Up to `max_pages` page fetches, 4 at a time.

## PROCEDURE (deterministic)
STEP 1: ASSETS. Label each page by its title and URL, with evidence required:
- `tool`: calculator, checker or generator, and the page has form inputs.
- `original data`: survey, study, statistics or benchmark, with 8 or more figures and 800 or more words.
- `template or checklist`, `visual`, `glossary or definition`.
- `in-depth guide`: guide or how-to, with 1,200 or more words.
Score linkability out of 100 from the type, depth, figures, tables and sections, and attach the tactic that fits each type.
STEP 2: PROSPECTS. Merge the exports by referring domain.
- Skip the site itself, domains already linking (from `--own-links`) and spam patterns (gambling, pills, adult and throwaway domains).
- Keep the strongest authority value, whether any link is followed, the newest first-seen date, and which competitors each domain links to.
STEP 3: TYPE each prospect from its referring page: community, education or government, guest-post site, roundup, resource page, directory, then news or magazine (by domain name or a dated URL), else blog post. Each type has its own tactic.
STEP 4: SCORE out of 100 and rank:
- authority relative to the strongest prospect: 30;
- linking to several competitors: 25;
- topic relevance: 25;
- a followed link: 10;
- a link first seen within a year: 10;
- the total weighted by how well the type tends to work (resource pages and news highest, directories lowest).
Domains linking to two or more competitors are the gap to close first.
STEP 5: MATCH an asset to each prospect: tools, templates and guides for resource pages, roundups and directories; data and visuals for news; guides and definitions for .edu and .gov pages.
STEP 6: WRITE THE CAMPAIGN:
- The weak spot to fix first: no assets, or few referring domains.
- The two or three assets to promote, or the one to create when none qualifies.
- The prospect segments, with the first 20 targets.
- A pitch per segment that offers value.
- A three-touch sequence (pitch, a follow-up after about five days, a last note a week later), tracked in the sheet.

## RATE LIMITS & ERROR HANDLING
- Four page requests at a time, 15-second timeout. Pages that fail are skipped.
- A bad `--site` STOPs `INPUT_INVALID`; an unreadable export STOPs `FILE_UNREADABLE`. An export without a recognizable referring URL or domain column is noted, not fatal.

## MISSING / INSUFFICIENT DATA
- Without competitor exports there are no prospects. Say so and ask for them (most tools export "referring pages" or "backlinks" as CSV).
- Asset and prospect types come from titles, URLs and page structure, so review the top items before outreach.
- Authority scales differ between tools (DR, Authority Score, DA). Rank within one tool's numbers where possible.
- Never buy links, swap links or use private blog networks: Google's spam policies treat links made to manipulate rankings as link spam. Paid placements must be marked `rel="sponsored"` (or nofollow).

## OUTPUT
One JSON object per `references/output.schema.json`, the tracking sheet when `--out` is set, and the written campaign plan.

## FILES
- `scripts/link_campaign.py`: asset inventory, export parsing, prospect typing, gap and scoring, asset matching and the tracking sheet.
- `references/output.schema.json`: output contract.
