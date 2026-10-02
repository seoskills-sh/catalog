---
name: seo-metrics-api-orchestrator
description: Fetches a requested metric set (domain authority, backlinks, keyword volume, difficulty) across whichever vendors are configured (Ahrefs, Semrush, Moz, DataForSEO), coalescing calls so one vendor endpoint that returns several metrics is hit once, enforcing a per-vendor credit budget with backoff, and degrading gracefully to a partial, reasoned result when a vendor is exhausted, unconfigured, or failing. Use when the user wants the same SEO metrics from multiple tools at once, a single source-attributed metric table, or to fetch metrics under a strict API credit budget.
metadata:
  title: SEO Metrics API Orchestrator
  category: integrations
---

# SEO Metrics API Orchestrator

AGENT ROLE: Autonomous multi-vendor metrics agent. Route each requested (metric, target) to the vendors that provide it and are configured, coalesce calls that share an endpoint, enforce each vendor's credit budget, and return a unified source-attributed table plus a spend report. Emit the JSON in `references/output.schema.json`. Never fabricate a value a vendor did not return.

## OBJECTIVE
Get the same metric set from every configured vendor under a hard credit budget, attribute each value to its source, compute a cross-vendor consensus, and stay useful (partial + reasons) when a vendor is missing or spent — instead of failing the whole request.

## INPUTS
- `domains` (OPTIONAL): comma-separated domains (targets for `domain_authority`, `backlinks`).
- `keywords` (OPTIONAL): comma-separated keywords (targets for `volume`, `difficulty`).
- `metrics` (OPTIONAL, default all four): subset of `domain_authority,backlinks,volume,difficulty`.
- `budgets` (OPTIONAL): JSON object of vendor→credit budget, e.g. `{"ahrefs":100,"semrush":50}` (default 100 each).
- `max-requests` (OPTIONAL, default 1000): global network-call guard.

## AUTHENTICATION (multi-vendor; at least one required)
Configure any of, and only those are used:
1. Ahrefs: `AHREFS_API_TOKEN` (Bearer).
2. Semrush: `SEMRUSH_API_KEY` (optional `SEMRUSH_DB`, default `us`).
3. Moz Links API v2: `MOZ_ACCESS_ID` + `MOZ_SECRET_KEY` (HTTP Basic).
4. DataForSEO: `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD` (HTTP Basic).
IF none is configured THEN STOP `error.code="AUTH_NO_VENDOR_CONFIGURED"`. A vendor whose creds are absent is simply skipped with reason `unconfigured` (never an error).

## EXPECTED TOOL CALLS
- Run `scripts/metrics_orchestrator.py --domains example.com --keywords "seo tools" --metrics domain_authority,backlinks,volume,difficulty`.
- One network call per unique (vendor endpoint, target); Moz `url_metrics` and Semrush `phrase_this` each return multiple metrics and are coalesced to a single call per target.

## PROCEDURE (deterministic)
STEP 1 — DETECT configured vendors; STOP if none. Validate requested metrics against the route table.
STEP 2 — BUILD the request set: for each metric, for each target of its type, list the supporting vendors in preference order.
STEP 3 — For each (vendor, endpoint-group, target): IF the group was already fetched for this target THEN reuse it (dedup/coalesce — no extra credit). ELSE check the vendor budget: IF `spent + cost > budget` THEN mark the vendor `exhausted` and skip (reason `budget_exhausted`); ELSE call, charging `cost` credits, with `2^attempt` backoff.
STEP 4 — EXTRACT the metric from the group result. Collect one attributed value per vendor that answered.
STEP 5 — For each (metric, target) row: IF ≥1 value THEN `status="ok"` with `sources`, per-vendor `values`, and a `consensus` mean. ELSE `status="unavailable"` with the per-vendor `reasons`.
STEP 6 — EMIT the rows, a per-vendor `spend_report` (requests, credits spent, remaining, status), the count of coalesced calls saved, and `degraded` when any vendor was skipped/exhausted/rate-limited or any row is unavailable.

## RATE LIMITS & ERROR HANDLING
- Any vendor `429` → backoff `2^attempt` (max 5); persistent → vendor status `rate_limited`, its pending rows degrade (not the whole run).
- `5xx` retry ≤3; DataForSEO `402` → reason `payment_required`. Calls are paced (`sleep 0.2`); effective concurrency 1.
- Budgets are enforced BEFORE each network call so a run can never overspend a vendor's credits.

## MISSING / INSUFFICIENT DATA
- A vendor that returns no value for a target yields reason `no_value`; the value is never invented.
- Semrush "NOTHING FOUND" and empty vendor responses degrade that source only.
- A metric with no supplied targets of its type is skipped with a note (e.g. `volume` with no `--keywords`).
- If every vendor for a row is unconfigured/exhausted/failing, the row is `unavailable` with reasons — the run still returns `status="partial"`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/metrics_orchestrator.py` — vendor routing, call coalescing, credit budgeting, unified table + spend report.
- `references/output.schema.json` — output contract.
