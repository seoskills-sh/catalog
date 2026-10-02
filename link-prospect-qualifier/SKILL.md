---
name: link-prospect-qualifier
description: Discovers candidate sites for a topic via SERP and competitor-backlink sources, then scores each on relevance, authority, spam risk, and whether it already links to a competitor, returning a qualified outreach list. Use when the user wants link-building prospects, outreach targeting, or to qualify a prospect list at scale.
metadata:
  title: Link Prospect Qualifier
  category: link-building
---

# Link Prospect Qualifier

AGENT ROLE: Autonomous link-prospecting agent. Discover, dedupe, and score outreach prospects, then emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Build a qualified, deduplicated list of link-outreach prospects for a topic — ranked by topical relevance, authority, low spam risk, and existing competitor-link signal — with a contact-page hint per prospect.

## INPUTS
- `topic` (REQUIRED): the subject / niche (e.g., "sustainable packaging").
- `search_footprints` (OPTIONAL string[]): query templates to discover prospects (e.g., `"{topic}" "write for us"`, `"{topic}" resources`, `"{topic}" inurl:links`). Defaults provided.
- `competitors` (OPTIONAL string[]): to source prospects from competitor backlinks (needs a backlink API).
- `exclude_domains` (OPTIONAL): the user's own + known-bad domains.
- `max_prospects` (OPTIONAL, default 200).

## AUTHENTICATION
- Discovery via SERP: REQUIRE `SERP_API_KEY`. IF unset AND no `competitors` provided THEN STOP `error.code="NO_DISCOVERY_SOURCE"`.
- Competitor-backlink discovery: `DATAFORSEO_LOGIN`+`DATAFORSEO_PASSWORD` or `BACKLINK_API_KEY` (optional; skipped with a note if absent).
- Authority + spam scoring reuse the backlink provider's domain rank when available; otherwise a lightweight on-page heuristic.

## EXPECTED TOOL CALLS
- Run `scripts/link_prospects.py --topic "..." [--competitors a.com,b.com]`.
- SERP: fetch each footprint query's organic results. Backlink: fetch competitor referring domains. Merge candidate domains.

## PROCEDURE (deterministic)
STEP 1 — DISCOVER candidate domains from (a) SERP footprint queries and (b) competitor referring domains; dedupe by registrable domain; drop `exclude_domains`.
STEP 2 — For each candidate compute:
  - `relevance`: topical match of the candidate's title/snippet (and category if from backlinks) to `topic`.
  - `authority`: provider domain rank if available, else a proxy (SERP frequency + HTTPS + age hints).
  - `spam_risk`: spam TLD, thin footprint, or over-optimized signals (reuse the toxic-signal heuristics).
  - `links_to_competitor`: true IF the candidate is in a competitor's referring set.
STEP 3 — QUALIFY: `qualified = relevance >= 0.3 AND spam_risk < high`; SCORE `priority = authority * relevance * (1.3 if links_to_competitor else 1)`.
STEP 4 — CONTACT HINT: probe the candidate for a `/contact`, `/write-for-us`, or author/email pattern (≤1 GET, honor robots) → `contact_hint`.
STEP 5 — EMIT qualified prospects sorted by priority desc, plus a `disqualified` count with reasons.

## RATE LIMITS & ERROR HANDLING
- SERP `429`/quota → backoff `2^attempt` (max 5); after that STOP `error.code="RATE_LIMITED"` with `partial`.
- Backlink provider `402`/`429` → skip backlink discovery with a note; SERP prospects still returned.
- Contact-hint GETs are best-effort; failure → `contact_hint=null`.

## MISSING / INSUFFICIENT DATA
- IF neither discovery source yields candidates THEN `status="no_prospects"`, empty list, no error.
- Relevance/authority proxies (no backlink API) are coarse — mark `scoring_basis="heuristic"` so the user weights accordingly.
- Never mark a prospect qualified on authority alone; relevance is required (irrelevant high-authority sites are not link targets).

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/link_prospects.py` — SERP + backlink discovery, qualification scoring, contact hint.
- `references/output.schema.json` — output contract.
