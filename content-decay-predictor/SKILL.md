---
name: content-decay-predictor
description: Builds per-URL clicks and impressions time-series from Search Console, fits a trend to detect sustained decline and inflection points, and predicts which pages will keep decaying ranked by projected click loss. Use when the user asks which content to refresh, what pages are losing traffic, or wants a proactive content-refresh queue.
metadata:
  title: Content Decay Predictor
  category: content
---

# Content Decay Predictor

AGENT ROLE: Autonomous content-decay agent. Model each URL's organic trajectory, classify decay, project future loss, and emit the JSON in `references/output.schema.json`. Distinguish genuine sustained decay from seasonality and one-off dips.

## OBJECTIVE
Identify URLs on a sustained downward organic trajectory, estimate how much more traffic they will lose if untouched, and rank them so refresh effort targets the biggest recoverable losses.

## INPUTS
- `site_url` (REQUIRED): verified GSC property.
- `lookback_weeks` (OPTIONAL, default 26): history window (needs ≥ 12 for a stable trend).
- `min_baseline_clicks` (OPTIONAL, default 20): weekly-clicks floor at peak to consider a URL worth analyzing.
- `horizon_weeks` (OPTIONAL, default 8): projection horizon.

## AUTHENTICATION (Search Console API)
1. REQUIRE `GOOGLE_APPLICATION_CREDENTIALS` (SA) OR OAuth, scope `https://www.googleapis.com/auth/webmasters.readonly`.
   - IF absent THEN STOP `error.code="AUTH_MISSING_CREDENTIALS"`.
2. Verified user on `site_url`. IF `403` THEN STOP `error.code="AUTH_NO_SITE_ACCESS"`.
3. Endpoint: `searchAnalytics/query` with `dimensions=["page","date"]`, weekly-aggregated client-side.

## EXPECTED TOOL CALLS
- Run `scripts/decay.py --site {site_url} --weeks 26`.
- Fetch daily `page`×`date` clicks/impressions for the window (paged), aggregate to weekly per URL.

## PROCEDURE (deterministic, per URL)
STEP 1 — BUILD the weekly clicks series over `lookback_weeks`.
STEP 2 — QUALIFY: skip URLs whose peak weekly clicks < `min_baseline_clicks` (too small to matter) → `skipped`.
STEP 3 — DESEASONALIZE: subtract a 4-week trailing seasonal component (or use a robust trend via Theil–Sen slope, which resists outliers).
STEP 4 — CLASSIFY:
  - `peak_to_recent = mean(last 4 weeks) / max(rolling 4-week mean)`.
  - `decaying` IF Theil–Sen slope < 0 AND `peak_to_recent <= 0.7` (lost ≥30% from peak) sustained ≥ 4 weeks.
  - `inflection_week` = the week the sustained decline began.
STEP 5 — PROJECT: extend the slope `horizon_weeks` forward (floored at 0); `projected_additional_loss = current_weekly − projected_weekly_at_horizon`, summed over the horizon.
STEP 6 — SCORE `refresh_priority = projected_additional_loss` (biggest recoverable loss first); EMIT decaying URLs sorted desc.

## RATE LIMITS & ERROR HANDLING
- GSC `429`/`RESOURCE_EXHAUSTED` → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`.
- Only `final` `dataState` rows; exclude the trailing incomplete week.
- Large sites: cap analyzed URLs at the top 2000 by total clicks; note `truncated=true`.

## MISSING / INSUFFICIENT DATA
- IF a URL has < 12 weeks of data THEN mark `insufficient_history` and exclude from decay classification (cannot separate decay from noise).
- Seasonality guard: a decline that matches a prior-year same-season dip is flagged `seasonal_suspected=true`, not `decaying`, when multi-year data exists; otherwise note the caveat.
- Never predict negative traffic; floor projections at 0.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/decay.py` — GSC weekly series, Theil–Sen trend, decay classification + projection.
- `references/output.schema.json` — output contract.
