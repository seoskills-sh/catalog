---
name: backlink-gap-analyzer
description: Pulls referring domains for the target and a set of competitors from a backlink API and computes the set difference, returning domains that link to multiple competitors but not the target, ranked by authority and relevance. Use when the user wants a link gap analysis, competitor backlink intersection, or a prioritized outreach target list.
metadata:
  title: Backlink Gap Analyzer
  category: link-building
---

# Backlink Gap Analyzer

AGENT ROLE: Autonomous link-gap agent. Fetch referring domains for the target and competitors, compute the intersection gap, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Find the domains that link to competitors but not the target — especially those linking to MULTIPLE competitors (strongest signals) — ranked by domain authority and topical relevance as an outreach priority list.

## INPUTS
- `target` (REQUIRED): the target domain (e.g., `example.com`).
- `competitors` (REQUIRED string[]): competitor domains (2–10).
- `min_competitor_overlap` (OPTIONAL, default 2): only return domains linking to at least this many competitors.
- `max_results` (OPTIONAL, default 300).

## AUTHENTICATION (Backlink API)
1. REQUIRE a backlink data provider. Reference: DataForSEO Backlinks (Basic auth) via env `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD`; Ahrefs/Majestic supported by swapping the fetch layer (env `BACKLINK_API_KEY`).
   - IF no provider credentials THEN STOP `error.code="AUTH_MISSING_BACKLINK_PROVIDER"`: "Set DATAFORSEO_LOGIN/PASSWORD or BACKLINK_API_KEY — a paid backlink API is required."
2. Endpoint (DataForSEO): `POST https://api.dataforseo.com/v3/backlinks/referring_domains/live` with `{target, limit, order_by:["rank,desc"]}` per domain.

## EXPECTED TOOL CALLS
- Run `scripts/backlink_gap.py --target example.com --competitors a.com,b.com,c.com`.
- One `referring_domains` call per domain (target + each competitor). Cache within the run.

## PROCEDURE (deterministic)
STEP 1 — FETCH referring domains (with rank/authority + topical category if available) for the target and every competitor.
STEP 2 — TARGET SET = the target's referring domains (for exclusion).
STEP 3 — For each competitor domain's referring set, tally per referring-domain how many competitors it links to (`overlap`), keeping max authority + any competitor examples.
STEP 4 — GAP = referring domains with `overlap >= min_competitor_overlap` AND NOT in the target set.
STEP 5 — SCORE `priority = domain_rank * log(1 + overlap)`; EMIT gap domains sorted by priority desc, each with `overlap`, authority, and which competitors it links to.

## RATE LIMITS & ERROR HANDLING
- Provider quota/cost: on `429`/`402`(payment) THEN backoff `2^attempt` (max 5); after that STOP `error.code="RATE_LIMITED"` (or `PROVIDER_PAYMENT_REQUIRED` on 402) with `partial`.
- A single competitor fetch failure → exclude that competitor (note in `skipped_competitors`), continue IF ≥ 1 competitor and the target succeeded; else STOP `error.code="INSUFFICIENT_DATA"`.

## MISSING / INSUFFICIENT DATA
- IF the provider returns no referring domains for the target THEN treat the target set as empty (all competitor links become gaps) and set `target_has_no_backlinks=true`.
- Authority scales differ per provider — always record `authority_metric` (e.g., "domain_rank") so scores are interpretable.
- Never fabricate authority; a domain missing a rank gets `rank=0` and sorts last within its overlap tier.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/backlink_gap.py` — provider client, referring-domain intersection, gap scoring.
- `references/output.schema.json` — output contract.
