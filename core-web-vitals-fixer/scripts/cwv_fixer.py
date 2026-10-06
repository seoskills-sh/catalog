#!/usr/bin/env python3
"""Core Web Vitals Fixer: reference implementation.

Diagnoses why a page fails Core Web Vitals and what to change. It reads real-
user field data (CrUX, via PageSpeed Insights) and a Lighthouse lab run,
then breaks each metric down: the LCP element and its four phases, what
blocks rendering, the images to shrink, the scripts and third parties that
keep the main thread busy (the lab proxy for INP), and the elements that
shift. It also checks the HTML itself for common causes (a lazy-loaded hero
image, images without dimensions, blocking scripts in the head, fonts without
font-display). Fixes are ranked by Lighthouse's estimated savings.

Auth:   PSI_API_KEY (a free Google Cloud API key; PageSpeed Insights refuses
        keyless requests). Without a key, pass --lighthouse with a report
        saved from Chrome DevTools or the Lighthouse CLI; the HTML checks
        need no key either way.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 cwv_fixer.py --url https://example.com/ [--strategy mobile|desktop|both]
       python3 cwv_fixer.py --lighthouse report.json [--url https://example.com/]
"""
from __future__ import annotations
import argparse, json, os, re, sys
import urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

PSI = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
UA = "seoskills-cwv-fixer/1.0 (+https://seoskills.sh)"
# Google's thresholds at the 75th percentile: (good up to, poor above).
THRESHOLDS = {"LCP": (2500, 4000), "INP": (200, 500), "CLS": (0.1, 0.25), "FCP": (1800, 3000), "TTFB": (800, 1800)}
FIELD_KEYS = {"LARGEST_CONTENTFUL_PAINT_MS": "LCP", "INTERACTION_TO_NEXT_PAINT": "INP", "CUMULATIVE_LAYOUT_SHIFT_SCORE": "CLS",
              "FIRST_CONTENTFUL_PAINT_MS": "FCP", "EXPERIMENTAL_TIME_TO_FIRST_BYTE": "TTFB"}
LAB = {"largest-contentful-paint": "LCP", "cumulative-layout-shift": "CLS", "total-blocking-time": "TBT",
       "first-contentful-paint": "FCP", "speed-index": "SI", "interactive": "TTI"}
# Lighthouse audits (legacy names and the newer insights) -> the metric they hurt and the change that fixes them.
FIXES = {
    "render-blocking-insight": ("LCP", "Inline the critical CSS, load the rest without blocking (media or preload swap), and add defer to scripts in the head."),
    "render-blocking-resources": ("LCP", "Inline the critical CSS, load the rest without blocking, and add defer to scripts in the head."),
    "image-delivery-insight": ("LCP", "Serve images at the size they display, in AVIF or WebP, with higher compression; use srcset and sizes."),
    "uses-responsive-images": ("LCP", "Serve images at the size they display, with srcset and sizes."),
    "modern-image-formats": ("LCP", "Serve AVIF or WebP instead of JPEG or PNG."),
    "uses-optimized-images": ("LCP", "Compress images more."),
    "offscreen-images": ("LCP", "Lazy-load images below the fold (never the LCP image)."),
    "lcp-discovery-insight": ("LCP", "Put the LCP image in the initial HTML as an <img> (not a CSS background or JS-inserted), with fetchpriority=\"high\" and no loading=\"lazy\"."),
    "prioritize-lcp-image": ("LCP", "Preload the LCP image or give it fetchpriority=\"high\"."),
    "lcp-lazy-loaded": ("LCP", "Remove loading=\"lazy\" from the LCP image."),
    "document-latency-insight": ("LCP", "Cut server response time: cache HTML at the edge or CDN, avoid redirects, and compress the document."),
    "server-response-time": ("LCP", "Cut server response time: cache HTML at the edge or CDN and speed up the backend."),
    "redirects": ("LCP", "Link straight to the final URL; every redirect adds a round trip."),
    "network-dependency-tree-insight": ("LCP", "Shorten request chains: preload critical late-discovered files and preconnect to the few origins the page needs first."),
    "font-display-insight": ("FCP", "Add font-display: swap (or optional) to @font-face rules, and preload the main font file."),
    "font-display": ("FCP", "Add font-display: swap (or optional) to @font-face rules."),
    "unused-css-rules": ("LCP", "Remove unused CSS, or split CSS by page so each page loads only what it uses."),
    "unused-javascript": ("INP", "Remove or code-split unused JavaScript and load non-critical scripts after the page is interactive."),
    "legacy-javascript-insight": ("INP", "Stop shipping polyfills and transpiled code to modern browsers (target modern browsers in the build)."),
    "legacy-javascript": ("INP", "Stop shipping polyfills and transpiled code to modern browsers."),
    "duplicated-javascript-insight": ("INP", "Deduplicate libraries bundled more than once."),
    "duplicated-javascript": ("INP", "Deduplicate libraries bundled more than once."),
    "bootup-time": ("INP", "Reduce JavaScript execution: remove unused scripts, split long-running code, and defer non-essential work."),
    "mainthread-work-breakdown": ("INP", "Reduce main-thread work: less script, simpler style recalculation and layout."),
    "third-party-summary": ("INP", "Remove, defer or lazy-load third-party scripts (chat widgets, tag managers, embeds) until after interaction."),
    "third-parties-insight": ("INP", "Remove, defer or lazy-load third-party scripts until after interaction."),
    "long-tasks": ("INP", "Break long tasks into chunks that yield to the main thread (scheduler.yield() or setTimeout)."),
    "forced-reflow-insight": ("INP", "Avoid reading layout (offsetHeight, getBoundingClientRect) right after changing styles in the same task."),
    "dom-size-insight": ("INP", "Reduce DOM size: render long lists virtually and remove hidden markup."),
    "dom-size": ("INP", "Reduce DOM size: render long lists virtually and remove hidden markup."),
    "cls-culprits-insight": ("CLS", "Reserve space for late content: width and height (or aspect-ratio) on images and embeds, fixed slots for ads and banners."),
    "layout-shifts": ("CLS", "Reserve space for the elements that shift."),
    "unsized-images": ("CLS", "Add width and height attributes (or CSS aspect-ratio) to every image."),
    "cache-insight": ("LCP", "Give static files long cache lifetimes so repeat visits load faster."),
    "uses-long-cache-ttl": ("LCP", "Give static files long cache lifetimes."),
    "viewport-insight": ("INP", "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> so taps are not delayed."),
}


