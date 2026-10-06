---
name: schema-markup-generator
description: Generates complete JSON-LD structured data for a page (Organization, WebSite, BreadcrumbList and the main entity, such as Article, Product, LocalBusiness, SoftwareApplication, Event, Recipe, VideoObject, JobPosting, ProfilePage or FAQPage) from facts on the live page or a draft HTML file, validates any markup already there against Google's required properties, and marks every value it could not find as a placeholder instead of guessing. Use when the user wants schema markup or structured data written, fixed or checked, JSON-LD for a page, or to know if a page is eligible for rich results.
metadata:
  title: Schema Markup Generator
  category: on-page-seo
---

# Schema Markup Generator

AGENT ROLE: Autonomous structured-data agent. Read the page, check its current markup, generate one complete JSON-LD graph from facts the page shows, emit the JSON in `references/output.schema.json`, and resolve every placeholder with the user before anything is published.

## OBJECTIVE
Give the page markup that is complete, valid and true: built from what the page visibly says, linked into one graph with `@id` references, and checked against the properties Google requires for each rich result.

## INPUTS
- `url` (REQUIRED unless `--html`, via `--url`, repeatable or comma-separated, up to 10): live pages.
- `html` + `page_url` (OPTIONAL): a local HTML file for a page that is not live yet, and the absolute URL it will have.
- `type` (OPTIONAL via `--type`): force the main entity type: Article, BlogPosting, NewsArticle, Product, LocalBusiness, SoftwareApplication, Event, Recipe, VideoObject, JobPosting, ProfilePage, FAQPage, Organization or WebPage.
- `org_name`, `logo`, `same_as` (OPTIONAL): organization facts the page does not show. `--same-as` is repeatable and takes the brand's official profile URLs.

## DATA SOURCES (no keys needed)
The page's HTML, fetched as `seoskills-schema-generator/1.0`, or the `--html` file. Required properties follow Google Search Central's structured data documentation.

## EXPECTED TOOL CALLS
- Run `scripts/generate_schema.py --url https://example.com/page [--type Product] [--org-name "Brand"]`, or `--html draft.html --page-url https://example.com/new-page`.
- One request per URL.

## PROCEDURE (deterministic)
STEP 1: READ the page. Parse every JSON-LD block and note any microdata or RDFa.
STEP 2: VALIDATE the existing markup and report each problem once, with a count:
- `INVALID_JSON`, `NO_CONTEXT`, `NO_TYPE`: the block cannot be read as intended. A fragment without `@context` and `@type` attaches to nothing.
- `MISSING_REQUIRED`: a property Google needs for that rich result is absent. Reference nodes (only `@type`, `@id`, `url` or `name`, as inside `hasVariant`) are not checked.
- `RELATIVE_URL`, `INVALID_DATE` (dates must be ISO 8601), `PLACEHOLDER_TEXT`.
- `PRICE_NOT_VISIBLE`: an Offer price that does not appear in the page text. Markup must match what users see.
- `SELF_SERVING_REVIEW`: a business marking up ratings of itself on its own site, which Google does not show as stars.
- `NO_GOOGLE_RICH_RESULT`: valid Schema.org with no Google rich result. FAQ rich results stopped in May 2026 and HowTo in 2023.
STEP 3: DETECT the main type, unless `--type` is set. Existing markup decides first, then `og:type`, the URL path, and page signals: a price with stock or a buy button (Product), a street address with a phone number (LocalBusiness), ingredient and step lists (Recipe), a publish date on a text page (Article).
STEP 4: EXTRACT facts: title, H1, description, `og:image`, site name, logo, official profile links, author, dates, price and currency, stock, rating text, phone, US-format address, map coordinates, the visible breadcrumb trail, question-and-answer pairs, recipe lists and embedded videos.
STEP 5: GENERATE one `@graph`: Organization (`/#organization`), WebSite (homepage only), WebPage, BreadcrumbList (from the visible trail, else the URL path) and the main entity, linked by `@id`. Values from the existing markup fill gaps and its extra properties are kept, except a business's own ratings of itself.
STEP 6: RESOLVE. `[FILL: ...]` marks a required value: ask the user for it. `[FILL (recommended): ...]` marks a recommended one: fill it or delete the property. Confirm everything in `to_confirm`, since those values were read from loose page text. Never publish a snippet that still contains `[FILL`.
STEP 7: APPLY per `recommendation`:
- `add`: the page has no markup for its main entity. Add the generated graph.
- `keep_existing_and_fix`: the current markup already covers the main entity (it may be richer, such as a ProductGroup with variants). Fix the reported issues in place and add only the nodes it lacks.
- `replace`: the current main entity is missing required properties. Swap in the generated graph.
Then test the final markup in Google's Rich Results Test and the Schema.org validator. Both are manual; neither has a public API.

## REQUIRED PROPERTIES CHECKED
Product: `name`, plus `offers`, `review` or `aggregateRating` (Offer: `price`). LocalBusiness and its subtypes: `name`, `address`. SoftwareApplication: `name`, `offers`, plus `aggregateRating` or `review`. Event: `name`, `startDate`, `location`. Recipe: `name`, `image`. VideoObject: `name`, `thumbnailUrl`, `uploadDate`. JobPosting: `title`, `description`, `datePosted`, `hiringOrganization`, `jobLocation`. BreadcrumbList: `itemListElement`. ProfilePage: `mainEntity`. AggregateRating: `ratingValue`, plus `ratingCount` or `reviewCount`. Article and Organization have no required properties.

## RATE LIMITS & ERROR HANDLING
- 15-second timeout per request; pages are fetched one at a time.
- A page that does not return HTML with status 200 is listed as `unreachable` with its `http_status`. IF none can be read THEN STOP `error.code="PAGES_UNREACHABLE"`.
- No `--url` or `--html`, a relative URL, or `--html` without `--page-url` STOPs `INPUT_INVALID`. An unreadable file STOPs `HTML_UNREADABLE`.

## MISSING / INSUFFICIENT DATA
- The script reads the HTML as served. JSON-LD added by JavaScript is not seen, though Google can read it, so check such pages in the Rich Results Test before calling them unmarked.
- Address parsing understands US-format addresses ("street, city, ST 12345"). Others come through as `address.text` with placeholders for each field.
- Mark up only what the page shows. Never add ratings, reviews, prices, FAQs, authors or dates the page does not display, and never invent them to fill a placeholder.
- Valid markup makes a page eligible for a rich result; Google never guarantees one.

## OUTPUT
One JSON object per `references/output.schema.json`. Then a short report for the user: the detected type and why, the problems in the current markup, the recommendation, the questions needed to fill the placeholders, and the final snippet once they are answered.

## FILES
- `scripts/generate_schema.py`: page parsing, validation, type detection, fact extraction, graph generation, merging with existing markup and placeholder tracking.
- `references/output.schema.json`: output contract.
