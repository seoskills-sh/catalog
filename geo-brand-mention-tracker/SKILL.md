---
name: geo-brand-mention-tracker
description: Runs a defined prompt set through multiple LLM APIs on a schedule and detects whether a brand is mentioned, linked, and how it is framed, tracking presence rate, sentiment, and factual accuracy per engine over time. Use when the user asks how AI assistants describe their brand, whether ChatGPT/Gemini/Claude recommend them, or wants Generative Engine Optimization (GEO) monitoring. Supersedes the legacy geo-brand-mentions skill.
metadata:
  title: GEO Brand Mention Tracker
  category: ai-search
---

# GEO Brand Mention Tracker

AGENT ROLE: Autonomous GEO-monitoring agent. Query each configured answer engine with each prompt, extract brand presence/sentiment/accuracy deterministically, and emit the JSON in `references/output.schema.json`. This is measurement, not manipulation — never instruct an engine to praise the brand.

## OBJECTIVE
Measure, per engine and per prompt, whether the target brand appears in the generated answer, whether it is linked/cited, the sentiment of its framing, and whether stated facts about it are correct — then aggregate into presence-rate and sentiment trends.

## INPUTS
- `brand` (REQUIRED): `{ name, domains: string[], aliases?: string[] }`.
- `prompts` (REQUIRED string[]): buyer-intent questions (see `references/prompt_template.json` for structure).
- `engines` (OPTIONAL): subset of `["openai","anthropic","gemini","perplexity"]`. Default all with a configured key.
- `facts` (OPTIONAL): array of `{ claim, correct: bool }` ground-truths to accuracy-check.
- `baseline` (OPTIONAL): prior run's aggregate for trend deltas.
- `models` (OPTIONAL via `--model ENGINE=ID`, repeatable): override an engine's default model, for example `--model openai=chat-latest` (the Instant model ChatGPT uses).

## AUTHENTICATION (per engine)
- Read keys from env: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `PERPLEXITY_API_KEY`.
- For each engine in `engines`: IF its key is missing THEN skip that engine and add it to `skipped_engines` with reason `no_api_key`. IF `engines` end up empty THEN STOP `error.code="NO_ENGINE_CREDENTIALS"`.
- Endpoints: OpenAI `POST /v1/chat/completions`; Anthropic `POST /v1/messages` (header `anthropic-version`); Gemini `POST /v1beta/models/{model}:generateContent`; Perplexity `POST /chat/completions` (returns `citations`).
- Default models: OpenAI `gpt-6.1-sol`, Anthropic `claude-sonnet-5-5`, Gemini `gemini-3.8-flash`, Perplexity `sonar`. Override one with `--model ENGINE=ID`. The output's `models` records the models used, so a baseline from a different model is not compared blindly.

## EXPECTED TOOL CALLS
- Run `scripts/track_mentions.py --brand brand.json --prompts prompts.json`.
- Per (engine, prompt): send the user prompt; request citations where the engine supports them. `temperature=0` goes to Perplexity only: current OpenAI, Anthropic and Gemini models reject a non-default temperature or advise against it, so their answers vary between runs. Read presence rates across runs, not single answers.

## PROCEDURE (deterministic, per engine × prompt)
STEP 1 — SEND prompt; capture `answer_text` and `citations[]` (URLs) if provided.
STEP 2 — PRESENCE: `mentioned = true` IF `brand.name` or any alias appears (case-insensitive, word-boundary). `linked = true` IF any `brand.domains` appears in `answer_text` or `citations`.
STEP 3 — SENTIMENT: classify the sentence(s) mentioning the brand as `positive|neutral|negative` using a strict rubric (recommendation/superlative → positive; caveat/warning/negative comparative → negative; else neutral). Record the exact quoted sentence as `evidence`.
STEP 4 — ACCURACY (if `facts`): for each ground-truth claim, check whether the answer asserts it correctly, incorrectly, or not at all → `correct|incorrect|absent`. Any `incorrect` about the brand = `hallucination` flag.
STEP 5 — AGGREGATE per engine: `presence_rate = mentioned_count / prompt_count`; `link_rate`; sentiment distribution; `accuracy_rate`.
STEP 6 — TREND (if baseline): delta each aggregate vs baseline.
STEP 7 — EMIT.

## RATE LIMITS & ERROR HANDLING
- Respect each provider's RPM/TPM. On `429` THEN honor `Retry-After` if present, else backoff `2^attempt` (max 5); after that mark that (engine,prompt) `status="rate_limited"` and continue — never abort the whole run.
- On `5xx`/timeout (120s) THEN retry ≤3; then record `status="engine_error"` with its HTTP `code` for that cell. `code` 404 on every cell usually means the vendor retired the model: override it with `--model`.
- Cap concurrency at 3 per engine.

## MISSING / INSUFFICIENT DATA
- IF an engine returns an empty/blocked answer THEN `mentioned=false`, `status="empty_answer"` — do not infer absence as negative sentiment.
- Sentiment/accuracy are computed ONLY when the brand is mentioned; otherwise `null`.
- Never claim a citation the engine did not return.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/track_mentions.py` — multi-engine client, presence/sentiment/accuracy extraction, aggregation.
- `references/prompt_template.json` — recommended buyer-intent prompt structure.
- `references/output.schema.json` — output contract.
