#!/usr/bin/env python3
"""Local Grid Rank Tracker — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 grid_rank.py --name "Acme Plumbing" --keyword "plumber" \
       --lat 37.7749 --lng -122.4194 --grid 7 --spacing 1.5
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, time, urllib.request, urllib.error, urllib.parse

BASE = "https://serpapi.com/search.json"


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def normalize(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def similar(a, b):
    """Token Jaccard similarity for fuzzy business-name match."""
    sa, sb = set(normalize(a).split()), set(normalize(b).split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def grid_points(lat, lng, n, spacing_km):
    half = n // 2
    dlat = spacing_km / 111.0
    dlng = spacing_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    pts = []
    for row in range(n):          # north -> south
        for col in range(n):      # west -> east
            plat = lat + (half - row) * dlat
            plng = lng + (col - half) * dlng
            pts.append((row, col, round(plat, 6), round(plng, 6)))
    return pts


def fetch(key, kw, lat, lng, zoom):
    ll = f"@{lat},{lng},{zoom}z"
    q = urllib.parse.urlencode({"engine": "google_maps", "type": "search", "q": kw, "ll": ll, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(BASE + "?" + q, timeout=45) as r:
                return 200, json.loads(r.read()).get("local_results", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                return 429, []
            if e.code >= 500 and attempt < 2:
                time.sleep(2 ** attempt); continue
            return e.code, []
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt); continue
            return 0, []
    return 0, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True); ap.add_argument("--keyword", required=True)
    ap.add_argument("--lat", type=float, required=True); ap.add_argument("--lng", type=float, required=True)
    ap.add_argument("--grid", type=int, default=7); ap.add_argument("--spacing", type=float, default=1.5)
    ap.add_argument("--zoom", type=int, default=14); ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    n = a.grid if a.grid % 2 == 1 else a.grid + 1
    pts = grid_points(a.lat, a.lng, n, a.spacing)
    cost_note = f"{n*n} billed searches" if n >= 9 else None

    matrix = [[None] * n for _ in range(n)]
    found_ranks, top3, done = [], 0, 0
    for row, col, plat, plng in pts:
        status, results = fetch(key, a.keyword, plat, plng, a.zoom)
        if status == 429:
            json.dump({"status": "partial", "error": {"code": "RATE_LIMITED"},
                       "grid_size": n, "matrix": matrix, "partial_points": done,
                       "cost_note": cost_note}, sys.stdout); return
        done += 1
        rank, conf = None, 0.0
        for i, item in enumerate(results[:20]):
            s = similar(a.name, item.get("title", ""))
            if s >= a.threshold:
                rank, conf = i + 1, round(s, 2); break
        cell = {"rank": rank if rank else "not_found", "match_confidence": conf,
                "lat": plat, "lng": plng}
        matrix[row][col] = cell
        if rank:
            found_ranks.append(rank)
            if rank <= 3:
                top3 += 1
        time.sleep(0.4)

    center_cell = matrix[n // 2][n // 2]
    total = n * n
    json.dump({"status": "ok", "business_name": a.name, "keyword": a.keyword,
               "grid_size": n, "spacing_km": a.spacing, "center": {"lat": a.lat, "lng": a.lng},
               "avg_rank": round(sum(found_ranks) / len(found_ranks), 2) if found_ranks else None,
               "found_points": len(found_ranks), "total_points": total,
               "share_of_top3": round(top3 / total, 3), "center_rank": center_cell["rank"],
               "cost_note": cost_note, "matrix": matrix}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
