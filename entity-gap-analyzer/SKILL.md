---
name: entity-gap-analyzer
description: Extracts weighted entities and TF-IDF terms from the top-ranking pages for a target query and diffs them against the user's page, returning the missing and under-weighted topics to add ranked by competitor coverage. Use when the user asks how to improve content depth, close a topical gap versus competitors, or what entities and subtopics a page is missing.
metadata:
  title: TF-IDF Entity Gap Analyzer
  category: content
---

# TF-IDF Entity Gap Analyzer

AGENT ROLE: Autonomous content-gap agent. Compare the user's page against the collective entity/term coverage of the ranking set and emit the JSON in `references/output.schema.json`. Report gaps as evidence-ranked recommendations, not filler suggestions.

## OBJECTIVE
For a target query and the user's URL, quantify which entities and salient terms the top-ranking pages cover that the user's page under-covers, so the content can be expanded to competitive depth.

## INPUTS
- `query` (REQUIRED): the target keyword.
- `target_url` (REQUIRED): the user's page to improve.
- `competitor_count` (OPTIONAL, default 10): top organic results to profile.
- `entity_backend` (OPTIONAL enum `google_nl|tfidf`): default `google_nl` if a key is set, else `tfidf` (local, keyless).

## AUTHENTICATION
- `SERP_API_KEY` (REQUIRED) to fetch the ranking set. IF unset THEN STOP `error.code="AUTH_MISSING_SERP_KEY"`.
- `google_nl` backend: REQUIRE env `NL_API_KEY` (Cloud Natural Language API enabled). Endpoint `POST https://language.googleapis.com/v1/documents:analyzeEntities?key={NL_API_KEY}`. IF unset THEN fall back to the built-in `tfidf` backend and note `entity_backend="tfidf"`.
- Page fetches: keyless HTTPS GET, UA `seoskills-entity-gap/1.0`, honor robots.

## EXPECTED TOOL CALLS
- Run `scripts/entity_gap.py --query "..." --url {target_url}`.
- SERP fetch → top-N organic URLs → GET + extract main text of each → entity/term extraction on each doc and on the target.

## PROCEDURE (deterministic)
STEP 1 — FETCH the top-`competitor_count` organic URLs for `query` (exclude the target's own domain from competitors).
STEP 2 — EXTRACT main text from each competitor + the target (strip nav/boilerplate; require ≥ 200 words or mark `thin`).
STEP 3 — PROFILE terms:
  - `google_nl`: entities with `salience` per doc.
  - `tfidf`: compute TF-IDF over the corpus (competitors + target) after stopword removal; keep top terms per doc.
STEP 4 — AGGREGATE competitor coverage: for each entity/term, `coverage = docs_containing / competitor_count` and mean weight.
STEP 5 — DIFF vs target: `gap = coverage - target_presence`. Keep terms with `coverage >= 0.4` AND `target_presence` low.
STEP 6 — SCORE `priority = coverage * mean_weight`; EMIT gaps sorted by priority desc, plus `covered_well` (target already strong) for context.

## RATE LIMITS & ERROR HANDLING
- SERP `429`/quota → backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"`.
- NL API `429` → backoff (max 4); on persistent failure switch that doc to `tfidf` and set `mixed_backend=true`.
- Per-page fetch failure (timeout 12s, non-200, robots) → skip that competitor, note in `skipped_competitors`; continue if ≥ 3 competitors succeeded, else STOP `error.code="INSUFFICIENT_CORPUS"`.

## MISSING / INSUFFICIENT DATA
- IF the target page is `thin` (< 200 words) THEN still return gaps but set `target_thin=true`.
- Never recommend a term the target already covers well; the gap must be positive.
- Exclude the target's own domain from the competitor set to avoid self-comparison.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/entity_gap.py` — SERP + page extraction, NL/TF-IDF profiling, gap scoring.
- `references/output.schema.json` — output contract.
