---
name: core-web-vitals-fixer
description: Diagnoses why a page fails Core Web Vitals (LCP, INP, CLS) and what to change. It reads real-user field data (CrUX) and a Lighthouse run through PageSpeed Insights, or a saved Lighthouse report, then breaks each metric down (the LCP element and its four phases, render-blocking files, oversized images, the scripts and third parties that block the main thread, the elements that shift) and checks the HTML for common causes. Fixes are ranked by estimated savings. Use when the user asks why a page is slow, how to pass Core Web Vitals, how to improve LCP, INP or CLS, or what a PageSpeed report means.
metadata:
  title: Core Web Vitals Fixer
  category: technical-seo
---

# Core Web Vitals Fixer

AGENT ROLE: Autonomous page-speed agent. Get field and lab data, run the script, emit the JSON in `references/output.schema.json`, and turn it into a short, ordered fix list with the code-level change for each item.

## OBJECTIVE
Answer "does this page pass Core Web Vitals for real users, and which changes will move each metric most?" Field data says whether there is a problem. The lab run and the HTML say where it comes from.

## INPUTS
- `url` (via `--url`, repeatable or comma-separated, up to 5): pages to diagnose. Needs env `PSI_API_KEY` for field and lab data.
- `strategy` (OPTIONAL, default `mobile`): `mobile`, `desktop` or `both`. Google's mobile results are the ones that usually matter.
- `lighthouse` (OPTIONAL via `--lighthouse`, repeatable): a saved Lighthouse JSON report (Chrome DevTools, Lighthouse panel, then save as JSON; or `lighthouse <url> --output=json`) or a saved PageSpeed Insights API response. No key needed.
- With neither a key nor a report, the script still runs the HTML checks and says what is missing.

## DATA SOURCES
1. PageSpeed Insights API v5 (`PSI_API_KEY`, a free Google Cloud API key with the PageSpeed Insights API enabled; Google refuses keyless requests). It returns CrUX field data for the URL, or the whole origin when the URL lacks traffic, plus a Lighthouse lab run.
2. A saved Lighthouse report, read offline.
3. The page's HTML, fetched as `seoskills-cwv-fixer/1.0`.

## EXPECTED TOOL CALLS
- `scripts/cwv_fixer.py --url https://example.com/ [--strategy both]` with `PSI_API_KEY` set, or `scripts/cwv_fixer.py --lighthouse report.json`.
- One PageSpeed Insights call per URL and strategy (up to two minutes each), plus one HTML fetch per URL.

## PROCEDURE (deterministic)
STEP 1: FIELD. From CrUX, take the 75th percentile of LCP, INP, CLS, FCP and TTFB and rate each with Google's thresholds:
- LCP: good up to 2.5 s, poor over 4 s.
- INP: good up to 200 ms, poor over 500 ms.
- CLS: good up to 0.1, poor over 0.25.
- FCP: good up to 1.8 s, poor over 3 s.
- TTFB: good up to 0.8 s, poor over 1.8 s.
The page passes only when LCP, INP and CLS are all good. `scope` says whether the data is for the URL or the whole origin.
STEP 2: LAB. Take Lighthouse's LCP, CLS, TBT, FCP, Speed Index and TTI, and its performance score. TBT is the lab stand-in for INP; a lab run cannot measure INP itself.
STEP 3: LCP DETAIL: the LCP element (selector and HTML snippet), its four phases (time to first byte, resource load delay, resource load duration, element render delay) and the biggest one with what fixes it, and the discovery checks (in the initial HTML, fetchpriority=high, not lazy-loaded).
STEP 4: FIXES. Every failing Lighthouse audit that maps to a metric becomes a fix, with the files or elements involved (the top 5), Lighthouse's estimated savings, and the change to make. An older audit and its newer insight that report the same problem appear once. Rank by estimated savings.
STEP 5: HTML CHECKS: the first image lazy-loaded, no image priority hint, blocking scripts in the head, images without dimensions, fonts without font-display, many third-party script hosts, no viewport tag.
STEP 6: REPORT, metric by metric:
1. Start with the field verdict (or say there is no field data).
2. Give the three to five fixes with the biggest savings, each with its concrete change, for example adding `fetchpriority="high"` to the named hero image, deferring a named script, or setting width and height on named images.
3. Note third parties the site owner may not control.

## RATE LIMITS & ERROR HANDLING
- PageSpeed Insights calls have a 150-second timeout. A refused call is reported per page in `psi_error` with Google's message (an invalid key, quota, or a Lighthouse error such as the page not loading), and the HTML checks still run.
- No `--url` and no `--lighthouse` STOPs `INPUT_INVALID`. An unreadable report STOPs `FILE_UNREADABLE`; a JSON that is not Lighthouse STOPs `NOT_LIGHTHOUSE`. IF nothing at all can be measured THEN STOP `NOTHING_MEASURED`.

## MISSING / INSUFFICIENT DATA
- Field data needs enough real Chrome traffic over the past 28 days. Most small sites have none at URL level and some none at all. Then say so, rely on the lab run, and never present lab numbers as field data.
- Lab runs vary from run to run and use a simulated slow phone, so they show where time goes, not exactly what users see. Compare runs on the same settings only.
- Savings estimates are Lighthouse's own and do not add up across fixes. Treat them as a ranking.
- The HTML checks read the HTML as served. Images and scripts added later by JavaScript are only in the Lighthouse data.

## OUTPUT
One JSON object per `references/output.schema.json`. Then the fix list: field verdict, then the fixes in order with the exact elements, files and code changes, and how to confirm them (a new run, then CrUX after 28 days).

## FILES
- `scripts/cwv_fixer.py`: PageSpeed Insights client, CrUX field parsing, Lighthouse audit diagnosis and ranking, and the HTML checks.
- `references/output.schema.json`: output contract.
