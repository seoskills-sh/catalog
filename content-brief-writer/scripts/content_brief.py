#!/usr/bin/env python3
"""Content Brief Writer: reference implementation.

Measures what the pages ranking for a keyword cover, so a brief can match the
search intent and beat them: the dominant format, a word-count range, the
subtopics most of them share (in the order they usually appear), the angles
only one covers, the questions searchers ask, the terms most pages use, their
schema, media and freshness, plus internal links from the user's own site.
The agent turns this into an editor-ready brief.

Auth:   none with --urls (the top results, from any web search). Set
        SERP_API_KEY and pass --serp to fetch Google's top results, People
        Also Ask and related searches from SerpApi instead.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 content_brief.py --keyword "crm for real estate" --urls https://a.com/x,https://b.com/y
       python3 content_brief.py --keyword "crm for real estate" --serp [--site https://example.com]
"""
from __future__ import annotations
import argparse, collections, datetime, json, os, re, statistics, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; seoskills-content-brief/1.0; +https://seoskills.sh)"
SERP = "https://serpapi.com/search.json"
SUGGEST = "https://suggestqueries.google.com/complete/search"
STOP = set("""a an the of to in for and or is are be was were it its on with by at as from that this these those your you
our we i my me how what why when where which who can do does did will would should could than then there their they them
not no yes if but so about into over under more most less very just also any all each other such only own same too up out
get got make made use used using vs per via etc""".split())
YMYL = re.compile(r"\b(health|medical|medicine|symptoms?|disease|diet|drug|dosage|pregnan|mental|therapy|doctor|clinic|"
                  r"finance|financial|loan|mortgage|credit|debt|tax|taxes|invest|investing|stock|crypto|insurance|retire|"
                  r"legal|law|lawyer|attorney|visa|immigration|divorce|lawsuit|safety|emergency|poison|weapon)\b", re.I)
FORMATS = [("comparison", r"\b(vs\.?|versus|compared?|comparison)\b"), ("listicle", r"^\d+\s|\b(top|best)\s+\d+\b|\b\d+\s+(best|ways|tips|ideas|examples|tools|reasons)\b"),
           ("how-to", r"\bhow to\b|\bstep[- ]by[- ]step\b|\btutorial\b"), ("review", r"\breview\b"),
           ("definition", r"^what (is|are)\b|\bmeaning\b|\bexplained\b"), ("product or category page", r"\b(pricing|buy|shop|plans)\b")]
QUESTION_PREFIXES = ["what is {k}", "how to {k}", "how does {k}", "how much {k}", "how long {k}", "why {k}", "is {k}", "can {k}", "does {k}", "which {k}"]
GENERIC = set("""some part like need needs needed look allow allows help helps find ensure ensures important consider relevant action
information detail details make makes made take way ways thing things good great better new many much well even first one two three
time times year years day days people know want see go going come work works keep give set show start right sure able every lot lots
often really without within across around still may might must including include includes based example examples however therefore
because while after before between during through each both few several other others key top provide provides use uses used using
check checks process overall simple easy quick essential crucial various different specific potential possible common available related
additional single multiple full complete comprehensive effective successful significant main major learn read click share post article
guide tips tip step steps ways list best free get getting got also following below above here there page pages number numbers
data result results change changes improve improving improved conduct conducting conducted negative positive opportunity
opportunities""".split())
NOISE_HEAD = re.compile(r"\b(share (this|the) (post|article)|related (articles?|posts?|reading)|you may also like|subscribe|newsletter|"
                        r"leave a (reply|comment)|comments?|about the author|table of contents|sign up|get started|contact us|"
                        r"free (seo )?(starter|trial|audit|consultation|tool)|book a (demo|call)|follow us|recent posts|categories|tags|"
                        r"conclusion|final thoughts|wrapping up|bottom line|key takeaways|frequently asked questions|faqs?)\b", re.I)


