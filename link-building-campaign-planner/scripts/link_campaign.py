#!/usr/bin/env python3
"""Link-Building Campaign Planner: reference implementation.

Plans a link campaign from two things: the site's own linkable assets (found
by reading its sitemap pages and labelling tools, original data, in-depth
guides, templates, glossaries and visuals) and the pages that link to its
competitors (from backlink exports of any tool). Prospects are de-duplicated
by domain, typed (resource page, roundup, guest-post site, news, directory,
community, education or government, blog), matched to the asset and tactic
that fit, and scored; domains linking to several competitors but not to the
site come first. Writes a tracking sheet.

Auth:   none. Assets come from fetching the site; prospects from CSV exports
        the user provides (Ahrefs, Semrush, Moz, Majestic or Search Console
        links reports).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 link_campaign.py --site https://example.com --competitor-links a.csv --competitor-links b.csv \
       [--own-links ours.csv] [--topic "crm"] [--out prospects.csv]
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, csv, datetime, json, math, re, sys
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UA = "seoskills-link-campaign-planner/1.0 (+https://seoskills.sh)"
ASSET_RULES = [  # (type, pattern on title or URL, tactic)
    ("tool", r"calculator|generator|checker|\btool\b|tester|estimator|converter|analy[sz]er|grader", "Pitch to resource pages and tool roundups; list in relevant directories."),
    ("original data", r"\b(survey|study|statistics|stats|benchmark|we analy[sz]ed|state of|census|research report|data report|trends report|index)\b", "Digital PR: pitch the headline finding to journalists and newsletters; answer source requests with it."),
    ("template or checklist", r"template|checklist|worksheet|swipe file|download|cheat ?sheet|planner", "Pitch to resource pages and communities that collect templates."),
    ("glossary or definition", r"^what (is|are)\b|glossary|definition|meaning", "Pitch as the reference definition to pages that mention the term without explaining it."),
    ("visual", r"infographic|chart|map|illustrat", "Offer the visual for embedding with credit; pitch to writers covering the topic."),
    ("in-depth guide", r"guide|how to|ultimate|complete|tutorial|playbook|handbook", "Broken-link replacement and resource-page outreach; offer it to roundups."),
]
PROSPECT_RULES = [  # checked in order against the referring page URL and title; the first match wins
    ("community", r"reddit\.com|quora\.com|forum|community|/thread|discourse|stackexchange|stackoverflow", "Join the discussion with real answers; link only where it helps.", 4),
    ("education or government", r"\.edu(\.[a-z]{2})?(/|$)|\.gov(\.[a-z]{2})?(/|$)|\.ac\.[a-z]{2}(/|$)", "Offer genuinely useful resources to the department or library page; no sales pitch.", 8),
    ("guest-post site", r"write[- ]for[- ]us|guest[- ]post|contribut|submit[- ](an[- ])?article|become[- ]an?[- ]author", "Pitch two or three original topics their readers lack.", 6),
    ("roundup", r"\bbest\b|\btop[- ]?\d+|\d+[- ](best|top|tools|ways|tips|examples|alternatives)|alternatives|\bvs\b|comparison|review", "Ask to be included, with a one-line reason and proof.", 7),
    ("resource page", r"resources|useful[- ]links|helpful[- ]links|recommended[- ](links|resources|sites|tools)|/links/?$|reading[- ]list", "Suggest your best matching asset for the list.", 9),
    ("directory", r"directory|listings?\b|/companies/|vendors|suppliers|find-a-|marketplace", "Submit an accurate listing where the directory is relevant and curated.", 3),
]
NEWS_DOMAIN = re.compile(r"news|press|magazine|journal|times|post|daily|herald|tribune|gazette|insider|wired|forbes|techcrunch|verge")
SPAM = re.compile(r"casino|poker|betting|viagra|cialis|porn|xxx|escort|payday|loan[s]?-?online|replica|essay[- ]writing|seo[- ]?backlinks?[- ]?(cheap|buy)|\.(xyz|top|loan|click|work|gq|tk|ml|cf|ga)(/|$)", re.I)
STOP = set("a an the of to in for and or with your you on by at as from is are be how what why best top new".split())


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read(3_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, url, "", ""
    except Exception:
        return 0, url, "", ""


def domain(url):
    host = urllib.parse.urlsplit(url if "//" in url else "http://" + url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def site_key(host):
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in {"co", "com", "org", "net", "ac", "gov", "edu"} and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class PageFacts(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.text, self.inputs, self.tables, self.headings, self.images = "", [], 0, 0, 0, 0
        self._in_title, self._skip = False, 0

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip += 1
        self.inputs += tag in ("input", "select", "textarea")
        self.tables += tag == "table"
        self.headings += tag in ("h2", "h3")
        self.images += tag == "img"

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.text.append(data)


def sitemap_urls(origin, limit):
    st, _, _, robots = fetch(origin + "/robots.txt")
    queue = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots) if st == 200 else []
    queue, urls, seen = queue or [origin + "/sitemap.xml"], [], set()
    while queue and len(seen) < 10 and len(urls) < limit * 10:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        st, _, _, body = fetch(sm)
        if st != 200:
            continue
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            continue
        locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
        (queue if root.tag.endswith("sitemapindex") else urls).extend(locs)
    return urls


def classify_asset(url, page):
    text = " ".join(page.text)
    words = len(re.findall(r"[A-Za-z]{2,}", text))
    hay = (page.title + " " + urllib.parse.urlsplit(url).path.replace("-", " ").replace("/", " ")).lower()
    stats = len(re.findall(r"\d[\d,.]*\s?%|\b\d{1,3}(?:,\d{3})+\b", text))
    for kind, rx, tactic in ASSET_RULES:
        if re.search(rx, hay, re.I):
            if kind == "tool" and page.inputs == 0:
                continue  # a page that only talks about tools is not a tool
            if kind == "in-depth guide" and words < 1200:
                continue
            if kind == "original data" and (stats < 8 or words < 800):
                continue
            base = {"tool": 30, "original data": 30, "template or checklist": 22, "visual": 20, "glossary or definition": 15, "in-depth guide": 20}[kind]
            score = min(100, base + min(30, words // 100) + min(20, stats * 2) + min(10, page.tables * 5) + min(10, page.headings))
            return {"url": url, "title": re.sub(r"\s+", " ", page.title).strip()[:140], "type": kind, "words": words,
                    "statistics": stats, "tables": page.tables, "linkability": score, "tactic": tactic}
    return None


def read_links(path):
    """Rows from any backlink export: referring URL, its domain, title, anchor, authority, nofollow, first seen, target."""
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
            except csv.Error:
                dialect = csv.excel
            raw = list(csv.DictReader(f, dialect=dialect))
    except OSError as e:
        fail("FILE_UNREADABLE", "Could not read %s: %s" % (path, e))
    rows = []
    for r in raw:
        n = {re.sub(r"[^a-z0-9]+", "_", (k or "").lower()).strip("_"): (v or "").strip() for k, v in r.items()}

        def pick(*keys):
            for k in keys:
                for col, v in n.items():
                    if (col == k or col.startswith(k)) and v:
                        return v
            return ""
        src = pick("referring_page_url", "source_url", "url_from", "source_page", "referring_url", "from_url", "linking_page", "url")
        dom = pick("referring_domain", "source_domain", "domain", "linking_site", "linking_domain")
        if not src and not dom:
            continue
        auth = pick("domain_rating", "dr", "ascore", "page_ascore", "authority_score", "domain_authority", "da", "trust_flow", "citation_flow", "pa")
        try:
            auth_v = float(auth.replace(",", "")) if auth else None
        except ValueError:
            auth_v = None
        nofollow = pick("nofollow", "link_type", "rel", "type").lower()
        rows.append({"url": src, "domain": domain(src) if src else domain(dom), "title": pick("referring_page_title", "source_title", "title", "page_title"),
                     "anchor": pick("anchor", "anchor_text"), "authority": auth_v,
                     "nofollow": nofollow in ("true", "yes", "1", "nofollow") or "nofollow" in nofollow,
                     "first_seen": pick("first_seen", "first_found", "first_indexed", "found_date"), "target": pick("target_url", "url_to", "target")})
    return rows


def classify_prospect(url, title):
    hay = (url + " " + title).lower()
    for kind, rx, tactic, value in PROSPECT_RULES:
        if re.search(rx, hay):
            return kind, tactic, value
    if NEWS_DOMAIN.search(domain(url)) or re.search(r"/20\d\d/\d\d/", url):
        return "news or magazine", "Pitch data or an expert comment tied to what they already cover.", 8
    return "blog post", "Offer a relevant asset where the post already discusses the topic (add value, not just a link).", 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="Your site, e.g. https://example.com")
    ap.add_argument("--competitor-links", action="append", default=[], help="Backlink export for one competitor (CSV/TSV; repeat per competitor)")
    ap.add_argument("--own-links", help="Your own backlink export, to skip domains that already link to you")
    ap.add_argument("--topic", action="append", default=[], help="Topic words for relevance scoring (repeatable)")
    ap.add_argument("--max-pages", type=int, default=150, help="Site pages to read for linkable assets")
    ap.add_argument("--out", help="Write the prospect tracking sheet to this CSV file")
    args = ap.parse_args()
    if not re.match(r"^https?://[^/\s]+", args.site):
        fail("INPUT_INVALID", "--site must be an absolute URL such as https://example.com")
    sp = urllib.parse.urlsplit(args.site)
    origin = "%s://%s" % (sp.scheme, sp.netloc)
    own_key = site_key(domain(args.site))
    today = datetime.date.today()
    notes = []

    # 1. Linkable assets: read sitemap pages, likeliest asset URLs first.
    urls = sitemap_urls(origin, args.max_pages)
    if not urls:
        notes.append("No sitemap found, so only the homepage was read for assets.")
        urls = [origin + "/"]
    hint = re.compile(r"tool|calculator|generator|checker|research|study|report|survey|data|statistic|guide|template|checklist|glossary|what-is|infographic|resource|blog|learn", re.I)
    urls = sorted(dict.fromkeys(urls), key=lambda u: (0 if hint.search(u) else 1, len(u)))[: args.max_pages]

    def read(u):
        st, final, ctype, body = fetch(u)
        if st != 200 or "html" not in ctype.lower():
            return None
        p = PageFacts()
        try:
            p.feed(body)
        except Exception:
            pass
        return classify_asset(final, p)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        assets = [a for a in pool.map(read, urls) if a]
    assets.sort(key=lambda a: -a["linkability"])

    # 2. Prospects: pages linking to competitors, minus domains already linking to the site.
    own_domains = {r["domain"] for r in read_links(args.own_links)} if args.own_links else set()
    topic_terms = {w for t in args.topic for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP}
    by_domain = {}
    skipped = collections.Counter()
    for ci, path in enumerate(args.competitor_links):
        rows = read_links(path)
        if not rows:
            notes.append("%s had no recognizable referring URL or domain column." % path)
        for r in rows:
            d = r["domain"]
            if not d or site_key(d) == own_key:
                continue
            if SPAM.search(d) or SPAM.search(r["url"]):
                skipped["spam_pattern"] += 1
                continue
            if d in own_domains:
                skipped["already_links_to_you"] += 1
                continue
            e = by_domain.setdefault(d, {"domain": d, "pages": [], "competitors": set(), "authority": None, "dofollow": False, "first_seen": None})
            e["competitors"].add(ci)
            if len(e["pages"]) < 5:
                e["pages"].append({"url": r["url"], "title": r["title"][:140], "anchor": r["anchor"][:80]})
            if r["authority"] is not None:
                e["authority"] = max(e["authority"] or 0, r["authority"])
            e["dofollow"] = e["dofollow"] or not r["nofollow"]
            if r["first_seen"][:10] and (e["first_seen"] is None or r["first_seen"][:10] > e["first_seen"]):
                e["first_seen"] = r["first_seen"][:10]
    max_auth = max([e["authority"] for e in by_domain.values() if e["authority"] is not None] or [100])
    prospects = []
    for e in by_domain.values():
        best = e["pages"][0] if e["pages"] else {"url": "", "title": ""}
        kind, tactic, type_value = classify_prospect(best["url"], best["title"])
        hay = " ".join(p["url"] + " " + p["title"] for p in e["pages"]).lower()
        relevance = (len(topic_terms & set(re.findall(r"[a-z0-9]+", hay))) / len(topic_terms)) if topic_terms else None
        auth = (e["authority"] / max_auth) if e["authority"] is not None and max_auth else None
        recent = bool(e["first_seen"]) and e["first_seen"] >= (today - datetime.timedelta(days=365)).isoformat()
        score = (30 * (auth if auth is not None else 0.4) + 25 * min(1.0, len(e["competitors"]) / max(1, min(3, len(args.competitor_links))))
                 + 25 * (relevance if relevance is not None else 0.5) + 10 * (1 if e["dofollow"] else 0.3) + 10 * (1 if recent else 0.5)) * (0.7 + type_value / 30)
        asset = next((a for a in assets if (kind in ("resource page", "roundup", "directory") and a["type"] in ("tool", "template or checklist", "in-depth guide"))
                      or (kind == "news or magazine" and a["type"] in ("original data", "visual"))
                      or (kind == "education or government" and a["type"] in ("in-depth guide", "glossary or definition", "tool"))), assets[0] if assets else None)
        prospects.append({"domain": e["domain"], "type": kind, "tactic": tactic, "score": round(min(100, score), 1),
                          "links_to_competitors": len(e["competitors"]), "authority": e["authority"], "dofollow": e["dofollow"],
                          "first_seen": e["first_seen"], "relevance": round(relevance, 2) if relevance is not None else None,
                          "example_pages": e["pages"][:3], "suggested_asset": asset["url"] if asset else None})
    prospects.sort(key=lambda p: (-p["score"], p["domain"]))
    segments = collections.defaultdict(list)
    for p in prospects:
        segments[p["type"]].append(p)
    if args.out and prospects:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["domain", "type", "score", "links_to_competitors", "authority", "example_page", "suggested_asset", "tactic", "contact", "status", "next_step_date", "notes"])
            for p in prospects:
                w.writerow([p["domain"], p["type"], p["score"], p["links_to_competitors"], p["authority"] if p["authority"] is not None else "",
                            p["example_pages"][0]["url"] if p["example_pages"] else "", p["suggested_asset"] or "", p["tactic"], "", "to contact", "", ""])
    if not args.competitor_links:
        notes.append("No --competitor-links exports were given, so there are no prospects yet. Export each competitor's referring pages from your backlink tool and run again.")
    if not assets:
        notes.append("No linkable assets were found among the pages read. Plan one (original data, a free tool or a template) before outreach; links to plain service pages are hard to earn.")
    json.dump({
        "status": "ok", "site": origin, "checked": today.isoformat(),
        "assets": {"pages_read": len(urls), "found": len(assets), "by_type": dict(collections.Counter(a["type"] for a in assets)), "top": assets[:20]},
        "prospects": {"domains": len(prospects), "skipped": dict(skipped), "by_type": {k: len(v) for k, v in segments.items()},
                      "multi_competitor": sum(1 for p in prospects if p["links_to_competitors"] >= 2), "top": prospects[:40],
                      "by_segment": {k: v[:10] for k, v in segments.items()}},
        "tracking_sheet": args.out if args.out and prospects else None,
        "notes": notes,
    }, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
