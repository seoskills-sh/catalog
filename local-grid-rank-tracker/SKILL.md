---
name: Local Grid Rank Tracker
description: Generates a geographic grid of coordinates around each location and queries localized SERPs at every point to record local-pack and map rankings, producing a proximity heatmap and average grid rank. Use when the user wants geo-grid rank tracking, to see how local visibility drops with distance, or to benchmark local-pack presence across a service area.
category: local-seo
---

# Local Grid Rank Tracker

AGENT ROLE: Autonomous local-rank agent. Sample rankings across a coordinate grid and emit the JSON in `references/output.schema.json`, including a per-point rank matrix and aggregate metrics. Local rank is proximity-dependent — a single point is never the whole picture.

## OBJECTIVE
For a business and a keyword, measure where in its service area it appears in the local pack / map results, quantify how visibility decays with distance, and summarize with average grid rank and share-of-top-3.

## INPUTS
- `business_name` (REQUIRED): the exact GBP name to match in results.
- `keyword` (REQUIRED): the search term (e.g., "emergency plumber").
- `center` (REQUIRED): `{ lat, lng }` of the business (or service-area center).
- `grid_size` (OPTIONAL, default 7): odd N for an N×N grid (49 points at 7).
- `spacing_km` (OPTIONAL, default 1.5): distance between adjacent grid points.
- `zoom` (OPTIONAL, default 14): map zoom passed to the SERP.

## AUTHENTICATION (SERP API)
1. REQUIRE env `SERP_API_KEY`. IF unset THEN STOP `error.code="AUTH_MISSING_API_KEY"`.
2. Endpoint (SerpApi shape): `GET https://serpapi.com/search.json?engine=google_maps&q={keyword}&ll=@{lat},{lng},{zoom}z&type=search&api_key={key}` → `local_results`.
3. Grid points are computed locally from `center` + `spacing_km` (no geocoding key needed since `center` is provided; if only an address is given, geocode via a keyless service first and note `geocoded=true`).

## EXPECTED TOOL CALLS
- Run `scripts/grid_rank.py --name "..." --keyword "..." --lat .. --lng .. --grid 7 --spacing 1.5`.
- One SERP fetch per grid point (N×N total); parse `local_results` order to find the business's rank.

## PROCEDURE (deterministic)
STEP 1 — BUILD GRID: N×N points centered on `center`, offset by `spacing_km` per step (convert km→degrees: lat `/111`, lng `/(111*cos(lat))`).
STEP 2 — For each point: fetch `google_maps` local results at that `ll`; find the business by fuzzy-normalized name match; record its 1-based `rank` (or `>20`/`not_found`).
STEP 3 — MATRIX: assemble the N×N rank matrix (rows N→S, cols W→E) for the heatmap.
STEP 4 — AGGREGATE: `avg_rank` over found points; `share_of_top3 = points_in_top3 / total_points`; `visibility_falloff` = correlation of rank vs distance from center.
STEP 5 — EMIT the matrix + aggregates + the center point's rank.

## RATE LIMITS & ERROR HANDLING
- N×N can be many billed searches (49 at grid 7). On `429`/quota THEN backoff `2^attempt` (max 5) then STOP `error.code="RATE_LIMITED"` and return the partial matrix filled so far (`partial_points`).
- Cap concurrency at 2; a 7×7 grid is 49 searches — WARN in output if `grid_size >= 9` (81+ searches) via `cost_note`.
- Per-point `5xx`/timeout retry ≤2 then mark that cell `null` (unqueried) — never abort the whole grid.

## MISSING / INSUFFICIENT DATA
- IF the business is absent from a point's local results THEN that cell = `not_found` (a real, meaningful result — it means no visibility there), distinct from `null` (query failed).
- Name matching uses normalized fuzzy compare; record `match_confidence` per found cell; below threshold → treat as `not_found`.
- Never fabricate a rank; absence is `not_found`.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/grid_rank.py` — grid generation, per-point SERP fetch, name matching, aggregation.
- `references/output.schema.json` — output contract.
