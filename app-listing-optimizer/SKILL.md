---
name: app-listing-optimizer
description: Audits and optimizes an App Store or Google Play listing from its live data. It reads the title, subtitle or short description, description, ratings, screenshots, video, update date and release notes (Apple's free iTunes Lookup API and the public store pages), checks each field against the store's character limits and metadata rules, finds target keywords missing from the fields that count most, flags words the stores' policies prohibit, and compares the listing with competitors to surface the title words they use and you do not. Use when the user wants app store optimization (ASO), to rewrite an app title, subtitle or description, or to compare an app listing with competitors.
metadata:
  title: App Listing Optimizer
  category: aso
---

# App Listing Optimizer

AGENT ROLE: Autonomous ASO agent. Read the live listing and its competitors, run the script, emit the JSON in `references/output.schema.json`, then write the new metadata within each store's limits.

## OBJECTIVE
Make the listing rank for the searches that matter and convert the people who see it, without breaking the store's metadata rules.

## INPUTS
- `app` (REQUIRED via `--app`): an App Store ID (`570060128`) or apps.apple.com URL, or a Google Play package (`com.example.app`) or play.google.com URL.
- `competitor` (OPTIONAL via `--competitor`, repeatable or comma-separated, up to 5): apps on the same store.
- `keyword` (OPTIONAL via `--keyword`, repeatable): the searches the app should rank for.
- `country` (OPTIONAL, default `us`), `language` (OPTIONAL, default `en`, Google Play only).

## DATA SOURCES (no keys needed)
1. App Store: Apple's iTunes Lookup API (about 20 requests a minute; HTTP 403 when exceeded) for the name, description, release notes, ratings, screenshots, languages and update date, plus the public app page for the subtitle.
2. Google Play: the public app page for the title, short description, full description, rating, downloads, update date, screenshots and promo video.

## EXPECTED TOOL CALLS
- Run `scripts/app_listing.py --app <id or package> [--competitor ...] [--keyword "..."] [--country us]`.
- Two requests per App Store app (lookup and page, paced 3.1 seconds apart for competitors), one per Google Play app.

## PROCEDURE (deterministic)
STEP 1: READ the listing and each competitor.
STEP 2: CHECK the fields against each store's limits. App Store: name 30 characters, subtitle 30, description 4,000; the 100-character keyword field is not public. Google Play: title 30, short description 80, full description 4,000.
- `TITLE_TOO_LONG`, `TITLE_UNDERUSED` (under 20 characters).
- `SUBTITLE_MISSING` or `SUBTITLE_UNDERUSED` (iOS), `SHORT_DESCRIPTION_MISSING` or `SHORT_DESCRIPTION_UNDERUSED` (Play).
- `REPEATED_WORDS` between the title and the second field.
- `POLICY_RISK_TERMS` in the title (and the iOS subtitle): words like free, best, top, #1, sale or prices. App Store Review Guideline 2.3.7 bars pricing and irrelevant phrases in metadata; Google Play's metadata policy bars them in titles.
- `DESCRIPTION_SHORT` (under 1,000 characters).
STEP 3: KEYWORDS: for each `--keyword`, whether all its words appear in the title, the second field and the description, and how often in the description. `KEYWORD_NOT_IN_TOP_FIELDS` means it is in neither the title nor the second field. `KEYWORD_REPETITION` (Google Play) means it is repeated heavily, which Play's policy prohibits.
STEP 4: HEALTH: `STALE_UPDATE` (over 90 days), `GENERIC_RELEASE_NOTES` ("bug fixes" only), `LOW_RATING` (under 4.0), `FEW_RATINGS` (under 100), `FEW_SCREENSHOTS` (under 5 on iOS, 4 on Play), `NO_IPAD_SCREENSHOTS`, `NO_PROMO_VIDEO` (Play, best effort).
STEP 5: COMPETITORS: the same fields and checks for each, plus `keyword_ideas_from_competitors`, the title and second-field words competitors use that the app does not (brand names excluded).
STEP 6: WRITE the new metadata:
- Title: brand plus the top keyword, 30 characters or fewer.
- Second field: the next keywords plus the main benefit, without repeating title words.
- iOS keyword field: comma-separated, no spaces, no words already in the title or subtitle, up to 100 characters. Draft it for the user to paste in App Store Connect.
- Description opening: the first 250 characters must sell the app.
- Release notes: say what changed.
Every line must stay true to the app. Never claim a ranking, award or price the store page cannot back up.

## RATE LIMITS & ERROR HANDLING
- 25-second timeout per request. App Store lookups for competitors are paced 3.1 seconds apart (Apple allows about 20 a minute), Play pages 1 second apart.
- An app that cannot be found STOPs `APP_NOT_FOUND`; a store that does not answer STOPs `STORE_UNAVAILABLE`. A competitor that cannot be read is listed with its status. An unrecognized `--app`, or a competitor on the other store, STOPs `INPUT_INVALID`.

## MISSING / INSUFFICIENT DATA
- The subtitle comes from Apple's web page and is `null` when the page layout cannot be read. Say so rather than assuming there is none.
- The hidden iOS keyword field, in-app event cards and custom product pages cannot be read from outside. Ask the user for them.
- Search volume and rankings are not measured here. Rank tracking and keyword volumes belong to dedicated ASO tools; pass the user's keyword list in with `--keyword`.
- Listings differ by country and language. Run once per market you optimize.

## OUTPUT
One JSON object per `references/output.schema.json`. Then the proposed metadata for each field, with its character count, and the top fixes.

## FILES
- `scripts/app_listing.py`: App Store and Google Play readers, field checks, keyword placement, competitor comparison.
- `references/output.schema.json`: output contract.
