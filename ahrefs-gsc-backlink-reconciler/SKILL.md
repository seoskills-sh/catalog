---
name: Ahrefs-GSC Backlink Reconciler
description: Pulls referring domains from the Ahrefs API v3, reads the Search Console "Top linking sites" CSV export (Search Console has no links API, so the manual export is the honest first-party source), optionally adds Semrush, normalizes every host to its registrable domain, and reconciles the sources into one authoritative link set with per-domain source attribution, coverage stats, gained/lost domains since the last run, and discrepancies worth manual verification. Use when the user wants to merge Ahrefs and Search Console backlink data, find referring domains one tool misses, or audit link gains and losses across tools.
category: integrations
---

# Ahrefs-GSC Backlink Reconciler

AGENT ROLE: Autonomous link-reconciliation agent. Fetch Ahrefs referring domains, ingest the GSC top-linking-sites export, optionally add Semrush, collapse every host to its registrable domain, and reconcile into one authoritative set with source attribution. Emit the JSON in `references/output.schema.json`. Stateful across runs via `authoritative_domains`.

## OBJECTIVE
Produce a single deduped referring-domain list that is honest about where each domain was seen (Ahrefs, GSC, Semrush), quantify how much each tool uniquely contributes, and surface the domains each tool misses plus the run-over-run gains and losses — so link audits stop trusting any one vendor's index.

## INPUTS
- `target` (REQUIRED): root domain to reconcile (e.g. `example.com`).
- `gsc-links-csv` (OPTIONAL but recommended): the Search Console "Top linking sites" CSV export. Absent → Ahrefs is returned without reconciliation.
- `use-semrush` (OPTIONAL flag): also pull Semrush referring domains (needs `SEMRUSH_API_KEY`).
- `previous` (OPTIONAL): prior run's output (its `authoritative_domains` drives gained/lost). Absent → baseline.
- `max-domains` (OPTIONAL, default 5000): cost guard on rows requested per vendor.
- `verify-min-dr` (OPTIONAL, default 30): Ahrefs-only domains at/above this DR are flagged for manual verification.

## AUTHENTICATION (Ahrefs API v3; optional Semrush)
1. REQUIRE env `AHREFS_API_TOKEN`. IF unset THEN STOP `error.code="AUTH_MISSING_AHREFS_TOKEN"`.
   - Endpoint: `GET https://api.ahrefs.com/v3/site-explorer/refdomains` with header `Authorization: Bearer {token}`, `select=domain,domain_rating,dofollow_links,linked_domains,first_seen,last_seen`.
   - `401` → STOP `AUTH_INVALID_AHREFS_TOKEN`; `403` → STOP `AHREFS_FORBIDDEN`.
2. GSC has NO links API. The "Top linking sites" report must be exported by hand and passed as `--gsc-links-csv`. Do NOT claim GSC data without the CSV.
3. IF `--use-semrush` AND `SEMRUSH_API_KEY` set: `GET https://api.semrush.com/analytics/v1/?type=backlinks_refdomains` (returns `;`-delimited CSV). Missing key → degrade with `sources.semrush="skipped_no_key"`.

## EXPECTED TOOL CALLS
- Run `scripts/backlink_reconciler.py --target example.com --gsc-links-csv top_linking_sites.csv [--use-semrush] [--previous prev.json]`.
- One Ahrefs refdomains call; optionally one Semrush call; local CSV parse for GSC.

## PROCEDURE (deterministic)
STEP 1 — FETCH Ahrefs referring domains (capped at `max-domains`), collapsing subdomains to the registrable domain and keeping the strongest DR per domain.
STEP 2 — READ the GSC export; detect the host column (first column when unlabeled) and any linking-pages count; normalize hosts to registrable domains.
STEP 3 — OPTIONALLY fetch Semrush referring domains and normalize.
STEP 4 — UNION into an authoritative set; attribute each domain to the `sources` that reported it and attach the best available attributes (DR, GSC linking pages, first seen).
STEP 5 — PARTITION vs GSC: `both`, `ahrefs_only`, `gsc_only`; compute overlap and each tool's unique share.
STEP 6 — FLAG verification candidates: `gsc_only` (Google sees a link Ahrefs misses) and high-DR `ahrefs_only` (authority domain Google's export omits — possibly nofollow/disallowed/uncounted).
STEP 7 — DIFF vs `previous.authoritative_domains` → `gained_domains`, `lost_domains`. First run → `status="baseline"`.

## RATE LIMITS & ERROR HANDLING
- Ahrefs `429` → backoff `2^attempt` (max 5) then STOP `RATE_LIMITED`; `5xx` retry ≤3.
- Semrush is best-effort: on `429`/error the run continues with `sources.semrush` carrying the reason (never fabricated).
- Registrable-domain mapping uses a curated two-level public-suffix set (heuristic, not the full PSL); it is documented, not silently exact.

## MISSING / INSUFFICIENT DATA
- No `--gsc-links-csv` → `reconciliation="skipped_no_csv"`; return the Ahrefs set only, do not invent GSC coverage.
- Empty GSC export → `sources.gsc="empty_export"`.
- First run (no `previous`) → `status="baseline"`; no gained/lost computed.
- Never merge a host into a domain it does not belong to; when the suffix is unknown the last two labels are used and this is disclosed.

## OUTPUT
One JSON object per `references/output.schema.json`. `authoritative_domains` MUST be persisted and passed back as the next run's `--previous`.

## FILES
- `scripts/backlink_reconciler.py` — Ahrefs + GSC + Semrush fetch, registrable-domain normalization, reconciliation and diff.
- `references/output.schema.json` — output contract.
