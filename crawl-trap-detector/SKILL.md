---
name: Crawl-Trap & Facet Detector
description: Crawls a site with bounded BFS while modeling URL parameter and facet combinations to detect exponentially expanding, near-duplicate URL spaces such as faceted navigation, calendar loops, and session-ID paths, then recommends robots or canonical containment. Use when the user reports crawl-budget waste, index bloat, infinite URLs, or too many parameterized pages.
category: technical-seo
---

# Crawl-Trap & Facet Detector

AGENT ROLE: Autonomous crawl-space agent. Crawl within scope, group URLs by structural signature, detect combinatorial explosions, and emit the JSON in `references/output.schema.json` with a concrete containment recommendation per trap.

## OBJECTIVE
Find URL patterns whose parameter/facet combinations generate large near-duplicate spaces that waste crawl budget and bloat the index, and recommend the minimal robots/canonical fix for each.

## INPUTS
- `start_url` (REQUIRED): crawl seed; its host defines the scope.
- `max_pages` (OPTIONAL, default 3000): hard crawl cap (safety).
- `max_depth` (OPTIONAL, default 6).
- `param_explosion_threshold` (OPTIONAL, default 50): min distinct URLs sharing a path+param-key signature to flag.
- `respect_robots` (OPTIONAL bool, default true).

## AUTHENTICATION / RUNTIME
- No API key. HTTPS GET with UA `seoskills-crawltrap/1.0`.
- MUST fetch and honor `robots.txt` when `respect_robots=true`; record disallowed patterns (they are the site's existing containment).

## EXPECTED TOOL CALLS
- Run `scripts/crawl_trap_scan.py --start {url} --max {n}`.
- BFS from `start_url`; only enqueue same-host, non-fragment, non-mailto/tel links. Deduplicate by normalized URL.

## STRUCTURAL SIGNATURE (core idea)
For every URL derive `signature = path_template + "?" + sorted(param_keys)` where numeric/id path segments are templated to `{n}` and `{id}`. Group URLs by signature; a huge group with varied param VALUES but one signature = a generated space.

## PROCEDURE (deterministic)
STEP 1 — CRAWL up to caps; store each URL's `{signature, param_keys, param_values}`.
STEP 2 — GROUP by signature; for each group compute `url_count`, `distinct_param_value_sets`, `depth_range`.
STEP 3 — DETECT + classify each group over `param_explosion_threshold`:
  - `FACETED_NAV`: ≥3 independent param keys co-occurring (combinatorial) → `containment="canonical to unfaceted URL + robots disallow non-primary facet params"`.
  - `CALENDAR_LOOP`: a param/path that is a monotonic date/offset with unbounded growth (e.g., `?month=`, `/2027/03/…`) and thin/duplicate content → `containment="nofollow calendar nav + robots disallow future-dated ranges"`.
  - `SESSION_OR_TRACKING`: param key in `{sessionid, sid, phpsessid, utm_*, fbclid, gclid}` altering URL identity → `containment="canonical to param-stripped URL; do not link with tracking params internally"`.
  - `SORT_ORDER_PAGINATION`: `{sort, order, view, page, per_page}` producing duplicates → `containment="canonical to default sort; allow only paginated series via rel or clean pagination"`.
  - `GENERIC_PARAM_EXPLOSION`: none of the above but count over threshold → flag with the offending param keys.
STEP 4 — SCORE `waste_score = url_count * (1 − content_uniqueness_estimate)` where uniqueness is estimated from title/H1 similarity within the group sample.
STEP 5 — EMIT traps sorted by `waste_score` desc; include one example URL and the exact param keys per trap.

## RATE LIMITS & ERROR HANDLING
- Politeness: ≤ 5 concurrent requests, ≥ 200ms between same-host requests, obey `Crawl-delay` in robots.txt.
- IF host returns `429`/`503` THEN global backoff `2^attempt` (max 4); IF persistent THEN stop crawling and return partial with `status="partial_rate_limited"`.
- HARD STOP at `max_pages`/`max_depth` even if the frontier is non-empty (this is itself trap evidence — set `hit_cap=true`).

## MISSING / INSUFFICIENT DATA
- IF the site has < `param_explosion_threshold` parameterized URLs THEN `status="ok"`, `traps=[]` (clean).
- Never label a legitimate paginated series a trap without duplicate-content evidence; when uncertain, downgrade to `SORT_ORDER_PAGINATION` info-level.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/crawl_trap_scan.py` — bounded BFS crawler + signature grouping + classification.
- `references/output.schema.json` — output contract.
