#!/usr/bin/env python3
"""GBP Review Velocity & Sentiment Monitor — reference implementation.

Auth:   GBP_OAUTH_TOKEN (scope business.manage). OPENAI_API_KEY optional.
        Business Profile API needs Google-approved access.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 gbp_reviews.py --account accounts/1 --locations loc1,loc2 --window 90
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, datetime, urllib.request, urllib.error, urllib.parse

V4 = "https://mybusiness.googleapis.com/v4"
STAR = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
POS = re.compile(r"\b(great|excellent|love|friendly|clean|fast|helpful|recommend|amazing|perfect)\b", re.I)
NEG = re.compile(r"\b(rude|slow|dirty|wait|expensive|terrible|worst|disappoint|never|awful|poor)\b", re.I)
THEMES = {"service": r"service|staff|employee|manager", "wait": r"wait|slow|long time|queue",
          "price": r"price|expensive|cost|overcharg", "cleanliness": r"clean|dirty|hygiene",
          "quality": r"quality|broken|defect|cold food"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def token():
    t = os.environ.get("GBP_OAUTH_TOKEN")
    if t:
        return t
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            import google.auth
            from google.auth.transport.requests import Request
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/business.manage"])
            creds.refresh(Request()); return creds.token
        except Exception as e:
            fail("AUTH_MISSING_TOKEN", "Token error: %s" % e)
    fail("AUTH_MISSING_TOKEN", "Set GBP_OAUTH_TOKEN (scope business.manage).")


def get(url, tok):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"}), timeout=45) as r:
                return 200, json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = (e.read() or b"").decode("utf-8", "ignore")
            if e.code == 401:
                fail("AUTH_EXPIRED", "OAuth token expired.")
            if e.code == 403 and ("SERVICE_DISABLED" in body or "PERMISSION_DENIED" in body):
                fail("API_ACCESS_NOT_APPROVED", "Request Business Profile API access.")
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


def fetch_reviews(account, loc, tok, cutoff):
    reviews, page = [], None
    while True:
        url = f"{V4}/{account}/locations/{loc}/reviews?pageSize=50" + (f"&pageToken={page}" if page else "")
        status, data = get(url, tok)
        if status == 429:
            fail("RATE_LIMITED", "GBP quota exhausted.")
        if status != 200:
            return None
        for rv in data.get("reviews", []):
            ct = rv.get("createTime", "")
            if ct and ct[:10] < cutoff:
                return reviews  # older than window; stop paging
            reviews.append(rv)
        page = data.get("nextPageToken")
        if not page:
            return reviews


def sentiment(text, star):
    base = "positive" if star >= 4 else "negative" if star <= 2 else "neutral"
    if not text:
        return base, False
    p, n = len(POS.findall(text)), len(NEG.findall(text))
    if p > n and star >= 3:
        return "positive", True
    if n > p and star <= 3:
        return "negative", True
    return base, True


def analyze(reviews, window_days):
    n = len(reviews)
    now = datetime.date.today()
    mid = (now - datetime.timedelta(days=window_days // 2)).isoformat()
    older = [r for r in reviews if r.get("createTime", "")[:10] < mid]
    recent = [r for r in reviews if r.get("createTime", "")[:10] >= mid]

    def mean_star(rs):
        vals = [STAR.get(r.get("starRating", ""), 0) for r in rs if r.get("starRating") in STAR]
        return round(sum(vals) / len(vals), 2) if vals else None
    answered = sum(1 for r in reviews if r.get("reviewReply"))
    unanswered_recent = sum(1 for r in recent if not r.get("reviewReply"))
    themes = {}
    for r in reviews:
        st = STAR.get(r.get("starRating", ""), 0)
        c = r.get("comment", "")
        sent, _ = sentiment(c, st)
        if sent == "negative" and c:
            for theme, rx in THEMES.items():
                if re.search(rx, c, re.I):
                    themes[theme] = themes.get(theme, 0) + 1
    older_star, recent_star = mean_star(older), mean_star(recent)
    star_delta = round((recent_star or 0) - (older_star or 0), 2) if older_star and recent_star else None
    flags = []
    if len(older) and len(recent) < 0.5 * len(older):
        flags.append("velocity_drop")
    if star_delta is not None and star_delta <= -0.4:
        flags.append("sentiment_decline")
    if n and answered / n < 0.5:
        flags.append("low_response_rate")
    return {"total_reviews": n, "reviews_per_week": round(n / (window_days / 7), 2),
            "mean_star": mean_star(reviews), "star_delta_recent_vs_older": star_delta,
            "response_rate": round(answered / n, 2) if n else None,
            "unanswered_recent": unanswered_recent,
            "negative_themes": dict(sorted(themes.items(), key=lambda kv: kv[1], reverse=True)),
            "flags": flags, "low_confidence": n < 5}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True); ap.add_argument("--locations", required=True)
    ap.add_argument("--window", type=int, default=90)
    a = ap.parse_args()
    tok = token()
    cutoff = (datetime.date.today() - datetime.timedelta(days=a.window)).isoformat()
    results = []
    for loc in [x.strip() for x in a.locations.split(",") if x.strip()]:
        reviews = fetch_reviews(a.account, loc, tok, cutoff)
        if reviews is None:
            results.append({"location": loc, "status": "fetch_error"}); continue
        results.append({"location": loc, "status": "ok", **analyze(reviews, a.window)})
    order = {True: 0, False: 1}
    results.sort(key=lambda r: (r.get("status") != "ok", -len(r.get("flags", []))))
    json.dump({"status": "ok", "account": a.account, "window_days": a.window,
               "sentiment_backend": "lexicon", "locations": results}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
