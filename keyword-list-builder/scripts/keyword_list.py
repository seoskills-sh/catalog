#!/usr/bin/env python3
"""Keyword List Builder: reference implementation.

Builds a prioritized keyword list from seed topics: expands each seed through
Google Autocomplete (the seed alone, a-z and question or modifier prefixes),
merges any keyword-tool export the user has (volume, difficulty, CPC) and
their Search Console queries, then classifies intent, assigns a tier and a
content type, decides create / update / fix-CTR / consolidate for each
keyword, groups them into starter clusters and scores priority.

Auth:   none for Autocomplete. Volumes come from the user's own export
        (Ahrefs, Semrush, Keyword Planner, Moz or any CSV with keyword and
        volume columns); rankings from a Search Console export.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 keyword_list.py --seed "crm software" [--seed ...] [--country us] [--language en]
       [--volumes keywords.csv] [--gsc queries.csv] [--brand Acme] [--competitor hubspot] [--exclude stock]
"""
from __future__ import annotations
import argparse, collections, csv, json, math, re, string, sys, time
import urllib.error, urllib.parse, urllib.request

SUGGEST = "https://suggestqueries.google.com/complete/search"
UA = "seoskills-keyword-list-builder/1.0 (+https://seoskills.sh)"
PREFIXES = ["how to {s}", "what is {s}", "why {s}", "best {s}", "{s} vs", "{s} for", "{s} alternatives", "{s} pricing",
            "{s} cost", "{s} free", "{s} examples", "{s} template", "{s} near me", "is {s}", "can {s}", "{s} without", "{s} with"]
STOP = {"the", "a", "an", "of", "to", "in", "for", "and", "or", "is", "are", "on", "with", "by", "at", "my", "your", "vs",
        "how", "what", "why", "best", "can", "do", "does", "i", "near", "me"}
# Intent rules, checked in order: the first match wins. (intent, sub-intent, content type, pattern)
INTENT_RULES = [
    ("transactional", "purchase", "Product or pricing page", r"\b(buy|price|prices|pricing|cost|costs|cheap|discount|deal|coupon|order|for sale|subscription|quote)\b"),
    ("transactional", "signup", "Landing page with a sign-up", r"\b(free trial|sign ?up|demo|download|get started|login|log in|install)\b"),
    ("transactional", "hire-local", "Service or location page", r"\b(near me|nearby|near by|hire|appointment|agency|agencies|contractors?|in my area)\b|\bservices?$"),
    ("commercial", "comparison", "Comparison page", r"\b(vs|versus|compared? to|or)\b|\bcomparison\b"),
    ("commercial", "alternatives", "Alternatives page", r"\b(alternatives?|competitors?|similar to)\b|\b(apps?|tools?|sites?|software) like\b"),
    ("commercial", "best-of", "Curated list", r"\b(best|top|leading|recommended)\b"),
    ("commercial", "review", "Review", r"\b(review|reviews|rating|ratings|worth it|legit|pros and cons)\b"),
    ("informational", "how-to", "Step-by-step tutorial", r"^(how|how to)\b|\b(how to|steps|tutorial|guide|setup|set up)\b"),
    ("informational", "definition", "Explainer", r"^(what|who|what's|whats|define)\b|\b(meaning|definition|explained)\b"),
    ("informational", "troubleshooting", "Diagnostic guide", r"\b(not working|error|fix|problem|issue|why is|why does|broken)\b"),
    ("informational", "examples", "Examples roundup", r"\b(examples?|ideas|templates?|samples?|checklist|types of)\b"),
    ("informational", "question", "Answer article or FAQ", r"^(is|are|can|does|do|should|will|when|where|which|why)\b|\?$"),
    ("commercial", "category", "Category page or product list", r"\b(software|tools?|apps?|platforms?|solutions?|systems?|providers?|vendors?)\b"),
    ("commercial", "audience-fit", "Buyer's guide or solution page", r"\bfor [a-z]"),
]
INTENT_VALUE = {"transactional": 30, "commercial": 25, "informational": 15, "navigational": 5}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def norm(q):
    return re.sub(r"\s+", " ", str(q).strip().lower())


SYNONYMS = {"pricing": "price", "prices": "price", "cost": "price", "costs": "price", "cheap": "price",
            "businesses": "business", "companies": "company", "examples": "example", "alternative": "alternatives"}


