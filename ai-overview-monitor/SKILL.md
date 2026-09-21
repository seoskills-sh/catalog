---
name: AI Overview Citation Monitor
description: Queries a keyword set through a SERP API that captures Google AI Overview (SGE) blocks, extracts the cited URLs and snippet text, and reports AI Overview trigger rate per keyword and whether the brand is cited versus competitors. Use when the user asks about Google AI Overviews, SGE visibility, being cited in AI Overviews, or losing clicks to AI answers.
category: ai-search
---

# AI Overview Citation Monitor

AGENT ROLE: Autonomous AI-Overview surveillance agent. For each keyword, retrieve the SERP via a SERP API, detect the AI Overview block, extract its cited sources, and emit the JSON in `references/output.schema.json`.

## OBJECTIVE
Report, per keyword: whether Google shows an AI Overview, which URLs it cites, whether the brand or competitors are among them, and the aggregate AI-Overview trigger rate and brand citation rate for the keyword set.

## INPUTS
- `keywords` (REQUIRED string[]).
- `brand_domains` (OPTIONAL string[]).
- `competitor_domains` (OPTIONAL string[]).
- `location` (OPTIONAL, default "United States"), `hl` (lang, default "en"), `device` (`desktop|mobile`, default mobile — AIO surfaces differ by device).
- `serp_provider` (OPTIONAL enum): default `serpapi`. Also supports `dataforseo` request shape via `references`-defined mapping.

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`.
   - IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`: "Set SERP_API_KEY for your SERP provider (SerpApi, DataForSEO, etc.)."
2. Provider endpoints:
   - `serpapi`: `GET https://serpapi.com/search.json?engine=google&q={kw}&api_key={key}&...` → AI Overview under `ai_overview` (sometimes returned as a `page_token` requiring a second `engine=google_ai_overview` fetch).
   - `dataforseo`: `POST` the SERP advanced task with `people_also_ask`/`ai_overview` item types (Basic auth).
3. This is third-party SERP data; Google is NOT queried directly. Record `serp_provider` in output.

## EXPECTED TOOL CALLS
- Run `scripts/aio_monitor.py --keywords keywords.json [--brand a.com] [--competitors b.com,c.com]`.
- Per keyword: fetch SERP; IF `ai_overview` present as a token THEN issue the provider's follow-up call to expand it before parsing.

## PROCEDURE (deterministic, per keyword)
STEP 1 — FETCH SERP for `{keyword, location, hl, device}`.
STEP 2 — DETECT AI Overview: `has_aio = true` IF the response contains a non-empty `ai_overview` block.
STEP 3 — EXTRACT citations: collect the block's `references`/`sources` URLs (dedupe, preserve order/rank).
STEP 4 — MATCH: `brand_cited` IF any `brand_domains` in the citation set; `competitors_cited` = matching competitor domains; capture each cited source's `position` within the AIO.
STEP 5 — ALSO capture the top organic position of the brand for the same keyword (for the "cited vs ranked" gap).
STEP 6 — AGGREGATE: `aio_trigger_rate = has_aio_count / keyword_count`; `brand_aio_citation_rate = brand_cited_count / has_aio_count`.
STEP 7 — EMIT, keywords with AIO first.

## RATE LIMITS & ERROR HANDLING
- SERP APIs bill per search and rate-limit. On `429`/quota THEN backoff `2^attempt` (max 5); after that STOP `error.code="RATE_LIMITED"` with `partial` = keywords processed. Do NOT silently drop keywords.
- On provider `5xx`/timeout retry ≤3 then mark that keyword `status="serp_error"`.
- Cap concurrency at 3 (SERP APIs meter aggressively).

## MISSING / INSUFFICIENT DATA
- IF a keyword returns no AI Overview THEN `has_aio=false`, `citations:[]` — a valid, common result (AIO does not trigger for every query), never an error.
- IF the AIO block is present but citations are withheld by the provider tier THEN `has_aio=true`, `citations_available=false` — do not fabricate sources.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/aio_monitor.py` — SERP-API client with AIO detection + citation extraction.
- `references/output.schema.json` — output contract.
