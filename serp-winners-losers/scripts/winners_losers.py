#!/usr/bin/env python3
"""SERP Winners & Losers Detector — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only. Stateful: pass the prior run's snapshots as --previous.

Usage: python3 winners_losers.py --keywords keywords.json [--previous previous.json]
"""
from __future__ import annotations
import argparse, json, os, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict
from urllib.parse import urlparse

BASE = "https://serpapi.com/search.json"
CTR = {1: 0.271, 2: 0.152, 3: 0.101, 4: 0.070, 5: 0.051, 6: 0.039, 7: 0.031, 8: 0.026, 9: 0.022, 10: 0.019}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def domain_of(u):
    return urlparse(u).netloc.lower().lstrip("www.")


def fetch(key, kw, top_n):
    q = urllib.parse.urlencode({"engine": "google", "q": kw, "num": top_n, "api_key": key})
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
    ap.add_argument("--min-movement", type=float, default=2.0, dest="min_move")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    prev_snaps = (json.load(open(a.previous)) if a.previous else {}).get("snapshots", {})
    OUT = a.top_n + 1

    current = {}
    for kw in keywords:
        status, pos = fetch(key, kw, a.top_n)
        if status == 429:
            break
        if status != 200 or not pos:
            if kw in prev_snaps:
                pos = prev_snaps[kw]
            else:
                continue
        current[kw] = pos
        time.sleep(0.4)

    if not prev_snaps:
        json.dump({"status": "baseline", "keywords_tracked": len(current), "snapshots": current}, sys.stdout, indent=2)
        return

    common = [k for k in current if k in prev_snaps]
    move = defaultdict(lambda: {"delta_sum": 0.0, "n": 0, "gained": [], "lost": [], "improved": [], "declined": [],
                                "vis_change": 0.0})
    for kw in common:
        cur, pre = current[kw], prev_snaps[kw]
        domains = set(cur) | set(pre)
        for d in domains:
            cp, pp = cur.get(d, OUT), pre.get(d, OUT)
            delta = pp - cp  # positive = improved (moved up)
            m = move[d]
            m["delta_sum"] += delta; m["n"] += 1
            m["vis_change"] += CTR.get(cp, 0) - CTR.get(pp, 0)
            if d not in pre and d in cur:
                m["gained"].append(kw)
            elif d in pre and d not in cur:
                m["lost"].append(kw)
            elif delta >= 2:
                m["improved"].append((kw, delta))
            elif delta <= -2:
                m["declined"].append((kw, delta))

    movers = []
    for d, m in move.items():
        avg = m["delta_sum"] / m["n"] if m["n"] else 0
        if abs(avg) < a.min_move and not m["gained"] and not m["lost"]:
            continue
        drivers = sorted(m["improved"] + m["declined"], key=lambda x: abs(x[1]), reverse=True)[:5]
        movers.append({"domain": d, "avg_position_change": round(avg, 2),
                       "visibility_change": round(m["vis_change"], 4),
                       "keywords_gained": len(m["gained"]), "keywords_lost": len(m["lost"]),
                       "top_movements": [{"keyword": k, "position_delta": dl} for k, dl in drivers],
                       "class": "winner" if m["vis_change"] > 0 else "loser"})
    winners = sorted([m for m in movers if m["class"] == "winner"], key=lambda x: x["visibility_change"], reverse=True)
    losers = sorted([m for m in movers if m["class"] == "loser"], key=lambda x: x["visibility_change"])
    json.dump({"status": "ok", "keywords_compared": len(common),
               "out_of_topn_convention": OUT,
               "keywords_added": [k for k in current if k not in prev_snaps],
               "keywords_dropped": [k for k in prev_snaps if k not in current],
               "winners": winners, "losers": losers, "snapshots": current}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
