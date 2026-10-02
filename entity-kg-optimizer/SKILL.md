---
name: entity-kg-optimizer
description: Queries the Google Knowledge Graph and Wikidata to assess how well a brand entity is defined, connected, and disambiguated, then recommends schema, sameAs, and corroborating-source fixes to strengthen entity grounding for AI answers. Use when the user asks about entity SEO, knowledge panels, being understood by AI, or improving how LLMs identify their brand.
metadata:
  title: Entity Knowledge-Graph Optimizer
  category: ai-search
---

# Entity Knowledge-Graph Optimizer

AGENT ROLE: Autonomous entity-grounding agent. Resolve the brand entity in the Google Knowledge Graph and Wikidata, score its grounding, and emit prioritized recommendations per `references/output.schema.json`. Recommend; never assert the entity is "verified" beyond what the graphs return.

## OBJECTIVE
Determine whether the brand is an established, disambiguated entity that AI systems can ground answers on, and produce the specific structured-data and corroboration steps to improve it.

## INPUTS
- `brand` (REQUIRED): `{ name, domain, type_hint?: "Organization|LocalBusiness|Person|Product" }`.
- `wikidata_qid` (OPTIONAL): known Wikidata Q-id to skip search disambiguation.
- `expected_sameas` (OPTIONAL string[]): canonical profile URLs the brand controls (LinkedIn, Crunchbase, official social).

## AUTHENTICATION
- Google Knowledge Graph Search API: REQUIRE env `KG_API_KEY` (Google Cloud API key with "Knowledge Graph Search API" enabled).
  - IF unset THEN set `kg_status="skipped_no_key"` and proceed with Wikidata only (do NOT hard-fail — Wikidata alone is still useful).
- Wikidata: public, no key. Use a descriptive UA `seoskills-entity-optimizer/1.0` and respect the WMF API etiquette.

## EXPECTED TOOL CALLS
- Run `scripts/entity_audit.py --brand brand.json [--qid Q123]`.
- KG: `GET https://kgsearch.googleapis.com/v1/entities:search?query={name}&types={type}&key={KG_API_KEY}&limit=5`.
- Wikidata: `GET https://www.wikidata.org/w/api.php?action=wbsearchentities&search={name}&language=en&format=json`; then `wbgetentities` for claims of the chosen Q-id.

## PROCEDURE (deterministic)
STEP 1 — KG RESOLUTION: search KG; pick the top result whose `@type`/`name` best matches AND whose `url`/`detailedDescription` references `brand.domain`. Record `resultScore`, `@type`, `description`, `detailedDescription.url`. IF no domain-consistent match THEN `kg_entity="unrecognized"`.
STEP 2 — WIKIDATA RESOLUTION: use `wikidata_qid` if given, else `wbsearchentities`; disambiguate by matching official website (property `P856`) to `brand.domain`. IF none matches domain THEN `wikidata_entity="unrecognized_or_ambiguous"`.
STEP 3 — GROUNDING CHECKS (each yields a finding with `severity`):
  - `NOT_IN_KG` (high) IF KG unrecognized (and key was present).
  - `NOT_IN_WIKIDATA` (high) IF no domain-matched Wikidata item — the single biggest AI-grounding gap.
  - `MISSING_OFFICIAL_WEBSITE` (high) IF the Wikidata item lacks `P856` = brand.domain.
  - `WEAK_SAMEAS` (medium): `expected_sameas` profiles not linked from the entity / not in the site's Organization schema `sameAs`.
  - `MISSING_ENTITY_TYPE` (medium) IF no clear `instance of` (P31) / schema `@type`.
  - `THIN_DESCRIPTION` (low) IF KG/Wikidata description < 10 words.
  - `NO_CORROBORATION` (medium) IF fewer than 2 independent authoritative references (Wikidata refs / KG detailedDescription source).
STEP 4 — RECOMMEND per finding (concrete): e.g., "Publish `Organization` schema on {domain} with `sameAs` linking {expected_sameas}", "Create/complete a Wikidata item with P856={domain} and P31={type}", "Add corroborating coverage on authoritative third-party sources".
STEP 5 — SCORE `grounding_score` 0–100 = weighted pass rate of the checks. EMIT.

## RATE LIMITS & ERROR HANDLING
- KG API: on `429` backoff `2^attempt` (max 4); on persistent failure set `kg_status="rate_limited"` and continue with Wikidata.
- Wikidata: serialize requests, ≥ 100ms apart; on `429`/`maxlag` honor the retry hint. Never hammer the WMF API.
- Any single upstream failure downgrades that source's findings to `unknown`, never to a false "missing".

## MISSING / INSUFFICIENT DATA
- IF BOTH graphs are unavailable THEN `status="sources_unavailable"`, return no false negatives.
- Absence of an entity is reported as a finding to fix, distinct from "we could not check".

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/entity_audit.py` — KG + Wikidata resolver, grounding checks, recommendations.
- `references/output.schema.json` — output contract.
