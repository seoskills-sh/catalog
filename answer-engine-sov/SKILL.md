---
name: answer-engine-sov
description: Runs a category prompt set across multiple answer engines and tallies citations and mentions for the brand and each named competitor to compute share-of-voice per engine and per topic cluster. Use when the user wants competitive AI visibility benchmarking, share of voice in ChatGPT/Perplexity/Gemini answers, or to see who dominates AI answers in their category.
metadata:
  title: Answer Engine Share-of-Voice Reporter
  category: ai-search
---

# Answer Engine Share-of-Voice Reporter

AGENT ROLE: Autonomous competitive-intelligence agent. Query each engine with each prompt, tally brand and competitor appearances, compute share-of-voice, and emit the JSON in `references/output.schema.json`. Objective measurement only.

## OBJECTIVE
Quantify each brand's Share of Voice (SoV) across answer engines for a category: what fraction of relevant AI answers mention/cite each competitor, broken down by engine and by topic cluster, with an overall SoV leaderboard.

## INPUTS
- `brands` (REQUIRED): array of `{ name, aliases?: string[], domains?: string[] }` — the user's brand PLUS competitors (mark the user's with `is_self: true`).
- `prompts` (REQUIRED): array of `{ text, cluster }` — buyer/category questions grouped by topic cluster.
- `engines` (OPTIONAL): subset of `["openai","anthropic","gemini","perplexity"]`. Default all configured.
- `weighting` (OPTIONAL enum `mention|citation|first_mention`): how a "voice" is counted. Default `mention`.

## AUTHENTICATION (per engine)
- Env keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `PERPLEXITY_API_KEY`.
- IF an engine's key is missing THEN skip it → `skipped_engines[]`. IF none configured THEN STOP `error.code="NO_ENGINE_CREDENTIALS"`.
- Same endpoints as the GEO Brand Mention Tracker; `temperature=0`.

## EXPECTED TOOL CALLS
- Run `scripts/share_of_voice.py --brands brands.json --prompts prompts.json`.
- Per (engine, prompt): send prompt; capture `answer_text` and `citations`.

## PROCEDURE (deterministic)
STEP 1 — For each (engine, prompt) get the answer.
STEP 2 — For each brand compute a `voice` per the `weighting`:
  - `mention`: 1 IF name/alias appears (word-boundary, case-insensitive).
  - `citation`: 1 IF a brand domain appears in `answer_text` or `citations`.
  - `first_mention`: 1 only to the brand mentioned earliest in the answer (position of first match).
STEP 3 — TALLY per engine: `voice_count[brand]`; `sov[brand] = voice_count[brand] / sum(all voice_counts)` (guard divide-by-zero).
STEP 4 — TALLY per cluster (across engines) similarly → `cluster_sov`.
STEP 5 — OVERALL: sum voices across engines & prompts → `overall_sov` leaderboard; include `self_rank` = the position of the `is_self` brand.
STEP 6 — EMIT with per-engine, per-cluster, and overall breakdowns.

## RATE LIMITS & ERROR HANDLING
- Per-engine backoff on `429` (honor `Retry-After`, else `2^attempt`, max 5); a rate-limited (engine,prompt) is recorded and excluded from denominators (`counted=false`) rather than dropped silently.
- `5xx`/timeout retry ≤3 then mark that cell `engine_error`, excluded from denominators.
- Concurrency ≤ 3 per engine.

## MISSING / INSUFFICIENT DATA
- IF an answer mentions NO brand from the set THEN it contributes 0 to all — record `no_brand_in_answer=true` (a real signal: the category answer ignores everyone tracked).
- SoV denominators use only successfully-answered cells; report `answered_cells` and `total_cells` so the user can judge coverage.
- Never award voice to a brand not actually present in the text/citations.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/share_of_voice.py` — multi-engine client + SoV tallying (mention/citation/first_mention).
- `references/output.schema.json` — output contract.
