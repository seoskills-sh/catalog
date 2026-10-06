#!/usr/bin/env python3
"""App Listing Optimizer: reference implementation.

Reads an app's live store listing (App Store through Apple's free iTunes
Lookup API and the public app page; Google Play through the public app page)
and checks every metadata field against the store's limits and rules: title
and subtitle or short description use, repeated words, target keywords,
policy-risk words, description, screenshots, video, ratings, update
recency and release notes. Compares the listing with competitors and lists
the title words they use that the app does not.

Auth:   none. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 app_listing.py --app 570060128 [--competitor 1234567890] [--keyword "learn spanish"] [--country us]
       python3 app_listing.py --app com.duolingo [--competitor com.babbel.mobile.android.en] [--language en]
"""
from __future__ import annotations
import argparse, datetime, html as htmllib, json, re, sys, time
import urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

LOOKUP = "https://itunes.apple.com/lookup"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
LIMITS = {"ios": {"title": 30, "subtitle": 30, "description": 4000}, "play": {"title": 30, "short_description": 80, "description": 4000}}
POLICY_TERMS = re.compile(r"(#\s?1\b|\bno\.?\s?1\b|\bbest\b|\btop\b|\bfree\b|\bsale\b|\bdiscount\b|\$\d|\d+% off|\bmillion downloads\b)", re.I)
GENERIC_NOTES = re.compile(r"^\s*(bug fixes( and| &)? (performance )?improvements?\.?|minor (bug )?fixes\.?|performance improvements\.?|we update the app regularly.*)\s*$", re.I)
STOP = set("a an the of to in for and or with your you my on by at as from app apps is are be & more etc plus all one get it".split())


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(3_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def store_of(app):
    a = app.strip()
    m = re.search(r"(?:^|/id|^id)(\d{6,12})(?:\D|$)", a)
    if re.fullmatch(r"\d{6,12}", a) or (m and "apps.apple.com" in a) or re.fullmatch(r"id\d{6,12}", a):
        return "ios", (m.group(1) if m else a)
    m = re.search(r"[?&]id=([\w.]+)", a)
    if m:
        return "play", m.group(1)
    if re.fullmatch(r"[A-Za-z][\w]*(\.[\w]+)+", a):
        return "play", a
    return None, a


def words(text):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9'+-]*", (text or "").lower())]


def meaningful(text):
    return [w for w in words(text) if w not in STOP]


class PlayDescription(HTMLParser):
    """Text inside the element marked data-g-id="description", plus screenshot and video counts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.parts, self.screens, self.video = 0, [], 0, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth:
            self.depth += tag not in ("br", "img", "meta", "link", "input")
            if tag == "br":
                self.parts.append("\n")
        elif a.get("data-g-id") == "description":
            self.depth = 1
        if tag == "img" and "screenshot" in (a.get("alt") or "").lower():
            self.screens += 1
        if (tag == "button" and "trailer" in (a.get("aria-label") or "").lower()) or (tag == "iframe" and "youtube" in (a.get("src") or "")):
            self.video = True

    def handle_endtag(self, tag):
        if self.depth and tag not in ("br", "img", "meta", "link", "input"):
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def ios_listing(app_id, country):
    st, body = get("%s?%s" % (LOOKUP, urllib.parse.urlencode({"id": app_id, "country": country})))
    if st == 403:
        return {"status": "rate_limited", "id": app_id}
    try:
        res = json.loads(body).get("results", []) if st == 200 else []
    except ValueError:
        res = []
    if not res:
        return {"status": "not_found", "id": app_id, "http_status": st}
    d = res[0]
    subtitle = None
    pst, page = get("https://apps.apple.com/%s/app/id%s" % (country, app_id))
    if pst == 200:
        m = re.search(r'class="[^"]*\bsubtitle\b[^"]*"[^>]*>([^<]{1,80})<', page)  # the first subtitle on the page is the app's own
        if m:
            subtitle = htmllib.unescape(m.group(1)).strip()
    released = d.get("currentVersionReleaseDate", "")[:10]
    return {
        "status": "ok", "store": "ios", "id": app_id, "url": d.get("trackViewUrl", "").split("?")[0],
        "title": d.get("trackName", ""), "subtitle": subtitle, "subtitle_found": pst == 200 and subtitle is not None,
        "description": d.get("description", ""), "release_notes": d.get("releaseNotes", ""),
        "developer": d.get("sellerName") or d.get("artistName"), "category": d.get("primaryGenreName"),
        "rating": round(d["averageUserRating"], 2) if d.get("averageUserRating") is not None else None,
        "ratings_count": d.get("userRatingCount"), "version": d.get("version"), "last_updated": released or None,
        "price": d.get("formattedPrice"), "screenshots": len(d.get("screenshotUrls", [])), "ipad_screenshots": len(d.get("ipadScreenshotUrls", [])),
        "supports_ipad": any("ipad" in x.lower() for x in d.get("supportedDevices", [])), "languages": len(d.get("languageCodesISO2A", [])),
    }


def play_listing(pkg, country, language):
    st, page = get("https://play.google.com/store/apps/details?%s" % urllib.parse.urlencode({"id": pkg, "hl": language, "gl": country.upper()}))
    if st != 200:
        return {"status": "not_found" if st == 404 else "unreachable", "id": pkg, "http_status": st}
    ld = {}
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "SoftwareApplication":
            ld = data
            break
    p = PlayDescription()
    try:
        p.feed(page)
    except Exception:
        pass
    full = re.sub(r"[ \t]+", " ", "".join(p.parts)).strip()
    updated = re.search(r"Updated on</div><div[^>]*>([^<]+)<", page)
    downloads = re.search(r">([\d.,]+[KMB]?\+)</div><div[^>]*>Downloads<", page)
    day = None
    if updated:
        try:
            day = datetime.datetime.strptime(updated.group(1).strip(), "%b %d, %Y").date().isoformat()
        except ValueError:
            day = None
    rating = (ld.get("aggregateRating") or {})
    title = ld.get("name") or re.sub(r"\s+-\s+Apps on Google Play$", "", (re.search(r'<meta property="og:title" content="([^"]*)"', page) or [None, ""])[1])
    return {
        "status": "ok", "store": "play", "id": pkg, "url": "https://play.google.com/store/apps/details?id=%s" % pkg,
        "title": htmllib.unescape(title or ""), "short_description": htmllib.unescape(ld.get("description", "")),
        "description": full, "developer": (ld.get("author") or {}).get("name"), "category": ld.get("applicationCategory"),
        "rating": round(float(rating["ratingValue"]), 2) if rating.get("ratingValue") else None,
        "ratings_count": int(rating["ratingCount"]) if str(rating.get("ratingCount", "")).isdigit() else None,
        "downloads": downloads.group(1) if downloads else None, "last_updated": day,
        "contains_ads": "Contains ads" in page, "in_app_purchases": "In-app purchases" in page,
        "screenshots": p.screens, "video": p.video, "content_rating": ld.get("contentRating"),
    }


def check(listing, keywords, today):
    s = listing["store"]
    lim = LIMITS[s]
    issues = []

    def flag(code, severity, detail, fix):
        issues.append({"code": code, "severity": severity, "detail": detail, "fix": fix})

    title = listing["title"]
    second_name = "subtitle" if s == "ios" else "short_description"
    second = listing.get(second_name) or ""
    if len(title) > lim["title"]:
        flag("TITLE_TOO_LONG", "high", "%d of %d characters." % (len(title), lim["title"]), "Cut the title to %d characters." % lim["title"])
    elif len(title) < 20:
        flag("TITLE_UNDERUSED", "medium", "The title uses %d of %d characters." % (len(title), lim["title"]),
             "Add the most important keyword after the brand, for example \"Brand: Main Keyword\".")
    if s == "ios":
        if listing.get("subtitle_found") and not second:
            flag("SUBTITLE_MISSING", "high", "No subtitle found.", "Add a 30-character subtitle with the next most important keywords.")
        elif second and len(second) < 20:
            flag("SUBTITLE_UNDERUSED", "medium", "The subtitle uses %d of 30 characters." % len(second), "Use the full 30 characters for keywords and the main benefit.")
    else:
        if not second:
            flag("SHORT_DESCRIPTION_MISSING", "high", "No short description found.", "Write an 80-character short description with the main keyword and benefit.")
        elif len(second) < 50:
            flag("SHORT_DESCRIPTION_UNDERUSED", "medium", "The short description uses %d of 80 characters." % len(second), "Use the 80 characters for the main benefit and keywords.")
    repeated = sorted(set(meaningful(title)) & set(meaningful(second)))
    if repeated:
        flag("REPEATED_WORDS", "low", "Title and %s both use: %s." % (second_name.replace("_", " "), ", ".join(repeated)),
             "Each word only needs to appear once across the title and %s; use the space for new keywords." % second_name.replace("_", " "))
    risky = sorted({m.group(0).lower() for m in POLICY_TERMS.finditer(title + " " + (second if s == "ios" else ""))})
    if risky:
        rule = ("App Store Review Guideline 2.3.7 bars pricing and irrelevant phrases in metadata" if s == "ios"
                else "Google Play's metadata policy bars terms like free, best, top, #1 and new in app titles")
        flag("POLICY_RISK_TERMS", "high", "Uses: %s." % ", ".join(risky), "Remove them; %s." % rule)
    desc = listing.get("description") or ""
    if len(desc) < 1000:
        flag("DESCRIPTION_SHORT", "low" if s == "ios" else "medium", "%d of %d characters." % (len(desc), lim["description"]),
             "Expand the description: benefits first, then features as short lines" + ("; on Google Play the full description is also searched." if s == "play" else "."))
    kw_report = []
    for k in keywords:
        kw = meaningful(k) or words(k)
        where = {"title": all(w in words(title) for w in kw), second_name: all(w in words(second) for w in kw),
                 "description": all(w in words(desc) for w in kw)}
        count = len(re.findall(r"\b%s\b" % r"\W+".join(map(re.escape, words(k))), desc.lower())) if words(k) else 0
        kw_report.append({"keyword": k, **where, "description_mentions": count})
        if not where["title"] and not where[second_name]:
            flag("KEYWORD_NOT_IN_TOP_FIELDS", "medium", "\"%s\" is in neither the title nor the %s." % (k, second_name.replace("_", " ")),
                 "Place the most important keywords in the title first, then the %s%s." % (second_name.replace("_", " "), " and the hidden keyword field" if s == "ios" else ""))
        if s == "play" and desc and count >= 6 and count * len(words(k)) / max(1, len(words(desc))) > 0.03:
            flag("KEYWORD_REPETITION", "medium", "\"%s\" appears %d times in the description." % (k, count),
                 "Google Play's policy prohibits repetitive keywords; mention it naturally a few times.")
    if listing.get("last_updated"):
        age = (today - datetime.date.fromisoformat(listing["last_updated"])).days
        listing["days_since_update"] = age
        if age > 90:
            flag("STALE_UPDATE", "medium", "Last updated %d days ago." % age, "Ship updates regularly; recency is a quality signal to users and stores.")
    if s == "ios" and listing.get("release_notes") and GENERIC_NOTES.match(listing["release_notes"]):
        flag("GENERIC_RELEASE_NOTES", "low", "The release notes only say \"bug fixes\".", "Name what changed; release notes are a chance to show the app improves.")
    if listing.get("rating") is not None and listing["rating"] < 4.0:
        flag("LOW_RATING", "high", "Average rating %.2f." % listing["rating"], "Fix the complaints in recent low reviews first, then prompt happy users with the in-app rating API.")
    if listing.get("ratings_count") is not None and listing["ratings_count"] < 100:
        flag("FEW_RATINGS", "medium", "%d ratings." % listing["ratings_count"],
             "Ask for ratings in the app (SKStoreReviewController on iOS, the In-App Review API on Android) after a success moment.")
    if listing.get("screenshots") is not None and listing["screenshots"] < (5 if s == "ios" else 4):
        flag("FEW_SCREENSHOTS", "medium", "%d screenshots." % listing["screenshots"], "Use more screenshots, with the first three showing the core value in captions.")
    if s == "ios" and listing.get("supports_ipad") and not listing.get("ipad_screenshots"):
        flag("NO_IPAD_SCREENSHOTS", "low", "The app runs on iPad but has no iPad screenshots.", "Add iPad screenshots.")
    if s == "play" and not listing.get("video"):
        flag("NO_PROMO_VIDEO", "low", "No promo video found.", "Add a short promo video showing the app in use.")
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda i: rank[i["severity"]])
    return issues, kw_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="App Store ID (570060128 or an apps.apple.com URL) or Google Play package (com.example.app or a play.google.com URL)")
    ap.add_argument("--competitor", action="append", default=[], help="Competitor app on the same store (repeat or comma-separate, up to 5)")
    ap.add_argument("--keyword", action="append", default=[], help="Target keyword to check (repeatable)")
    ap.add_argument("--country", default="us")
    ap.add_argument("--language", default="en", help="Google Play listing language")
    args = ap.parse_args()
    store, app_id = store_of(args.app)
    if not store:
        fail("INPUT_INVALID", "--app must be an App Store ID or URL, or a Google Play package or URL.")
    comps = []
    for c in [x.strip() for a in args.competitor for x in a.split(",") if x.strip()][:5]:
        cs, cid = store_of(c)
        if cs != store:
            fail("INPUT_INVALID", "Competitor %s is not on the same store as --app (%s)." % (c, store))
        comps.append(cid)
    fetcher = (lambda i: ios_listing(i, args.country.lower())) if store == "ios" else (lambda i: play_listing(i, args.country.lower(), args.language))
    today = datetime.date.today()
    main_listing = fetcher(app_id)
    if main_listing["status"] != "ok":
        fail("APP_NOT_FOUND" if main_listing["status"] == "not_found" else "STORE_UNAVAILABLE",
             "Could not read %s on the %s (%s)." % (app_id, "App Store" if store == "ios" else "Google Play", main_listing["status"]), app=main_listing)
    keywords = [k.strip() for k in args.keyword if k.strip()]
    issues, kw_report = check(main_listing, keywords, today)
    competitors = []
    for cid in comps:
        time.sleep(3.1 if store == "ios" else 1.0)  # Apple allows about 20 lookups a minute
        c = fetcher(cid)
        if c["status"] == "ok":
            c_issues, _ = check(c, [], today)
            c["issue_codes"] = [i["code"] for i in c_issues]
        competitors.append(c)
    second = "subtitle" if store == "ios" else "short_description"
    own_words = set(meaningful(main_listing["title"] + " " + (main_listing.get(second) or "")))
    ideas = {}
    for c in competitors:
        if c["status"] != "ok":
            continue
        for w in set(meaningful(c["title"] + " " + (c.get(second) or ""))) - own_words:
            if not re.fullmatch(r"\d+", w) and w not in meaningful(c.get("developer") or ""):
                ideas.setdefault(w, []).append(c["title"])
    slim = lambda l: {k: v for k, v in l.items() if k not in ("description", "release_notes")} | (
        {"description_chars": len(l.get("description") or ""), "description_opening": (l.get("description") or "")[:250]} if l.get("status") == "ok" else {})
    json.dump({
        "status": "ok", "store": store, "country": args.country.lower(), "checked": today.isoformat(),
        "limits": LIMITS[store], "app": slim(main_listing), "issues": issues, "keywords": kw_report,
        "competitors": [slim(c) for c in competitors],
        "keyword_ideas_from_competitors": [{"word": w, "used_by": sorted(set(t))[:3]} for w, t in sorted(ideas.items(), key=lambda kv: (-len(kv[1]), kv[0]))][:25],
    }, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
