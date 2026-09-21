---
name: Toxic Backlink Auditor
description: Fetches the site's referring domains and scores each for toxicity from spam signals such as link farms, irrelevant TLDs, sitewide footer links, unnatural velocity, and over-optimized anchors, outputting a prioritized disavow-candidate list. Use when the user wants a backlink risk audit, disavow file preparation, or to assess penalty exposure.
category: link-building
---

# Toxic Backlink Auditor

AGENT ROLE: Autonomous backlink-risk agent. Score each referring domain against the toxicity signals in `references/toxicity_signals.json` and emit the JSON in `references/output.schema.json`, including a disavow candidate list. Recommend review; never auto-submit a disavow.

## OBJECTIVE
Assign each referring domain a toxicity score from spam signals, classify low/medium/high risk, and produce a prioritized disavow-candidate list with the reason per domain — so a human can review before disavowing.

## INPUTS
- `target` (REQUIRED): the domain to audit.
- `disavow_threshold` (OPTIONAL, default 70): toxicity score at/above which a domain is a disavow candidate.
- `max_domains` (OPTIONAL, default 5000).

## AUTHENTICATION (Backlink API)
1. REQUIRE a provider: `DATAFORSEO_LOGIN`+`DATAFORSEO_PASSWORD` (Basic) or `BACKLINK_API_KEY`.
   - IF absent THEN STOP `error.code="AUTH_MISSING_BACKLINK_PROVIDER"`.
2. Endpoint (DataForSEO): `POST https://api.dataforseo.com/v3/backlinks/referring_domains/live` with backlink attributes (rank, `backlinks`, `dofollow`, `first_seen`, `lost_date`, anchor sample).

## EXPECTED TOOL CALLS
- Run `scripts/toxic_backlinks.py --target example.com`.
- Fetch the referring-domain list with per-domain attributes needed by the signals (rank, backlink count, dofollow ratio, TLD, first-seen dates, anchors).

## PROCEDURE (deterministic, per referring domain)
STEP 1 — FETCH referring domains + attributes.
STEP 2 — SCORE toxicity by summing weighted signals from `toxicity_signals.json`:
  - very low domain rank / no organic presence.
  - spammy or irrelevant TLD (`.xyz`, `.top`, `.loan`, country mismatches at scale).
  - sitewide link (backlinks count ≈ pages, footer/blogroll pattern).
  - over-optimized commercial anchor from a low-quality domain.
  - unnatural velocity (many links first-seen in a tight burst).
  - nofollow-only from a link farm, or link networks (shared IP/registrant when available).
STEP 3 — CLASSIFY `low (<40) | medium (40–69) | high (>=70)`; cap at 100.
STEP 4 — DISAVOW CANDIDATES = domains with score >= `disavow_threshold`; format as `domain:example-spam.com` lines.
STEP 5 — EMIT the scored list (worst first), the disavow candidates, and profile-level stats (toxic %, dofollow ratio).

## RATE LIMITS & ERROR HANDLING
- Provider `429`/`402` → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`/`PROVIDER_PAYMENT_REQUIRED` with `partial`.
- Page through large profiles; HARD cap at `max_domains` (`hit_cap=true`).

## MISSING / INSUFFICIENT DATA
- Toxicity is PROBABILISTIC — always mark output `advisory=true` and require human review before disavow. Never claim certainty that a link is harmful.
- A missing attribute lowers that signal's contribution rather than assuming the worst.
- Recently-lost links (`lost_date` set) are noted but excluded from disavow candidates (already gone).

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/toxic_backlinks.py` — provider client, weighted toxicity scoring, disavow formatting.
- `references/toxicity_signals.json` — signals + weights + spam TLD list.
- `references/output.schema.json` — output contract.
