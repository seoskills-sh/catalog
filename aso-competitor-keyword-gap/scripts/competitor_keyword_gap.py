#!/usr/bin/env python3
"""ASO Competitor Keyword Gap: reference implementation.

Profiles a target app and its competitors on the App Store: pulls listing
metadata (keyless iTunes Lookup), derives each app's keyword set from its
metadata (or reads it from your ASO tool's CSV export), measures real App Store
ranks for the top terms (keyless iTunes Search) and category rank (Apple's top
charts), diffs term sets to expose gaps/overlaps, and, stateful via
--previous, detects title/screenshot/description changes and the keyword rank
shifts that follow them.

Auth:   none. Every source is a free Apple endpoint or a local file.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 competitor_keyword_gap.py --target 6001112223 \
       --competitors 111,222,333 --country us [--keywords-file ranks.csv] [--previous prev.json]
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict

LOOKUP = "https://itunes.apple.com/lookup"
SEARCH = "https://itunes.apple.com/search"
CHART = "https://itunes.apple.com/{country}/rss/{chart}/limit=100/genre={genre}/json"
# Apple limits the iTunes Search API to about 20 calls a minute and answers
# with 403 beyond that, so rank searches are paced at one per 3.1 seconds.
SEARCH_PACING = 3.1
# Column names accepted in a --keywords-file export (lowercased), first match wins.
APP_COLS = ("app_id", "app id", "app", "id", "track id", "trackid")
KEYWORD_COLS = ("keyword", "term", "search term", "query")
RANK_COLS = ("rank", "position", "ranking")
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
            if exc.code == 403:  # Apple's rate-limit answer: stop rather than retry
                return 429, {}
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
    """Proxy indexed set from visible metadata (title and description n-grams)."""
    text = "%s %s" % (rec.get("trackName", ""), rec.get("description", ""))
    toks = [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 2 and t not in STOP_WORDS]
    freq = defaultdict(int)
    for t in toks:
        freq[t] += 1
    for i in range(len(toks) - 1):
        freq[toks[i] + " " + toks[i + 1]] += 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return {term: None for term, _ in ranked}  # rank unknown from metadata


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


def load_keyword_file(path):
    """{app_id: {keyword: rank or None}} from an ASO tool's ranked-keywords export."""
    out = defaultdict(dict)
    for row in read_csv(path, "keywords-file"):
        app = next((row[c] for c in APP_COLS if row.get(c)), "")
        kw = next((row[c] for c in KEYWORD_COLS if row.get(c)), "").lower()
        if not app or not kw:
            continue
        raw = next((row[c] for c in RANK_COLS if row.get(c)), "").replace("#", "")
        out[app][kw] = int(float(raw)) if raw.replace(".", "", 1).isdigit() and float(raw) >= 1 else None
    if not out:
        fail("INPUT_INVALID", "keywords-file has no rows with app id and keyword columns (accepted names: %s; %s)."
             % ("/".join(APP_COLS), "/".join(KEYWORD_COLS)))
    return out


def search_positions(term, country, limit):
    """("ok", {app_id: 1-based position}) from iTunes Search for a term, or
    ("limited", None) when Apple rate-limits, or ("error", None) on any other failure."""
    q = urllib.parse.urlencode({"term": term, "country": country, "entity": "software", "limit": limit})
    code, data = get_json(SEARCH + "?" + q, {"User-Agent": "aso-gap/1.0"})
    if code == 429:
        return "limited", None
    if code != 200:
        return "error", None
    return "ok", {str(rec.get("trackId")): i for i, rec in enumerate(data.get("results", []), start=1)}


def category_rank(rec, country, cache):
    """Position in Apple's top 100 free (or paid) chart for the app's primary genre, else None."""
    genre = rec.get("primaryGenreId")
    if not genre:
        return None
    chart = "toppaidapplications" if (rec.get("price") or 0) > 0 else "topfreeapplications"
    if (chart, genre) not in cache:
        code, data = get_json(CHART.format(country=country, chart=chart, genre=genre), {"User-Agent": "aso-gap/1.0"})
        entries = (data.get("feed", {}) or {}).get("entry", []) if code == 200 else []
        if isinstance(entries, dict):  # a one-entry feed is an object, not a list
            entries = [entries]
        cache[(chart, genre)] = [str(((e.get("id") or {}).get("attributes") or {}).get("im:id")) for e in entries]
        time.sleep(1.0)
    ids = cache[(chart, genre)]
    app_id = str(rec.get("trackId"))
    return ids.index(app_id) + 1 if app_id in ids else None


