#!/usr/bin/env python3
"""Perplexity Citation Analyzer — reference implementation.

Auth:   PERPLEXITY_API_KEY. Output: JSON on stdout per
        ../references/output.schema.json. Std-lib only.

Usage: python3 perplexity_citations.py --queries queries.json \
       [--brand example.com] [--competitors a.com,b.com] [--model sonar]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from collections import Counter, defaultdict
from urllib.parse import urlparse

ENDPOINT = "https://api.perplexity.ai/chat/completions"
CITING_MODELS = {"sonar", "sonar-pro", "sonar-reasoning"}


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout); sys.exit(1)


def registrable(url):
    host = urlparse(url).netloc.lower().lstrip("www.")
    return host


def page_type(url):
    p = urlparse(url).path.lower()
    if p in ("", "/"):
        return "homepage"
    if re.search(r"best|top|vs|review|alternativ", url, re.I):
        return "listicle"
    if re.search(r"/blog|/article|/guide|/post|/learn", p):
        return "blog_article"
    if re.search(r"/product|/pricing|/features", p):
        return "product"
    if re.search(r"/docs|/help|/support|/kb", p):
        return "docs"
    return "other"


def ask(key, model, query):
    body = json.dumps({"model": model, "temperature": 0,
                       "messages": [{"role": "user", "content": query}]}).encode()
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
                return 200, data.get("citations", []) or []
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, []
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else 2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, []
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, []
    return 429, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("--brand", default=""); ap.add_argument("--competitors", default="")
    ap.add_argument("--model", default="sonar")
    a = ap.parse_args()
    key = os.environ.get("PERPLEXITY_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set PERPLEXITY_API_KEY.")
    model, overridden = (a.model, False) if a.model in CITING_MODELS else ("sonar", True)
    queries = json.load(open(a.queries))
    brand = {d.strip().lower() for d in a.brand.split(",") if d.strip()}
    competitors = {d.strip().lower() for d in a.competitors.split(",") if d.strip()}

    dom_counter, type_counter, cells = Counter(), Counter(), []
    brand_hits, comp_share, gaps = 0, defaultdict(int), []
    total_citations = 0
    for q in queries:
        status, citations = ask(key, model, q)
        if status == 429:
            cells.append({"query": q, "status": "rate_limited"}); continue
        if status != 200:
            cells.append({"query": q, "status": "engine_error", "code": status}); continue
        if not citations:
            cells.append({"query": q, "status": "no_citations", "citations": []}); continue
        doms = [registrable(u) for u in citations]
        for d, u in zip(doms, citations):
            dom_counter[d] += 1; type_counter[page_type(u)] += 1; total_citations += 1
        brand_cited = any(any(bd in d for bd in brand) for d in doms) if brand else False
        comp_cited = {c for c in competitors for d in doms if c in d}
        for c in comp_cited:
            comp_share[c] += 1
        if brand_cited:
            brand_hits += 1
        if competitors and comp_cited and not brand_cited:
            gaps.append({"query": q, "competitors_cited": sorted(comp_cited)})
        cells.append({"query": q, "status": "ok", "citations": citations,
                      "brand_cited": brand_cited})
        time.sleep(0.5)

    n = len(queries)
    json.dump({
        "status": "ok", "model": model, "model_overridden": overridden,
        "queries": n, "total_citations": total_citations,
        "by_domain": [{"domain": d, "count": c, "share": round(c / max(total_citations, 1), 3)}
                      for d, c in dom_counter.most_common(50)],
        "by_page_type": dict(type_counter),
        "brand_citation_rate": round(brand_hits / n, 3) if brand else None,
        "competitor_share": {c: round(v / max(total_citations, 1), 3) for c, v in comp_share.items()},
        "citation_gaps": gaps, "cells": cells,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
