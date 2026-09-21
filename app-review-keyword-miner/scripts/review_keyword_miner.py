#!/usr/bin/env python3
"""App Review Keyword Miner — reference implementation.

Pulls App Store (RSS, keyless) and Google Play (Developer API) reviews, mines
the vocabulary real users use, detects feature requests and complaints, and
clusters themes by lexicon sentiment and rating impact. Std-lib NLP only.

Auth:   App Store RSS needs no key. Google Play needs GOOGLE_PLAY_ACCESS_TOKEN
        (OAuth bearer minted from GOOGLE_PLAY_SERVICE_ACCOUNT).
Output: JSON on stdout per ../references/output.schema.json.

Usage: python3 review_keyword_miner.py --app-id 6001112223 --package com.acme.budget \
       --country us --max-reviews 500
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict

RSS = "https://itunes.apple.com/{country}/rss/customerreviews/id={app_id}/sortBy=mostRecent/page={page}/json"
PLAY = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{pkg}/reviews"

STOP_WORDS = {
    "the", "a", "an", "and", "or", "for", "with", "to", "of", "in", "on", "at",
    "is", "it", "this", "that", "i", "you", "my", "me", "we", "so", "but", "not",
    "have", "has", "had", "was", "are", "be", "been", "app", "would", "could",
    "very", "really", "just", "get", "got", "can", "do", "does", "did", "if",
    "as", "by", "they", "them", "its", "im", "ive", "dont", "there", "all", "out",
    "up", "one", "when", "what", "then", "than", "your", "their", "too", "also",
}
POS = re.compile(r"\b(love|great|excellent|amazing|perfect|awesome|easy|clean|"
                 r"helpful|useful|intuitive|reliable|fast|beautiful|recommend|"
                 r"favorite|smooth|simple|best|fantastic)\b", re.I)
NEG = re.compile(r"\b(hate|terrible|awful|worst|useless|buggy|broken|crash|slow|"
                 r"lag|freeze|annoying|confusing|disappoint|glitch|frustrat|"
                 r"expensive|scam|refund|unusable|garbage|horrible)\b", re.I)
REQUEST_CUES = re.compile(r"\b(wish|hope|please add|needs to|need a|would love|"
                          r"would be nice|should add|add a|add an|ability to|"
                          r"missing|feature request|allow me to|let me)\b", re.I)
THEMES = {
    "crashes_stability": r"crash|freeze|frozen|stuck|hang|force close|won'?t open",
    "bugs_glitches": r"\bbug|glitch|broken|error|not work|doesn'?t work|won'?t work",
    "performance": r"slow|lag|laggy|sluggish|loading|battery|drain|heavy",
    "ui_ux": r"design|interface|\bui\b|\bux\b|layout|cluttered|confusing|hard to use",
    "pricing_subscription": r"price|expensive|subscription|subscribe|paywall|refund|"
                            r"cost|charge|free trial|premium",
    "ads": r"\bad\b|\bads\b|advert|pop.?up|banner",
    "login_account": r"login|log in|sign in|sign up|password|account|verify|otp",
    "sync_data": r"\bsync|backup|restore|cloud|lost data|data loss|export|import",
    "notifications": r"notification|reminder|alert|badge|push",
    "support": r"support|customer service|response|reply|contact|help desk",
}
TOKEN_RE = re.compile(r"[a-z0-9']+")


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def get_json(url, headers, timeout=45):
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.getcode(), json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                fail("AUTH_EXPIRED", "Google Play OAuth token expired or invalid.")
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


def _label(node):
    if isinstance(node, dict):
        return node.get("label", "")
    return node if isinstance(node, str) else ""


def fetch_appstore(app_id, country, max_pages, remaining):
    reviews = []
    for page in range(1, max_pages + 1):
        if remaining <= 0:
            break
        url = RSS.format(country=country, app_id=app_id, page=page)
        code, data = get_json(url, {"User-Agent": "aso-review-miner/1.0"})
        if code == 429:
            return reviews, "rate_limited"
        if code != 200:
            return reviews, ("empty" if not reviews else "partial")
        entries = data.get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        page_reviews = 0
        for e in entries:
            if "im:rating" not in e:  # first entry is the app header, skip
                continue
            try:
                star = int(_label(e.get("im:rating")))
            except (TypeError, ValueError):
                continue
            text = "%s %s" % (_label(e.get("title")), _label(e.get("content")))
            reviews.append({"store": "appstore", "rating": star, "text": text.strip()})
            page_reviews += 1
            remaining -= 1
            if remaining <= 0:
                break
        if page_reviews == 0:
            break
        time.sleep(0.3)
    return reviews, "ok"


def fetch_play(pkg, token, max_pages, remaining):
    reviews, page_token = [], None
    headers = {"Authorization": "Bearer %s" % token}
    for _ in range(max_pages):
        if remaining <= 0:
            break
        q = {"maxResults": 100}
        if page_token:
            q["token"] = page_token
        url = PLAY.format(pkg=urllib.parse.quote(pkg)) + "?" + urllib.parse.urlencode(q)
        code, data = get_json(url, headers)
        if code == 429:
            return reviews, "rate_limited"
        if code == 403:
            fail("PLAY_ACCESS_DENIED", "Service account lacks review access for %s." % pkg)
        if code != 200:
            return reviews, ("empty" if not reviews else "partial")
        for rv in data.get("reviews", []):
            for c in rv.get("comments", []):
                uc = c.get("userComment")
                if not uc:
                    continue
                reviews.append({"store": "play", "rating": int(uc.get("starRating", 0)),
                                "text": (uc.get("text") or "").strip()})
                remaining -= 1
                if remaining <= 0:
                    break
        page_token = data.get("tokenPagination", {}).get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.3)
    return reviews, "ok"


def sentiment(text, star):
    p, n = len(POS.findall(text)), len(NEG.findall(text))
    if star >= 4 and n <= p:
        return "positive"
    if star <= 2 and p <= n:
        return "negative"
    if p > n:
        return "positive"
    if n > p:
        return "negative"
    return "neutral"


def ngrams(text):
    toks = [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 2 and t not in STOP_WORDS]
    grams = list(toks)
    for i in range(len(toks) - 1):
        grams.append(toks[i] + " " + toks[i + 1])
    return grams


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", dest="app_id", help="App Store numeric id.")
    ap.add_argument("--package", help="Google Play package name.")
    ap.add_argument("--country", default="us")
    ap.add_argument("--stores", default="appstore,play")
    ap.add_argument("--max-reviews", type=int, default=500, dest="max_reviews")
    ap.add_argument("--max-pages", type=int, default=10, dest="max_pages")
    ap.add_argument("--min-mentions", type=int, default=3, dest="min_mentions")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    stores = [s.strip() for s in args.stores.split(",") if s.strip()]
    play_token = os.environ.get("GOOGLE_PLAY_ACCESS_TOKEN")
    if "play" in stores and args.package and not play_token:
        fail("AUTH_MISSING_PLAY_TOKEN",
             "Set GOOGLE_PLAY_ACCESS_TOKEN (OAuth bearer from GOOGLE_PLAY_SERVICE_ACCOUNT) for Play reviews.")
    if not (("appstore" in stores and args.app_id) or ("play" in stores and args.package)):
        fail("INPUT_MISSING", "Provide --app-id for App Store and/or --package for Google Play.")

    reviews, fetch_notes = [], {}
    remaining = args.max_reviews
    if "appstore" in stores and args.app_id:
        got, note = fetch_appstore(args.app_id, args.country, args.max_pages, remaining)
        reviews += got
        remaining -= len(got)
        fetch_notes["appstore"] = note
    if "play" in stores and args.package and play_token:
        got, note = fetch_play(args.package, play_token, args.max_pages, remaining)
        reviews += got
        fetch_notes["play"] = note

    reviews = [r for r in reviews if r["text"]]
    if not reviews:
        json.dump({"status": "insufficient", "reason": "No reviews returned for the given app/country.",
                   "fetch_notes": fetch_notes, "reviews_analyzed": 0}, sys.stdout, indent=2)
        return

    overall_avg = round(sum(r["rating"] for r in reviews if r["rating"]) /
                        max(1, sum(1 for r in reviews if r["rating"])), 2)
    sent_counts = {"positive": 0, "negative": 0, "neutral": 0}
    term_docs, term_stars, term_sent = defaultdict(int), defaultdict(list), defaultdict(lambda: defaultdict(int))
    theme_hits = defaultdict(lambda: {"mentions": 0, "stars": []})
    requests_agg = defaultdict(lambda: {"count": 0, "stars": []})

    for r in reviews:
        text, star = r["text"], r["rating"]
        s = sentiment(text, star)
        sent_counts[s] += 1
        seen = set()
        for g in ngrams(text):
            if g in seen:
                continue
            seen.add(g)
            term_docs[g] += 1
            if star:
                term_stars[g].append(star)
            term_sent[g][s] += 1
        for theme, rx in THEMES.items():
            if re.search(rx, text, re.I):
                theme_hits[theme]["mentions"] += 1
                if star:
                    theme_hits[theme]["stars"].append(star)
        if REQUEST_CUES.search(text):
            m = REQUEST_CUES.search(text)
            tail = text[m.end():].lower()
            obj = TOKEN_RE.findall(tail)
            obj = [t for t in obj if t not in STOP_WORDS][:2]
            if obj:
                key = " ".join(obj)
                requests_agg[key]["count"] += 1
                if star:
                    requests_agg[key]["stars"].append(star)

    candidates = []
    for term, docs in term_docs.items():
        if docs < args.min_mentions or REQUEST_CUES.search(term):
            continue
        stars = term_stars[term]
        dom = max(term_sent[term].items(), key=lambda kv: kv[1])[0] if term_sent[term] else "neutral"
        candidates.append({
            "term": term,
            "ngram": "bigram" if " " in term else "unigram",
            "frequency": docs,
            "avg_rating": round(sum(stars) / len(stars), 2) if stars else None,
            "dominant_sentiment": dom,
        })
    candidates.sort(key=lambda c: (-c["frequency"], c["term"]))

    themes = []
    for theme, agg in theme_hits.items():
        stars = agg["stars"]
        avg = round(sum(stars) / len(stars), 2) if stars else None
        impact = round(agg["mentions"] * (overall_avg - avg), 2) if avg is not None else None
        cls = "complaint" if (avg is not None and avg < 3) else ("praise" if (avg is not None and avg >= 4) else "mixed")
        themes.append({"theme": theme, "mentions": agg["mentions"], "avg_rating": avg,
                       "rating_impact": impact, "class": cls})
    themes = [t for t in themes if t["mentions"] > 0]
    themes.sort(key=lambda t: (-(t["rating_impact"] or 0), -t["mentions"]))

    feature_requests = []
    for phrase, agg in requests_agg.items():
        if agg["count"] < max(2, args.min_mentions - 1):
            continue
        stars = agg["stars"]
        feature_requests.append({"request": phrase, "count": agg["count"],
                                 "avg_rating": round(sum(stars) / len(stars), 2) if stars else None})
    feature_requests.sort(key=lambda f: -f["count"])

    json.dump({
        "status": "ok",
        "app_id": args.app_id,
        "package": args.package,
        "country": args.country,
        "reviews_analyzed": len(reviews),
        "reviews_by_store": {s: sum(1 for r in reviews if r["store"] == s) for s in {"appstore", "play"}},
        "fetch_notes": fetch_notes,
        "sentiment_summary": {"overall_avg_rating": overall_avg, **sent_counts},
        "keyword_candidates": candidates[:args.top],
        "themes": themes,
        "feature_requests": feature_requests[:args.top],
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
