#!/usr/bin/env python3
"""On-Page SEO Fixer: reference implementation.

Audits one page (or a few) for everything on the page itself that affects
search: title, meta description, H1 and heading order, URL, canonical, robots
directives, language and hreflang, Open Graph and Twitter tags, content depth
and keyword use, internal and external links, images and structured data.
Each finding comes with the exact tag or change to make, filled in with the
page's own values where possible, and the page gets a score per area.

Auth:   none (fetches the page; --check-links and --check-images add HEAD
        requests for linked pages and image sizes).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 on_page_fixer.py --url https://example.com/page [--keyword "target keyword"] [--check-links] [--check-images]
"""
from __future__ import annotations
import argparse, html as htmllib, json, re, sys
import urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

UA = "seoskills-on-page-fixer/1.0 (+https://seoskills.sh)"
STOP = set("a an the of to in for and or is are be with on by at as from that this it your you our we how what why".split())
GENERIC_ANCHORS = {"click here", "here", "read more", "learn more", "more", "this", "link", "this link", "go", "details", "see more", "continue"}
AREAS = {"metadata": 30, "headings": 15, "content": 20, "links": 15, "images": 10, "technical": 10}
SEVERITY_COST = {"critical": 1.0, "high": 0.6, "medium": 0.3, "low": 0.1}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({"status": code, "to": urllib.parse.urljoin(req.full_url, newurl)})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, method="GET", timeout=20):
    handler = _Redirects()
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"})
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(4_000_000) if method == "GET" else b""
            return {"status": r.status, "final": r.geturl(), "chain": handler.chain,
                    "headers": {k.lower(): v for k, v in r.headers.items()}, "body": body.decode("utf-8", "ignore")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final": url, "chain": handler.chain, "headers": {k.lower(): v for k, v in (e.headers or {}).items()}, "body": ""}
    except Exception:
        return {"status": 0, "final": url, "chain": handler.chain, "headers": {}, "body": ""}


class Page(HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "section", "article", "main", "header", "footer",
             "nav", "aside", "blockquote", "figcaption", "tr", "ul", "ol", "table", "dd", "dt", "details", "summary", "pre", "form", "body"}
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.titles, self.meta, self.metas = "", 0, {}, []
        self.lang, self.canonicals, self.hreflang, self.jsonld, self.links, self.images = None, [], [], [], [], []
        self.headings, self.segments, self.http_resources = [], [], []
        self._stack, self._buf, self._jbuf = [], [], []
        self._skip = self._main = self._chrome = 0
        self._in_title = self._in_jsonld = False
        self._heading = None
        self._link = None
        self.saw_main = False

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.segments.append((self._stack[-1] if self._stack else "body", text, self._main > 0, self._chrome > 0))

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        if tag == "title" and not self._skip:  # an SVG's <title> is not the page title
            self.titles += 1
            if self.titles == 1:
                self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.metas.append((key, a.get("content", "")))
                self.meta.setdefault(key, a.get("content", ""))
            if a.get("charset"):
                self.meta.setdefault("charset", a["charset"])
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel:
                self.canonicals.append(a.get("href", ""))
            if "alternate" in rel and a.get("hreflang"):
                self.hreflang.append((a["hreflang"].lower(), a.get("href", "")))
            if "stylesheet" in rel and a.get("href", "").startswith("http://"):
                self.http_resources.append(a["href"])
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._in_jsonld, self._jbuf = True, []
                return
            if a.get("src", "").startswith("http://"):
                self.http_resources.append(a["src"])
        elif tag == "img":
            self.images.append({"src": a.get("src") or a.get("data-src") or "", "alt": a.get("alt"), "width": a.get("width"),
                                "height": a.get("height"), "loading": a.get("loading", "").lower(), "srcset": bool(a.get("srcset")),
                                "in_content": not self._chrome and (self._main > 0 or not self.saw_main)})
            if a.get("src", "").startswith("http://"):
                self.http_resources.append(a["src"])
        elif tag == "a" and a.get("href") is not None:
            self._link = {"href": a.get("href", ""), "text": "", "rel": a.get("rel", "").lower(), "chrome": self._chrome > 0,
                          "img_alt": ""}
        if tag == "img" and self._link is not None:
            self._link["img_alt"] += a.get("alt") or ""
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
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = [int(tag[1]), "", self._chrome > 0]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jbuf))
            self._in_jsonld = False
            return
        if tag == "a" and self._link is not None:
            self._link["text"] = re.sub(r"\s+", " ", self._link["text"]).strip()
            self.links.append(self._link)
            self._link = None
        if re.fullmatch(r"h[1-6]", tag) and self._heading is not None:
            self.headings.append((self._heading[0], re.sub(r"\s+", " ", self._heading[1]).strip(), self._heading[2]))
            self._heading = None
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
            if self._link is not None:
                self._link["text"] += data
            if self._heading is not None:
                self._heading[1] += data

    def close(self):
        super().close()
        self._flush()


