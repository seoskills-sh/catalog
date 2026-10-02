---
name: crux-vitals-monitor
description: Polls the Chrome UX Report (CrUX) API for real-user field LCP, INP, and CLS by URL and device, classifies each against Google's thresholds, and detects regressions versus a stored baseline. Use when the user asks about field Core Web Vitals, real-user performance, passing CWV, or wants ongoing performance-regression monitoring.
metadata:
  title: Core Web Vitals CrUX Monitor
  category: technical-seo
---

# Core Web Vitals CrUX Monitor

AGENT ROLE: Autonomous field-performance agent. Query CrUX for each target, classify against the thresholds in `references/thresholds.json`, compare to any supplied baseline, and emit the JSON in `references/output.schema.json`. Field data only — do NOT substitute Lighthouse lab numbers.

## OBJECTIVE
Report the p75 of LCP, INP, and CLS per URL and form factor, the good/needs-improvement/poor rating for each, the overall CWV pass/fail, and any regression versus the previous run.

## INPUTS
- `targets` (REQUIRED): array of `{ "url": "..." }` OR `{ "origin": "https://example.com" }`. Origin returns aggregated data (useful when a URL lacks enough samples).
- `form_factors` (OPTIONAL): subset of `["PHONE","DESKTOP"]`. Default both.
- `baseline` (OPTIONAL): prior run's `results` array for regression comparison.
- `regression_delta` (OPTIONAL): min change to flag. Default: LCP/INP `+50` (ms worse), CLS `+0.02`.

## AUTHENTICATION (CrUX API)
1. REQUIRE env `CRUX_API_KEY` — a Google Cloud API key with the "Chrome UX Report API" enabled.
   - IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`: "Create an API key in Google Cloud, enable the Chrome UX Report API, and set CRUX_API_KEY."
2. Endpoint: `POST https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={CRUX_API_KEY}`.
3. This is an API key, NOT a service account — no OAuth scope, no property access needed (CrUX is public field data).

## EXPECTED TOOL CALLS
- Run `scripts/crux_monitor.py --targets targets.json [--baseline baseline.json]`.
- Per target × form factor: POST `{ "url"|"origin": ..., "formFactor": ..., "metrics": ["largest_contentful_paint","interaction_to_next_paint","cumulative_layout_shift"] }`.

## PROCEDURE (deterministic)
STEP 1 — For each target × form_factor call queryRecord.
STEP 2 — IF `404 record not found` (CrUX has no data for that URL) THEN:
  - IF the input was a `url` THEN retry once as `origin`; set `data_level="origin_fallback"`.
  - IF still 404 THEN emit `{status:"no_crux_data"}` for that target (common for low-traffic pages). Do NOT error the batch.
STEP 3 — Extract each metric's `percentiles.p75`. Rating: compare p75 to `thresholds.json` → `good | needs_improvement | poor`.
STEP 4 — `cwv_pass` = true IFF LCP, INP, and CLS are ALL `good` at p75.
STEP 5 — REGRESSION: IF baseline present, for each metric compute `delta = current_p75 − baseline_p75`; flag `regressed=true` IF delta ≥ `regression_delta[metric]` (worse direction). CLS worse = larger; LCP/INP worse = larger ms.
STEP 6 — EMIT results sorted poor-first.

## RATE LIMITS & ERROR HANDLING
- CrUX default quota is limited (per-key/minute + per-day). IF `429 RESOURCE_EXHAUSTED` THEN backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` with the count processed so far in `partial`.
- Serialize requests or cap concurrency at 2 to stay under the per-minute quota.
- IF `400 invalid url/origin` THEN record `{status:"bad_target"}` for that target and continue.

## MISSING / INSUFFICIENT DATA
- CrUX only reports metrics with sufficient real-user samples. IF a metric is absent from the response THEN set its value `null`, rating `insufficient_data`, and exclude it from `cwv_pass` (which then becomes `unknown`, never a false pass).
- Never fabricate a percentile; absence ≠ good.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/crux_monitor.py` — CrUX API client with fallback + regression logic.
- `references/thresholds.json` — Google's official CWV good/poor boundaries.
- `references/output.schema.json` — output contract.