def profile(rec, country, file_kws, charts):
    if file_kws is not None:
        kws, source = dict(file_kws), "file"
    else:
        kws, source = derive_keywords(rec), "derived_metadata"
    shots = rec.get("screenshotUrls", []) or []
    return {
        "app_id": str(rec.get("trackId")),
        "title": rec.get("trackName"),
        "subtitle": None,  # Apple's free APIs do not expose the subtitle
        "primary_genre": rec.get("primaryGenreName"),
        "version": rec.get("version"),
        "description_hash": sha1(rec.get("description")),
        "screenshot_hash": sha1("|".join(shots)),
        "screenshot_count": len(shots),
        "category_rank": category_rank(rec, country, charts) if charts is not None else None,
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


def measure_ranks(profiles, country, n_terms, limit):
    """Measure App Store ranks for the top terms with iTunes Search, in place.

    Terms carried by the most competitors go first. For a metadata-derived set
    the measurement is the truth: an app in the results gets the term with its
    rank, an app absent from them loses it. A file-sourced set (from an ASO
    tool) only gains ranks it lacked. A failed search changes nothing.
    Returns (terms measured, failed searches, rate limited)."""
    counts = defaultdict(int)
    for app_id, prof in profiles.items():
        for term in prof["keywords"]:
            counts[term] += 0 if prof.get("is_target") else 1
    terms = sorted(counts, key=lambda t: (-counts[t], t))[:n_terms]
    measured = failed = 0
    for i, term in enumerate(terms):
        if i:
            time.sleep(SEARCH_PACING)
        state, positions = search_positions(term, country, limit)
        if state == "limited":
            return measured, failed, True
        if state == "error":
            failed += 1
            continue
        measured += 1
        for app_id, prof in profiles.items():
            kws, rank = prof["keywords"], positions.get(app_id)
            if prof["keyword_source"] == "file":
                if term in kws and kws[term] is None and rank:
                    kws[term] = rank
            elif rank:
                kws[term] = rank
            else:
                kws.pop(term, None)
    return measured, failed, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--competitors", required=True)
    ap.add_argument("--country", default="us")
    ap.add_argument("--source", choices=["auto", "metadata"], default="auto",
                    help="auto adds free App Store ranks and category ranks; metadata skips them.")
    ap.add_argument("--keywords-file", dest="keywords_file",
                    help="CSV export of ranked keywords per app (app id, keyword, rank columns).")
    ap.add_argument("--rank-terms", type=int, default=30, dest="rank_terms",
                    help="Terms to measure with iTunes Search (about 3 seconds each); 0 skips.")
    ap.add_argument("--rank-limit", type=int, default=100, dest="rank_limit",
                    help="Search results scanned per term (max 200); absent means not ranked within it.")
    ap.add_argument("--previous")
    ap.add_argument("--max-competitors", type=int, default=15, dest="max_comp")
    ap.add_argument("--max-keywords", type=int, default=100, dest="max_kw")
    args = ap.parse_args()

    comp_ids = [c.strip() for c in args.competitors.split(",") if c.strip()][:args.max_comp]
    if not comp_ids:
        fail("INPUT_MISSING", "Provide at least one competitor id via --competitors.")
    file_kws = load_keyword_file(args.keywords_file) if args.keywords_file else {}
    all_ids = [args.target] + comp_ids
    records = lookup(all_ids, args.country)
    if args.target not in records:
        fail("TARGET_NOT_FOUND", "Target app %s not found in country %s." % (args.target, args.country))

    prev_snap = {}
    if args.previous:
        prev_snap = load_json(args.previous, "previous").get("snapshot", {}).get("apps", {})

    charts = {} if args.source == "auto" else None
    profiles = {}
    for app_id in all_ids:
        if app_id in records and app_id not in profiles:
            profiles[app_id] = profile(records[app_id], args.country, file_kws.get(app_id), charts)
    profiles[args.target]["is_target"] = True

    measured, failed, limited = 0, 0, False
    if args.source == "auto" and args.rank_terms > 0:
        measured, failed, limited = measure_ranks(profiles, args.country, args.rank_terms, min(args.rank_limit, 200))
    target = profiles[args.target]
    target.pop("is_target", None)
    target_terms = set(target["keywords"].keys())

    competitors, comp_union = [], defaultdict(int)
    comp_rank_sum = defaultdict(list)
    for cid in comp_ids:
        if cid not in profiles:
            competitors.append({"app_id": cid, "status": "not_found"})
            continue
        prof = profiles[cid]
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

    sources = {p["keyword_source"] for p in profiles.values()}
    keyword_source = sources.pop() if len(sources) == 1 else "mixed"
    warnings = []
    if not args.previous:
        warnings.append("First run: no --previous snapshot, so metadata_changes and rank_shifts are baselines.")
    if "derived_metadata" in {p["keyword_source"] for p in profiles.values()}:
        warnings.append("Keyword sets derived from visible title+description%s. Pass --keywords-file "
                        "(an export from your ASO tool) for each app's full indexed keywords."
                        % ("" if measured else "; ranks unavailable"))
    if measured:
        warnings.append("Ranks for %d term(s) measured with the iTunes Search API (top %d results), which "
                        "approximates App Store search order." % (measured, min(args.rank_limit, 200)))
    if limited:
        warnings.append("Rank measurement stopped early: Apple rate-limited the iTunes Search API.")
    if failed:
        warnings.append("%d rank search(es) failed and were skipped; those terms keep their derived sets." % failed)
    if charts is not None:
        warnings.append("category_rank is the position in Apple's top 100 chart for the app's primary genre; "
                        "null means outside the top 100.")
    warnings.append("Subtitles are not available from Apple's free APIs, so subtitle is null.")

    json.dump({
        "status": "ok",
        "country": args.country,
        "keyword_source": keyword_source,
        "ranks_measured": measured,
        "target": {"app_id": target["app_id"], "title": target["title"],
                   "keyword_count": len(target_terms), "category_rank": target["category_rank"]},
        "competitors": competitors,
        "keyword_gaps": gaps[:args.max_kw],
        "overlaps": overlaps,
        "unique_to_you": unique_to_you,
        "warnings": warnings,
        "snapshot": {"country": args.country, "apps": profiles},
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
