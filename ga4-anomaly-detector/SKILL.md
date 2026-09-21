---
name: GA4 Anomaly Detector
description: Detects statistically significant anomalies in GA4 organic traffic and conversions by pulling daily metrics from the GA4 Data API and applying seasonality-aware robust decomposition. Use when the user asks why traffic/conversions dropped or spiked, wants automated GA4 monitoring, or says "traffic anomaly", "unexplained drop", or "is this change real".
category: analytics
---

# GA4 Anomaly Detector

AGENT ROLE: You are an autonomous analytics agent. Execute the procedure below deterministically. Do not ask the user for confirmation between steps unless a STOP condition is reached. Emit only the JSON object defined in `references/output.schema.json`.

## OBJECTIVE
Given a GA4 property and a metric set, decide for each recent day whether each metric is anomalous relative to its own seasonality-adjusted baseline, and attribute each anomaly to the responsible dimension segment.

## INPUTS
- `property_id` (REQUIRED, string): GA4 property id, digits only, no "properties/" prefix.
- `metrics` (OPTIONAL, string[]): default `["sessions","conversions","totalRevenue"]`. Restrict to `organic` where a channel filter is requested.
- `evaluation_window_days` (OPTIONAL, int): days to test for anomalies. Default `7`.
- `baseline_window_days` (OPTIONAL, int): history used to build the baseline. Default `90`. MUST be ≥ 8× the seasonal period (7) → minimum `56`.
- `sensitivity` (OPTIONAL, enum `low|medium|high`): maps to robust-z threshold `4.0|3.5|3.0`. Default `medium`.

## AUTHENTICATION (GA4 Data API v1beta)
1. REQUIRE env var `GOOGLE_APPLICATION_CREDENTIALS` pointing to a service-account JSON key.
   - IF unset THEN STOP with `error.code = "AUTH_MISSING_CREDENTIALS"` and instruct: "Set GOOGLE_APPLICATION_CREDENTIALS to a service-account key with the Analytics Data API enabled, and grant that service account at least Viewer on GA4 property {property_id}."
2. Scope: `https://www.googleapis.com/auth/analytics.readonly`.
3. Endpoint: `POST https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport`.
4. The service account email MUST be added to the GA4 property's Property Access Management. IF the API returns `403 PERMISSION_DENIED` THEN STOP with `error.code = "AUTH_NO_PROPERTY_ACCESS"`.

## EXPECTED TOOL CALLS
- Prefer executing `scripts/detect_anomalies.py` (it encapsulates auth, paging, backoff, and the statistics). Invoke:
  `python3 scripts/detect_anomalies.py --property {property_id} --metrics {csv} --eval {n} --baseline {n} --sensitivity {level}`
- IF a Python runtime is unavailable THEN fall back to direct HTTPS `runReport` calls as specified below and reproduce the statistics inline.

## PROCEDURE (deterministic)
STEP 1 — FETCH BASELINE
- Call `runReport` with `dateRanges = [{startDate: "{baseline_window_days}daysAgo", endDate: "yesterday"}]`, `dimensions = ["date"]`, `metrics = {metrics}`.
- IF `channel` filter requested THEN add `dimensionFilter` on `sessionDefaultChannelGroup == "Organic Search"`.
- Page via `limit=100000` + `offset` until `rowCount` exhausted.

STEP 2 — BUILD SEASONAL BASELINE (per metric)
- Group the daily series by weekday (0–6) to remove weekly seasonality.
- For each weekday group compute `median` and `MAD` (median absolute deviation). Robust sigma = `1.4826 * MAD`.
- IF `MAD == 0` for a group THEN set robust sigma = `1.4826 * mean(|x - median|)`; IF still 0 THEN mark that group `insufficient_variance` and skip anomaly scoring for it.

STEP 3 — SCORE EVALUATION WINDOW
- For each day D in the last `evaluation_window_days` and each metric M:
  - `robust_z = (value_D - weekday_median) / weekday_sigma`.
  - IF `abs(robust_z) >= threshold(sensitivity)` THEN flag anomaly; `direction = "drop"` if negative else `"spike"`.

STEP 4 — ATTRIBUTE (only for flagged days)
- For each flagged (D, M): re-query `runReport` for date D with `dimensions = ["sessionDefaultChannelGroup","landingPagePlusQueryString","deviceCategory"]`, `metrics=[M]`, `orderBys` desc by M, `limit=25`.
- Compute each segment's contribution to the delta vs the same weekday's median composition. Return the top 3 segments by absolute contribution as `drivers`.

STEP 5 — EMIT
- Return the object in `references/output.schema.json`. Sort `anomalies` by `abs(robust_z)` desc.

## RATE LIMITS & ERROR HANDLING
- GA4 Data API enforces per-property token quotas. IF HTTP `429` OR body `code == RESOURCE_EXHAUSTED` THEN exponential backoff: sleep `min(60, 2^attempt) + jitter(0..1s)`, max 5 attempts, THEN STOP with `error.code = "RATE_LIMITED"` and include `quota` echoed from `propertyQuota` in the last response.
- IF `5xx` THEN retry up to 3 times with the same backoff.
- Batch attribution queries; never issue more than 5 concurrent requests.

## MISSING / INSUFFICIENT DATA
- IF baseline returns `< 56` distinct dates THEN set `status = "insufficient_history"`, still score with widened threshold `+0.5`, and set `confidence = "low"`.
- IF a metric is entirely zero across the baseline THEN drop it from output and add its name to `skipped_metrics`.
- Never fabricate values. A day with no row is treated as `value = 0` only if GA4 confirms zero rows for a fully elapsed day; IF the day is still in progress (`today`) THEN exclude it.

## OUTPUT
Emit exactly one JSON object validating against `references/output.schema.json`. No prose.

## FILES
- `scripts/detect_anomalies.py` — reference implementation (auth, paging, backoff, MAD scoring).
- `references/output.schema.json` — required output contract.
