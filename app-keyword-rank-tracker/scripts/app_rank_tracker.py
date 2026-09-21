#!/usr/bin/env python3
"""App Keyword Rank Tracker — reference implementation.

Records an app's keyword positions across the App Store (keyless iTunes
Search) and Google Play (ASO rank API), across locales, and — stateful via
--previous — reports movers, newly ranking / lost keywords, velocity, and
volatility. Returns an honest baseline on the first run instead of fabricated
deltas.

Auth:   App Store search needs no key. Google Play ranks require ASO_API_KEY.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 app_rank_tracker.py --app-id 6001112223 --package com.acme.budget \
       --keywords keywords.json --locales us,gb [--previous prev.json]
"""
from __future__ import annotations
import argparse, datetime, json, math, os, sys, time
import urllib.request, urllib.error, urllib.parse

SEARCH = "https://itunes.apple.com/search"
ASO_RANK = "https://api.asokeyword.io/v1/rank"


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        fail("INPUT_MISSING", "%s not found: %s" % (label, path))
    except (ValueError, OSError) as exc:
        fail("INPUT_INVALID", "%s not readable: %s" % (label, exc))


def get_json(url, headers=None, timeout=45):
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.getcode(), json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                fail("AUTH_EXPIRED", "ASO_API_KEY rejected.")
            if exc.code == 429:
                return 429, {}
            if exc.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return exc.code, {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return 0, {}
    return 429, {}


def rank_appstore(app_id, kw, locale, limit):
    q = urllib.parse.urlencode({"term": kw, "country": locale, "entity": "software", "limit": limit})
    code, data = get_json(SEARCH + "?" + q, {"User-Agent": "aso-rank/1.0"})
    if code == 429:
        return None, "rate_limited"
    if code != 200:
        return None, "error"
    for i, rec in enumerate(data.get("results", []), start=1):
        if str(rec.get("trackId")) == str(app_id):
            return i, "ok"
    return None, "not_found"


def rank_play(pkg, kw, locale, key, limit):
    q = urllib.parse.urlencode({"package": pkg, "term": kw, "country": locale, "limit": limit})
    code, data = get_json(ASO_RANK + "?" + q, {"Authorization": "Bearer %s" % key})
    if code == 429:
        return None, "rate_limited"
    if code != 200:
        return None, "error"
    r = data.get("rank")
    if isinstance(r, int) and r > 0:
        return r, "ok"
    return None, "not_found"


def pstdev(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def velocity(hist_ranks, out):
    """Positive = improving (rank decreasing). Uses OUT for not-ranked points."""
    vals = [r if isinstance(r, int) else out for r in hist_ranks]
    if len(vals) < 2:
        return 0.0
    return round((vals[0] - vals[-1]) / (len(vals) - 1), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", dest="app_id")
    ap.add_argument("--package")
    ap.add_argument("--keywords", required=True)
    ap.add_argument("--locales", default="us")
    ap.add_argument("--stores", default="appstore,play")
    ap.add_argument("--previous")
    ap.add_argument("--limit", type=int, default=50, help="Search depth scanned per keyword.")
    ap.add_argument("--min-move", type=int, default=3, dest="min_move")
    ap.add_argument("--max-keywords", type=int, default=100, dest="max_kw")
    ap.add_argument("--history-cap", type=int, default=30, dest="hist_cap")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()

    stores = [s.strip() for s in args.stores.split(",") if s.strip()]
    locales = [l.strip() for l in args.locales.split(",") if l.strip()]
    key = os.environ.get("ASO_API_KEY")
    if "play" in stores and args.package and not key:
        fail("AUTH_MISSING_ASO_KEY", "Set ASO_API_KEY for Google Play ranks, or drop 'play' from --stores.")
    if not (("appstore" in stores and args.app_id) or ("play" in stores and args.package)):
        fail("INPUT_MISSING", "Provide --app-id for App Store and/or --package for Google Play.")

    keywords = load_json(args.keywords, "keywords")
    if not isinstance(keywords, list) or not keywords:
        fail("INPUT_INVALID", "keywords file must be a non-empty JSON array of strings.")
    keywords = keywords[:args.max_kw]
    OUT = args.limit + 1

    prev_tracked = {}
    if args.previous:
        for t in load_json(args.previous, "previous").get("snapshot", {}).get("tracked", []):
            prev_tracked[t["key"]] = t

    tracked, rate_limited = [], False
    plan = []
    if "appstore" in stores and args.app_id:
        plan += [("appstore", loc) for loc in locales]
    if "play" in stores and args.package and key:
        plan += [("play", loc) for loc in locales]

    for kw in keywords:
        for store, loc in plan:
            key_id = "%s|%s|%s" % (kw, store, loc)
            if store == "appstore":
                rank, state = rank_appstore(args.app_id, kw, loc, args.limit)
            else:
                rank, state = rank_play(args.package, kw, loc, key, args.limit)
            prev = prev_tracked.get(key_id)
            prev_hist = prev.get("history", []) if prev else []
            if state == "rate_limited":
                rate_limited = True
                tracked.append({"key": key_id, "keyword": kw, "store": store, "locale": loc,
                                "rank": (prev_hist[-1]["rank"] if prev_hist else None),
                                "state": "stale", "history": prev_hist})
                continue
            entry = {"date": args.date, "rank": rank}
            hist = (prev_hist + [entry])[-args.hist_cap:]
            hist_ranks = [h["rank"] for h in hist]
            numeric = [r for r in hist_ranks if isinstance(r, int)]
            tracked.append({
                "key": key_id, "keyword": kw, "store": store, "locale": loc,
                "rank": rank, "state": state,
                "prev_rank": (prev_hist[-1]["rank"] if prev_hist else None),
                "velocity": velocity(hist_ranks, OUT),
                "volatility": round(pstdev([r if isinstance(r, int) else OUT for r in hist_ranks]), 2),
                "best_rank": min(numeric) if numeric else None,
                "history": hist,
            })
            time.sleep(0.3)

    persist = {"snapshot": {"date": args.date,
                            "tracked": [{"key": t["key"], "keyword": t["keyword"], "store": t["store"],
                                         "locale": t["locale"], "history": t["history"]} for t in tracked]}}

    if not prev_tracked:
        baseline = [{"key": t["key"], "keyword": t["keyword"], "store": t["store"], "locale": t["locale"],
                     "rank": t.get("rank"), "state": t.get("state", "ok")} for t in tracked]
        json.dump({"status": "baseline", "date": args.date,
                   "message": "First run: ranks recorded, no deltas computed. Re-run to get movement.",
                   "keywords_tracked": len(tracked), "positions": baseline, **persist},
                  sys.stdout, indent=2)
        return

    improved, declined, newly, lost, volatile = [], [], [], [], []
    for t in tracked:
        if t.get("state") == "stale":
            continue
        cur, prev = t.get("rank"), t.get("prev_rank")
        if prev is None and isinstance(cur, int):
            newly.append({"keyword": t["keyword"], "store": t["store"], "locale": t["locale"], "rank": cur})
        elif isinstance(prev, int) and cur is None:
            lost.append({"keyword": t["keyword"], "store": t["store"], "locale": t["locale"], "prev_rank": prev})
        elif isinstance(prev, int) and isinstance(cur, int):
            delta = prev - cur  # positive = improved
            row = {"keyword": t["keyword"], "store": t["store"], "locale": t["locale"],
                   "prev_rank": prev, "rank": cur, "delta": delta}
            if delta >= args.min_move:
                improved.append(row)
            elif delta <= -args.min_move:
                declined.append(row)
        if t.get("volatility", 0) >= args.min_move:
            volatile.append({"keyword": t["keyword"], "store": t["store"], "locale": t["locale"],
                             "volatility": t["volatility"]})
    improved.sort(key=lambda r: -r["delta"])
    declined.sort(key=lambda r: r["delta"])
    volatile.sort(key=lambda r: -r["volatility"])

    positions = [{"keyword": t["keyword"], "store": t["store"], "locale": t["locale"],
                  "rank": t.get("rank"), "prev_rank": t.get("prev_rank"), "state": t.get("state", "ok"),
                  "velocity": t.get("velocity"), "volatility": t.get("volatility"),
                  "best_rank": t.get("best_rank")} for t in tracked]

    json.dump({
        "status": "partial" if rate_limited else "ok",
        "date": args.date,
        "keywords_tracked": len(tracked),
        "stale_count": sum(1 for t in tracked if t.get("state") == "stale"),
        "positions": positions,
        "movers": {"improved": improved, "declined": declined},
        "newly_ranking": newly,
        "lost": lost,
        "volatility_leaders": volatile[:20],
        "out_of_results_convention": OUT,
        **persist,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
