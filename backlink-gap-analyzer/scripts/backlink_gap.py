#!/usr/bin/env python3
"""Backlink Gap Analyzer — reference implementation (DataForSEO Backlinks shape).

Auth:   DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD (Basic). Swap fetch_referring_domains
        for another provider (Ahrefs/Majestic) if preferred.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 backlink_gap.py --target example.com --competitors a.com,b.com
"""
from __future__ import annotations
import argparse, base64, json, os, sys, time
import urllib.request, urllib.error
from collections import defaultdict

ENDPOINT = "https://api.dataforseo.com/v3/backlinks/referring_domains/live"


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def auth_header():
    login, pw = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if login and pw:
        tok = base64.b64encode(f"{login}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {tok}"}
    fail("AUTH_MISSING_BACKLINK_PROVIDER",
         "Set DATAFORSEO_LOGIN/PASSWORD or BACKLINK_API_KEY — a paid backlink API is required.")


def fetch_referring_domains(domain, hdr, limit=1000):
    body = json.dumps([{"target": domain, "limit": limit, "order_by": ["rank,desc"]}]).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json", **hdr})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
                items = (data.get("tasks") or [{}])[0].get("result") or []
                rows = items[0].get("items", []) if items else []
                return 200, [{"domain": it.get("domain"), "rank": it.get("rank", 0) or 0,
                              "category": (it.get("categories") or [None])[0]} for it in rows if it.get("domain")]
        except urllib.error.HTTPError as e:
            if e.code == 402:
                fail("PROVIDER_PAYMENT_REQUIRED", "Backlink provider requires payment/credits.")
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
    ap.add_argument("--target", required=True)
    ap.add_argument("--competitors", required=True)
    ap.add_argument("--min-overlap", type=int, default=2, dest="min_overlap")
    ap.add_argument("--max-results", type=int, default=300, dest="max_results")
    a = ap.parse_args()
    hdr = auth_header()
    competitors = [c.strip() for c in a.competitors.split(",") if c.strip()]

    status, target_rows = fetch_referring_domains(a.target, hdr)
    if status == 429:
        fail("RATE_LIMITED", "Backlink API quota exhausted.")
    if status != 200:
        fail("REQUEST_FAILED", "target fetch HTTP %s" % status)
    target_set = {r["domain"] for r in target_rows}

    overlap = defaultdict(lambda: {"count": 0, "rank": 0, "category": None, "competitors": []})
    skipped = []
    for comp in competitors:
        st, rows = fetch_referring_domains(comp, hdr)
        if st == 429:
            fail("RATE_LIMITED", "Backlink API quota exhausted.", partial=len(overlap))
        if st != 200:
            skipped.append({"competitor": comp, "http": st}); continue
        for r in rows:
            d = r["domain"]
            overlap[d]["count"] += 1
            overlap[d]["rank"] = max(overlap[d]["rank"], r["rank"])
            overlap[d]["category"] = overlap[d]["category"] or r["category"]
            overlap[d]["competitors"].append(comp)
        time.sleep(0.3)

    if len(skipped) == len(competitors):
        fail("INSUFFICIENT_DATA", "All competitor fetches failed.")

    import math
    gaps = []
    for d, info in overlap.items():
        if info["count"] >= a.min_overlap and d not in target_set:
            gaps.append({"referring_domain": d, "overlap": info["count"], "authority": info["rank"],
                         "authority_metric": "domain_rank", "category": info["category"],
                         "links_to": info["competitors"],
                         "priority": round(info["rank"] * math.log(1 + info["count"]), 1)})
    gaps.sort(key=lambda g: g["priority"], reverse=True)
    json.dump({"status": "ok", "target": a.target, "competitors": competitors,
               "target_has_no_backlinks": len(target_set) == 0, "skipped_competitors": skipped,
               "gap_count": len(gaps), "gaps": gaps[:a.max_results]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
