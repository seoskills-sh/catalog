---
name: store-listing-cvr-auditor
description: Evaluates an App Store or Google Play listing — icon, screenshot sequence, captions, subtitle, description, preview video, and localization — against ASO conversion heuristics and current store guidelines, scoring first-impression clarity and message hierarchy. Returns prioritized CVR fixes and a statistically valid A/B experiment plan (variants, traffic split, success metric, and minimum sample from a power calculation) for the right store mechanism. Use when the user wants a conversion-rate audit of an app listing, screenshot/icon feedback, or a store A/B test plan.
metadata:
  title: Store Listing CVR Auditor
  category: aso
---

# Store Listing CVR Auditor

AGENT ROLE: Autonomous store-listing conversion auditor. Score the creative and copy against ASO CVR heuristics, rank the fixes by impact, design a valid experiment, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Grade every conversion-critical element of a store listing, tell the agent which fixes will move the product-page conversion rate the most, and produce a ready-to-run A/B experiment (variants, even traffic split, success metric, minimum sample size) matched to the store's own experimentation mechanism.

## INPUTS
- `listing` (REQUIRED): local JSON with `app_id`, `platform`, optional `baseline_cvr`, `icon` metadata, `subtitle`, `description`, `screenshots[]` (`order, width, height, orientation, has_caption, caption`), `preview_video`, `locales_supported[]`.
- `platform` (OPTIONAL enum `ios|android`): overrides `listing.platform`.
- `mde` (OPTIONAL, default 0.10): minimum detectable effect (relative) for the sample-size calc.
- `fetch_live` (OPTIONAL flag): pull the live listing before auditing.

## AUTHENTICATION (App Store Connect / Play Console)
1. The offline audit reads `--listing` and needs NO credentials.
2. `--fetch-live` REQUIRES a bearer token: env `APP_STORE_CONNECT_TOKEN` (iOS, minted from `APP_STORE_CONNECT_KEY_ID` / `APP_STORE_CONNECT_ISSUER_ID` / `APP_STORE_CONNECT_PRIVATE_KEY` as an ES256 JWT) or `GOOGLE_PLAY_ACCESS_TOKEN` (Android). IF `--fetch-live` is set and the token is unset THEN STOP `error.code="AUTH_MISSING_STORE_TOKEN"`. `401` → STOP `AUTH_EXPIRED`.
3. Live endpoint: iOS `GET https://api.appstoreconnect.apple.com/v1/apps/{id}`; Android `GET https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{id}`.

## EXPECTED TOOL CALLS
- Run `scripts/listing_cvr_auditor.py --listing listing.json --platform ios [--mde 0.10 --fetch-live]`.
- Offline: zero network calls. Image metadata (dimensions, has_text, caption text) is supplied in the listing — the auditor scores metadata, it does not decode pixels.

## PROCEDURE (deterministic)
STEP 1 — LOAD the platform guideline set (icon size, alpha rule, subtitle max, screenshot max/recommended, mechanism, treatment cap).
STEP 2 — AUDIT ICON: exact dimensions, no baked-in text, no alpha where the store forbids it.
STEP 3 — AUDIT SCREENSHOTS: count vs recommended; the first 1-3 (shown in search) MUST be captioned; caption concision (≤45 chars); orientation consistency across the first set; preview-video presence.
STEP 4 — AUDIT TEXT: subtitle present and within length; description substantial enough to front-load value above the fold.
STEP 5 — AUDIT LOCALIZATION: flag single-locale listings as a coverage/CVR miss.
STEP 6 — SCORE each check (pass=1, warn=0.5, fail=0) weighted; roll up per-category and an overall 0-100. RANK non-passing checks by severity then weight into `prioritized_fixes` with an `impact` value.
STEP 7 — EXPERIMENT PLAN: pick the top fix as the test element; build control + treatment variants under the store mechanism (App Store PPO ≤3 treatments / Play ≤4); even traffic split; success metric per platform; `min_sample_per_variant` from a two-proportion normal approximation (alpha=0.05 two-sided, power=0.80) on `baseline_cvr` and `mde`.

## RATE LIMITS & ERROR HANDLING
- Live `429` → backoff `2^attempt` (max 5) then STOP `RATE_LIMITED`; `5xx`/timeout retry ≤3 then STOP `LIVE_FETCH_FAILED`.
- Concurrency ≤ 1 (a single app fetch). The offline audit has no rate surface.

## MISSING / INSUFFICIENT DATA
- IF `baseline_cvr` is absent THEN the sample size is computed on an assumed 0.30 and `baseline_assumed=true` is set with a note — NEVER present an assumed sample size as measured.
- IF neither icon nor screenshot metadata is supplied THEN `status="insufficient"` with a warning; partial listings still score the elements that are present.
- Heuristics grade supplied metadata only; the auditor never claims to have inspected image pixels it was not given.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/listing_cvr_auditor.py` — heuristic listing audit, weighted CVR scoring, prioritized fixes, power-based A/B plan.
- `references/output.schema.json` — output contract.
