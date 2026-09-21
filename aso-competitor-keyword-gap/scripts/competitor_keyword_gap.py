#!/usr/bin/env python3
"""ASO Competitor Keyword Gap — reference implementation.

Profiles a target app and its competitors on the App Store: pulls listing
metadata (keyless iTunes Lookup), derives or fetches each app's indexed
keyword set, diffs term sets to expose gaps/overlaps, and — stateful via
--previous — detects title/subtitle/screenshot/description changes and the
keyword rank shifts that follow them.

Auth:   iTunes Lookup needs no key. ASO_API_KEY (OPTIONAL) enables indexed
        keyword ranks, subtitle, and category rank.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 competitor_keyword_gap.py --target 6001112223 \
       --competitors 111,222,333 --country us [--previous prev.json]
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict

LOOKUP = "https://itunes.apple.com/lookup"
ASO_BASE = "https://api.asokeyword.io/v1/app_keywords"
STOP_WORDS = {
    "the", "a", "an", "and", "or", "for", "with", "to", "of", "in", "on", "at",
    "is", "it", "this", "that", "you", "your", "app", "apps", "free", "best",
    "new", "get", "now", "all", "more", "from", "by", "our", "we", "can", "are",
}
TOKEN_RE = re.compile(r"[a-z0-9']+")


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
            if exc.code == 429:
                if attempt == 5:
                    return 429, {}
                time.sleep(2 ** attempt)
                continue
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


def sha1(text):
    return hashlib.sha1((text or "").encode("utf-8", "ignore")).hexdigest()[:16]


def lookup(ids, country):
    q = urllib.parse.urlencode({"id": ",".join(ids), "country": country, "entity": "software"})
    code, data = get_json(LOOKUP + "?" + q, {"User-Agent": "aso-gap/1.0"})
    if code == 429:
        fail("RATE_LIMITED", "iTunes Lookup quota exhausted.")
    if code != 200:
        fail("LOOKUP_FAILED", "iTunes Lookup returned HTTP %s." % code)
    out = {}
    for rec in data.get("results", []):
        out[str(rec.get("trackId"))] = rec
    return out


def derive_keywords(rec, top=40):
    """Proxy indexed set from visible metadata when no ASO API is available."""
    text = "%s %s" % (rec.get("trackName", ""), rec.get("description", ""))
    toks = [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 2 and t not in STOP_WORDS]
    freq = defaultdict(int)
    for t in toks:
        freq[t] += 1
    for i in range(len(toks) - 1):
        freq[toks[i] + " " + toks[i + 1]] += 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return {term: None for term, _ in ranked}  # rank unknown from metadata


def aso_keywords(app_id, country, key, cap):
    q = urllib.parse.urlencode({"app_id": app_id, "country": country, "limit": cap})
    code, data = get_json(ASO_BASE + "?" + q, {"Authorization": "Bearer %s" % key})
    if code == 401:
        fail("AUTH_EXPIRED", "ASO_API_KEY rejected.")
    if code != 200:
        return None, None
    kws = {str(k.get("keyword")): k.get("rank") for k in data.get("keywords", []) if k.get("keyword")}
    return kws, data.get("category_rank")


def profile(rec, country, key, cap):
    subtitle = rec.get("subtitle")  # only present if supplied via ASO/override
    cat_rank = None
    if key:
        kws, cat_rank = aso_keywords(str(rec.get("trackId")), country, key, cap)
        if kws is None:
            kws, source = derive_keywords(rec), "derived_metadata"
        else:
            source = "aso_api"
        time.sleep(0.25)
    else:
        kws, source = derive_keywords(rec), "derived_metadata"
    shots = rec.get("screenshotUrls", []) or []
    return {
        "app_id": str(rec.get("trackId")),
        "title": rec.get("trackName"),
        "subtitle": subtitle,
        "primary_genre": rec.get("primaryGenreName"),
        "version": rec.get("version"),
        "description_hash": sha1(rec.get("description")),
        "screenshot_hash": sha1("|".join(shots)),
        "screenshot_count": len(shots),
        "category_rank": cat_rank,
        "keyword_source": source,
        "keywords": kws,
    }


def diff_meta(prev, cur):
    changes = []
    for field in ("title", "subtitle", "primary_genre", "version", "description_hash",
                  "screenshot_hash", "screenshot_count", "category_rank"):
        if prev.get(field) != cur.get(field):
            changes.append({"field": field, "old": prev.get(field), "new": cur.get(field)})
    return changes


def rank_shifts(prev_kws, cur_kws, top=10):
    shifts = []
    for term, cur_rank in cur_kws.items():
        pr = prev_kws.get(term)
        if isinstance(pr, (int, float)) and isinstance(cur_rank, (int, float)) and pr != cur_rank:
            shifts.append({"keyword": term, "old_rank": pr, "new_rank": cur_rank,
                           "delta": pr - cur_rank})  # positive = improved (moved up)
    for term in prev_kws:
        if term not in cur_kws and isinstance(prev_kws[term], (int, float)):
            shifts.append({"keyword": term, "old_rank": prev_kws[term], "new_rank": None, "delta": None})
    shifts.sort(key=lambda s: abs(s["delta"]) if s["delta"] is not None else 999, reverse=True)
    return shifts[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--competitors", required=True)
    ap.add_argument("--country", default="us")
    ap.add_argument("--source", choices=["auto", "aso", "metadata"], default="auto")
    ap.add_argument("--previous")
    ap.add_argument("--max-competitors", type=int, default=15, dest="max_comp")
    ap.add_argument("--max-keywords", type=int, default=100, dest="max_kw")
    args = ap.parse_args()

    key = os.environ.get("ASO_API_KEY")
    if args.source == "aso" and not key:
        fail("AUTH_MISSING_ASO_KEY", "Set ASO_API_KEY or use --source metadata (keyless, derived).")
    if args.source == "metadata":
        key = None

    comp_ids = [c.strip() for c in args.competitors.split(",") if c.strip()][:args.max_comp]
    if not comp_ids:
        fail("INPUT_MISSING", "Provide at least one competitor id via --competitors.")
    all_ids = [args.target] + comp_ids
    records = lookup(all_ids, args.country)
    if args.target not in records:
        fail("TARGET_NOT_FOUND", "Target app %s not found in country %s." % (args.target, args.country))

    prev_snap = {}
    if args.previous:
        prev_snap = load_json(args.previous, "previous").get("snapshot", {}).get("apps", {})

    target = profile(records[args.target], args.country, key, args.max_kw)
    target_terms = set(target["keywords"].keys())

    competitors, comp_union = [], defaultdict(int)
    comp_rank_sum = defaultdict(list)
    for cid in comp_ids:
        if cid not in records:
            competitors.append({"app_id": cid, "status": "not_found"})
            continue
        prof = profile(records[cid], args.country, key, args.max_kw)
        for term, rank in prof["keywords"].items():
            comp_union[term] += 1
            if isinstance(rank, (int, float)):
                comp_rank_sum[term].append(rank)
        entry = {"app_id": cid, "status": "ok", "title": prof["title"], "subtitle": prof["subtitle"],
                 "primary_genre": prof["primary_genre"], "category_rank": prof["category_rank"],
                 "keyword_count": len(prof["keywords"])}
        if cid in prev_snap:
            entry["metadata_changes"] = diff_meta(prev_snap[cid], prof)
            entry["rank_shifts"] = rank_shifts(prev_snap[cid].get("keywords", {}), prof["keywords"])
        else:
            entry["metadata_changes"] = []
            entry["rank_shifts"] = []
            entry["baseline"] = True
        competitors.append(entry)
        target.setdefault("_snap_more", {})[cid] = prof

    gaps = []
    for term, ncomp in comp_union.items():
        if term in target_terms:
            continue
        ranks = comp_rank_sum.get(term, [])
        gaps.append({"term": term, "competitors_ranking": ncomp,
                     "avg_competitor_rank": round(sum(ranks) / len(ranks), 1) if ranks else None,
                     "you_rank": None})
    gaps.sort(key=lambda g: (-g["competitors_ranking"],
                             g["avg_competitor_rank"] if g["avg_competitor_rank"] is not None else 999))
    overlaps = sorted(t for t in target_terms if t in comp_union)
    unique_to_you = sorted(t for t in target_terms if t not in comp_union)

    snapshot_apps = {args.target: {k: v for k, v in target.items() if not k.startswith("_")}}
    for cid, prof in target.get("_snap_more", {}).items():
        snapshot_apps[cid] = prof
    target.pop("_snap_more", None)

    keyword_source = "aso_api" if key else "derived_metadata"
    status = "ok"
    warnings = []
    if not args.previous:
        warnings.append("First run: no --previous snapshot, so metadata_changes and rank_shifts are baselines.")
    if keyword_source == "derived_metadata":
        warnings.append("No ASO_API_KEY: keyword sets derived from visible title+description; ranks unavailable; "
                        "subtitle only present if supplied.")

    json.dump({
        "status": status,
        "country": args.country,
        "keyword_source": keyword_source,
        "target": {"app_id": target["app_id"], "title": target["title"],
                   "keyword_count": len(target_terms), "category_rank": target["category_rank"]},
        "competitors": competitors,
        "keyword_gaps": gaps[:args.max_kw],
        "overlaps": overlaps,
        "unique_to_you": unique_to_you,
        "warnings": warnings,
        "snapshot": {"country": args.country, "apps": snapshot_apps},
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
