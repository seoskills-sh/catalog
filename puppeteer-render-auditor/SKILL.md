---
name: puppeteer-render-auditor
description: Loads each URL as raw HTML and as a fully JavaScript-rendered DOM via headless Chrome, then diffs content, links, and metadata to flag anything an agent-crawler would miss before render. Use when the user suspects client-side-rendering indexation problems, asks "does Google see my JS content", or audits an SPA/React/Vue site for crawlability.
metadata:
  title: Puppeteer JS Rendering Auditor
  category: technical-seo
---

# Puppeteer JS Rendering Auditor

AGENT ROLE: Autonomous rendering-parity agent. For each URL, compare the pre-render and post-render states and emit the JSON in `references/output.schema.json`. Do not judge SEO quality — only report what content exists before vs after JavaScript.

## OBJECTIVE
Detect content, links, and metadata that appear ONLY after JavaScript executes, because a crawler that does not render (or renders on a delay) may never index them.

## INPUTS
- `urls` (REQUIRED, string[]): absolute URLs to audit.
- `wait_until` (OPTIONAL, enum `load|domcontentloaded|networkidle0|networkidle2`): default `networkidle2`.
- `render_timeout_ms` (OPTIONAL, default 15000).
- `viewport` (OPTIONAL, enum `mobile|desktop`): default `mobile` (Google indexes mobile-first).
- `block_resources` (OPTIONAL bool, default true): block images/fonts/media to speed rendering; NEVER block scripts or XHR.

## AUTHENTICATION / RUNTIME
- No external API key. REQUIRES a headless Chromium.
  - IF `puppeteer` is installed THEN use its bundled Chromium.
  - ELSE IF env `PUPPETEER_EXECUTABLE_PATH` is set THEN launch that binary.
  - ELSE STOP `error.code="NO_CHROMIUM"`: "Install puppeteer or set PUPPETEER_EXECUTABLE_PATH to a Chrome/Chromium binary."
- Chromium's sandbox stays on by default, which is what you want on a normal machine. Only when you are running as root inside a container, where the sandbox cannot start, set the env var `PUPPETEER_NO_SANDBOX=1` before running the script; leave it unset everywhere else so the sandbox keeps protecting the host.

## EXPECTED TOOL CALLS (per URL)
1. RAW: `fetch(url)` (or `page.goto` with JS disabled) → capture `raw_html`.
2. RENDERED: `page.goto(url, {waitUntil, timeout})` → `page.content()` → `rendered_html`.
3. Extract from BOTH with the same logic (word count of visible text, `a[href]` set, `<title>`, meta description, canonical, `robots` meta, count of `<script type=application/ld+json>`).

## PROCEDURE (deterministic, per URL)
STEP 1 — Fetch raw HTML. IF non-200 THEN record `{url, status, error:"NON_200"}` and continue to next URL.
STEP 2 — Render with Puppeteer. IF navigation throws/timeouts THEN record `render_status="timeout"` and use whatever DOM is available.
STEP 3 — Compute deltas:
  - `text_delta_words = rendered_words - raw_words`.
  - `links_added = rendered_links − raw_links` (set difference); `links_removed` likewise.
  - `meta_changed`: object of fields whose value differs (title, description, canonical, robots).
  - `jsonld_added = rendered_jsonld_count − raw_jsonld_count`.
STEP 4 — CLASSIFY `severity`:
  - `high` IF `text_delta_words > 100` OR `links_added.length > 10` OR canonical/robots differ.
  - `medium` IF any content or ≤10 links added.
  - `none` IF states are equivalent (good — content is in raw HTML).
STEP 5 — EMIT per URL; overall `status="ok"`.

## RATE LIMITS & ERROR HANDLING
- Concurrency: render at most `3` pages in parallel; reuse one browser instance, one page per task, ALWAYS `page.close()` in a finally block to avoid leaks.
- Per-URL failure is isolated — never abort the batch for one bad URL.
- IF the target host returns `429`/`503` on the raw fetch THEN backoff `2^attempt` (max 3) for that URL, then mark `error:"RATE_LIMITED"`.
- Honor `robots.txt`: IF a URL is disallowed for the default UA THEN skip with `skipped:"robots_disallow"` unless `respect_robots=false` is explicitly passed.

## MISSING / INSUFFICIENT DATA
- IF `rendered_html` equals `raw_html` byte-for-byte THEN `severity="none"`, `note="no client-side rendering detected"`.
- Never infer indexation status; only report the observable render delta.

## OUTPUT
One JSON object per `references/output.schema.json`. No prose.

## FILES
- `scripts/render_audit.js` — Puppeteer reference implementation (raw vs rendered diff).
- `references/output.schema.json` — output contract.
