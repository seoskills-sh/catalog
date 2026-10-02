---
name: app-review-keyword-miner
description: Pulls App Store (RSS) and Google Play reviews, extracts the real vocabulary users use plus recurring feature requests and complaints, and clusters them into themes by lexicon sentiment and rating impact. Surfaces high-intent keyword candidates for the store listing and a themed issue log ranked by frequency and star impact. Use when the user wants review-mined keywords, voice-of-customer themes, feature-request triage, or complaint clustering for an app.
metadata:
  title: App Review Keyword Miner
  category: aso
---

# App Review Keyword Miner

AGENT ROLE: Autonomous voice-of-customer miner. Pull reviews from both stores, run std-lib lexicon NLP, and emit the keyword candidates + themed issue log JSON in `references/output.schema.json`.

## OBJECTIVE
Turn raw store reviews into (1) high-intent keyword candidates phrased the way users actually search and describe the app, and (2) a themed issue log (crashes, pricing, sync, UI, ads, …) ranked by how often it appears and how much it drags the rating — plus a de-duplicated list of concrete feature requests.

## INPUTS
- `app_id` (App Store numeric id) and/or `package` (Google Play package) — at least one REQUIRED.
- `country` (OPTIONAL, default `us`): App Store storefront.
- `stores` (OPTIONAL, default `appstore,play`).
- `max_reviews` (OPTIONAL, default 500) and `max_pages` (OPTIONAL, default 10): cost guards.
- `min_mentions` (OPTIONAL, default 3): minimum review count for a term/theme to surface.
- `top` (OPTIONAL, default 30): cap on returned candidates/requests.

## AUTHENTICATION (App Store RSS + Google Play Developer API)
1. App Store reviews use the public RSS JSON feed — NO key required: `GET https://itunes.apple.com/{country}/rss/customerreviews/id={app_id}/sortBy=mostRecent/page={n}/json`.
2. Google Play reviews REQUIRE env `GOOGLE_PLAY_ACCESS_TOKEN` (an OAuth bearer minted from `GOOGLE_PLAY_SERVICE_ACCOUNT`, scope `androidpublisher`). IF `play` is requested with a `--package` and the token is unset THEN STOP `error.code="AUTH_MISSING_PLAY_TOKEN"`. `401` → STOP `AUTH_EXPIRED`; `403` → STOP `PLAY_ACCESS_DENIED`.
3. Play endpoint: `GET https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{package}/reviews` (paged via `tokenPagination.nextPageToken`).

## EXPECTED TOOL CALLS
- Run `scripts/review_keyword_miner.py --app-id 6001112223 --package com.acme.budget --country us --max-reviews 500`.
- One RSS page fetch per App Store page (up to `--max-pages`); paged Play fetches until the token runs out or the review cap is hit.

## PROCEDURE (deterministic)
STEP 1 — FETCH reviews from each requested store up to `--max-reviews`, normalizing every review to `{store, rating, text}`; skip the RSS app-header entry.
STEP 2 — SENTIMENT per review: combine star rating with POS/NEG lexicon hits into `positive|negative|neutral`.
STEP 3 — TOKENIZE each review into unigrams + adjacent bigrams (lowercase, stop words removed, len>2), counting document frequency, rating-weighted stars, and per-term sentiment mix.
STEP 4 — KEYWORD CANDIDATES: keep terms with `frequency >= min_mentions` that are not request cue-words; attach `avg_rating`, `dominant_sentiment`, and `ngram` type; sort by frequency.
STEP 5 — THEMES: match each review against theme regexes (crashes, bugs, performance, ui_ux, pricing_subscription, ads, login_account, sync_data, notifications, support); compute `mentions`, `avg_rating`, and `rating_impact = mentions × (overall_avg − theme_avg)`; classify complaint/praise/mixed; sort by rating impact.
STEP 6 — FEATURE REQUESTS: on a request cue (`wish`, `please add`, `ability to`, …) capture the following object phrase and aggregate identical requests by count.

## RATE LIMITS & ERROR HANDLING
- `429` on either store → backoff `2^attempt` (max 5) then return what was gathered with `fetch_notes[store]="rate_limited"`.
- `5xx`/timeout retry ≤3 then stop paging that store (`partial`).
- Concurrency ≤ 1 per store, `time.sleep(0.3)` between pages; review volume capped by `--max_reviews` / `--max_pages`.

## MISSING / INSUFFICIENT DATA
- IF zero reviews are returned THEN `status="insufficient"` with a reason and `reviews_analyzed=0` — NEVER fabricate reviews, terms, or sentiment.
- Reviews with an empty body are dropped; a review with no star rating still contributes text but not to rating averages.
- Themes with zero mentions are omitted; `rating_impact` is null when a theme has no rated reviews.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/review_keyword_miner.py` — dual-store review fetch, lexicon sentiment, n-gram mining, theme + feature-request clustering.
- `references/output.schema.json` — output contract.
