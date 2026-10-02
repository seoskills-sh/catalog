---
name: perplexity-citation-analyzer
description: Sends a set of target queries to the Perplexity API, parses the cited source URLs from each answer, and aggregates citation frequency by domain and page type to reveal which content earns AI citations. Use when the user asks what sources Perplexity cites, how to get cited by AI answer engines, or wants a citation gap analysis versus competitors.
metadata:
  title: Perplexity Citation Analyzer
  category: ai-search
---

# Perplexity Citation Analyzer

AGENT ROLE: Autonomous citation-intelligence agent. Query Perplexity for each target query, extract the citation set, aggregate by domain and content pattern, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
For a topic's query set, determine which domains and page types Perplexity cites most, whether the user's brand is cited, and where the citation gaps are versus competitors — so the user can target the formats that earn citations.

## INPUTS
- `queries` (REQUIRED string[]): the topic's buyer/informational questions.
- `brand_domains` (OPTIONAL string[]): the user's domains, to compute own citation share.
- `competitor_domains` (OPTIONAL string[]): to benchmark share of citations.
- `model` (OPTIONAL): default `sonar` (online model that returns citations).

## AUTHENTICATION (Perplexity API)
1. REQUIRE env `PERPLEXITY_API_KEY`.
   - IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`: "Set PERPLEXITY_API_KEY from your Perplexity API account."
2. Endpoint: `POST https://api.perplexity.ai/chat/completions`, header `Authorization: Bearer {key}`.
3. Only "online"/sonar models return a `citations` array — REQUIRE such a model; IF a non-citing model is requested THEN override to `sonar` and note `model_overridden=true`.

## EXPECTED TOOL CALLS
- Run `scripts/perplexity_citations.py --queries queries.json [--brand a.com,b.com] [--competitors c.com]`.
- Per query POST `{model, temperature:0, messages:[{role:"user", content:query}]}`; read `choices[0].message.content` AND the top-level `citations` array of URLs.

## PROCEDURE (deterministic)
STEP 1 — For each query, capture `citations` (list of URLs) and `answer_text`.
STEP 2 — NORMALIZE each citation: extract registrable domain; classify `page_type` from the path with `references`-free heuristics: `homepage` (path `/`), `blog_article` (`/blog|/article|/guide|/post`), `product` (`/product|/pricing|/features`), `docs` (`/docs|/help|/support`), `listicle` (title/URL contains `best|top|vs|review|alternatives`), else `other`.
STEP 3 — AGGREGATE:
  - `by_domain`: citation count + share (of total citations across all queries).
  - `by_page_type`: distribution → what FORMAT gets cited.
  - `brand_citation_rate` = queries where a `brand_domains` URL is cited / total queries.
  - `competitor_share`: per competitor domain, citation share; produce `share_of_citations` leaderboard.
STEP 4 — GAPS: queries where a competitor is cited but the brand is NOT → `citation_gaps` (highest-priority targets).
STEP 5 — EMIT, `by_domain` sorted by count desc.

## RATE LIMITS & ERROR HANDLING
- Perplexity enforces per-key RPM. On `429` THEN honor `Retry-After` else backoff `2^attempt` (max 5); after that mark that query `status="rate_limited"` and continue.
- On `5xx`/timeout (60s) retry ≤3 then `status="engine_error"` for that query.
- Serialize or cap concurrency at 2 to respect the rate limit.

## MISSING / INSUFFICIENT DATA
- IF a response has an empty `citations` array THEN record the query with `citations:[]`, `status="no_citations"` (the model answered from parametric knowledge) — this is a signal, not an error.
- Never infer a citation that is not in the returned `citations` array; do not parse URLs out of the prose as citations.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/perplexity_citations.py` — Perplexity client, citation normalization, aggregation.
- `references/output.schema.json` — output contract.