SAME_AS = {"render-blocking-resources": "render-blocking-insight", "uses-responsive-images": "image-delivery-insight",
           "modern-image-formats": "image-delivery-insight", "uses-optimized-images": "image-delivery-insight",
           "font-display": "font-display-insight", "legacy-javascript": "legacy-javascript-insight",
           "duplicated-javascript": "duplicated-javascript-insight", "third-party-summary": "third-parties-insight",
           "dom-size": "dom-size-insight", "uses-long-cache-ttl": "cache-insight", "server-response-time": "document-latency-insight",
           "redirects": "document-latency-insight", "prioritize-lcp-image": "lcp-discovery-insight", "lcp-lazy-loaded": "lcp-discovery-insight",
           "layout-shifts": "cls-culprits-insight", "bootup-time": "mainthread-work-breakdown"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def rate(metric, value):
    if value is None or metric not in THRESHOLDS:
        return None
    good, poor = THRESHOLDS[metric]
    return "good" if value <= good else "poor" if value > poor else "needs-improvement"


def psi(url, strategy, key):
    q = urllib.parse.urlencode({"url": url, "strategy": strategy, "category": "performance", "key": key})
    try:
        with urllib.request.urlopen(PSI + "?" + q, timeout=150) as r:
            return json.loads(r.read().decode("utf-8", "ignore")), None
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "ignore")).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        return None, {"http_status": e.code, "message": msg[:300] or "PageSpeed Insights returned HTTP %d" % e.code}
    except Exception as e:
        return None, {"http_status": 0, "message": "PageSpeed Insights request failed: %s" % type(e).__name__}


def field_data(block):
    if not block or not block.get("metrics"):
        return None
    out = {"scope": None, "overall": block.get("overall_category"), "metrics": {}}
    for k, name in FIELD_KEYS.items():
        m = block["metrics"].get(k)
        if m and m.get("percentile") is not None:
            v = m["percentile"] / 100 if name == "CLS" else m["percentile"]
            out["metrics"][name] = {"p75": v, "rating": rate(name, v)}
    core = [out["metrics"].get(x, {}).get("rating") for x in ("LCP", "INP", "CLS")]
    out["passes_core_web_vitals"] = None if None in core else all(r == "good" for r in core)
    return out


def items(audit):
    det = (audit or {}).get("details") or {}
    return det.get("items") or []


def short(url):
    return url if len(url) <= 110 else url[:107] + "..."


