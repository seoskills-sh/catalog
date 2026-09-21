#!/usr/bin/env python3
"""Zero-Click Risk Scorer — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 zero_click.py --keywords keywords.json [--device mobile]
  keywords.json: [{"term":"what is seo","volume":40000}, {"term":"seo agency"}]
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.request, urllib.error, urllib.parse

BASE = "https://serpapi.com/search.json"
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "..", "references", "feature_weights.json")))
WEIGHTS = CFG["weights"]           # feature -> weight
SERP_KEYS = CFG["serpapi_keys"]    # feature -> serpapi response key
PUSH_PENALTY = CFG.get("push_down_penalty", 15)


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def fetch(key, term, device):
    q = urllib.parse.urlencode({"engine": "google", "q": term, "device": device, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(BASE + "?" + q, timeout=45) as r:
                return 200, json.loads(r.read())
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


def score(data):
    present = [f for f, k in SERP_KEYS.items() if data.get(k)]
    risk = min(100, sum(WEIGHTS.get(f, 0) for f in present))
    # push-down: count feature blocks that typically render above organic
    above = sum(1 for f in present if f in ("ai_overview", "featured_snippet", "knowledge_panel", "shopping"))
    if above >= 2:
        risk = min(100, risk + PUSH_PENALTY)
    cls = "high" if risk > 55 else "moderate" if risk >= 25 else "low"
    return risk, cls, present


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True)
    ap.add_argument("--device", default="mobile")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    scored, skipped = [], []
    for i, kw in enumerate(keywords):
        term = kw["term"] if isinstance(kw, dict) else kw
        vol = kw.get("volume") if isinstance(kw, dict) else None
        status, data = fetch(key, term, a.device)
        if status == 429:
            fail("RATE_LIMITED", "SERP quota exhausted.", partial=i)
        if status != 200:
            skipped.append({"term": term, "reason": "serp_error"}); continue
        risk, cls, present = score(data)
        adjusted = round(vol * (1 - risk / 100)) if vol is not None else None
        scored.append({"term": term, "volume": vol, "zero_click_risk": risk, "class": cls,
                       "adjusted_opportunity": adjusted, "features_present": present})
        time.sleep(0.4)
    with_opp = [s for s in scored if s["adjusted_opportunity"] is not None]
    no_opp = [s for s in scored if s["adjusted_opportunity"] is None]
    with_opp.sort(key=lambda s: s["adjusted_opportunity"], reverse=True)
    no_opp.sort(key=lambda s: s["zero_click_risk"])
    json.dump({"status": "ok", "device": a.device, "scored_count": len(scored),
               "keywords": with_opp + no_opp, "skipped": skipped}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
