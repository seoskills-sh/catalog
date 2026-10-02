---
name: gbp-suspension-monitor
description: Polls the Google Business Profile API across locations to detect suspensions, verification loss, and unauthorized edits to NAP, categories, or hours, then alerts on any status change with exact field diffs and a remediation path. Use when the user manages Google Business Profiles and needs suspension monitoring, listing-integrity alerts, or NAP-edit detection.
metadata:
  title: GBP Suspension Monitor
  category: local-seo
---

# GBP Suspension Monitor

AGENT ROLE: Autonomous listing-integrity agent. Snapshot each location's Business Profile state, diff it against the prior snapshot, and emit alerts per `references/output.schema.json`. Stateful across runs via the `previous` input.

## OBJECTIVE
Detect — as early as possible — suspensions, loss of verification, and unauthorized/Google-applied edits to critical fields across many locations, with the exact before/after diff and a remediation recommendation for each.

## INPUTS
- `account_id` (REQUIRED): GBP account resource id (`accounts/{id}`).
- `location_ids` (OPTIONAL): subset to monitor; default all locations under the account.
- `previous` (OPTIONAL): prior run's `snapshots`; absent on first run (baseline).
- `watch_fields` (OPTIONAL): default `["title","storefrontAddress","phoneNumbers","categories","regularHours","websiteUri","openInfo.status"]`.

## AUTHENTICATION (Google Business Profile API)
1. REQUIRE an OAuth access token with scope `https://www.googleapis.com/auth/business.manage` (env `GBP_OAUTH_TOKEN` or `GOOGLE_APPLICATION_CREDENTIALS` for a delegated service account).
   - IF absent THEN STOP `error.code="AUTH_MISSING_TOKEN"`.
2. NOTE: the Business Profile APIs require **Google-approved project access** (an allowlist request). IF the API returns `403 PERMISSION_DENIED` or `SERVICE_DISABLED` THEN STOP `error.code="API_ACCESS_NOT_APPROVED"` with the message: "Request Business Profile API access for this GCP project; it is gated behind an approval form."
3. Endpoints: locations via `GET https://mybusinessbusinessinformation.googleapis.com/v1/{account_id}/locations?readMask=...`; verification/state via the location's `metadata` + Verifications API.

## EXPECTED TOOL CALLS
- Run `scripts/gbp_monitor.py --account accounts/123 [--previous previous.json]`.
- List locations (paged via `pageToken`) with a `readMask` limited to `watch_fields` + `metadata`.

## PROCEDURE (deterministic, per location)
STEP 1 — SNAPSHOT: capture the `watch_fields` values + `metadata` flags (`hasVoiceOfMerchant`, `canOperateLocalPost`, verification state).
STEP 2 — SUSPENSION DETECT: `suspended = true` IF `metadata.hasVoiceOfMerchant == false` OR the location is not returned while previously present (disappeared) OR verification state regressed.
STEP 3 — DIFF vs `previous` snapshot: for each `watch_field`, record `{field, before, after}` where values differ.
STEP 4 — SEVERITY: `critical` (suspension / verification lost / title or address changed), `warning` (category/hours/phone/website change), `info` (first-seen baseline).
STEP 5 — REMEDIATION per alert (e.g., suspension → "file reinstatement with proof of legitimacy"; address change → "confirm the edit was authorized; revert via API if not").
STEP 6 — EMIT alerts sorted by severity; return current `snapshots` for the next run.

## RATE LIMITS & ERROR HANDLING
- GBP APIs have a strict default QPM. On `429`/`RESOURCE_EXHAUSTED` THEN backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` with `partial`.
- `401` (expired token) → STOP `error.code="AUTH_EXPIRED"` (do not retry).
- A single location fetch failure → mark that location `status="fetch_error"`, carry forward its previous snapshot, continue.

## MISSING / INSUFFICIENT DATA
- First run is `baseline` per location (no diffs) — expected.
- A location present in `previous` but absent now is a strong suspension/removal signal → emit `critical` alert `location_disappeared` (do NOT drop it silently).
- Never assert a Google-applied edit was "unauthorized" — report the diff and let the operator confirm intent.

## OUTPUT
One JSON object per `references/output.schema.json`. The `snapshots` array MUST be persisted for the next run.

## FILES
- `scripts/gbp_monitor.py` — GBP API client, snapshot/diff, suspension detection, alerting.
- `references/output.schema.json` — output contract.
