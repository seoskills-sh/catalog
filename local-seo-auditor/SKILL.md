---
name: local-seo-auditor
description: Audits a local business's search presence and scores four areas. It checks the website's local signals (name, address and phone on the page, LocalBusiness markup, click to call, map, hours, location pages and near-duplicate city pages), and, from files the user exports, the Google Business Profile details, the reviews (rating, recency, reply rate) and the citations, comparing each with the business's real name, address and phone. Use when the user wants a local SEO audit, to fix NAP consistency, to improve Google Maps or local pack visibility, or to check location pages, reviews or listings.
metadata:
  title: Local SEO Auditor
  category: local-seo
---

# Local SEO Auditor

AGENT ROLE: Autonomous local-search agent. Audit the website, compare it with the profile, review and citation data the user provides, emit the JSON in `references/output.schema.json`, and turn the findings into a fix list ordered by impact.

## OBJECTIVE
Answer "why doesn't this business show up locally, and what should it fix first?" One run measures the site and, when the user supplies the exports, the Google Business Profile, the reviews and the listings, all against the same name, address and phone.

## INPUTS
- `site` (REQUIRED via `--site`): the website origin, for example `https://example.com`.
- `gbp` (OPTIONAL via `--gbp`): a Google Business Profile export, CSV or JSON with one row per location. The bulk spreadsheet's columns are recognized (Store code, Business name, Address line 1, Locality, Administrative area, Postal code, Primary phone, Website, Primary category, Additional categories, and the day hours columns), as are simple names such as name, address, city, state, zip and phone.
- `reviews` (OPTIONAL via `--reviews`): a reviews export, CSV or JSON, with a date and a rating per review, plus optional reply and platform columns. ISO dates (2026-10-01) are safest; US month/day/year also works.
- `citations` (OPTIONAL via `--citations`): the business's directory listings, one row each, with source, name, address (or address, city, state, zip), phone and url.
- `name`, `phone`, `address` (OPTIONAL): the canonical details, when they differ from what the profile shows.
- `max_pages` (OPTIONAL, default 25): pages to audit.
Without the optional files, those areas are reported as `assessed: false` and left out of the total. They are never scored as zero.

## DATA SOURCES
1. The website, fetched as `seoskills-local-seo-auditor/1.0`: the homepage, its sitemap, the locations index and the pages it links to, contact pages, and every website link in the profile export. No keys needed.
2. The files the user exports. Profile data comes from the Business Profile bulk spreadsheet or the Business Profile API; reviews from the profile, Yelp or a review tool; citations from a listings tool or a manual check.

## EXPECTED TOOL CALLS
- Run `scripts/local_seo_audit.py --site https://example.com [--gbp profiles.csv] [--reviews reviews.csv] [--citations citations.csv]`.
- About 2 to 30 requests: robots.txt, the sitemap and each audited page, plus one per profile website link that no audited page covers.

## PROCEDURE (deterministic)
STEP 1: DISCOVER pages. Start at the homepage, then a locations index, then the pages that index links to (and the homepage does not), contact pages, and the profiles' website links. Sitemap URLs with location-style paths are added too.
STEP 2: READ each page for phone numbers (tel: links and US-format numbers in the text), US-format street addresses (lines are joined, so a street and its "City, ST 12345" line read as one), LocalBusiness JSON-LD and its subtypes, map embeds or directions links, opening hours, click-to-call links, and the main text.
STEP 3: SET the canonical name, address and phone: the `--name`, `--phone` and `--address` flags first, then a single profile, then the site's own markup and text. With several locations and no profile export there is no single canonical, and checks run per location. With no street address anywhere, the business is treated as service-area and address checks are skipped.
STEP 4: CHECK the website: `NO_LOCAL_SCHEMA`, `ORG_NOT_LOCALBUSINESS`, `NAP_MISSING`, `SCHEMA_NAP_MISMATCH`, `SCHEMA_INCOMPLETE`, `DUPLICATE_SCHEMA_ID`, `NO_CLICK_TO_CALL`, `NO_MAP`, `NO_HOURS`, `TITLE_NO_CITY`, `H1_NO_CITY`, `LOCATION_PAGES_NEAR_DUPLICATE` (under 30% unique text across three or more location pages, where any 3-word sequence on at least half of them counts as shared) and `MISSING_LOCATION_PAGES`.
STEP 5: CHECK each profile against its matched page, or the whole site when no page matches: `GBP_NAME_MISMATCH`, `GBP_ADDRESS_MISMATCH`, `GBP_PHONE_MISMATCH`, `GBP_WEBSITE_MISMATCH`, `GBP_NO_HOURS`, `GBP_NO_ADDITIONAL_CATEGORIES` and `GBP_CATEGORY_NOT_ON_SITE`. Names compare without punctuation or legal suffixes such as LLC. Addresses compare after standard abbreviations (Street to St, Suite to Ste); a missing suite is `partial`, not a mismatch.
STEP 6: MEASURE reviews: count, average, reviews in the last 30 and 90 days, days since the last one, reply rate, and the split by rating and platform. Flags: `FEW_REVIEWS` (under 10), `LOW_RATING` (under 4.0), `REVIEW_GAP` (none in 30 days), `LOW_RESPONSE_RATE` (under 50% replied).
STEP 7: CHECK citations against the matching location: `CITATION_MISMATCH` (with the fields that differ), `DUPLICATE_LISTING` and `CORE_DIRECTORY_MISSING` (Google Business Profile, Apple Business Connect, Bing Places, Yelp, Facebook).
STEP 8: SCORE. Website 40, profile 25, reviews 20, citations 15. Each finding costs its weight times the share of pages, profiles or listings it affects. The total is the sum of the assessed areas, out of 100. This is the script's weighting, not a Google formula. Google ranks local results on relevance, distance and prominence, and a business can only work on relevance and prominence.

## RATE LIMITS & ERROR HANDLING
- Pages are fetched one at a time with a 15-second timeout, up to `--max-pages`. A page that fails is listed with its status and skipped in the checks.
- IF the homepage cannot be loaded THEN STOP `error.code="SITE_UNREACHABLE"`. A bad `--site` STOPs `INPUT_INVALID`; an unreadable data file STOPs `FILE_UNREADABLE`.

## MISSING / INSUFFICIENT DATA
- Address parsing reads US-format addresses ("street, city, ST 12345"), and phone parsing in page text reads US and Canadian numbers. Elsewhere, phones come from tel: links and addresses from the profile export and LocalBusiness markup only. Say so for non-US businesses.
- The script reads HTML as served. Locations loaded by a JavaScript store locator are invisible to it, so ask for the location page URLs or the profile export instead.
- It cannot see rankings, the local pack or the Business Profile itself. Profile fields are only what the export contains.
- Review rules: never suggest offering incentives for reviews or asking only happy customers (review gating); both break Google's policies.
- Report the score with the areas that were assessed. A website-only score is not a full local audit.

## OUTPUT
One JSON object per `references/output.schema.json`. Then a short report for the user: the score per assessed area, the five most important fixes with the pages, profiles or listings they affect, and which exports would complete the audit if any were missing.

## FILES
- `scripts/local_seo_audit.py`: page discovery, NAP extraction and normalization, website, profile, review and citation checks, and area scoring.
- `references/output.schema.json`: output contract.
