---
name: Competitor Content Gap Matrix
description: Builds each competitor's ranking-keyword set and diffs them against the target's to find shared gaps — terms multiple competitors rank for but the target does not — returning a prioritized gap matrix by volume, overlap, and difficulty. Use when the user wants a content gap analysis, keyword opportunities competitors own, or topic-coverage gaps versus rivals.
category: competitor-analysis
---

# Competitor Content Gap Matrix

AGENT ROLE: Autonomous content-gap agent. Determine, across a keyword universe, which terms competitors rank for that the target does not, and emit the JSON in `references/output.schema.json`. Distinct from on-page entity gap analysis — this is site-level keyword/ranking coverage.

## OBJECTIVE
Produce a gap matrix: keywords where ≥ N competitors rank in the top results but the target does not (or ranks poorly) — prioritized by search volume, competitor overlap, and ranking difficulty — as a content roadmap.

## INPUTS
- `keywords` (REQUIRED string[]): the keyword universe to evaluate (a seed set or discovered list).
- `target` (REQUIRED): the user's domain.
- `competitors` (REQUIRED string[]): 2–10 competitor domains.
- `min_competitor_overlap` (OPTIONAL, default 2): min competitors ranking to call it a gap.
- `target_rank_floor` (OPTIONAL, default 10): the target "has" a keyword only if it ranks ≤ this.
- `volumes` (OPTIONAL): map `keyword → monthly_volume` for prioritization.

## AUTHENTICATION
- Ranking detection via SERP: REQUIRE `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
- Target rankings can additionally use GSC (`GOOGLE_APPLICATION_CREDENTIALS`/OAuth) when connected, for the target's true positions; otherwise the SERP determines the target's rank too.

## EXPECTED TOOL CALLS
- Run `scripts/content_gap_matrix.py --keywords keywords.json --target example.com --competitors a.com,b.com`.
- One SERP fetch per keyword; record top-N domains and each competitor's + the target's position.

## PROCEDURE (deterministic, per keyword)
STEP 1 — FETCH the SERP; map domain→position for the top N.
STEP 2 — `competitors_ranking` = competitors with position ≤ N; `target_ranks` = target position ≤ `target_rank_floor`.
STEP 3 — GAP IF `len(competitors_ranking) >= min_competitor_overlap` AND NOT `target_ranks`.
STEP 4 — For each gap record: volume (if provided), competitor overlap count + their positions, best competitor position (a difficulty proxy — if competitors rank #1–3 it is harder), and the target's current position (or "not in top N").
STEP 5 — SCORE `priority = volume_weight * overlap / difficulty`; EMIT the gap matrix sorted by priority desc, plus a `shared_by_all` subset (every competitor ranks, target does not).

## RATE LIMITS & ERROR HANDLING
- SERP `429`/quota → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` with `partial`.
- `5xx`/timeout retry ≤3 then mark keyword `skipped`; continue.
- GSC `429` → backoff (max 4); on failure fall back to SERP-derived target position.

## MISSING / INSUFFICIENT DATA
- IF `volumes` absent THEN rank by overlap + difficulty only and set `priority_basis="no_volume"`.
- A keyword where the target ranks #11–20 is a "near gap" (`target_near=true`), reported distinctly from a full gap — it may be an easier win.
- Never assert a gap when the target ranks within `target_rank_floor`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/content_gap_matrix.py` — SERP ranking detection, gap computation, prioritization.
- `references/output.schema.json` — output contract.
