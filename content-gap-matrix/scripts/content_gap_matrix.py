#!/usr/bin/env python3
"""Competitor Content Gap Matrix — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 content_gap_matrix.py --keywords kw.json --target example.com \
       --competitors a.com,b.com [--volumes vol.json]
"""
from __future__ import annotations
import argparse, json, math, os, sys, time
import urllib.request, urllib.error, urllib.parse
from urllib.parse import urlparse

BASE = "https://serpapi.com/search.json"


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True); ap.add_argument("--target", required=True)
    ap.add_argument("--competitors", required=True)
    ap.add_argument("--min-overlap", type=int, default=2, dest="min_overlap")
    ap.add_argument("--target-floor", type=int, default=10, dest="floor")
    ap.add_argument("--top-n", type=int, default=20, dest="top_n")
    ap.add_argument("--volumes")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    target = a.target.lower().lstrip("www.")
    competitors = [c.strip().lower().lstrip("www.") for c in a.competitors.split(",") if c.strip()]
    volumes = json.load(open(a.volumes)) if a.volumes else {}
    have_vol = bool(volumes)

    gaps, near, shared_all, skipped = [], [], [], []
    for i, kw in enumerate(keywords):
        status, pos = fetch(key, kw, a.top_n)
        if status == 429:
            fail("RATE_LIMITED", "SERP quota exhausted.", partial=i)
        if status != 200 or not pos:
            skipped.append(kw); continue
        comp_ranking = {c: pos[c] for c in competitors if c in pos}
        target_pos = pos.get(target)
        target_has = target_pos is not None and target_pos <= a.floor
        if len(comp_ranking) >= a.min_overlap and not target_has:
            best_comp = min(comp_ranking.values())
            difficulty = 1.0 if best_comp <= 3 else 0.6 if best_comp <= 6 else 0.35
            vol = volumes.get(kw, 0)
            vol_w = math.log10(vol + 10) if have_vol else 1.0
            entry = {"keyword": kw, "volume": vol or None,
                     "competitor_overlap": len(comp_ranking), "competitors_ranking": comp_ranking,
                     "best_competitor_position": best_comp,
                     "target_position": target_pos, "target_near": target_pos is not None,
                     "priority": round(vol_w * len(comp_ranking) / difficulty, 2)}
            (near if target_pos is not None else gaps).append(entry)
            if len(comp_ranking) == len(competitors) and not target_has:
                shared_all.append(kw)
        time.sleep(0.4)

    gaps.sort(key=lambda g: g["priority"], reverse=True)
    near.sort(key=lambda g: g["priority"], reverse=True)
    json.dump({"status": "ok", "target": target, "competitors": competitors,
               "keywords_evaluated": len(keywords) - len(skipped), "skipped": skipped,
               "priority_basis": "volume" if have_vol else "no_volume",
               "gap_count": len(gaps), "gaps": gaps, "near_gaps": near,
               "shared_by_all_competitors": shared_all}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