def words(text):
    return re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())


def stem(w):
    for suf in ("ing", "es", "ed", "er", "or", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def phrase_in(phrase, text):
    """All the keyword's meaningful words appear in the text, in any order and form (audit matches auditor)."""
    need = {stem(w) for w in words(phrase) if w not in STOP} or {stem(w) for w in words(phrase)}
    have = {stem(w) for w in words(text)}
    return bool(need) and need <= have


def syllables(word):
    groups = re.findall(r"[aeiouy]+", word.lower())
    n = len(groups) - (1 if word.lower().endswith("e") and len(groups) > 1 else 0)
    return max(1, n)


def flesch(text):
    sents = [s for s in re.split(r"[.!?]+\s", text) if len(s.split()) >= 3]
    ws = re.findall(r"[A-Za-z]+", text)
    if len(sents) < 3 or len(ws) < 100:
        return None
    return round(206.835 - 1.015 * (len(ws) / len(sents)) - 84.6 * (sum(syllables(w) for w in ws) / len(ws)), 1)


def jsonld_types(blocks):
    types, errors = set(), 0
    for b in blocks:
        try:
            data = json.loads(b)
        except ValueError:
            errors += 1
            continue
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, list):
                stack.extend(n)
            elif isinstance(n, dict):
                t = n.get("@type")
                types.update([t] if isinstance(t, str) else [x for x in t if isinstance(x, str)] if isinstance(t, list) else [])
                stack.extend(v for v in n.values() if isinstance(v, (dict, list)))
    return sorted(types), errors


