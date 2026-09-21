#!/usr/bin/env python3
"""AI Overview Citation Monitor — reference implementation (SerpApi shape).

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only. Adapt fetch_serp() for a different SERP provider.

Usage: python3 aio_monitor.py --keywords keywords.json \
       [--brand example.com] [--competitors a.com,b.com] [--device mobile]
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.request, urllib.error, urllib.parse
from urllib.parse import urlparse

BASE = "https://serpapi.com/search.json"


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def domain(url):
    return urlparse(url).netloc.lower().lstrip("www.")


def http_get(url):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=45) as r:
                return 200, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, {}
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}
    return 429, {}


def fetch_serp(key, kw, location, hl, device):
    params = {"engine": "google", "q": kw, "location": location, "hl": hl,
              "device": device, "api_key": key}
    status, data = http_get(BASE + "?" + urllib.parse.urlencode(params))
    if status != 200:
        return status, None, data
    aio = data.get("ai_overview")
    # SerpApi sometimes returns only a page_token that must be expanded.
    if aio and aio.get("page_token") and "text_blocks" not in aio:
        p2 = {"engine": "google_ai_overview", "page_token": aio["page_token"], "api_key": key}
        s2, d2 = http_get(BASE + "?" + urllib.parse.urlencode(p2))
        if s2 == 200:
            aio = d2.get("ai_overview", aio)
    organic = data.get("organic_results", [])
    return 200, aio, organic


def extract_citations(aio):
    if not aio:
        return [], True
    refs = aio.get("references") or aio.get("sources") or []
    urls = []
    for i, r in enumerate(refs):
        u = r.get("link") or r.get("source") or r.get("url")
        if u:
            urls.append({"url": u, "domain": domain(u), "position": i + 1})
    citations_available = bool(refs) or not aio.get("references_withheld")
    return urls, citations_available


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True)
    ap.add_argument("--brand", default=""); ap.add_argument("--competitors", default="")
    ap.add_argument("--location", default="United States")
    ap.add_argument("--hl", default="en"); ap.add_argument("--device", default="mobile")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    brand = {d.strip().lower() for d in a.brand.split(",") if d.strip()}
    competitors = {d.strip().lower() for d in a.competitors.split(",") if d.strip()}

    results, has_aio_n, brand_cited_n = [], 0, 0
    for i, kw in enumerate(keywords):
        status, aio, organic = fetch_serp(key, kw, a.location, a.hl, a.device)
        if status == 429:
            fail("RATE_LIMITED", "SERP quota exhausted.", partial=i)
        if status != 200:
            results.append({"keyword": kw, "status": "serp_error", "code": status}); continue
        citations, avail = extract_citations(aio)
        has = bool(aio)
        doms = {c["domain"] for c in citations}
        brand_cited = any(any(bd in d for bd in brand) for d in doms) if brand else False
        comps = sorted({c for c in competitors for d in doms if c in d})
        brand_organic_pos = next((idx + 1 for idx, o in enumerate(organic or [])
                                  if any(bd in domain(o.get("link", "")) for bd in brand)), None) if brand else None
        if has:
            has_aio_n += 1
        if brand_cited:
            brand_cited_n += 1
        results.append({"keyword": kw, "status": "ok", "has_aio": has,
                        "citations_available": avail, "citations": citations,
                        "brand_cited": brand_cited, "competitors_cited": comps,
                        "brand_organic_position": brand_organic_pos})
        time.sleep(0.5)

    n = len(keywords)
    results.sort(key=lambda r: (not r.get("has_aio", False), r.get("keyword", "")))
    json.dump({"status": "ok", "serp_provider": "serpapi", "device": a.device,
               "keywords": n, "aio_trigger_rate": round(has_aio_n / n, 3) if n else 0,
               "brand_aio_citation_rate": round(brand_cited_n / has_aio_n, 3) if has_aio_n else None,
               "results": results}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