def clean_heading(text):
    return re.sub(r"^\s*(step\s*)?\d+\s*[.):\-]?\s*", "", text, flags=re.I).strip(" :.-")


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def fetch(url, timeout=20, accept="text/html,*/*;q=0.8"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read(4_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, url, "", ""
    except Exception:
        return 0, url, "", ""


class Page(HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "td", "th", "section", "article", "main", "header", "footer",
             "nav", "aside", "blockquote", "figcaption", "tr", "ul", "ol", "table", "dl", "dd", "dt", "details", "summary", "pre", "form", "body"}
    SKIP = {"script", "style", "noscript", "template", "svg", "select", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.jsonld, self.segments = "", {}, [], []
        self.images = self.tables = self.lists = self.videos = 0
        self.links, self.times = [], []
        self._stack, self._buf, self._jbuf = [], [], []
        self._skip = self._main = self._chrome = 0
        self._in_title = self._in_jsonld = False
        self.saw_main = False

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.segments.append((self._stack[-1] if self._stack else "body", text, self._main > 0, self._chrome > 0))

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title" and not self.title:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld, self._jbuf = True, []
            return
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        countable = not self._chrome and (self._main or not self.saw_main)
        if countable:
            self.images += tag == "img"
            self.tables += tag == "table"
            self.lists += tag in ("ul", "ol")
            self.videos += tag == "video" or (tag == "iframe" and bool(re.search(r"youtube|vimeo|wistia|loom", a.get("src", ""))))
        if tag == "a" and a.get("href"):
            self.links.append((a["href"], self._main > 0, self._chrome > 0))
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self._flush()
            if tag in ("main", "article"):
                self._main += 1
                self.saw_main = True
            if tag in ("nav", "footer", "aside") or (tag == "header" and not self._main):
                self._chrome += 1
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jbuf))
            self._in_jsonld = False
            return
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK and tag in self._stack:
            self._flush()
            while self._stack:
                top = self._stack.pop()
                if top in ("main", "article") and self._main:
                    self._main -= 1
                if (top in ("nav", "footer", "aside") or (top == "header" and not self._main)) and self._chrome:
                    self._chrome -= 1
                if top == tag:
                    break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_jsonld:
            self._jbuf.append(data)
        elif not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def words(text):
    return re.findall(r"[a-z0-9][a-z0-9'+-]*", text.lower())


def stem(w):
    for suf in ("ing", "ies", "es", "s", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def key_terms(text):
    return {stem(w) for w in words(text) if w not in STOP and not w.isdigit() and len(w) > 2}


def topic_terms(text):
    """Heading words that name a subtopic: no stop words, no generic verbs such as check or review."""
    return {stem(w) for w in words(text) if w not in STOP and w not in GENERIC and not w.isdigit() and len(w) > 2}


def schema_types(blocks):
    out = set()
    for b in blocks:
        try:
            data = json.loads(b)
        except ValueError:
            continue
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, list):
                stack.extend(n)
            elif isinstance(n, dict):
                t = n.get("@type")
                out.update([t] if isinstance(t, str) else [x for x in t if isinstance(x, str)] if isinstance(t, list) else [])
                stack.extend(v for v in n.values() if isinstance(v, (dict, list)))
    return out


def newest_date(p):
    found = []
    for b in p.jsonld:
        found += re.findall(r'"date(?:Modified|Published)"\s*:\s*"(\d{4}-\d{2}-\d{2})', b)
    found += [p.meta.get(k, "")[:10] for k in ("article:modified_time", "article:published_time", "og:updated_time")]
    found += [t[:10] for t in p.times]
    today = datetime.date.today().isoformat()
    valid = []
    for d in found:
        try:
            if datetime.date.fromisoformat(d).isoformat() <= today:
                valid.append(d)
        except ValueError:
            continue
    return max(valid) if valid else None


def detect_format(title, h1, heads):
    text = (h1 or title).lower()
    for name, rx in FORMATS:
        if re.search(rx, text):
            return name
    numbered = sum(1 for h in heads if re.match(r"^\d+[.)]?\s", h))
    return "listicle" if numbered >= 3 else "guide"


def analyze(url):
    status, final, ctype, html = fetch(url)
    if status != 200 or "html" not in ctype.lower():
        return {"url": url, "status": "unreachable", "http_status": status}
    p = Page()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    content = [s for s in p.segments if not s[3] and (s[2] or not p.saw_main)]
    h1 = next((t for tag, t, _, _ in p.segments if tag == "h1"), "")
    outline = [(tag, clean_heading(t)) for tag, t, _, _ in content
               if tag in ("h2", "h3") and 2 <= len(t) <= 160 and not NOISE_HEAD.search(t) and clean_heading(t)]
    body = " ".join(t for tag, t, _, _ in content if tag not in ("h1", "h2", "h3", "h4"))
    host = urllib.parse.urlsplit(final).netloc.lower()
    out_links = sum(1 for h, m, c in p.links if not c and urllib.parse.urlsplit(urllib.parse.urljoin(final, h)).netloc.lower() not in ("", host))
    return {
        "url": url, "status": "ok", "final_url": final, "title": p.title.strip(), "h1": h1,
        "meta_description": p.meta.get("description", "").strip(), "words": len(words(body)),
        "format": detect_format(p.title, h1, [t for _, t in outline]), "outline": outline,
        "questions": [t for _, t in outline if t.strip().endswith("?")],
        "images": p.images, "tables": p.tables, "lists": p.lists, "videos": p.videos, "external_links": out_links,
        "schema_types": sorted(schema_types(p.jsonld)), "newest_date": newest_date(p),
        "has_author": bool(p.meta.get("author") or re.search(r'"author"\s*:', " ".join(p.jsonld))), "_body": body,
    }


def serpapi(keyword, gl, hl):
    key = os.environ.get("SERP_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "--serp needs SERP_API_KEY. Or pass the top results with --urls.")
    q = urllib.parse.urlencode({"engine": "google", "q": keyword, "gl": gl, "hl": hl, "num": 10, "api_key": key})
    try:
        with urllib.request.urlopen(SERP + "?" + q, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        fail("SERP_FAILED", "SerpApi returned HTTP %d." % e.code)
    except Exception as e:
        fail("SERP_FAILED", "SerpApi request failed: %s" % e)
    urls = [o["link"] for o in d.get("organic_results", []) if o.get("link")]
    paa = [x.get("question", "") for x in d.get("related_questions", []) if x.get("question")]
    related = [x.get("query", "") for x in d.get("related_searches", []) if x.get("query")]
    features = sorted(k for k in ("answer_box", "knowledge_graph", "related_questions", "local_results", "shopping_results",
                                  "top_stories", "inline_videos", "ai_overview", "inline_images") if d.get(k))
    return urls, paa, related, features


def autocomplete_questions(keyword, hl, gl):
    out = []
    for pattern in QUESTION_PREFIXES:
        q = pattern.format(k=keyword)
        url = SUGGEST + "?" + urllib.parse.urlencode({"client": "firefox", "hl": hl, "gl": gl, "q": q})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=10) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            out += [s for s in data[1] if isinstance(s, str)]
        except Exception:
            break
        time.sleep(0.2)
    return [s for s in dict.fromkeys(out) if re.match(r"^(how|what|why|is|are|can|does|do|should|which|when|where|who)\b", s)]


def site_urls(site, limit=5000):
    origin = "%s://%s" % urllib.parse.urlsplit(site)[:2]
    st, _, _, robots = fetch(origin + "/robots.txt", accept="text/plain")
    queue = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots) if st == 200 else []
    queue, urls, seen = queue or [origin + "/sitemap.xml"], [], set()
    while queue and len(seen) < 8 and len(urls) < limit:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        st, _, _, body = fetch(sm, accept="application/xml,text/xml,*/*")
        if st != 200:
            continue
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            continue
        locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
        (queue if root.tag.endswith("sitemapindex") else urls).extend(locs)
    return urls[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True, help="The primary keyword the content should rank for")
    ap.add_argument("--urls", action="append", default=[], help="Top-ranking URLs for the keyword (repeat or comma-separate, up to 10)")
    ap.add_argument("--serp", action="store_true", help="Fetch Google's top 10, People Also Ask and related searches from SerpApi (SERP_API_KEY)")
    ap.add_argument("--site", help="Your site, for internal link targets and pages that already target the keyword")
    ap.add_argument("--country", default="us")
    ap.add_argument("--language", default="en")
    args = ap.parse_args()
    keyword = re.sub(r"\s+", " ", args.keyword.strip())
    if not keyword:
        fail("INPUT_INVALID", "--keyword is empty.")
    urls = [u.strip() for a in args.urls for u in a.split(",") if u.strip()]
    paa, related, features = [], [], []
    if args.serp:
        serp_urls, paa, related, features = serpapi(keyword, args.country, args.language)
        urls = urls + serp_urls
    urls = list(dict.fromkeys(urls))
    if args.site:
        own = urllib.parse.urlsplit(args.site).netloc.lower().removeprefix("www.")
        urls = [u for u in urls if urllib.parse.urlsplit(u).netloc.lower().removeprefix("www.") != own]
    urls = urls[:10]
    if len(urls) < 3 or any(not re.match(r"^https?://", u) for u in urls):
        fail("INPUT_INVALID", "Give at least 3 absolute top-result URLs with --urls, or use --serp with SERP_API_KEY.", urls=urls)

    pages = [analyze(u) for u in urls]
    ok = [p for p in pages if p["status"] == "ok" and p["words"] >= 150]
    if len(ok) < 2:
        fail("TOO_FEW_PAGES", "Fewer than 2 of the URLs could be read as articles (blocked, not HTML, or under 150 words).",
             pages=[{k: v for k, v in p.items() if k not in ("_body", "outline")} for p in pages])
    n = len(ok)

    # Format and length: what the current results take to answer the query.
    formats = collections.Counter(p["format"] for p in ok)
    wc = sorted(p["words"] for p in ok)
    q1, q3 = (statistics.quantiles(wc, n=4)[0], statistics.quantiles(wc, n=4)[2]) if n >= 4 else (wc[0], wc[-1])
    rnd = lambda x: int(round(x / 100.0) * 100)

    # Subtopics: group headings across pages by shared terms; coverage and typical position give the outline.
    kw_terms = key_terms(keyword)
    groups = []
    for pi, p in enumerate(ok):
        heads = p["outline"]
        for hi, (tag, text) in enumerate(heads):
            t = topic_terms(text) - kw_terms or topic_terms(text) or key_terms(text)
            if not t:
                continue
            g = next((g for g in groups if len(t & g["terms"]) / len(t | g["terms"]) >= 0.5), None)
            if g is None:
                g = {"terms": set(t), "labels": [], "levels": [], "pages": set(), "positions": []}
                groups.append(g)
            g["labels"].append(text)
            g["levels"].append(tag)
            g["pages"].add(pi)
            g["positions"].append(hi / max(len(heads) - 1, 1))
    subtopics = []
    for g in groups:
        cov = len(g["pages"]) / n
        label = min(g["labels"], key=lambda s: (len(s) > 70, -g["labels"].count(s), len(s)))
        level = collections.Counter(g["levels"]).most_common(1)[0][0]
        subtopics.append({"heading": label, "level": level, "coverage": round(cov, 2), "pages": len(g["pages"]),
                          "typical_position": round(statistics.mean(g["positions"]), 2), "variants": sorted(set(g["labels"]))[:4]})
    must = sorted([s for s in subtopics if s["coverage"] >= 0.4 and s["pages"] >= 2], key=lambda s: s["typical_position"])
    should = sorted([s for s in subtopics if s["pages"] >= 2 and s not in must], key=lambda s: (-s["coverage"], s["typical_position"]))
    unique = [s for s in subtopics if s["pages"] == 1 and s["level"] == "h2"]

    # Questions searchers ask: People Also Ask, Autocomplete, and competitors' question headings.
    ac_questions = autocomplete_questions(keyword, args.language, args.country)
    q_sources = collections.defaultdict(set)
    for q in paa:
        q_sources[q.strip()].add("people_also_ask")
    for q in ac_questions:
        q_sources[q.strip()].add("autocomplete")
    for p in ok:
        for q in p["questions"]:
            q_sources[q.strip()].add("competitor_heading")
    page_heads = [[key_terms(t) for _, t in p["outline"]] for p in ok]
    questions = []
    for q, src in q_sources.items():
        qt = key_terms(q)
        answered = sum(1 for heads in page_heads if qt and any(len(qt & h) / len(qt | h) >= 0.5 for h in heads))
        questions.append({"question": q, "sources": sorted(src), "pages_answering": answered})
    questions.sort(key=lambda x: (-len(x["sources"]), x["pages_answering"], x["question"]))

    # Terms most ranking pages use (single words and two-word phrases), beyond the keyword itself.
    df, surface = collections.Counter(), collections.defaultdict(collections.Counter)
    ok_word = lambda w: w not in STOP and w not in GENERIC and len(w) > 2 and not w.isdigit()
    kw_phrase = " ".join(words(keyword))
    for p in ok:
        ws = words(p["_body"])
        uni = set()
        for w in ws:
            if ok_word(w) and stem(w) not in kw_terms:
                uni.add(stem(w))
                surface[stem(w)][w] += 1
        bi = {"%s %s" % (a, b) for a, b in zip(ws, ws[1:]) if ok_word(a) and ok_word(b)}
        tri = {"%s %s %s" % (a, b, c) for a, b, c in zip(ws, ws[1:], ws[2:]) if ok_word(a) and ok_word(c) and (ok_word(b) or b in ("of", "for", "to"))}
        df.update(uni | {x for x in bi | tri if x != kw_phrase})
    need = max(2, -(-n // 2))
    kw_words = set(words(keyword))
    phrases = [{"term": t, "pages": c} for t, c in df.most_common() if c >= need and " " in t and not set(t.split()) <= kw_words]
    singles = [{"term": surface[t].most_common(1)[0][0], "pages": c} for t, c in df.most_common() if c >= need and " " not in t]
    common_terms = phrases[:30] + singles[:20]

    # The user's site: pages that already target the keyword, and internal link candidates.
    internal, existing = [], []
    if args.site:
        for u in site_urls(args.site):
            slug_terms = key_terms(re.sub(r"[-_/]+", " ", urllib.parse.urlsplit(u).path))
            if not slug_terms:
                continue
            overlap = len(slug_terms & kw_terms) / len(kw_terms) if kw_terms else 0
            if overlap >= 0.99 and len(slug_terms - kw_terms) <= 1:
                existing.append(u)
            elif overlap >= 0.34:
                internal.append((overlap, u))
        internal = [u for _, u in sorted(internal, key=lambda x: -x[0])][:15]

    fresh = [p["newest_date"] for p in ok if p["newest_date"]]
    year_ago = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()
    schema_counts = collections.Counter(t for p in ok for t in p["schema_types"] if t not in ("WebSite", "Organization", "WebPage", "ImageObject", "ListItem", "SearchAction", "EntryPoint", "PropertyValueSpecification", "SiteNavigationElement", "Person", "ReadAction"))
    json.dump({
        "status": "ok", "keyword": keyword, "checked": datetime.date.today().isoformat(),
        "serp": {"source": "serpapi" if args.serp else "urls", "features": features, "related_searches": related[:10]} ,
        "recommendation": {
            "format": formats.most_common(1)[0][0], "format_votes": dict(formats),
            "word_count_range": [rnd(q1), rnd(q3)], "median_words": int(statistics.median(wc)),
            "ymyl": bool(YMYL.search(keyword)),
            "freshness_matters": len(fresh) >= max(2, n // 2) and sum(1 for d in fresh if d >= year_ago) >= len(fresh) * 0.6,
            "media": {"pages_with_tables": sum(1 for p in ok if p["tables"]), "pages_with_video": sum(1 for p in ok if p["videos"]),
                      "median_images": int(statistics.median(p["images"] for p in ok))},
            "schema_types_seen": dict(schema_counts.most_common(8)),
            "title_length_limit": 60, "meta_description_limit": 155,
        },
        "outline": {"must_cover": must[:15], "should_cover": should[:15], "unique_angles": unique[:15]},
        "questions": questions[:25],
        "common_terms": common_terms,
        "site": {"existing_pages_for_keyword": existing[:5], "internal_link_candidates": internal} if args.site else None,
        "competitors": [{k: v for k, v in p.items() if k not in ("_body", "outline")} | {"h2_count": sum(1 for t, _ in p.get("outline", []) if t == "h2")}
                        for p in pages],
        "pages_analyzed": n,
    }, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
