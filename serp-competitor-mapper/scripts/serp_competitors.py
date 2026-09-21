#!/usr/bin/env python3
"""SERP Competitor Landscape Mapper — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 serp_competitors.py --keywords keywords.json [--target example.com]
"""
from __future__ import annotations
import argparse, json, os, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict
from urllib.parse import urlparse

BASE = "https://serpapi.com/search.json"
# position -> visibility weight (approx CTR share)
POS_WEIGHT = {1: 0.31, 2: 0.16, 3: 0.10, 4: 0.07, 5: 0.05, 6: 0.04, 7: 0.03, 8: 0.03, 9: 0.02, 10: 0.02}
AGGREGATORS = {"wikipedia.org", "amazon.com", "youtube.com", "reddit.com", "pinterest.com",
               "facebook.com", "linkedin.com", "quora.com"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def domain_of(u):
    return urlparse(u).netloc.lower().lstrip("www.")


def fetch(key, kw, top_n, loc, hl):
    q = urllib.parse.urlencode({"engine": "google", "q": kw, "num": top_n, "location": loc, "hl": hl, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(BASE + "?" + q, timeout=45) as r:
                d = json.loads(r.read())
                return 200, [domain_of(o["link"]) for o in d.get("organic_results", []) if o.get("link")][:top_n]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, []
                time.sleep(2 ** attempt); continue
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
    ap.add_argument("--keywords", required=True)
    ap.add_argument("--target", default="")
    ap.add_argument("--top-n", type=int, default=10, dest="top_n")
    ap.add_argument("--location", default="United States"); ap.add_argument("--hl", default="en")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    target = a.target.lower().lstrip("www.")

    dom = defaultdict(lambda: {"coverage": 0, "visibility": 0.0, "positions": []})
    target_wins, competitor_wins = [], []
    skipped, fetched = [], 0
    for i, kw in enumerate(keywords):
        status, domains = fetch(key, kw, a.top_n, a.location, a.hl)
        if status == 429:
            fail("RATE_LIMITED", "SERP quota exhausted.", partial=i)
        if status != 200 or not domains:
            skipped.append(kw); continue
        fetched += 1
        tgt_pos = None
        for pos, d in enumerate(domains, start=1):
            dom[d]["coverage"] += 1
            dom[d]["visibility"] += POS_WEIGHT.get(pos, 0.01)
            dom[d]["positions"].append(pos)
            if target and d == target and tgt_pos is None:
                tgt_pos = pos
        if target:
            (target_wins if tgt_pos and tgt_pos <= 3 else competitor_wins).append(
                {"keyword": kw, "target_position": tgt_pos, "top_domain": domains[0]})
        time.sleep(0.4)

    total_vis = sum(v["visibility"] for v in dom.values()) or 1
    threshold = max(2, fetched * 0.1)
    leaderboard = []
    for d, v in dom.items():
        leaderboard.append({"domain": d, "serp_share": round(v["visibility"] / total_vis, 4),
                            "keyword_coverage": v["coverage"],
                            "avg_position": round(sum(v["positions"]) / len(v["positions"]), 1),
                            "is_aggregator": d in AGGREGATORS,
                            "tier": "true_competitor" if v["coverage"] >= threshold else "incidental"})
    leaderboard.sort(key=lambda x: x["serp_share"], reverse=True)
    out = {"status": "ok", "keywords_analyzed": fetched, "skipped": skipped,
           "competitor_landscape": leaderboard}
    if target:
        rank = next((i + 1 for i, x in enumerate(leaderboard) if x["domain"] == target), None)
        out["target"] = {"domain": target, "landscape_rank": rank,
                         "keywords_top3": len([w for w in target_wins if w["target_position"]]),
                         "outranked_on": [w["keyword"] for w in competitor_wins][:50]}
    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
