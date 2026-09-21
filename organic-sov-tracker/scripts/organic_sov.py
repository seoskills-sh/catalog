#!/usr/bin/env python3
"""Organic Share-of-Voice Tracker — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only. Stateful: pass the prior run as --previous.

Usage: python3 organic_sov.py --keywords keywords.json [--previous previous.json]
"""
from __future__ import annotations
import argparse, json, os, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict
from urllib.parse import urlparse

BASE = "https://serpapi.com/search.json"
HERE = os.path.dirname(os.path.abspath(__file__))
CTR = json.load(open(os.path.join(HERE, "..", "references", "ctr_curve.json")))["curve"]


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def ctr(pos):
    return CTR.get(str(pos), 0.0)


def domain_of(u):
    return urlparse(u).netloc.lower().lstrip("www.")


def fetch(key, term, top_n):
    q = urllib.parse.urlencode({"engine": "google", "q": term, "num": top_n, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(BASE + "?" + q, timeout=45) as r:
                d = json.loads(r.read())
                pos = {}
                for i, o in enumerate(d.get("organic_results", []), start=1):
                    dm = domain_of(o.get("link", ""))
                    if dm and dm not in pos:
                        pos[dm] = i
                return 200, pos
        except urllib.error.HTTPError as e:
            if e.code == 429:
                return 429, {}
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}
    return 429, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True); ap.add_argument("--previous")
    ap.add_argument("--top-n", type=int, default=10, dest="top_n")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    prev = json.load(open(a.previous)) if a.previous else {}
    prev_sov = prev.get("sov", {})
    prev_snaps = prev.get("snapshots", {})
    have_vol = all(isinstance(k, dict) and k.get("volume") for k in keywords)

    earned = defaultdict(float)
    cluster_earned = defaultdict(lambda: defaultdict(float))
    total_weight = 0.0
    snapshots = {}
    for i, kw in enumerate(keywords):
        term = kw["term"] if isinstance(kw, dict) else kw
        vol = (kw.get("volume") if isinstance(kw, dict) else None) or 1
        cluster = (kw.get("cluster") if isinstance(kw, dict) else None) or "default"
        status, pos = fetch(key, term, a.top_n)
        if status == 429:
            # carry forward remaining snapshots and stop
            break
        if status != 200 or not pos:
            if term in prev_snaps:
                pos = prev_snaps[term]  # reuse stale
            else:
                continue
        snapshots[term] = pos
        total_weight += vol
        for dm, p in pos.items():
            e = vol * ctr(p)
            earned[dm] += e
            cluster_earned[dm][cluster] += e
        time.sleep(0.4)

    total_weight = total_weight or 1
    sov = {dm: round(v / total_weight, 4) for dm, v in earned.items()}
    leaderboard = sorted(sov.items(), key=lambda kv: kv[1], reverse=True)
    baseline = not prev_sov
    movers = []
    if not baseline:
        for dm, s in sov.items():
            delta = round(s - prev_sov.get(dm, 0), 4)
            if abs(delta) >= 0.005:
                movers.append({"domain": dm, "sov_delta": delta,
                               "top_clusters": dict(sorted(cluster_earned[dm].items(),
                                                           key=lambda kv: kv[1], reverse=True)[:3])})
        movers.sort(key=lambda m: abs(m["sov_delta"]), reverse=True)
    json.dump({"status": "ok", "baseline": baseline,
               "volume_basis": "search_volume" if have_vol else "uniform",
               "keywords_tracked": len(snapshots),
               "leaderboard": [{"domain": d, "sov": s, "sov_delta": round(s - prev_sov.get(d, 0), 4) if not baseline else None}
                               for d, s in leaderboard],
               "movers": movers, "sov": sov, "snapshots": snapshots}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
