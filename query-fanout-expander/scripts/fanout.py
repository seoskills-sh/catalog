#!/usr/bin/env python3
"""Query Fan-Out Expander — reference implementation.

Auth:   Google Suggest is keyless; PAA/related need SERP_API_KEY.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 fanout.py --seeds seeds.json --depth 2 [--sources autocomplete,paa,related]
"""
from __future__ import annotations
import argparse, json, os, sys, time, re, urllib.request, urllib.error, urllib.parse
from collections import deque, Counter

SUGGEST = "https://suggestqueries.google.com/complete/search"
SERP = "https://serpapi.com/search.json"
INTENT = [
    ("transactional", re.compile(r"\b(buy|price|cheap|deal|coupon|order|for sale)\b", re.I)),
    ("commercial", re.compile(r"\b(best|top|review|vs|compare|alternative)\b", re.I)),
    ("question", re.compile(r"\b(how|what|why|when|where|who|which|can|does|is)\b", re.I)),
]


def norm(q):
    return re.sub(r"\s+", " ", q.strip().lower())


def autocomplete(q, hl):
    try:
        url = SUGGEST + "?" + urllib.parse.urlencode({"client": "firefox", "hl": hl, "q": q})
        req = urllib.request.Request(url, headers={"User-Agent": "seoskills-fanout/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
            return 200, data[1] if len(data) > 1 else []
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception:
        return 0, []


def serp_questions(key, q, gl):
    try:
        url = SERP + "?" + urllib.parse.urlencode({"engine": "google", "q": q, "gl": gl, "api_key": key})
        with urllib.request.urlopen(url, timeout=45) as r:
            d = json.loads(r.read())
            paa = [x.get("question", "") for x in d.get("related_questions", [])]
            rel = [x.get("query", "") for x in d.get("related_searches", [])]
            return 200, [s for s in paa + rel if s]
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception:
        return 0, []


def intent_of(q):
    for name, rx in INTENT:
        if rx.search(q):
            return name
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--max-queries", type=int, default=500, dest="max_queries")
    ap.add_argument("--sources", default="autocomplete,paa,related")
    ap.add_argument("--hl", default="en"); ap.add_argument("--gl", default="us")
    a = ap.parse_args()
    sources = set(s.strip() for s in a.sources.split(","))
    key = os.environ.get("SERP_API_KEY")
    if (sources & {"paa", "related"}) and not key:
        sources -= {"paa", "related"}
    if not sources:
        json.dump({"status": "error", "error": {"code": "NO_SOURCES_AVAILABLE"}}, sys.stdout); sys.exit(1)

    seeds = [norm(s) for s in json.load(open(a.seeds))]
    seen = {s: {"query": s, "depth": 0, "source": "seed", "parent": None} for s in seeds}
    frontier = deque((s, 0) for s in seeds)
    ac_status, serp_status, hit_cap = "ok", "ok", False

    while frontier:
        if len(seen) >= a.max_queries:
            hit_cap = True; break
        q, d = frontier.popleft()
        if d >= a.depth:
            continue
        expansions = []
        if "autocomplete" in sources and ac_status != "throttled":
            st, sug = autocomplete(q, a.hl)
            if st == 429:
                ac_status = "throttled"
            expansions += [(s, "autocomplete") for s in sug]
            time.sleep(0.15)
        if (sources & {"paa", "related"}) and serp_status != "rate_limited":
            st, qs = serp_questions(key, q, a.gl)
            if st == 429:
                serp_status = "rate_limited"
            expansions += [(s, "serp") for s in qs]
            time.sleep(0.3)
        for s, src in expansions:
            ns = norm(s)
            if ns and ns not in seen:
                seen[ns] = {"query": ns, "depth": d + 1, "source": src, "parent": q}
                frontier.append((ns, d + 1))
                if len(seen) >= a.max_queries:
                    hit_cap = True; break

    queries = list(seen.values())
    buckets = Counter(intent_of(x["query"]) for x in queries)
    json.dump({"status": "ok", "hl": a.hl, "gl": a.gl,
               "sources_used": sorted(sources), "autocomplete_status": ac_status, "serp_status": serp_status,
               "hit_cap": hit_cap, "query_count": len(queries),
               "intent_buckets": dict(buckets), "queries": queries}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
