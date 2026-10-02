---
name: internal-link-optimizer
description: Crawls the site, embeds every page, and builds the internal link graph to find semantically related pages that are not yet linked, then recommends contextual internal links with anchor suggestions. Use when the user wants to improve internal linking, distribute link equity, strengthen topic clusters, or fix orphan and under-linked pages.
metadata:
  title: Internal Link Opportunity Mapper
  category: content
---

# Internal Link Opportunity Mapper

AGENT ROLE: Autonomous internal-linking agent. Build the site's page-similarity matrix and existing link graph, find high-similarity unlinked pairs, and emit ranked recommendations per `references/output.schema.json`.

## OBJECTIVE
Recommend the highest-value missing internal links: pairs of pages that are semantically related but not currently linked, prioritized by relevance and the target page's link deficit — plus surface orphan pages.

## INPUTS
- `start_url` (REQUIRED): crawl seed; host defines scope.
- `max_pages` (OPTIONAL, default 1000).
- `similarity_threshold` (OPTIONAL, default 0.75): cosine floor to call two pages "related".
- `max_recommendations` (OPTIONAL, default 200).
- `embedding_backend` (OPTIONAL enum `openai|tfidf`): default `openai` if a key is set, else local `tfidf`.

## AUTHENTICATION
- Crawl: keyless HTTPS GET, UA `seoskills-internal-links/1.0`, honor robots.
- `openai` backend: REQUIRE env `OPENAI_API_KEY`; endpoint `POST https://api.openai.com/v1/embeddings` (model `text-embedding-3-small`). IF unset THEN fall back to local `tfidf` cosine and note `embedding_backend="tfidf"`.

## EXPECTED TOOL CALLS
- Run `scripts/internal_links.py --start {url} --max 1000`.
- BFS crawl (same host); per page store main text + outbound internal links; then embed each page's text.

## PROCEDURE (deterministic)
STEP 1 — CRAWL (bounded BFS), capturing per page: `{url, title, main_text, internal_links_out}`.
STEP 2 — EMBED each page's `title + main_text` (OpenAI batch, or local TF-IDF vectors).
STEP 3 — EXISTING GRAPH: build the set of directed internal links already present.
STEP 4 — CANDIDATES: for every unordered page pair with `cosine >= similarity_threshold`, IF neither direction is already linked THEN it is a candidate.
STEP 5 — DIRECTION + PRIORITY: recommend linking FROM the higher-authority/older page TO the one with the larger link deficit (fewer inbound internal links). `priority = cosine * (1 / (1 + inbound_links(target)))`.
STEP 6 — ANCHOR: propose an anchor from the target page's title / most salient shared n-gram present in the source's text.
STEP 7 — ORPHANS: list pages with 0 inbound internal links. EMIT recommendations sorted by priority desc.

## RATE LIMITS & ERROR HANDLING
- Crawl politeness: ≤ 5 concurrent, ≥ 200ms between same-host requests, obey robots `Crawl-delay`. HARD stop at `max_pages` (`hit_cap=true`).
- OpenAI embeddings `429` → honor `Retry-After` else backoff `2^attempt` (max 5); on persistent failure switch to `tfidf` and set `embedding_backend="tfidf"` (never fail the whole run for embeddings).
- Per-page fetch failure → skip; a page needs ≥ 100 words to be embedded (else excluded, listed in `skipped`).

## MISSING / INSUFFICIENT DATA
- IF fewer than 5 embeddable pages THEN STOP `error.code="INSUFFICIENT_PAGES"` (nothing meaningful to link).
- Never recommend a link that already exists in either direction; never recommend self-links.
- Anchor text must appear in the source page's vocabulary; if none fits, return the target title and mark `anchor_confidence="low"`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/internal_links.py` — crawler, embeddings/TF-IDF cosine, candidate ranking, anchor suggestion.
- `references/output.schema.json` — output contract.
