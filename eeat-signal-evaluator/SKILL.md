---
name: E-E-A-T Signal Evaluator
description: Crawls a page and extracts E-E-A-T signals — author identity and credentials, citations, first-hand experience markers, freshness, and external corroboration — scoring each via NLP and entity lookups into a per-signal scorecard. Use when the user asks how to improve trust and authority, audit YMYL content, or find missing E-E-A-T elements.
category: content
---

# E-E-A-T Signal Evaluator

AGENT ROLE: Autonomous trust-signal agent. Detect and score each E-E-A-T signal on a page against the rubric in `references/eeat_rubric.json` and emit the JSON in `references/output.schema.json`. Score observable signals only; never assert authority the page does not demonstrate.

## OBJECTIVE
Produce a per-signal E-E-A-T scorecard (Experience, Expertise, Authoritativeness, Trust) with the specific missing elements to add — grounded in what is actually present on the page and its author entity.

## INPUTS
- `url` (REQUIRED): the page to evaluate.
- `topic` (OPTIONAL): the page's subject, to check author-topic relevance.
- `is_ymyl` (OPTIONAL bool): if true, apply the rubric's stricter YMYL weights.
- `verify_author_entity` (OPTIONAL bool, default true): look the author up in the Knowledge Graph / Wikidata.

## AUTHENTICATION
- Page fetch: keyless HTTPS GET, UA `seoskills-eeat/1.0`, honor robots.
- `verify_author_entity`: uses the Google Knowledge Graph API IF `KG_API_KEY` is set, else Wikidata (keyless). IF neither resolves the author THEN the authoritativeness sub-check is marked `unverifiable`, not failed.

## EXPECTED TOOL CALLS
- Run `scripts/eeat.py --url {url} [--topic "..."] [--ymyl]`.
- GET the page; parse structured data (`Article`/`Person` schema, `author`, `datePublished`/`dateModified`), byline, bio, outbound citations, and experience-language cues.

## PROCEDURE (deterministic)
STEP 1 — FETCH + PARSE: extract author name (schema `author` → byline → rel=author), author bio/credential text, `datePublished`/`dateModified`, outbound links to authoritative domains, and citation/reference markers.
STEP 2 — EXPERIENCE: detect first-hand markers (`references/eeat_rubric.json` cue lists: "I tested", "we measured", original images, "in my experience"). Score by cue density + original-media presence.
STEP 3 — EXPERTISE: author bio contains credentials/qualifications relevant to `topic`; presence of a detailed author bio and `Person` schema with `jobTitle`/`knowsAbout`.
STEP 4 — AUTHORITATIVENESS: resolve the author entity (KG/Wikidata) and count authoritative outbound citations; site-level `sameAs`/organization signals.
STEP 5 — TRUST: `dateModified` recency, HTTPS, contact/about presence, citation of sources, absence of deceptive patterns, `Article` schema completeness.
STEP 6 — SCORE each pillar 0–100 via the rubric weights (YMYL weights if `is_ymyl`); overall = weighted mean. EMIT per-pillar scores + the concrete `missing_signals` list.

## RATE LIMITS & ERROR HANDLING
- Single page fetch; timeout 15s. IF non-200 THEN STOP `error.code="PAGE_UNREACHABLE"` with the status.
- KG/Wikidata `429` → backoff `2^attempt` (max 4); on failure set authoritativeness author-check `unverifiable`.
- Robots-disallowed → STOP `error.code="ROBOTS_DISALLOW"` unless `respect_robots=false`.

## MISSING / INSUFFICIENT DATA
- Absent author is itself a finding (`missing_signals: ["author_identity"]`), not an error.
- Distinguish "signal absent" (a fixable finding) from "could not verify" (author entity lookup failed) — never conflate them.
- Do not infer expertise from prose tone; require concrete credential/experience markers.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/eeat.py` — page parse, signal extraction, entity verification, rubric scoring.
- `references/eeat_rubric.json` — signal cues, weights, and YMYL overrides.
- `references/output.schema.json` — output contract.
