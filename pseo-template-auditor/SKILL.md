---
name: pseo-template-auditor
description: Samples pages from one programmatic template, computes a 64-bit SimHash over word-shingles for each page, clusters near-duplicates by Hamming distance, extracts the shared boilerplate shingle set, and quantifies the unique-token budget every page actually adds. It flags thin pages, near-duplicate clusters, and boilerplate-dominant doorway pages so you learn whether a template mints unique value or sprays duplicates. Use when the user wants to check a pSEO template for thin or duplicate output, find near-duplicate pages, or prove a template is not a doorway before scaling it.
metadata:
  title: Programmatic Template Auditor
  category: programmatic-seo
---

# Programmatic Template Auditor

AGENT ROLE: Autonomous template-quality agent. Sample a template's output, fingerprint each page with SimHash, cluster near-duplicates, separate boilerplate from unique content, and emit the JSON in `references/output.schema.json`. Judge the template, not one page.

## OBJECTIVE
Answer, with numbers: does this template produce pages that are meaningfully unique, or thin and near-duplicate? Return per-page uniqueness ratios and unique-token budgets, the near-duplicate clusters, and a single template verdict (healthy / duplicate_heavy / boilerplate_dominant / thin / doorway).

## INPUTS
- `urls` (REQUIRED unless `--sitemap` via `--urls`): JSON array of template URLs to sample.
- `sitemap` + `url_pattern` (ALTERNATIVE via `--sitemap`/`--url-pattern`): fetch a sitemap and keep only URLs matching the template pattern.
- `k` (OPTIONAL, default 4): shingle size in words.
- `hamming_threshold` (OPTIONAL, default 3): max 64-bit Hamming distance to call two pages near-duplicates.
- `min_tokens` (OPTIONAL, default 150): below this main-content token count a page is `thin`.
- `uniqueness_floor` (OPTIONAL, default 0.20): below this unique-shingle ratio a non-thin page is a `doorway`.
- `boilerplate_df` (OPTIONAL, default 0.80): a shingle present in this fraction of sampled pages is boilerplate.
- `sample_size` (OPTIONAL, default 50) and `max_sample` (OPTIONAL, default 300): cost guard; pairwise clustering is O(sample^2).
- `site` (OPTIONAL via `--site`): GSC property to attach clicks per page for triage.

## AUTHENTICATION (crawler keyless + GSC)
1. Page fetches are keyless HTTP with a descriptive User-Agent; no credentials required.
2. IF `--site` is passed THEN REQUIRE env `GSC_ACCESS_TOKEN` (OAuth bearer, `webmasters.readonly`). IF unset THEN STOP `error.code="AUTH_MISSING_GSC"`. IF the token is rejected THEN STOP `error.code="AUTH_GSC_FORBIDDEN"`.

## EXPECTED TOOL CALLS
- Run `scripts/template_auditor.py --urls urls.json [--sitemap https://x.com/sitemap.xml --url-pattern "/product/"] [--k 4] [--hamming-threshold 3] [--site sc-domain:x.com]`.
- One HTTP GET per sampled page (thread-pooled, capped); at most one GSC query.

## PROCEDURE (deterministic)
STEP 1 — RESOLVE the sample: read `--urls`, or fetch `--sitemap` and filter by `--url-pattern`; dedupe; cap at min(`sample_size`, `max_sample`).
STEP 2 — FETCH each page; strip script/style/nav/header/footer/form blocks and tags to isolate main text; tokenize to `[a-z0-9]+`.
STEP 3 — SHINGLE each page into k-word shingles; hash every shingle to 64 bits with blake2b; build a Charikar SimHash by weighted bit-vote (frequency-weighted). Record the fingerprint.
STEP 4 — CLUSTER near-duplicates: compute pairwise Hamming distance and union pages with distance <= `hamming_threshold` (union-find); clusters of size >= 2 are near-duplicate groups.
STEP 5 — BOILERPLATE: a shingle whose document frequency across the sample >= `boilerplate_df` is boilerplate; per page `content_uniqueness_ratio` = unique shingles / total shingles, and `unique_token_budget` = distinct tokens appearing only in non-boilerplate shingles.
STEP 6 — FLAG: `thin` (token_count < `min_tokens`), `near_duplicate` (in a cluster), `doorway` (uniqueness < floor, not thin, boilerplate exists).
STEP 7 — VERDICT: aggregate shares -> `thin`, `doorway`, `duplicate_heavy`, `boilerplate_dominant`, or `healthy`. EMIT pages sorted least-unique first.

## RATE LIMITS & ERROR HANDLING
- Page fetches are thread-pooled with `--workers` (default 5, hard cap 8) as the concurrency cap; a non-200 or non-HTML response is recorded in `unreachable`, not retried indefinitely.
- GSC `429`/`5xx` -> backoff `2^attempt` (max 5) then proceed WITHOUT clicks (non-fatal); `401`/`403` STOP `AUTH_GSC_FORBIDDEN`.
- IF the sitemap cannot be fetched THEN STOP `error.code="SITEMAP_UNREACHABLE"`.
- Sampling is capped by `max_sample` to bound O(n^2) clustering cost.

## MISSING / INSUFFICIENT DATA
- IF every sampled page is unreachable THEN STOP `error.code="ALL_UNREACHABLE"` (never emit uniqueness for pages that were not fetched).
- With a sample of 1 the boilerplate set is empty and no near-duplicate cluster can form; the verdict then rests on thinness alone and this is disclosed by `pages_analyzed`.
- Uniqueness and token budgets are computed only over successfully fetched pages; `pages_sampled` vs `pages_analyzed` expose any shortfall.
- Clicks are `null` without `--site`; they inform triage only and never change a flag.

## OUTPUT
One JSON object per `references/output.schema.json`.

## FILES
- `scripts/template_auditor.py` — sampling, main-text extraction, SimHash fingerprinting, Hamming clustering, boilerplate/uniqueness math, template verdict.
- `references/output.schema.json` — output contract.