def audit(url, keyword, check_links, check_images):
    r = fetch(url)
    if r["status"] != 200 or "html" not in r["headers"].get("content-type", "html"):
        return {"url": url, "status": "unreachable", "http_status": r["status"]}
    final = r["final"]
    p = Page()
    try:
        p.feed(r["body"])
        p.close()
    except Exception:
        pass
    issues = []

    def flag(area, code, severity, detail, fix, snippet=None):
        item = {"area": area, "code": code, "severity": severity, "detail": detail, "fix": fix}
        if snippet:
            item["snippet"] = snippet
        issues.append(item)

    title = htmllib.unescape(p.title).strip()
    desc = p.meta.get("description", "").strip()
    h1s = [t for lvl, t, _ in p.headings if lvl == 1]
    content_heads = [(lvl, t) for lvl, t, chrome in p.headings if not chrome]
    content = [s for s in p.segments if not s[3] and (s[2] or not p.saw_main)]
    body = " ".join(t for tag, t, _, _ in content)
    body_words = words(body)
    sp = urllib.parse.urlsplit(final)
    site = sp.netloc.lower().removeprefix("www.")
    brand = p.meta.get("og:site_name", "")

    # Metadata: title and description.
    if not title:
        flag("metadata", "TITLE_MISSING", "critical", "The page has no <title>.", "Add a unique title of about 50 to 60 characters with the main keyword near the start.",
             "<title>%s</title>" % (keyword.title() + (" | " + brand if brand else "") if keyword else "Primary Keyword | Brand"))
    else:
        if p.titles > 1:
            flag("metadata", "MULTIPLE_TITLES", "medium", "%d <title> tags." % p.titles, "Keep one <title> in the head.")
        if len(title) > 60:
            flag("metadata", "TITLE_LONG", "medium", "%d characters; Google cuts titles at about 600 pixels (roughly 55 to 60 characters)." % len(title),
                 "Shorten to about 55 characters and keep the keyword and the brand.")
        elif len(title) < 25:
            flag("metadata", "TITLE_SHORT", "low", "%d characters." % len(title), "Use the space: add the main benefit or qualifier (about 50 to 60 characters).")
        if keyword and not phrase_in(keyword, title):
            flag("metadata", "KEYWORD_NOT_IN_TITLE", "high", "The title does not contain \"%s\"." % keyword, "Put the keyword (or its closest natural form) in the first half of the title.")
    if not desc:
        flag("metadata", "META_DESCRIPTION_MISSING", "high", "No meta description; Google will pick text from the page.",
             "Write a 120 to 155 character description that answers the query and invites the click.",
             '<meta name="description" content="...">')
    else:
        if len(desc) > 160:
            flag("metadata", "META_DESCRIPTION_LONG", "low", "%d characters; it will be cut in results." % len(desc), "Trim to about 150 characters.")
        elif len(desc) < 70:
            flag("metadata", "META_DESCRIPTION_SHORT", "low", "%d characters." % len(desc), "Expand to 120 to 155 characters with a specific benefit.")
        if title and desc.lower().startswith(title.lower()[: max(20, len(title) // 2)]):
            flag("metadata", "META_DESCRIPTION_REPEATS_TITLE", "medium", "The description opens by repeating the title.",
                 "Write a description that adds what the title does not: the answer, the proof or the offer.")
        if keyword and not phrase_in(keyword, desc):
            flag("metadata", "KEYWORD_NOT_IN_DESCRIPTION", "low", "The description does not contain the keyword.", "Use the keyword naturally once; Google bolds matching words.")

    # Headings.
    if not h1s:
        flag("headings", "H1_MISSING", "high", "No H1.", "Add one H1 that states the page's topic.", "<h1>%s</h1>" % (keyword.capitalize() if keyword else title or "Page topic"))
    elif len(h1s) > 1:
        flag("headings", "MULTIPLE_H1", "low", "%d H1s: %s" % (len(h1s), "; ".join(h[:60] for h in h1s[:3])), "Use one H1 for the page topic and H2s for sections.")
    if h1s and keyword and not any(phrase_in(keyword, h) for h in h1s):
        flag("headings", "KEYWORD_NOT_IN_H1", "medium", "The H1 does not contain the keyword.", "Work the keyword into the H1 naturally.")
    if any(not t for _, t in content_heads):
        flag("headings", "EMPTY_HEADING", "low", "%d empty heading tag(s)." % sum(1 for _, t in content_heads if not t), "Remove empty headings or give them text.")
    skips = [(a[0], b[0], b[1][:50]) for a, b in zip(content_heads, content_heads[1:]) if b[0] > a[0] + 1]
    if skips:
        flag("headings", "SKIPPED_HEADING_LEVEL", "low", "Heading levels jump, e.g. H%d to H%d at \"%s\"." % skips[0], "Step down one level at a time (H2, then H3).")
    if len([1 for lvl, _ in content_heads if lvl == 2]) == 0 and len(body_words) > 600:
        flag("headings", "NO_SUBHEADINGS", "medium", "A %d-word page with no H2s." % len(body_words), "Break the content into sections with descriptive H2s.")

    # Content.
    n = len(body_words)
    if n < 300:
        flag("content", "THIN_CONTENT", "medium" if n < 150 else "low", "%d words of main content." % n,
             "Add the information a searcher needs (answers, specifics, examples); length itself is not the goal.")
    if keyword:
        first100 = " ".join(body_words[:100])
        if not phrase_in(keyword, first100):
            flag("content", "KEYWORD_NOT_EARLY", "low", "The keyword does not appear in the first 100 words.", "Mention the topic plainly in the opening paragraph.")
        kw_words = [w for w in words(keyword) if w not in STOP] or words(keyword)
        if kw_words and n:
            hits = len(re.findall(r"\b%s\b" % r"\W+".join(map(re.escape, words(keyword))), " ".join(body_words)))
            density = hits * len(words(keyword)) / n
            if hits >= 8 and density > 0.04:
                flag("content", "KEYWORD_STUFFING_RISK", "medium", "\"%s\" appears %d times (%.1f%% of words)." % (keyword, hits, density * 100),
                     "Write for readers; Google's spam policies name keyword stuffing. Use synonyms and related terms instead of repeats.")
    reading = flesch(body)

    # Links.
    internal, external, generic, empty, nofollow_internal = [], [], [], [], []
    for l in p.links:
        href = l["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absu = urllib.parse.urljoin(final, href).split("#")[0]
        host = urllib.parse.urlsplit(absu).netloc.lower().removeprefix("www.")
        anchor = (l["text"] or l["img_alt"]).strip()
        (internal if host == site else external).append(absu)
        if not l["chrome"]:
            if not anchor:
                empty.append(absu)
            elif anchor.lower() in GENERIC_ANCHORS:
                generic.append("%s -> %s" % (anchor, absu))
        if host == site and "nofollow" in l["rel"]:
            nofollow_internal.append(absu)
    content_internal = len({urllib.parse.urljoin(final, l["href"]).split("#")[0] for l in p.links if not l["chrome"]
                            and urllib.parse.urlsplit(urllib.parse.urljoin(final, l["href"])).netloc.lower().removeprefix("www.") == site})
    if content_internal < 3 and n > 300:
        flag("links", "FEW_CONTEXTUAL_INTERNAL_LINKS", "medium", "%d internal links inside the main content." % content_internal,
             "Link to 3 or more related pages from the body text, with descriptive anchors.")
    if generic:
        flag("links", "GENERIC_ANCHOR_TEXT", "low", "%d link(s) say things like \"click here\": %s" % (len(generic), "; ".join(generic[:3])),
             "Use anchor text that names the destination page's topic.")
    if empty:
        flag("links", "EMPTY_ANCHOR", "medium", "%d link(s) have no text or image alt." % len(empty), "Give every link text, or an alt on its image.")
    if nofollow_internal:
        flag("links", "NOFOLLOW_INTERNAL", "low", "%d internal link(s) carry rel=nofollow." % len(nofollow_internal), "Remove nofollow from links to your own pages.")
    broken = []
    if check_links:
        for u in list(dict.fromkeys(internal))[:50]:
            st = fetch(u, method="HEAD", timeout=10)["status"]
            if st in (0, 403, 405, 429):  # some servers refuse HEAD or bots; confirm with a GET
                st = fetch(u, timeout=10)["status"]
            if st in (404, 410) or st >= 500 or st == 0:
                broken.append({"url": u, "status": st})
        if broken:
            flag("links", "BROKEN_INTERNAL_LINKS", "high", "%d internal link(s) fail: %s" % (len(broken), ", ".join("%s (%s)" % (b["url"], b["status"]) for b in broken[:3])),
                 "Fix or remove the broken links.")

    # Images.
    imgs = [i for i in p.images if i["src"] and not i["src"].startswith("data:")]
    no_alt = [i["src"] for i in imgs if i["alt"] is None]
    unsized = [i["src"] for i in imgs if not (i["width"] and i["height"])]
    legacy = [i["src"] for i in imgs if re.search(r"\.(jpe?g|png|gif|bmp)(\?|$)", i["src"], re.I)]
    if no_alt:
        flag("images", "IMG_ALT_MISSING", "medium", "%d image(s) have no alt attribute." % len(no_alt),
             "Describe each meaningful image in its alt (what it shows, in context); use alt=\"\" for decorative ones.",
             "\n".join('<img src="%s" alt="DESCRIBE THIS IMAGE">' % s for s in no_alt[:5]))
    if unsized:
        flag("images", "IMG_NO_DIMENSIONS", "low", "%d image(s) lack width and height." % len(unsized), "Set width and height (or CSS aspect-ratio) to stop layout shift.")
    if legacy and len(legacy) >= max(3, len(imgs) // 2):
        flag("images", "LEGACY_IMAGE_FORMATS", "low", "%d of %d images are JPEG, PNG or GIF." % (len(legacy), len(imgs)), "Serve WebP or AVIF versions.")
    if imgs and imgs[0]["loading"] == "lazy" and imgs[0]["in_content"]:
        flag("images", "FIRST_IMAGE_LAZY", "medium", "The first content image is lazy-loaded.", "Do not lazy-load the hero image; give it fetchpriority=\"high\".")
    heavy = []
    if check_images:
        for i in imgs[:20]:
            h = fetch(urllib.parse.urljoin(final, i["src"]), method="HEAD", timeout=10)
            size = int(h["headers"].get("content-length", "0") or 0)
            if size > 200_000:
                heavy.append({"src": i["src"], "kb": size // 1024})
        if heavy:
            flag("images", "HEAVY_IMAGES", "high" if any(x["kb"] > 500 for x in heavy) else "medium",
                 "%d image(s) over 200 KB: %s" % (len(heavy), ", ".join("%s (%d KB)" % (h["src"][-50:], h["kb"]) for h in heavy[:3])),
                 "Resize to the displayed size and compress (WebP or AVIF), with srcset for smaller screens.")

    # Technical.
    robots = ",".join(v for k, v in p.metas if k in ("robots", "googlebot")).lower() + "," + r["headers"].get("x-robots-tag", "").lower()
    if "noindex" in robots:
        flag("technical", "NOINDEX", "critical", "The page is noindex.", "Remove noindex if this page should appear in search.")
    if "nosnippet" in robots:
        flag("technical", "NOSNIPPET", "high", "nosnippet hides the page's text in results and AI Overviews.", "Remove it unless that is intended.")
    canon = urllib.parse.urljoin(final, p.canonicals[0]) if p.canonicals else None
    if not p.canonicals:
        flag("technical", "CANONICAL_MISSING", "medium", "No canonical tag.", "Add a self-referencing canonical.", '<link rel="canonical" href="%s">' % final)
    elif len(set(p.canonicals)) > 1:
        flag("technical", "MULTIPLE_CANONICALS", "high", "%d different canonical tags." % len(set(p.canonicals)), "Keep exactly one canonical.")
    elif canon and canon.rstrip("/") != final.split("#")[0].rstrip("/"):
        flag("technical", "CANONICAL_POINTS_ELSEWHERE", "high", "The canonical points to %s." % canon,
             "If this page should rank on its own, make the canonical self-referencing; otherwise this is expected.")
    if r["chain"]:
        flag("technical", "REDIRECTED", "low", "The URL redirects %d time(s) to %s." % (len(r["chain"]), final), "Link to the final URL directly.")
    if not p.lang:
        flag("technical", "LANG_MISSING", "low", "No lang attribute on <html>.", "Declare the language.", '<html lang="en">')
    if not any(k == "viewport" for k, _ in p.metas):
        flag("technical", "VIEWPORT_MISSING", "high", "No viewport meta tag, so the page is not mobile-friendly.", "Add the viewport tag.",
             '<meta name="viewport" content="width=device-width, initial-scale=1">')
    if p.hreflang:
        codes = [c for c, _ in p.hreflang]
        selfref = any(urllib.parse.urljoin(final, h).rstrip("/") == final.rstrip("/") for _, h in p.hreflang)
        if not selfref:
            flag("technical", "HREFLANG_NO_SELF_REFERENCE", "medium", "hreflang tags do not include this page itself.", "Add a hreflang entry for this page's own language.")
        if "x-default" not in codes:
            flag("technical", "HREFLANG_NO_X_DEFAULT", "low", "No x-default hreflang.", "Add x-default for the language-selector or fallback page.")
    if sp.scheme == "https" and p.http_resources:
        flag("technical", "MIXED_CONTENT", "medium", "%d resource(s) load over http://." % len(p.http_resources), "Load every resource over https.")
    if len(final) > 115 or re.search(r"[A-Z_]", sp.path) or sp.query:
        flag("technical", "URL_NOT_CLEAN", "low", "The URL is long, has uppercase or underscores, or uses query parameters.",
             "Prefer short, lowercase, hyphenated paths for pages meant to rank (change only with a 301 redirect).")
    og_missing = [k for k in ("og:title", "og:description", "og:image") if not p.meta.get(k)]
    if og_missing:
        snippet = "\n".join('<meta property="%s" content="%s">' % (k, htmllib.escape({"og:title": title, "og:description": desc}.get(k) or "...")) for k in og_missing)
        flag("technical", "OPEN_GRAPH_INCOMPLETE", "low", "Missing %s." % ", ".join(og_missing), "Add Open Graph tags so shares show a proper card.", snippet)
    types, ld_errors = jsonld_types(p.jsonld)
    if ld_errors:
        flag("technical", "JSONLD_INVALID", "high", "%d JSON-LD block(s) do not parse." % ld_errors, "Fix the JSON syntax (validate in the Rich Results Test).")
    if not types:
        flag("technical", "NO_STRUCTURED_DATA", "low", "No JSON-LD.", "Add markup for the page type (Article, Product, LocalBusiness, BreadcrumbList).")

    scores = {}
    for area, weight in AREAS.items():
        lost = sum(SEVERITY_COST[i["severity"]] for i in issues if i["area"] == area)
        scores[area] = round(max(0.0, weight * (1 - min(1.0, lost / 1.5))), 1)
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda i: (rank[i["severity"]], i["area"]))
    return {
        "url": url, "status": "ok", "final_url": final, "keyword": keyword or None,
        "score": round(sum(scores.values())), "area_scores": scores,
        "page": {"title": title, "title_chars": len(title), "meta_description": desc, "meta_description_chars": len(desc),
                 "h1": h1s, "headings": [{"level": lvl, "text": t[:120]} for lvl, t in content_heads[:40]],
                 "words": n, "flesch_reading_ease": reading, "canonical": canon, "robots": robots.strip(","), "lang": p.lang,
                 "internal_links": len(set(internal)), "external_links": len(set(external)), "images": len(imgs),
                 "images_missing_alt": len(no_alt), "schema_types": types, "hreflang": len(p.hreflang)},
        "issues": issues,
        "broken_links": broken, "heavy_images": heavy,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", required=True, help="Page to fix (repeat or comma-separate, up to 10)")
    ap.add_argument("--keyword", action="append", default=[], help="Target keyword; one for all pages, or one per --url in order")
    ap.add_argument("--check-links", action="store_true", help="HEAD-check up to 50 internal links per page")
    ap.add_argument("--check-images", action="store_true", help="HEAD-check up to 20 images per page for file size")
    args = ap.parse_args()
    urls = list(dict.fromkeys(u.strip() for a in args.url for u in a.split(",") if u.strip()))[:10]
    if any(not re.match(r"^https?://[^/\s]+", u) for u in urls):
        fail("INPUT_INVALID", "Each --url must be an absolute http(s) URL.")
    kws = [k.strip() for k in args.keyword if k.strip()]
    pages = []
    for i, u in enumerate(urls):
        kw = kws[i] if len(kws) == len(urls) else (kws[0] if kws else "")
        pages.append(audit(u, kw, args.check_links, args.check_images))
    if all(p["status"] != "ok" for p in pages):
        fail("PAGES_UNREACHABLE", "None of the pages could be fetched as HTML.", pages=pages)
    json.dump({"status": "ok", "pages": pages}, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
