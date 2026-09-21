#!/usr/bin/env python3
"""N-gram SERP Intent Clusterer — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only. Clusters keywords by shared top-N SERP URLs (single-linkage).

Usage: python3 cluster_intent.py --keywords keywords.json --overlap 3 [--own example.com]
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.request, urllib.error, urllib.parse, re
from collections import Counter
from urllib.parse import urlparse

BASE = "https://serpapi.com/search.json"
INTENT_RULES = [
    ("transactional", re.compile(r"\b(buy|price|cheap|deal|coupon|for sale|order|discount|shop)\b", re.I)),
    ("commercial", re.compile(r"\b(best|top|review|vs|compare|alternative|software|tool|service)\b", re.I)),
    ("informational", re.compile(r"\b(how|what|why|when|guide|tutorial|ideas|examples|meaning|tips)\b", re.I)),
]


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def norm(url):
    u = urlparse(url)
    return (u.netloc.lower().lstrip("www.") + u.path.rstrip("/")).lower()


def fetch(key, kw, top_n, loc, hl):
    q = urllib.parse.urlencode({"engine": "google", "q": kw, "num": top_n, "location": loc, "hl": hl, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(BASE + "?" + q, timeout=45) as r:
                data = json.loads(r.read())
                return 200, [norm(o["link"]) for o in data.get("organic_results", []) if o.get("link")][:top_n], \
                    [o.get("title", "") for o in data.get("organic_results", [])]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, [], []
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, [], []
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, [], []
    return 429, [], []


def label_intent(keywords, titles):
    text = " ".join(keywords) + " " + " ".join(titles)
    for intent, rx in INTENT_RULES:
        if rx.search(text):
            return intent
    return "navigational" if len(keywords) == 1 else "informational"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True)
    ap.add_argument("--overlap", type=int, default=3)
    ap.add_argument("--top-n", type=int, default=10, dest="top_n")
    ap.add_argument("--own", default="")
    ap.add_argument("--location", default="United States"); ap.add_argument("--hl", default="en")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    urls, titles, skipped = {}, {}, []
    for i, kw in enumerate(keywords):
        status, u, t = fetch(key, kw, a.top_n, a.location, a.hl)
        if status == 429:
            fail("RATE_LIMITED", "SERP quota exhausted.", partial=i)
        if status != 200 or len(u) < 3:
            skipped.append({"keyword": kw, "reason": "serp_error" if status != 200 else "too_few_results"})
            urls[kw] = set(u); titles[kw] = t
            continue
        urls[kw] = set(u); titles[kw] = t
        time.sleep(0.4)

    # single-linkage union-find on SERP overlap
    parent = {k: k for k in keywords}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    ks = list(keywords)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            if len(urls.get(ks[i], set()) & urls.get(ks[j], set())) >= a.overlap:
                parent[find(ks[i])] = find(ks[j])

    groups = {}
    for k in keywords:
        groups.setdefault(find(k), []).append(k)

    clusters = []
    for members in groups.values():
        modal = Counter(u for k in members for u in urls.get(k, set())).most_common(1)
        target = modal[0][0] if modal else None
        owns = bool(a.own) and target is not None and a.own.lower() in target
        clusters.append({
            "keywords": members, "size": len(members),
            "intent": label_intent(members, [t for k in members for t in titles.get(k, [])]),
            "canonical_target": target, "owns_target": owns,
            "low_confidence": any(len(urls.get(k, set())) < 3 for k in members),
        })
    clusters.sort(key=lambda c: c["size"], reverse=True)
    json.dump({"status": "ok", "serp_provider": "serpapi", "keyword_count": len(keywords),
               "cluster_count": len(clusters), "clusters": clusters, "skipped": skipped},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
