---
name: comparison-page-builder
description: Builds "X vs Y", "X alternatives" and "best X" comparison pages from sourced facts. It reads each product's own site (positioning, pricing plans and prices, free plan and trial, feature lists, integration and customer claims), checks a feature list against every product, finds what people compare each product with and the questions they ask, and returns target keywords, titles and an outline, with the source URL and date for every fact. Use when the user wants a comparison page, a vs page, an alternatives page, a competitor comparison table or a roundup of tools.
metadata:
  title: Comparison Page Builder
  category: competitor-analysis
---

# Comparison Page Builder

AGENT ROLE: Autonomous comparison-content agent. Gather each product's facts from its own site, run the script, emit the JSON in `references/output.schema.json`, then write a fair, sourced comparison page.

## OBJECTIVE
Win "X vs Y" and "X alternatives" searches with a page readers trust. Every price and claim is dated and linked to its source, gaps are labelled "not found" rather than "missing", and the page says who each product suits instead of declaring one winner for everyone.

## INPUTS
- `product` (REQUIRED via `--product`, repeat 2 to 8 times): `"Name=https://homepage"`.
- `type` (OPTIONAL via `--type`): `vs` (default for two products), `alternatives` (default for three or more) or `roundup`.
- `subject` (OPTIONAL via `--subject`): the product people want alternatives to (default: the first), or the category for a roundup.
- `feature` (OPTIONAL via `--feature`, repeatable or comma-separated): the features buyers compare on, such as "pipeline management" or "mobile app". Each one is checked on every product's pages.
- `yours` (OPTIONAL via `--yours`): your product's name when it is one of them, which triggers a disclosure.
- `country`, `language` (OPTIONAL, default `us`, `en`): the market for Autocomplete.

## DATA SOURCES (no keys needed)
1. Each product's homepage, then its pricing, features and integrations pages, found from the homepage's links (exact matches first, such as a `/pricing` path or a link reading "Pricing"), else common paths such as `/pricing`.
2. Google Autocomplete for "{product} vs" (what people compare it with), "{A} vs {B}", "is {A} better than {B}" and "{subject} alternatives".

## EXPECTED TOOL CALLS
- Run `scripts/compare_products.py --product "A=https://a.com" --product "B=https://b.com" [--feature "..."] [--yours A]`.
- Up to 5 page requests per product, plus about 5 Autocomplete requests.

## PROCEDURE (deterministic)
STEP 1: READ each product: homepage title, meta description and H1 (its own positioning); pricing (every price with its currency, period, per-seat flag, the nearest plan heading and the surrounding text, the lowest paid price as a monthly equivalent, free plan, trial length, custom or contact-sales pricing); listed features (short list items and subheadings); integration and customer-count claims.
STEP 2: CHECK the `--feature` list. A feature counts as mentioned when one passage on the product's pages contains all its words. `null` means not found on the pages read, which is not proof the product lacks it.
STEP 3: MATCH the listed features across products where their wording overlaps by half or more (`feature_matrix`). Marketing phrasing varies, so the checklist from STEP 2 is the more reliable table.
STEP 4: DEMAND: `people_also_compare` counts the products Autocomplete pairs with each name; `questions` holds the comparison questions searchers type.
STEP 5: PLAN: target keywords, title options of 60 characters or fewer, and the outline. A vs page runs verdict, table, pricing (as of the read date), the differences that matter, what they share, integrations, pros and cons, alternatives, FAQ, and how we compared. An alternatives page runs why people switch, quick picks by use case, table, one section per product, pricing, how to choose, FAQ, and how we compared.
STEP 6: WRITE the page from the facts:
- Lead with who each product is best for.
- Show prices with their period, per-seat basis and the date read.
- Link each fact to its source page.
- Verify every item in `verification_needed` and every "not found" by hand before publishing.
- When `disclosure_needed` is true, say near the top that the page is published by one of the companies compared.

## RATE LIMITS & ERROR HANDLING
- Pages are fetched one at a time (20-second timeout), with a 0.3-second pause between a product's pages.
- A product site that cannot be read is listed with its `http_status`. IF fewer than 2 can be read THEN STOP `TOO_FEW_PRODUCTS`.
- Fewer than 2 or more than 8 products, or a malformed `--product`, STOPs `INPUT_INVALID`.
- Pricing pages often block automated requests (HTTP 403) or build their price tables with JavaScript. Both are reported in `verification_needed`; read those prices in a browser.

## MISSING / INSUFFICIENT DATA
- Prices are what the site served to the requesting location, so currency and amounts can change by country. Check them for the page's audience.
- Plan names are the nearest heading and can be wrong. The `text` beside each price shows the context.
- Comparative claims must be truthful and verifiable: the FTC encourages truthful comparative advertising, US false-advertising law (the Lanham Act) and the EU's comparative advertising rules prohibit misleading comparisons. Never state that a competitor lacks a feature because the script did not find it, never invent ratings or reviews, and never use review stars in markup for products you did not review.
- Review-site ratings (G2, Capterra) are not fetched. Cite them only from the live review page, with the date.

## OUTPUT
One JSON object per `references/output.schema.json`. Then the draft page, with a sources list and the date the facts were read.

## FILES
- `scripts/compare_products.py`: page discovery, price, plan, trial and feature extraction, feature checklist, Autocomplete demand and the page plan.
- `references/output.schema.json`: output contract.
