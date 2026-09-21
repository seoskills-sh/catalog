#!/usr/bin/env python3
"""Inbound Anchor Risk Analyzer — reference implementation (DataForSEO shape).

Auth:   DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD (Basic) or BACKLINK_API_KEY.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 inbound_anchors.py --target example.com --brand "Acme,Acme Inc" \
       [--money "buy widgets,cheap widgets"]
"""
from __future__ import annotations
import argparse, base64, json, os, re, sys, time
import urllib.request, urllib.error

ENDPOINT = "https://api.dataforseo.com/v3/backlinks/anchors/live"
GENERIC = {"click here", "here", "website", "this website", "visit", "read more", "link", "this"}
TARGET_MIX = {"branded": "40-60%", "generic_or_url": "20-30%", "partial_commercial": "<=20%",
              "exact_match_commercial": "<=10%"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def auth_header():
    login, pw = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if login and pw:
        return {"Authorization": "Basic " + base64.b64encode(f"{login}:{pw}".encode()).decode()}
    fail("AUTH_MISSING_BACKLINK_PROVIDER", "Set DATAFORSEO_LOGIN/PASSWORD or BACKLINK_API_KEY.")


def fetch_anchors(target, hdr, limit=1000):
    body = json.dumps([{"target": target, "limit": limit, "order_by": ["backlinks,desc"]}]).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json", **hdr})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = (json.loads(r.read()).get("tasks") or [{}])[0].get("result") or []
                return 200, (res[0].get("items", []) if res else [])
        except urllib.error.HTTPError as e:
            if e.code == 402:
                fail("PROVIDER_PAYMENT_REQUIRED", "Backlink provider requires credits.")
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


def classify(anchor, brands, money):
    a = (anchor or "").strip().lower()
    if not a:
        return "empty_or_image"
    if re.match(r"^https?://|^www\.", a):
        return "naked_url"
    branded = any(b.lower() in a for b in brands)
    money_hit = any(m.lower() in a for m in money) if money else False
    money_exact = any(a == m.lower() for m in money) if money else False
    if branded and money_hit:
        return "branded_plus_keyword"
    if branded:
        return "branded"
    if money_exact:
        return "exact_match_commercial"
    if money_hit:
        return "partial_commercial"
    if a in GENERIC:
        return "generic"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--brand", required=True)
    ap.add_argument("--money", default="")
    ap.add_argument("--exact-ceiling", type=float, default=0.2, dest="ceiling")
    a = ap.parse_args()
    hdr = auth_header()
    brands = [b.strip() for b in a.brand.split(",") if b.strip()]
    money = [m.strip() for m in a.money.split(",") if m.strip()]
    status, items = fetch_anchors(a.target, hdr)
    if status == 429:
        fail("RATE_LIMITED", "Backlink API quota exhausted.")
    if status != 200:
        fail("REQUEST_FAILED", "HTTP %s" % status)
    if not items:
        json.dump({"status": "no_backlinks", "target": a.target, "distribution": {}}, sys.stdout); return

    from collections import Counter
    weighted = Counter()
    have_rd = any(it.get("referring_domains") is not None for it in items)
    for it in items:
        w = it.get("referring_domains") if have_rd else it.get("backlinks", 1)
        weighted[classify(it.get("anchor"), brands, money)] += (w or 1)
    total = sum(weighted.values()) or 1
    dist = {k: round(v / total, 3) for k, v in weighted.items()}
    exact = dist.get("exact_match_commercial", 0)
    branded = dist.get("branded", 0) + dist.get("branded_plus_keyword", 0)
    generic = dist.get("generic", 0)
    flags = []
    if exact > a.ceiling:
        flags.append("OVER_OPTIMIZED_COMMERCIAL")
    if branded < 0.3:
        flags.append("LOW_BRANDED")
    risk = "high" if "OVER_OPTIMIZED_COMMERCIAL" in flags else "medium" if branded < 0.3 else "low"
    json.dump({"status": "ok", "advisory": True, "target": a.target,
               "weighting": "referring_domains" if have_rd else "backlinks",
               "velocity_available": False,
               "distribution": dist, "exact_commercial_ratio": exact, "branded_ratio": round(branded, 3),
               "generic_ratio": generic, "flags": flags, "risk_level": risk,
               "recommended_mix": TARGET_MIX}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
