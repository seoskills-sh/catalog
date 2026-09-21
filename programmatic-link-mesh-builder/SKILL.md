---
name: Programmatic Link Mesh Builder
description: Models a generated page set as a directed graph, computes PageRank by power iteration and finds orphan and unreachable pages, then proposes a contextual hub-spoke plus related-entity internal-link plan that spreads authority without over-linking and guarantees every money page is reachable. Every proposed link is capped against template link-spam and de-duplicated against existing links. Use when the user wants to build internal links across programmatic pages, fix orphan pages, distribute PageRank, or guarantee money-page reachability at scale.
category: programmatic-seo
---

# Programmatic Link Mesh Builder

AGENT ROLE: Autonomous internal-linking agent. Build the page graph, compute PageRank and reachability, and return a capped, de-duplicated per-page link plan (hub-spoke + related-entity) that guarantees money-page reachability, emitting the JSON in `references/output.schema.json`.

## OBJECTIVE
Convert a set of programmatic pages (with or without existing links) into an internal-link plan: which new links each page should receive and emit, so authority flows to money pages, no page is orphaned or unreachable, and no page exceeds a link-spam outlink cap.

## INPUTS
- Node/edge sources (at least one REQUIRED): `--edges` (JSON `[[source,dest],...]`), `--crawl` (crawler inlinks CSV with Source/Destination), `--sitemap` (URL; nodes only), `--pages` (JSON array of node URLs).
- `money_pages` (OPTIONAL via `--money-pages`): URLs that MUST be reachable and adequately linked.
- `home` (OPTIONAL via `--home`): BFS root for reachability; defaults to a root-path URL, else the highest in-degree node.
- `damping` (OPTIONAL, default 0.85), `max_iter` (100), `eps` (1e-6): PageRank power-iteration controls.
- `min_inlinks` (OPTIONAL, default 1): a page below this in-degree is an orphan.
- `max_outlinks` (OPTIONAL, default 100) and `max_new_links_per_page` (OPTIONAL, default 5): link-spam caps.
- `top_k_related` (OPTIONAL, default 5): related-entity links proposed per page.
- `embeddings` (OPTIONAL flag): use semantic similarity instead of URL-token Jaccard.
- `max_nodes` (50000), `max_cluster_compare` (400), `max_embeddings` (1000): cost guards.

## AUTHENTICATION (graph keyless + optional embeddings)
1. The graph is built from local files or a public sitemap and needs no credentials.
2. IF `--embeddings` is passed THEN REQUIRE env `OPENAI_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_EMBEDDINGS"`. IF the key is rejected (401) THEN STOP `error.code="AUTH_INVALID_EMBEDDINGS"`.

## EXPECTED TOOL CALLS
- Run `scripts/link_mesh_builder.py --edges edges.json [--pages pages.json] [--money-pages money.json] [--home https://x.com/] [--max-outlinks 100]`.
- No paid calls in the default path; embeddings are batched (256/request) only when `--embeddings` is set.

## PROCEDURE (deterministic)
STEP 1 — BUILD the node set from every provided source; build de-duplicated out-adjacency and in-degree over edges whose endpoints are both known (drop self-loops). IF node count > `max_nodes` THEN STOP `TOO_MANY_NODES`.
STEP 2 — PAGERANK by power iteration with damping; dangling-node mass is redistributed uniformly; stop when L1 delta < `eps` or `max_iter` is hit; record `pagerank_iterations` and `pagerank_converged`.
STEP 3 — REACHABILITY: BFS from `home` over existing edges; anything not reached is unreachable. Orphan = in-degree < `min_inlinks`.
STEP 4 — CLUSTER pages by first path segment; the highest-PageRank page per section is its hub.
STEP 5 — PROPOSE, in priority order, each proposal passing the caps (never exceed `max_outlinks`; at most `max_new_links_per_page` new outlinks per page; never duplicate an existing link): (a) for each under-linked/unreachable MONEY page, an inbound link from the highest-PageRank reachable hub; (b) for each orphan spoke, a hub->spoke and spoke->hub link; (c) related-entity links to the top-`top_k_related` most-similar pages within the section (embedding cosine if enabled and under cap, else URL-token Jaccard).
STEP 6 — EMIT per page: pagerank, current in/out links, orphan + reachability flags, proposed in/out links with reasons, and projected inlinks after the plan; plus global counts and any money page still unreachable.

## RATE LIMITS & ERROR HANDLING
- Embeddings `429` -> backoff `2^attempt` (max 5) then STOP `RATE_LIMITED`; `5xx` retry <=3 then STOP `REQUEST_FAILED`; requests are batched and paced (effective concurrency 1).
- IF `--sitemap` cannot be fetched THEN STOP `error.code="SITEMAP_UNREACHABLE"`.
- Relatedness comparison is bounded to within-section clusters and `max_cluster_compare` members per cluster to keep cost near-linear; embeddings are skipped (Jaccard fallback, disclosed via `relatedness_method`) when node count exceeds `max_embeddings`.

## MISSING / INSUFFICIENT DATA
- With only a sitemap or `--pages` (no edges) every page is legitimately orphan/unreachable; the plan proposes the full initial mesh and this is reflected honestly in `edges=0`.
- A money page that cannot be linked without breaching caps is reported in `unreachable_money_pages` rather than silently force-linked past the spam cap.
- PageRank is reported even when it did not converge (`pagerank_converged=false`); values are never fabricated as converged.
- The relatedness method actually used is always disclosed; no semantic similarity is claimed when Jaccard was used.

## OUTPUT
One JSON object per `references/output.schema.json`.

## FILES
- `scripts/link_mesh_builder.py` — graph build, PageRank power iteration, BFS reachability, hub-spoke + related-entity proposal engine with link-spam caps.
- `references/output.schema.json` — output contract.