def diagnose(lhr):
    a = lhr.get("audits", {})
    lab = {}
    for k, name in LAB.items():
        if k in a and a[k].get("numericValue") is not None:
            v = a[k]["numericValue"]
            lab[name] = {"value": round(v, 3) if name == "CLS" else round(v), "display": a[k].get("displayValue"), "score": a[k].get("score")}
    out = {"lab": lab, "performance_score": round((lhr.get("categories", {}).get("performance", {}).get("score") or 0) * 100),
           "lighthouse_version": lhr.get("lighthouseVersion"), "form_factor": (lhr.get("configSettings") or {}).get("formFactor")}

    # LCP: the element, its phases and how the browser found it.
    lcp, phase_sets = {}, {"insight": [], "legacy": []}
    for source, it in [("legacy", x) for x in items(a.get("largest-contentful-paint-element"))] + \
            [("insight", x) for x in items(a.get("lcp-breakdown-insight"))]:
        if it.get("type") == "node" or "node" in it:
            node = it if it.get("type") == "node" else it.get("node", {})
            lcp.setdefault("element", {"selector": node.get("selector"), "snippet": (node.get("snippet") or "")[:200]})
        for sub in it.get("items", []) if isinstance(it.get("items"), list) else []:
            if isinstance(sub, dict) and "node" in sub:
                node = sub["node"]
                lcp.setdefault("element", {"selector": node.get("selector"), "snippet": (node.get("snippet") or "")[:200]})
            if isinstance(sub, dict) and ("phase" in sub or "subpart" in sub):
                phase_sets[source].append({"phase": sub.get("phase") or sub.get("label"),
                                           "ms": round(sub.get("timing", sub.get("duration", 0)) or 0)})
    if phase_sets["insight"] or phase_sets["legacy"]:
        lcp["phases"] = phase_sets["insight"] or phase_sets["legacy"]
    for it in items(a.get("lcp-discovery-insight")):
        if it.get("type") == "checklist":
            lcp["discovery"] = {k: v.get("value") for k, v in (it.get("items") or {}).items()}
    if lcp.get("phases"):
        worst = max(lcp["phases"], key=lambda p: p["ms"])
        advice = {"ttfb": "Server response is the biggest part: cache the HTML at a CDN, cut redirects and backend time.",
                  "time to first byte": "Server response is the biggest part: cache the HTML at a CDN, cut redirects and backend time.",
                  "load delay": "The browser finds the LCP resource late: put it in the HTML, preload it, give it fetchpriority=high.",
                  "resource load delay": "The browser finds the LCP resource late: put it in the HTML, preload it, give it fetchpriority=high.",
                  "load time": "The LCP file is slow to download: shrink it (AVIF or WebP, right dimensions) and serve it from a CDN.",
                  "resource load duration": "The LCP file is slow to download: shrink it (AVIF or WebP, right dimensions) and serve it from a CDN.",
                  "render delay": "The LCP element waits to render: remove render-blocking CSS and JS, and avoid rendering it with client-side JavaScript.",
                  "element render delay": "The LCP element waits to render: remove render-blocking CSS and JS, and avoid rendering it with client-side JavaScript."}
        lcp["biggest_phase"] = {**worst, "advice": advice.get(str(worst["phase"]).lower(), "")}
    out["lcp"] = lcp

    # Fixes ranked by Lighthouse's own estimated savings.
    best = {}
    for k, (metric, fix) in FIXES.items():
        au = a.get(k)
        if not au or au.get("score") is None or au.get("score") >= 0.9 or au.get("scoreDisplayMode") in ("notApplicable", "manual"):
            continue
        savings = au.get("metricSavings") or {}
        det = au.get("details") or {}
        est_ms = max([v for m, v in savings.items() if m in ("LCP", "FCP", "TBT", "INP") and isinstance(v, (int, float))] + [det.get("overallSavingsMs") or 0])
        est_cls = savings.get("CLS") if isinstance(savings.get("CLS"), (int, float)) else 0
        top = []
        for it in items(au)[:5]:
            if not isinstance(it, dict):
                continue
            url = it.get("url") or it.get("entity") or it.get("groupLabel") or (it.get("node") or {}).get("selector") or (it.get("source") or {}).get("url")
            if isinstance(url, dict):
                url = url.get("text") or url.get("url")
            if url:
                top.append({"what": short(str(url)), **{x: round(it[x]) for x in ("wastedMs", "wastedBytes", "blockingTime", "duration", "total") if isinstance(it.get(x), (int, float))}})
        cand = {"audit": k, "metric": metric, "title": au.get("title"), "display": au.get("displayValue"),
                "estimated_savings_ms": round(est_ms), "estimated_cls_savings": round(est_cls, 3), "fix": fix, "top_items": top}
        group = SAME_AS.get(k, k)  # a legacy audit and its newer insight report one problem; keep the bigger estimate
        if group not in best or (cand["estimated_savings_ms"], cand["estimated_cls_savings"]) > (best[group]["estimated_savings_ms"], best[group]["estimated_cls_savings"]):
            best[group] = cand
    fixes = list(best.values())
    fixes.sort(key=lambda f: (-(f["estimated_savings_ms"] + f["estimated_cls_savings"] * 10000), f["audit"]))
    out["fixes"] = fixes[:15]
    tp = items(a.get("third-party-summary")) or items(a.get("third-parties-insight"))
    out["third_parties"] = [{"entity": t.get("entity") if isinstance(t.get("entity"), str) else (t.get("entity") or {}).get("text"),
                             "blocking_ms": round(t.get("blockingTime") or 0), "transfer_kb": round((t.get("transferSize") or 0) / 1024)}
                            for t in tp[:8] if isinstance(t, dict)]
    shifts = items(a.get("layout-shifts"))
    out["layout_shifts"] = [{"element": (s.get("node") or {}).get("selector"), "score": round(s.get("score", 0), 4)} for s in shifts[:5] if isinstance(s, dict)]
    return out