def terms(q):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'+.-]*", q.lower()) if w not in STOP}


def topic_terms(q, seed_terms):
    """The words that make a keyword more than its seed, with plurals and price words folded together."""
    out = set()
    for w in terms(q) - seed_terms:
        w = SYNONYMS.get(w, w)
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def autocomplete(q, hl, gl):
    url = SUGGEST + "?" + urllib.parse.urlencode({"client": "firefox", "hl": hl, "gl": gl, "q": q})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
            return 200, [s for s in (data[1] if len(data) > 1 else []) if isinstance(s, str)]
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception:
        return 0, []


def read_table(path):
    try:
        if path.lower().endswith(".json"):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rows = data if isinstance(data, list) else data.get("rows") or data.get("keywords") or []
            rows = [r for r in rows if isinstance(r, dict)]
        else:
            with open(path, newline="", encoding="utf-8-sig") as f:
                sample = f.read(4096)
                f.seek(0)
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;") if sample else csv.excel
                rows = list(csv.DictReader(f, dialect=dialect))
    except (OSError, ValueError, csv.Error) as e:
        fail("FILE_UNREADABLE", "Could not read %s: %s" % (path, e))
    return [{re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_"): v for k, v in r.items() if k} for r in rows]


def col(row, *names):
    for n in names:
        for k, v in row.items():
            if (k == n or k.startswith(n)) and v not in (None, ""):
                return v
    return None


def num(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace(",", "").replace("$", "").replace("%", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(k|m)?(?:\s*[-\u2013]\s*(\d+(?:\.\d+)?)\s*(k|m)?)?$", s)
    if not m:
        return None
    lo = float(m.group(1)) * {"k": 1e3, "m": 1e6}.get(m.group(2), 1)
    if m.group(3):  # Keyword Planner ranges such as "1K - 10K": use the midpoint
        hi = float(m.group(3)) * {"k": 1e3, "m": 1e6}.get(m.group(4), 1)
        return (lo + hi) / 2
    return lo


def classify(kw, brands, competitors):
    words = set(re.findall(r"[a-z0-9]+", kw))
    if brands & words or any(b in kw for b in brands if " " in b):
        return "navigational", "own-brand", "Brand or feature page"
    comp = [c for c in competitors if c in words or (" " in c and c in kw)]
    if comp and not re.search(r"\b(vs|versus|alternatives?|competitors?|compared?|review)\b", kw):
        return "navigational", "competitor-brand", "Comparison or alternatives page (their brand term)"
    for intent, sub, ctype, rx in INTENT_RULES:
        if re.search(rx, kw):
            return intent, sub, ctype
    return "informational", "topic", "Guide or pillar page"


def tier(kw, volume, cut_head, cut_body):
    n = len(terms(kw))
    if volume is not None and cut_head is not None:
        return "head" if volume >= cut_head else "body" if volume >= cut_body else "long-tail"
    return "head" if n <= 2 else "body" if n == 3 else "long-tail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="append", default=[], help="Seed topic (repeat or comma-separate, up to 10)")
    ap.add_argument("--country", default="us", help="Two-letter country for Autocomplete (gl)")
    ap.add_argument("--language", default="en", help="Language for Autocomplete (hl)")
    ap.add_argument("--volumes", help="Keyword-tool export (CSV/TSV/JSON): keyword, volume, optional difficulty and CPC")
    ap.add_argument("--gsc", help="Search Console queries export: query, clicks, impressions, position, optional page")
    ap.add_argument("--brand", action="append", default=[], help="Your brand name(s), for navigational intent")
    ap.add_argument("--competitor", action="append", default=[], help="Competitor brand name(s)")
    ap.add_argument("--exclude", action="append", default=[], help="Drop keywords containing this term (repeatable)")
    ap.add_argument("--no-expand", action="store_true", help="Skip Autocomplete; use only the files")
    ap.add_argument("--max", type=int, default=300, help="Keywords to return")
    args = ap.parse_args()

    seeds = list(dict.fromkeys(norm(s) for a in args.seed for s in a.split(",") if s.strip()))[:10]
    if not seeds and not args.volumes and not args.gsc:
        fail("INPUT_INVALID", "Pass at least one --seed, or a --volumes or --gsc file.")
    split = lambda xs: {norm(x) for a in xs for x in a.split(",") if x.strip()}
    brands, competitors, excludes = split(args.brand), split(args.competitor), split(args.exclude)
    hl, gl = args.language.lower(), args.country.lower()

    kws = collections.OrderedDict()

    def add(k, source, weight=0.0):
        k = norm(k)
        if not k or len(k) > 120 or any(x in k for x in excludes):
            return None
        e = kws.setdefault(k, {"keyword": k, "sources": set(), "suggest_score": 0.0})
        e["sources"].add(source)
        e["suggest_score"] += weight
        return e

    # 1. Autocomplete expansion.
    requests_made, limited, notes = 0, False, []
    if seeds and not args.no_expand:
        for seed in seeds:
            add(seed, "seed", 1.0)
            prefixes = [seed] + ["%s %s" % (seed, c) for c in string.ascii_lowercase] + [p.format(s=seed) for p in PREFIXES]
            for q in prefixes:
                status, sugg = autocomplete(q, hl, gl)
                requests_made += 1
                if status in (403, 429, 503):
                    limited = True
                    notes.append("Autocomplete stopped answering (HTTP %d) after %d requests; the list is partial." % (status, requests_made))
                    break
                for pos, s in enumerate(sugg):
                    if terms(s) & terms(seed):
                        add(s, "autocomplete", 1.0 / (pos + 1))
                time.sleep(0.25)
            if limited:
                break

    # 2. The user's keyword-tool export.
    if args.volumes:
        for r in read_table(args.volumes):
            k = col(r, "keyword", "query", "term", "search_term", "keywords")
            if not k:
                continue
            e = add(k, "volumes")
            if e is None:
                continue
            v = num(col(r, "volume", "search_volume", "avg_monthly_searches", "monthly_searches", "sv", "avg_volume"))
            d = num(col(r, "kd", "keyword_difficulty", "difficulty", "seo_difficulty"))
            c = num(col(r, "cpc", "top_of_page_bid_high", "top_of_page_bid"))
            if v is not None:
                e["volume"] = max(e.get("volume") or 0, v)
            if d is not None:
                e["difficulty"] = d
            if c is not None:
                e["cpc"] = c

    # 3. Search Console: current rankings, CTR gaps and pages competing for one query.
    gsc_pages, band_median = collections.defaultdict(list), {}
    if args.gsc:
        for r in read_table(args.gsc):
            q = col(r, "query", "top_queries", "queries", "keyword")
            if not q:
                continue
            clicks, imps = num(col(r, "clicks")) or 0, num(col(r, "impressions")) or 0
            pos = num(col(r, "position", "average_position", "avg_position"))
            page = col(r, "page", "landing_page", "url", "top_pages")
            e = add(q, "gsc")
            if e is None:
                continue
            g = e.setdefault("gsc", {"clicks": 0, "impressions": 0, "position": None, "pages": []})
            g["clicks"] += clicks
            g["impressions"] += imps
            if pos is not None:  # impression-weighted average position across rows
                prev = g["position"]
                g["position"] = pos if prev is None else (prev * (g["impressions"] - imps) + pos * imps) / max(g["impressions"], 1)
            if page:
                g["pages"].append({"page": page, "impressions": imps, "clicks": clicks})
                gsc_pages[e["keyword"]].append(page)
        # Median CTR per position band from the site's own data, to spot titles that under-earn clicks.
        bands = collections.defaultdict(list)
        for e in kws.values():
            g = e.get("gsc")
            if g and g["position"] and g["impressions"] >= 50:
                bands[min(int(g["position"]), 10)].append(g["clicks"] / g["impressions"])
        band_median = {b: sorted(v)[len(v) // 2] for b, v in bands.items() if len(v) >= 3}

    if not kws:
        fail("NO_KEYWORDS", "No keywords came back. Check the seeds, or pass --volumes or --gsc.", notes=notes)

    # 4. Classify, tier, decide the action, score.
    vols = sorted(e["volume"] for e in kws.values() if e.get("volume") is not None)
    cut_head = vols[int(len(vols) * 0.9)] if len(vols) >= 10 else None
    cut_body = vols[int(len(vols) * 0.6)] if len(vols) >= 10 else None
    max_sugg = max((e["suggest_score"] for e in kws.values()), default=1) or 1
    quick_wins, ctr_gaps, cannibal = [], [], []
    for e in kws.values():
        k = e["keyword"]
        e["intent"], e["sub_intent"], e["content_type"] = classify(k, brands, competitors)
        e["tier"] = tier(k, e.get("volume"), cut_head, cut_body)
        g = e.get("gsc")
        action = "create"
        if g:
            distinct_pages = {p["page"] for p in g["pages"] if p["impressions"] > 0}
            pos = g["position"]
            if len(distinct_pages) > 1:
                action = "consolidate"
                cannibal.append({"keyword": k, "pages": sorted(distinct_pages)})
            elif pos is not None and pos <= 10 and g["impressions"] >= 50 and band_median.get(min(int(pos), 10)) \
                    and g["clicks"] / g["impressions"] < 0.5 * band_median[min(int(pos), 10)]:
                action = "fix CTR"
                ctr_gaps.append(k)
            elif pos is not None and 8 <= pos <= 20 and g["impressions"] >= 20:
                action = "update existing page"
                quick_wins.append(k)
            elif pos is not None and pos <= 20:
                action = "maintain"
            else:
                action = "update existing page" if g["pages"] else "create"
        if e["intent"] == "navigational" and action == "create":
            action = "brand page" if e["sub_intent"] == "own-brand" else "comparison page"
        e["action"] = action
        if e.get("volume") is not None:
            demand = min(40.0, 40 * math.log10(e["volume"] + 1) / 5)  # 100,000 searches a month scores the full 40
        else:
            demand = 25 * e["suggest_score"] / max_sugg + (5 if "gsc" in e["sources"] else 0)
        ease = (20 * (1 - min(e["difficulty"], 100) / 100)) if e.get("difficulty") is not None else 10
        bonus = 10 if action in ("update existing page", "fix CTR") else 0
        e["priority"] = round(min(100, demand + INTENT_VALUE[e["intent"]] + ease + bonus), 1)

    # 5. Starter clusters: keywords that share most of their meaningful terms.
    ordered = sorted(kws.values(), key=lambda e: -e["priority"])
    seed_terms = set().union(*(terms(s) for s in seeds)) if seeds else set()
    clusters = []
    for e in ordered:
        t = topic_terms(e["keyword"], seed_terms)
        home = next((c for c in clusters if (t == c["terms"]) or (t and c["terms"] and len(t & c["terms"]) / len(t | c["terms"]) >= 0.6)), None)
        if home:
            home["keywords"].append(e["keyword"])
        else:
            clusters.append({"primary": e["keyword"], "terms": t, "keywords": [e["keyword"]], "intent": e["intent"]})
        e["cluster"] = (home or clusters[-1])["primary"]
    out_keywords = []
    for e in ordered[: args.max]:
        row = {k: e[k] for k in ("keyword", "intent", "sub_intent", "content_type", "tier", "action", "priority", "cluster")}
        row["sources"] = sorted(e["sources"])
        for k in ("volume", "difficulty", "cpc"):
            if e.get(k) is not None:
                row[k] = e[k]
        if e.get("gsc"):
            g = e["gsc"]
            row["gsc"] = {"clicks": g["clicks"], "impressions": g["impressions"],
                          "position": round(g["position"], 1) if g["position"] is not None else None,
                          "pages": sorted({p["page"] for p in g["pages"]})[:5]}
        out_keywords.append(row)
    if not args.volumes:
        notes.append("No search volumes were given, so priority uses Autocomplete prominence instead. Pass --volumes with a keyword-tool export for volume-based priority.")
    json.dump({
        "status": "ok", "seeds": seeds, "country": gl, "language": hl,
        "summary": {"keywords": len(kws), "returned": len(out_keywords), "autocomplete_requests": requests_made,
                    "by_intent": dict(collections.Counter(e["intent"] for e in kws.values())),
                    "by_action": dict(collections.Counter(e["action"] for e in kws.values())),
                    "with_volume": sum(1 for e in kws.values() if e.get("volume") is not None)},
        "keywords": out_keywords,
        "clusters": [{"primary": c["primary"], "intent": c["intent"], "size": len(c["keywords"]), "keywords": c["keywords"][:15]}
                     for c in clusters if len(c["keywords"]) >= 2][:40],
        "quick_wins": quick_wins[:25], "ctr_gaps": ctr_gaps[:25], "cannibalization": cannibal[:25],
        "notes": notes,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
