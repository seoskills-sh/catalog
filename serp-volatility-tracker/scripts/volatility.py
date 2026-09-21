#!/usr/bin/env python3
"""SERP Feature Volatility Tracker — reference implementation.

Auth:   SERP_API_KEY. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only. Stateful: pass the prior run's snapshots as --previous.

Usage: python3 volatility.py --keywords keywords.json [--previous previous.json]
"""
from __future__ import annotations
import argparse, json, os, sys, time, datetime, urllib.request, urllib.error, urllib.parse
from urllib.parse import urlparse

BASE = "https://serpapi.com/search.json"
FEATURE_KEYS = {
    "featured_snippet": "answer_box", "people_also_ask": "related_questions",
    "ai_overview": "ai_overview", "local_pack": "local_results", "video": "inline_videos",
    "image_pack": "inline_images", "shopping": "shopping_results", "top_stories": "top_stories",
}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def norm(u):
    p = urlparse(u); return (p.netloc.lower().lstrip("www.") + p.path.rstrip("/")).lower()


def fetch(key, kw, top_n, features):
    q = urllib.parse.urlencode({"engine": "google", "q": kw, "num": top_n, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(BASE + "?" + q, timeout=45) as r:
                d = json.loads(r.read())
                ranking = [norm(o["link"]) for o in d.get("organic_results", []) if o.get("link")][:top_n]
                feats = {f: (FEATURE_KEYS[f] in d and bool(d[FEATURE_KEYS[f]])) for f in features}
                return 200, ranking, feats
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, [], {}
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, [], {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, [], {}
    return 429, [], {}


def rbo(prev, curr, p=0.9):
    """Rank-biased overlap; top positions weighted more."""
    if not prev or not curr:
        return 0.0
    score, seen_prev, seen_curr = 0.0, set(), set()
    depth = max(len(prev), len(curr))
    for d in range(depth):
        if d < len(prev):
            seen_prev.add(prev[d])
        if d < len(curr):
            seen_curr.add(curr[d])
        overlap = len(seen_prev & seen_curr) / (d + 1)
        score += (p ** d) * overlap
    return round((1 - p) * score, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True); ap.add_argument("--previous")
    ap.add_argument("--top-n", type=int, default=10, dest="top_n")
    a = ap.parse_args()
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set SERP_API_KEY.")
    keywords = json.load(open(a.keywords))
    features = list(FEATURE_KEYS.keys())
    prev = {s["keyword"]: s for s in (json.load(open(a.previous)) if a.previous else [])}

    snapshots, changes = [], []
    now = datetime.datetime.utcnow().isoformat() + "Z"
    for i, kw in enumerate(keywords):
        status, ranking, feats = fetch(key, kw, a.top_n, features)
        if status == 429:
            fail("RATE_LIMITED", "SERP quota exhausted.", partial=i)
        if status != 200:
            if kw in prev:
                snapshots.append(prev[kw])  # carry forward, don't lose history
            changes.append({"keyword": kw, "status": "serp_error"})
            continue
        snap = {"keyword": kw, "ranking": ranking, "features": feats, "captured_at": now}
        snapshots.append(snap)
        p = prev.get(kw)
        if not p:
            changes.append({"keyword": kw, "status": "baseline", "volatility_score": None})
            continue
        rank_vol = round(1 - rbo(p.get("ranking", []), ranking), 3)
        pf, cf = p.get("features", {}), feats
        changed = [f for f in features if pf.get(f, False) != cf.get(f, False)]
        feat_vol = round(len(changed) / max(len(features), 1), 3)
        score = round(0.7 * rank_vol + 0.3 * feat_vol, 3)
        cls = "volatile" if score > 0.5 else "shifting" if score >= 0.2 else "stable"
        changes.append({
            "keyword": kw, "status": "ok", "volatility_score": score, "class": cls,
            "rank_volatility": rank_vol, "feature_volatility": feat_vol,
            "features_gained": [f for f in changed if cf.get(f)],
            "features_lost": [f for f in changed if pf.get(f) and not cf.get(f)],
        })
        time.sleep(0.4)

    changes.sort(key=lambda c: (c.get("volatility_score") is None, -(c.get("volatility_score") or 0)))
    json.dump({"status": "ok", "keyword_count": len(keywords), "changes": changes,
               "snapshots": snapshots}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
