---
name: Audit Remediation Prioritizer
description: Ingests raw findings from any audit source (the JSON emitted by the other native audit skills, or a flat finding list), normalises the heterogeneous shapes into one finding model, deduplicates and groups them by type, and scores each by traffic-at-stake, impact, confidence, and effort (RICE or ICE) using real GSC/GA4 signals where available, then outputs a phase-sequenced remediation roadmap with owners, effort, and score plus ready-to-file ticket payloads. Use when the user has audit output to turn into a prioritized action plan, wants a RICE/ICE-scored roadmap, or asks "what do we fix first" instead of an undifferentiated issue dump.
category: audit
---

# Audit Remediation Prioritizer

AGENT ROLE: Autonomous remediation-planning agent (capstone). Read findings from other audits, normalise + dedupe + score them, sequence a roadmap, build ticket payloads, and emit the JSON in `references/output.schema.json`. NEVER file tickets without explicit user approval — the script only produces dry-run payloads.

## OBJECTIVE
Convert a pile of raw, overlapping audit findings into a defensible, sequenced plan: one scored record per unique issue, grouped into work templates, ordered into phases by return-on-effort, each with an owner, an estimate, and a filable ticket.

## INPUTS
- `findings` (REQUIRED): comma-separated JSON files. Each may be a full audit output (Site Migration, Indexation Coverage, Index Bloat, Canonicalization) or a generic list/`{findings:[...]}` of `{type, url|urls, severity, evidence, clicks}`.
- `traffic` (OPTIONAL): JSON map `url -> clicks|sessions` to weight reach when a finding carries no traffic of its own.
- `effort_map` / `owner_map` / `impact_map` (OPTIONAL): JSON overrides `finding_type -> value` for the built-in tables.
- `model` (OPTIONAL, `rice`|`ice`, default `rice`), `phases` (OPTIONAL, default 3), `jira_export` (OPTIONAL flag): build Jira-shaped payloads (still dry-run).

## AUTHENTICATION (none required)
- Scoring needs no credentials. Reach uses the traffic carried inside findings (e.g. Site Migration `clicks_at_risk`, Index Bloat `clicks`) or the optional `--traffic` map.
- Jira export is a PAYLOAD BUILDER only. This skill NEVER POSTs a ticket; `export_mode` is always `dry_run`. Filing tickets is a side-effecting action the agent must confirm with the user and perform explicitly outside this script. `jira_credentials_present` merely reports whether `JIRA_BASE_URL`/`JIRA_TOKEN` are set.

## EXPECTED TOOL CALLS
- Run `scripts/remediation_prioritizer.py --findings a.json,b.json[,...] [--traffic clicks.json] [--model rice] [--phases 3]`.
- The script auto-detects each file's shape (`at_risk`+`redirect_class` → migration; `gaps`+`cause` → indexation; `results`+`action` → bloat; `conflicts`+`conflict_types` → canonicalization; else generic) and maps every item to `{finding_type, urls, traffic_at_stake, evidence, source}`.

## PROCEDURE (deterministic)
STEP 1 — NORMALISE every input item to the canonical finding model; carry each source's own traffic signal.
STEP 2 — DEDUPE on `(finding_type, sorted(urls))`: merge duplicates across sources, keep the max traffic, union the source list, count merges.
STEP 3 — SCORE each finding. `impact` (0.5–3) and `effort` (points) come from the built-in `finding_type` table (overridable); a `severity` hint of high/low clamps impact. `confidence` = 0.5 + 0.3 (has traffic) + 0.2 (known type) − 0.1 (no URLs), clamped [0.3,1.0]. `reach` = traffic-at-stake, or the affected-URL count when no traffic exists. `RICE = reach·impact·confidence / effort`; `ICE = impact₁₀·confidence₁₀·ease₁₀ / 100`.
STEP 4 — GROUP findings into `finding_type` templates; aggregate score, URL count, and traffic; sort templates by score then traffic.
STEP 5 — SEQUENCE templates into `phases` equal-rank bands (phase 1 = highest ROI). Build one ticket payload per template. EMIT summary, roadmap, per-finding scores, and tickets.

## RATE LIMITS & ERROR HANDLING
- Pure local computation; no network, no rate limits. IF a `--findings` file is missing THEN STOP `error.code="INPUT_FILE_MISSING"`; IF it is not valid JSON THEN STOP `error.code="INPUT_INVALID"`.
- Unrecognised shapes contribute zero findings (reported per-source in `sources`), never a crash.

## MISSING / INSUFFICIENT DATA
- Zero recognisable findings → `status="insufficient"` with an empty roadmap and a reason (never a fabricated plan).
- No traffic anywhere → reach falls back to URL counts and `has_traffic_data=false`; scores are still comparable but confidence is lower. NEVER invent clicks or a severity the source did not provide.
- Unknown `finding_type` → default owner (by prefix), effort 3, impact 2, confidence without the "known type" bonus — surfaced honestly, not silently dropped.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/remediation_prioritizer.py` — shape detection/normalisation, dedupe, RICE/ICE scoring, phase sequencing, ticket payloads.
- `references/output.schema.json` — output contract.
