---
name: inbound-anchor-risk-analyzer
description: Pulls the distribution of inbound anchor text across all referring domains and classifies it into branded, exact-match, partial, generic, and URL anchors, flagging unnatural exact-match ratios and spikes that signal over-optimization risk. Use when the user wants an anchor-text profile audit, penalty-risk assessment, or a natural anchor-ratio target.
metadata:
  title: Inbound Anchor Risk Analyzer
  category: link-building
---

# Inbound Anchor Risk Analyzer

AGENT ROLE: Autonomous anchor-profile agent. Aggregate the inbound anchor distribution, classify it, assess manipulation risk, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Report the inbound anchor-text distribution for a domain (and optionally per target page), classify anchors, and flag unnatural exact-match/commercial concentration or velocity spikes that indicate over-optimization or link-scheme risk — with a recommended natural target mix.

## INPUTS
- `target` (REQUIRED): the domain (or a specific URL) to analyze.
- `brand_terms` (REQUIRED string[]): to classify branded anchors correctly.
- `money_keywords` (OPTIONAL string[]): commercial terms to detect exact/partial commercial anchors.
- `exact_ceiling` (OPTIONAL, default 0.2): exact-match commercial ratio above which the profile is risky.

## AUTHENTICATION (Backlink API)
1. REQUIRE `DATAFORSEO_LOGIN`+`DATAFORSEO_PASSWORD` or `BACKLINK_API_KEY`.
   - IF absent THEN STOP `error.code="AUTH_MISSING_BACKLINK_PROVIDER"`.
2. Endpoint (DataForSEO): `POST https://api.dataforseo.com/v3/backlinks/anchors/live` with `{target, limit, order_by:["backlinks,desc"]}` → anchors with referring-domain counts.

## EXPECTED TOOL CALLS
- Run `scripts/inbound_anchors.py --target example.com --brand "Acme,Acme Inc"`.
- Fetch the anchors report (anchor text + referring-domain count + first-seen distribution when available).

## PROCEDURE (deterministic)
STEP 1 — FETCH anchors weighted by REFERRING DOMAINS (not raw backlinks — sitewide links skew raw counts).
STEP 2 — CLASSIFY each anchor: `branded`, `branded_plus_keyword`, `exact_match_commercial`, `partial_commercial`, `generic` ("click here", "website", "here"), `naked_url`, `empty_or_image`, `other`.
STEP 3 — DISTRIBUTION: ratios by referring-domain weight; compute `exact_commercial_ratio`, `branded_ratio`, `generic_ratio`.
STEP 4 — RISK FLAGS:
  - `OVER_OPTIMIZED_COMMERCIAL` IF `exact_commercial_ratio > exact_ceiling`.
  - `LOW_BRANDED` IF `branded_ratio < 0.3` (natural profiles are branded-heavy).
  - `VELOCITY_SPIKE` IF a large share of exact-commercial anchors share a tight first-seen window (link-scheme signal), when first-seen data is present.
STEP 5 — TARGET MIX: recommend a natural distribution (e.g., branded 40–60%, generic/URL 20–30%, partial ≤20%, exact ≤5–10%). EMIT distribution, flags, `risk_level`, and the recommended mix.

## RATE LIMITS & ERROR HANDLING
- Provider `429`/`402` → backoff `2^attempt` (max 5) then STOP `RATE_LIMITED`/`PROVIDER_PAYMENT_REQUIRED`.
- IF the anchors report is empty THEN `status="no_backlinks"`, no error.

## MISSING / INSUFFICIENT DATA
- Weight by referring domains; IF only backlink counts are available THEN note `weighting="backlinks"` and caveat that sitewide links may inflate ratios.
- Velocity analysis requires first-seen data; IF absent THEN skip `VELOCITY_SPIKE` and note `velocity_available=false`.
- Risk is advisory — mark `advisory=true`; do not claim a penalty is certain.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/inbound_anchors.py` — anchors fetch, classification, ratio + risk analysis.
- `references/output.schema.json` — output contract.