class HtmlChecks(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.in_head, self.blocking_scripts, self.head_css = base, False, [], 0
        self.imgs, self.unsized, self.first_img_lazy, self.fetchpriority_high = 0, 0, None, False
        self.preloads, self.preconnects, self.third_party_hosts, self.font_faces_no_display = [], 0, set(), 0
        self.gfonts_no_swap, self.viewport, self._style, self.tags = 0, False, False, 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        self.tags += 1
        if tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif tag == "script" and a.get("src"):
            host = urllib.parse.urlsplit(urllib.parse.urljoin(self.base, a["src"])).netloc.lower()
            if host and host.removeprefix("www.") != urllib.parse.urlsplit(self.base).netloc.lower().removeprefix("www."):
                self.third_party_hosts.add(host)
            if self.in_head and "async" not in a and "defer" not in a and a.get("type", "") != "module":
                self.blocking_scripts.append(short(a["src"]))
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "stylesheet" in rel and self.in_head:
                self.head_css += 1
                if "fonts.googleapis.com" in a.get("href", "") and "display=" not in a.get("href", ""):
                    self.gfonts_no_swap += 1
            if "preload" in rel:
                self.preloads.append(a.get("as", ""))
            if "preconnect" in rel:
                self.preconnects += 1
        elif tag == "img":
            self.imgs += 1
            if not (a.get("width") and a.get("height")) and "aspect-ratio" not in a.get("style", ""):
                self.unsized += 1
            if self.first_img_lazy is None:
                self.first_img_lazy = a.get("loading", "").lower() == "lazy"
            if a.get("fetchpriority", "").lower() == "high":
                self.fetchpriority_high = True
        elif tag == "meta" and a.get("name", "").lower() == "viewport":
            self.viewport = True
        elif tag == "style":
            self._style = True

    def handle_endtag(self, tag):
        if tag == "style":
            self._style = False
        if tag == "head":
            self.in_head = False

    def handle_data(self, data):
        if self._style:
            for block in re.findall(r"@font-face\s*{[^}]*}", data, re.I):
                if "font-display" not in block.lower():
                    self.font_faces_no_display += 1


def html_checks(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(5_000_000).decode("utf-8", "ignore")
            final = r.geturl()
    except Exception as e:
        return {"status": "unreachable", "error": type(e).__name__}
    p = HtmlChecks(final)
    try:
        p.feed(html)
    except Exception:
        pass
    findings = []
    if p.first_img_lazy:
        findings.append({"code": "FIRST_IMAGE_LAZY", "metric": "LCP", "fix": "The first image in the HTML has loading=\"lazy\"; if it is the hero or LCP image, remove it and add fetchpriority=\"high\"."})
    if p.imgs and not p.fetchpriority_high and "image" not in p.preloads:
        findings.append({"code": "NO_IMAGE_PRIORITY_HINT", "metric": "LCP", "fix": "Give the LCP image fetchpriority=\"high\" (or preload it)."})
    if p.blocking_scripts:
        findings.append({"code": "BLOCKING_SCRIPTS_IN_HEAD", "metric": "LCP", "count": len(p.blocking_scripts), "examples": p.blocking_scripts[:5],
                         "fix": "Add defer (or async for independent scripts) to scripts in the head."})
    if p.unsized:
        findings.append({"code": "UNSIZED_IMAGES", "metric": "CLS", "count": p.unsized, "fix": "Add width and height attributes (or aspect-ratio) to images."})
    if p.font_faces_no_display or p.gfonts_no_swap:
        findings.append({"code": "FONT_DISPLAY_MISSING", "metric": "FCP", "count": p.font_faces_no_display + p.gfonts_no_swap,
                         "fix": "Add font-display: swap to @font-face rules, or &display=swap to Google Fonts URLs."})
    if len(p.third_party_hosts) >= 8:
        findings.append({"code": "MANY_THIRD_PARTY_HOSTS", "metric": "INP", "count": len(p.third_party_hosts), "examples": sorted(p.third_party_hosts)[:8],
                         "fix": "Audit third-party scripts; remove unused ones and load the rest after interaction."})
    if not p.viewport:
        findings.append({"code": "NO_VIEWPORT", "metric": "INP", "fix": "Add a responsive viewport meta tag."})
    return {"status": "ok", "html_kb": round(len(html.encode("utf-8")) / 1024), "elements": p.tags, "images": p.imgs,
            "head_stylesheets": p.head_css, "preconnects": p.preconnects, "third_party_script_hosts": len(p.third_party_hosts), "findings": findings}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", default=[], help="Page to diagnose (repeat or comma-separate, up to 5)")
    ap.add_argument("--strategy", choices=["mobile", "desktop", "both"], default="mobile")
    ap.add_argument("--lighthouse", action="append", default=[], help="A saved Lighthouse JSON report (no key needed)")
    args = ap.parse_args()
    urls = list(dict.fromkeys(u.strip() for a in args.url for u in a.split(",") if u.strip()))[:5]
    if not urls and not args.lighthouse:
        fail("INPUT_INVALID", "Pass --url (with PSI_API_KEY set) or --lighthouse report.json.")
    if any(not re.match(r"^https?://[^/\s]+", u) for u in urls):
        fail("INPUT_INVALID", "Each --url must be an absolute http(s) URL.")
    key = os.environ.get("PSI_API_KEY")
    results, notes = [], []
    for path in args.lighthouse:
        try:
            with open(path, encoding="utf-8") as f:
                lhr = json.load(f)
        except (OSError, ValueError) as e:
            fail("FILE_UNREADABLE", "Could not read %s: %s" % (path, e))
        lhr = lhr.get("lighthouseResult", lhr)  # a saved PageSpeed Insights response works too
        if "audits" not in lhr:
            fail("NOT_LIGHTHOUSE", "%s is not a Lighthouse JSON report." % path)
        page = lhr.get("finalDisplayedUrl") or lhr.get("finalUrl") or lhr.get("requestedUrl")
        results.append({"url": page, "source": "lighthouse_file", "strategy": (lhr.get("configSettings") or {}).get("formFactor"),
                        "field": None, "diagnosis": diagnose(lhr), "html": html_checks(page) if page and page.startswith("http") else None})
    if urls and not key:
        notes.append("PSI_API_KEY is not set, so no field data or lab run was fetched (PageSpeed Insights refuses keyless requests). "
                     "Create a free key in Google Cloud (enable the PageSpeed Insights API), or save a Lighthouse report from Chrome DevTools and pass --lighthouse.")
    done = {r["url"] for r in results}
    for u in urls:
        strategies = ["mobile", "desktop"] if args.strategy == "both" else [args.strategy]
        for st in strategies:
            entry = {"url": u, "source": "pagespeed_insights" if key else "html_only", "strategy": st, "field": None, "diagnosis": None}
            if key:
                data, err = psi(u, st, key)
                if err:
                    entry["psi_error"] = err
                else:
                    le = data.get("loadingExperience") or {}
                    url_level = bool(le.get("metrics")) and not le.get("origin_fallback")
                    entry["field"] = field_data(le if url_level else data.get("originLoadingExperience"))
                    if entry["field"]:
                        entry["field"]["scope"] = "url" if url_level else "origin"
                    entry["diagnosis"] = diagnose(data.get("lighthouseResult", {}))
            if u not in done or st == strategies[0]:
                entry["html"] = html_checks(u)
            results.append(entry)
    if all(r.get("diagnosis") is None and (r.get("html") or {}).get("status") != "ok" for r in results):
        fail("NOTHING_MEASURED", "No lab run, field data or HTML could be read.", pages=results, notes=notes)
    json.dump({"status": "ok", "pages": results, "thresholds": {k: {"good": v[0], "poor_above": v[1]} for k, v in THRESHOLDS.items()},
               "notes": notes}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
