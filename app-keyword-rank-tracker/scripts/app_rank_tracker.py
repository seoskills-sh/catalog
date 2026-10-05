#!/usr/bin/env python3
"""App Keyword Rank Tracker: reference implementation.

Records an app's keyword positions across the App Store (keyless iTunes
Search) and Google Play (ranks you export from your ASO tool as a CSV), across
locales, and, stateful via --previous, reports movers, newly ranking and lost
keywords, velocity, and volatility. Returns an honest baseline on the first run
instead of fabricated deltas.

Auth:   none. Google Play has no free rank API, so its ranks come from --play-ranks.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 app_rank_tracker.py --app-id 6001112223 --keywords keywords.json \
       --locales us,gb [--play-ranks play_ranks.csv] [--previous prev.json]
"""
from __future__ import annotations
import argparse, csv, datetime, json, math, sys, time
import urllib.request, urllib.error, urllib.parse

SEARCH = "https://itunes.apple.com/search"
# Apple limits the iTunes Search API to about 20 calls a minute and answers
# with 403 beyond that, so App Store lookups are paced at one per 3.1 seconds.
SEARCH_PACING = 3.1
# Column names accepted in a --play-ranks export (lowercased), first match wins.
KEYWORD_COLS = ("keyword", "term", "search term", "query")
LOCALE_COLS = ("locale", "country", "storefront", "market")
RANK_COLS = ("rank", "position", "ranking")


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
            if exc.code in (403, 429):  # Apple signals its rate limit with 403
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


def read_csv(path, label):
    """Rows of a CSV export as dicts keyed by lowercased, trimmed column names."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(fh, dialect=dialect))
    except FileNotFoundError:
        fail("INPUT_MISSING", "%s not found: %s" % (label, path))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        fail("INPUT_INVALID", "%s not readable: %s" % (label, exc))
    return [{(k or "").strip().lower(): v.strip() for k, v in row.items() if isinstance(v, str)} for row in rows]


def pick(row, names):
    return next((row[n] for n in names if row.get(n)), "")


def load_play_ranks(path):
    """{(keyword, locale): rank or None} from an ASO tool's Google Play export.
    A blank, zero or non-numeric rank means not ranked; a missing row means no data."""
    ranks = {}
    for row in read_csv(path, "play-ranks"):
        kw, loc = pick(row, KEYWORD_COLS).lower(), pick(row, LOCALE_COLS).lower()
        if not kw or not loc:
            continue
        raw = pick(row, RANK_COLS).replace("#", "")
        rank = int(float(raw)) if raw.replace(".", "", 1).isdigit() and float(raw) >= 1 else None
        ranks[(kw, loc)] = rank
    if not ranks:
        fail("INPUT_INVALID", "play-ranks has no rows with keyword and locale columns "
             "(accepted names: %s; %s)." % ("/".join(KEYWORD_COLS), "/".join(LOCALE_COLS)))
    return ranks


def rank_play(play_ranks, kw, locale):
    key = (kw.lower(), locale.lower())
    if key not in play_ranks:
        return None, "no_data"
    rank = play_ranks[key]
    return (rank, "ok") if rank else (None, "not_found")


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
    ap.add_argument("--package", help="Google Play package name, reported with the Play ranks.")
    ap.add_argument("--play-ranks", dest="play_ranks",
                    help="CSV export of Google Play ranks (keyword, locale, rank columns).")
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
    if not (("appstore" in stores and args.app_id) or ("play" in stores and args.play_ranks)):
        fail("INPUT_MISSING", "Provide --app-id for App Store ranks and/or --play-ranks for Google Play ranks.")
    warnings = []
    if "play" in stores and not args.play_ranks:
        warnings.append("Google Play skipped: it has no free rank API, so pass its ranks with --play-ranks "
                        "(a CSV export from your ASO tool).")
    play_ranks = load_play_ranks(args.play_ranks) if "play" in stores and args.play_ranks else {}

    keywords = load_json(args.keywords, "keywords")
    if not isinstance(keywords, list) or not keywords:
        fail("INPUT_INVALID", "keywords file must be a non-empty JSON array of strings.")
    keywords = keywords[:args.max_kw]
    OUT = args.limit + 1

    prev_tracked = {}
    if args.previous:
        for t in load_json(args.previous, "previous").get("snapshot", {}).get("tracked", []):
            prev_tracked[t["key"]] = t

    tracked, incomplete = [], False
    plan = []
    if "appstore" in stores and args.app_id:
        plan += [("appstore", loc) for loc in locales]
    if play_ranks:
        plan += [("play", loc) for loc in locales]

    for kw in keywords:
        for store, loc in plan:
            key_id = "%s|%s|%s" % (kw, store, loc)
            if store == "appstore":
                rank, state = rank_appstore(args.app_id, kw, loc, args.limit)
                time.sleep(SEARCH_PACING)
            else:
                rank, state = rank_play(play_ranks, kw, loc)
            prev = prev_tracked.get(key_id)
            prev_hist = prev.get("history", []) if prev else []
            if state in ("rate_limited", "error", "no_data"):
                # No reading this run: carry the history forward, never a fabricated point.
                incomplete = incomplete or state != "no_data"
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

    persist = {"snapshot": {"date": args.date,
                            "tracked": [{"key": t["key"], "keyword": t["keyword"], "store": t["store"],
                                         "locale": t["locale"], "history": t["history"]} for t in tracked]}}

    if not prev_tracked:
        baseline = [{"key": t["key"], "keyword": t["keyword"], "store": t["store"], "locale": t["locale"],
                     "rank": t.get("rank"), "state": t.get("state", "ok")} for t in tracked]
        json.dump({"status": "baseline", "date": args.date,
                   "message": "First run: ranks recorded, no deltas computed. Re-run to get movement.",
                   "keywords_tracked": len(tracked), "positions": baseline, "warnings": warnings, **persist},
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
        "status": "partial" if incomplete else "ok",
        "date": args.date,
        "keywords_tracked": len(tracked),
        "stale_count": sum(1 for t in tracked if t.get("state") == "stale"),
        "positions": positions,
        "movers": {"improved": improved, "declined": declined},
        "newly_ranking": newly,
        "lost": lost,
        "volatility_leaders": volatile[:20],
        "out_of_results_convention": OUT,
        "warnings": warnings,
        **persist,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
