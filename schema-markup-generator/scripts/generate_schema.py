#!/usr/bin/env python3
"""Schema Markup Generator: reference implementation.

Reads a page (live URL or local HTML file), works out what kind of page it is,
validates any JSON-LD it already carries, and generates a complete JSON-LD
@graph for it (Organization, WebSite, BreadcrumbList and the page's main
entity) from facts found on the page. Anything a required property needs but
the page does not show becomes a "[FILL: ...]" placeholder, never a guess.

Auth:   none (fetches the page, or reads --html).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 generate_schema.py --url https://example.com/product/x [--type Product]
       python3 generate_schema.py --html draft.html --page-url https://example.com/new-page
"""
from __future__ import annotations
import argparse, datetime, json, re, sys
import urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

UA = "seoskills-schema-generator/1.0 (+https://seoskills.sh)"
TYPES = ["Article", "BlogPosting", "NewsArticle", "Product", "LocalBusiness", "SoftwareApplication", "Event",
         "Recipe", "VideoObject", "JobPosting", "ProfilePage", "FAQPage", "Organization", "WebPage"]
# Required properties for Google rich results, per Google Search Central (Article and Organization have none).
REQUIRED = {
    "Product": ["name", ("offers", "review", "aggregateRating")],
    "Offer": [("price", "priceSpecification")],
    "LocalBusiness": ["name", "address"],
    "SoftwareApplication": ["name", "offers", ("aggregateRating", "review")],
    "Event": ["name", "startDate", "location"],
    "Recipe": ["name", "image"],
    "VideoObject": ["name", "thumbnailUrl", "uploadDate"],
    "JobPosting": ["title", "description", "datePosted", "hiringOrganization", ("jobLocation", "applicantLocationRequirements")],
    "BreadcrumbList": ["itemListElement"],
    "ProfilePage": ["mainEntity"],
    "AggregateRating": ["ratingValue", ("ratingCount", "reviewCount")],
}
LOCAL_SUBTYPES = {"Restaurant", "Dentist", "Store", "AutoRepair", "HomeAndConstructionBusiness", "Plumber", "Electrician",
                  "LegalService", "MedicalBusiness", "HealthAndBeautyBusiness", "FoodEstablishment", "LodgingBusiness",
                  "ProfessionalService", "RealEstateAgent", "FinancialService", "Hotel", "CafeOrCoffeeShop", "Bakery"}
NO_RICH_RESULT = {
    "FAQPage": "Google stopped showing FAQ rich results in May 2026.",
    "HowTo": "Google stopped showing HowTo rich results in 2023.",
    "SpecialAnnouncement": "Google retired this rich result in 2025.",
    "ClaimReview": "Google no longer shows a Search rich result for it (Fact Check Explorer still reads it).",
    "VehicleListing": "Google retired this rich result in 2025.",
}
SELF_SERVING = {"LocalBusiness", "Organization"} | LOCAL_SUBTYPES
URL_PROPS = {"url", "image", "logo", "item", "sameAs", "thumbnailUrl", "contentUrl", "embedUrl", "mainEntityOfPage"}
DATE_PROPS = {"datePublished", "dateModified", "startDate", "endDate", "uploadDate", "datePosted", "validThrough", "priceValidUntil"}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
PLACEHOLDER = re.compile(r"\[(fill|insert|your)[^\]]*\]|lorem ipsum|\bTODO\b|\bXXX\b|\{\{|example\.com", re.I)
SOCIAL = re.compile(r"^https?://(www\.)?(facebook\.com|instagram\.com|x\.com|twitter\.com|linkedin\.com/(company|in|school)|youtube\.com/(@|c/|channel/|user/)"
                    r"|tiktok\.com/@|pinterest\.com|github\.com|[a-z]{2}\.wikipedia\.org/wiki|wikidata\.org/wiki|crunchbase\.com/organization|threads\.net/@|bsky\.app/profile)", re.I)
PRICE = re.compile(r"([$€£₹¥])\s?(\d{1,3}(?:[,\d]{0,12})(?:\.\d{2})?)")
CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
RATING = re.compile(r"(\d(?:\.\d)?)\s*(?:out of 5|/\s*5\b|stars?\b)", re.I)
COUNT = re.compile(r"(\d[\d,]*)\s*(?:reviews|ratings|customer reviews)\b", re.I)
US_ADDRESS = re.compile(r"(\d{1,6}[A-Za-z0-9 .#'-]{2,60}(?:,\s*(?:suite|ste|unit|apt|#|floor|fl)\.?\s*[A-Za-z0-9-]+)?),"
                        r"\s*([A-Za-z .'-]{2,40}),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", re.I)
VIDEO = re.compile(r"(?:youtube(?:-nocookie)?\.com/embed/|youtu\.be/)([\w-]{11})|player\.vimeo\.com/video/(\d+)")
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "section", "article", "main", "header",
              "footer", "nav", "aside", "blockquote", "summary", "dd", "dt", "figcaption", "tr", "ul", "ol", "table",
              "dl", "details", "pre", "form", "body", "address"}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "select", "button"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read(3_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, url, "", ""
    except Exception:
        return 0, url, "", ""


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.segments, self.meta, self.jsonld, self.links, self.times = [], {}, [], [], []
        self.title, self.logo, self.images, self.iframes, self.videos = "", "", [], [], []
        self.microdata = self.rdfa = False
        self.address, self.crumbs, self.crumb_tail = [], [], []
        self.byline = []
        self._stack, self._buf, self._jbuf, self._abuf = [], [], [], []
        self._skip = self._main = self._chrome = self._svg = 0
        self._in_title = self._in_jsonld = False
        self._details = None
        self._details_n = 0
        self._crumb_depth = self._address_depth = self._byline_depth = 0
        self._link = None
        self.saw_main = False

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.segments.append({"tag": self._stack[-1] if self._stack else "body", "text": text, "main": self._main > 0,
                                  "chrome": self._chrome > 0, "details": self._details})
            if self._crumb_depth:
                self.crumb_tail.append(text)

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        marker = " ".join([a.get("class", ""), a.get("id", ""), a.get("aria-label", "")]).lower()
        if "itemscope" in a or "itemtype" in a:
            self.microdata = True
        if "typeof" in a or "vocab" in a:
            self.rdfa = True
        if tag == "svg":
            self._svg += 1
        if tag == "title" and not self._svg and not self.title:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld, self._jbuf = True, []
            return
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            if src and not src.startswith("data:"):
                if not self.logo and "logo" in (marker + " " + a.get("alt", "") + " " + src).lower():
                    self.logo = src
                elif self._main or not self._chrome:
                    self.images.append(src)
        elif tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])
        elif tag in ("video", "source") and a.get("src"):
            self.videos.append(a["src"])
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag not in VOID:
            if self._crumb_depth:
                self._crumb_depth += 1
            elif "breadcrumb" in marker:
                self._flush()
                self._crumb_depth = 1
            if self._byline_depth:
                self._byline_depth += 1
            elif re.search(r"\b(author|byline)\b", marker) and not self._skip:
                self._byline_depth = 1
                self.byline.append("")
        if tag == "br":
            self._flush()
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in ("main", "article"):
                self._main += 1
                self.saw_main = True
            if tag in ("nav", "footer", "aside") or (tag == "header" and not self._main):
                self._chrome += 1
            if tag == "details":
                self._details_n += 1
                self._details = self._details_n
            if tag == "address":
                self._address_depth, self._abuf = 1, []
            self._stack.append(tag)
        if tag == "a":
            self._link = {"href": a.get("href", ""), "text": "", "crumb": self._crumb_depth > 0,
                          "rel": a.get("rel", "").lower(), "main": self._main > 0, "chrome": self._chrome > 0}

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "svg" and self._svg:
            self._svg -= 1
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jbuf))
            self._in_jsonld = False
            return
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "a" and self._link is not None:
            self._link["text"] = re.sub(r"\s+", " ", self._link["text"]).strip()
            self.links.append(self._link)
            self._link = None
        if tag in BLOCK_TAGS and tag in self._stack:
            self._flush()
            while self._stack:
                top = self._stack.pop()
                if top in ("main", "article") and self._main:
                    self._main -= 1
                if (top in ("nav", "footer", "aside") or (top == "header" and not self._main)) and self._chrome:
                    self._chrome -= 1
                if top == "details":
                    self._details = None
                if top == "address" and self._address_depth:
                    self.address.append(re.sub(r"\s+", " ", " ".join(self._abuf)).strip())
                    self._address_depth = 0
                if top == tag:
                    break
        if tag not in VOID:
            if self._crumb_depth:
                self._crumb_depth -= 1
            if self._byline_depth:
                self._byline_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_jsonld:
            self._jbuf.append(data)
        elif not self._skip and not self._svg:
            self._buf.append(data)
            if self._link is not None:
                self._link["text"] += data
            if self._address_depth:
                self._abuf.append(data)
            if self._byline_depth and self.byline:
                self.byline[-1] += data

    def close(self):
        super().close()
        self._flush()


# ---------- helpers ----------

def absolute(base, u):
    return urllib.parse.urljoin(base, u) if u else u


def as_url(v):
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("url") or v.get("contentUrl") or ""
    return v if isinstance(v, str) else ""


def iso(s):
    s = (s or "").strip()
    return s if ISO_DATE.match(s) else None


def jsonld_blocks(raw_blocks):
    blocks, errors = [], []
    for i, raw in enumerate(raw_blocks):
        try:
            blocks.append(json.loads(raw))
        except ValueError as e:
            errors.append({"block": i + 1, "code": "INVALID_JSON", "detail": str(e)[:120]})
    return blocks, errors


def walk(node, path="$"):
    """Yield (path, dict) for every typed object in a JSON-LD value."""
    if isinstance(node, list):
        for i, n in enumerate(node):
            yield from walk(n, "%s[%d]" % (path, i))
    elif isinstance(node, dict):
        if "@type" in node:
            yield path, node
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                yield from walk(v, "%s.%s" % (path, k))


def types_of(node):
    t = node.get("@type")
    return [t] if isinstance(t, str) else [x for x in t if isinstance(x, str)] if isinstance(t, list) else []


def required_for(t):
    if t in LOCAL_SUBTYPES:
        return REQUIRED["LocalBusiness"]
    if t in ("WebApplication", "MobileApplication", "VideoGame"):
        return REQUIRED["SoftwareApplication"]
    return REQUIRED.get(t, [])


def is_reference(node):
    """A pointer such as {"@type": "Product", "url": ...} inside hasVariant or itemReviewed, not a full entity."""
    return not set(node) - {"@type", "@id", "url", "name", "sameAs", "identifier"}


def missing_required(node, t):
    missing = []
    for req in required_for(t):
        options = req if isinstance(req, tuple) else (req,)
        if not any(node.get(o) not in (None, "", [], {}) for o in options):
            missing.append(" or ".join(options))
    return missing


def validate(blocks, page_text, page_url):
    issues, found = [], []
    for b_i, block in enumerate(blocks):
        tops = block if isinstance(block, list) else block.get("@graph", [block]) if isinstance(block, dict) else []
        has_ctx = isinstance(block, dict) and "@context" in block or isinstance(block, list) and all(isinstance(x, dict) and "@context" in x for x in block)
        if not has_ctx:
            issues.append({"block": b_i + 1, "code": "NO_CONTEXT", "detail": "No @context, so parsers cannot read the types."})
        for top in tops:
            if isinstance(top, dict) and "@type" not in top and "@id" not in top:
                issues.append({"block": b_i + 1, "code": "NO_TYPE", "detail": "A top-level object has no @type."})
        for path, node in walk(block):
            for t in types_of(node):
                found.append(t)
                miss = [] if path != "$" and is_reference(node) else missing_required(node, t)
                if miss:
                    issues.append({"block": b_i + 1, "path": path, "type": t, "code": "MISSING_REQUIRED",
                                   "detail": "Google needs %s for this rich result" % "; ".join(miss)})
                if t in NO_RICH_RESULT:
                    issues.append({"block": b_i + 1, "path": path, "type": t, "code": "NO_GOOGLE_RICH_RESULT", "detail": NO_RICH_RESULT[t]})
                if t in SELF_SERVING and (node.get("aggregateRating") or node.get("review")):
                    issues.append({"block": b_i + 1, "path": path, "type": t, "code": "SELF_SERVING_REVIEW",
                                   "detail": "Google does not show review stars for a business's reviews of itself on its own site."})
            for k, v in node.items():
                vals = v if isinstance(v, list) else [v]
                if k in URL_PROPS:
                    for u in vals:
                        if isinstance(u, str) and u and not re.match(r"^https?://", u) and not u.startswith("[FILL"):
                            issues.append({"block": b_i + 1, "path": "%s.%s" % (path, k), "code": "RELATIVE_URL", "detail": u[:120]})
                if k in DATE_PROPS:
                    for d in vals:
                        if isinstance(d, str) and not iso(d) and not d.startswith("[FILL"):
                            issues.append({"block": b_i + 1, "path": "%s.%s" % (path, k), "code": "INVALID_DATE", "detail": d[:60]})
                for s in vals:
                    if isinstance(s, str) and PLACEHOLDER.search(s) and "example.com" not in page_url:
                        issues.append({"block": b_i + 1, "path": "%s.%s" % (path, k), "code": "PLACEHOLDER_TEXT", "detail": s[:80]})
            if "Offer" in types_of(node) and node.get("price") not in (None, "") and not str(node["price"]).startswith("[FILL"):
                num = str(node["price"]).replace(",", "")
                variants = {num, num.rstrip("0").rstrip(".") if "." in num else num}
                plain = page_text.replace(",", "")
                if not any(re.search(r"(?<![\d.])%s(?![\d])" % re.escape(v), plain) for v in variants if v):
                    issues.append({"block": b_i + 1, "path": path, "code": "PRICE_NOT_VISIBLE",
                                   "detail": "Price %s is not in the page's visible text; markup must match what users see." % node["price"]})
    grouped = {}
    for i in issues:  # one line per distinct problem, with how often it occurs
        key = (i["block"], i["code"], i.get("type"), i.get("detail"))
        if key in grouped:
            grouped[key]["count"] += 1
        else:
            grouped[key] = {**i, "count": 1}
    return list(grouped.values()), sorted(set(found))


# ---------- fact extraction ----------

def extract_facts(p, url, blocks):
    content = [s for s in p.segments if not s["chrome"] and (s["main"] or not p.saw_main)]
    text = " ".join(s["text"] for s in content)
    meta = p.meta
    nodes = [n for b in blocks for _, n in walk(b)]

    def first_node(*names):
        return next((n for n in nodes if set(types_of(n)) & set(names)), None)

    org_node = first_node("Organization", "Corporation", *LOCAL_SUBTYPES, "LocalBusiness")
    h1 = next((s["text"] for s in p.segments if s["tag"] == "h1"), "")
    first_para = next((s["text"] for s in content if s["tag"] == "p" and len(s["text"].split()) >= 12), "")
    facts = {
        "url": url, "h1": h1, "title": (meta.get("og:title") or p.title).strip(),
        "description": (meta.get("description") or meta.get("og:description") or first_para[:300]).strip(),
        "image": absolute(url, meta.get("og:image") or meta.get("twitter:image") or (p.images[0] if p.images else "")),
        "site_name": (meta.get("og:site_name") or meta.get("application-name") or (org_node or {}).get("name") or "").strip(),
        "logo": absolute(url, as_url((org_node or {}).get("logo")) or p.logo),
        "same_as": sorted({l["href"].split("?")[0].rstrip("/") for l in p.links if SOCIAL.match(l["href"] or "") and "/share" not in l["href"] and "intent" not in l["href"]}),
        "same_as_content": sorted({l["href"].split("?")[0].rstrip("/") for l in p.links if SOCIAL.match(l["href"] or "") and not l["chrome"]}),
        "og_type": meta.get("og:type", "").lower(),
        "words": len(text.split()),
    }
    author = meta.get("author") or meta.get("article:author") or ""
    if not author:
        rel = next((l["text"] for l in p.links if "author" in l["rel"] and l["text"]), "")
        line = next((b.strip() for b in p.byline if 2 <= len(b.split()) <= 8), "")
        author = rel or re.sub(r"^(by|written by|author:?)\s+", "", line, flags=re.I)
    if author.startswith("http"):
        author = ""
    facts["author"] = author.strip()[:80]
    pub = meta.get("article:published_time") or next((n.get("datePublished") for n in nodes if n.get("datePublished")), None)
    mod = meta.get("article:modified_time") or meta.get("og:updated_time") or next((n.get("dateModified") for n in nodes if n.get("dateModified")), None)
    times = [t for t in p.times if iso(t)]
    facts["date_published"] = iso(pub) or (times[0] if times else None)
    facts["date_modified"] = iso(mod)
    price = meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price")
    currency = meta.get("product:price:currency") or meta.get("og:price:currency") or meta.get("pricecurrency")
    price_guess = False
    if not price:
        m = PRICE.search(text)
        if m:
            price, currency, price_guess = m.group(2).replace(",", ""), CURRENCY.get(m.group(1)), True
    facts["price"], facts["currency"], facts["price_from_text"] = price, currency, price_guess
    low = text.lower()
    facts["availability"] = ("https://schema.org/OutOfStock" if re.search(r"\b(out of stock|sold out)\b", low)
                             else "https://schema.org/PreOrder" if "pre-order" in low or "preorder" in low
                             else "https://schema.org/InStock" if re.search(r"\b(in stock|add to (cart|bag|basket)|buy now)\b", low) else None)
    r, c = RATING.search(text), COUNT.search(text)
    facts["rating"] = {"value": r.group(1), "count": c.group(1).replace(",", "")} if r and c else None
    tel = next((l["href"][4:] for l in p.links if l["href"].lower().startswith("tel:")), "")
    facts["telephone"] = re.sub(r"[^\d+]", "", tel) if tel else ""
    addr_text = next((a for a in p.address if a), "")
    m = US_ADDRESS.search(addr_text) or US_ADDRESS.search(text[:20000]) if addr_text or re.search(r"\b[A-Z]{2}\s+\d{5}\b", text) else None
    facts["address"] = ({"streetAddress": m.group(1).strip()[-80:], "addressLocality": m.group(2).strip(), "addressRegion": m.group(3),
                         "postalCode": m.group(4)} if m else ({"text": addr_text} if addr_text else None))
    geo = None
    for l in p.links:
        g = re.search(r"@(-?\d+\.\d{4,}),(-?\d+\.\d{4,})|[?&](?:q|ll|query)=(-?\d+\.\d{4,}),(-?\d+\.\d{4,})", l["href"] or "")
        if g and "google." in l["href"]:
            lat, lng = (g.group(1), g.group(2)) if g.group(1) else (g.group(3), g.group(4))
            geo = {"latitude": float(lat), "longitude": float(lng)}
            break
    facts["geo"] = geo
    crumbs = [{"name": l["text"], "item": absolute(url, l["href"])} for l in p.links if l["crumb"] and l["text"] and l["href"]]
    if crumbs and p.crumb_tail:
        rest = p.crumb_tail[-1]
        for c in crumbs:
            if c["name"] in rest:
                rest = rest.split(c["name"])[-1]
        rest = rest.strip(" /\u203a\u00bb>|-\u2013\u00b7")
        if rest and len(rest.split()) <= 12:
            crumbs.append({"name": rest, "item": url})
    facts["breadcrumbs"] = crumbs
    faqs, by_details = [], {}
    for s in content:
        if s["details"]:
            by_details.setdefault(s["details"], []).append(s)
    for segs in by_details.values():
        q = next((s["text"] for s in segs if s["tag"] == "summary"), "")
        a = " ".join(s["text"] for s in segs if s["tag"] != "summary")
        if q and a:
            faqs.append({"question": q, "answer": a[:1000]})
    for i, s in enumerate(content):
        if s["tag"] in ("h2", "h3", "h4") and s["text"].strip().endswith("?") and not s["details"]:
            ans = []
            for nxt in content[i + 1:i + 5]:
                if nxt["tag"] in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    break
                ans.append(nxt["text"])
            if ans:
                faqs.append({"question": s["text"], "answer": " ".join(ans)[:1000]})
    facts["faqs"] = faqs[:20]

    def list_under(pattern):
        items, on = [], False
        for s in content:
            if s["tag"] in ("h2", "h3", "h4"):
                on = bool(re.search(pattern, s["text"], re.I))
            elif on and s["tag"] == "li":
                items.append(s["text"])
        return items
    facts["ingredients"] = list_under(r"ingredients")
    facts["instructions"] = list_under(r"instructions|method|directions|steps")
    vids = []
    for src in p.iframes:
        m = VIDEO.search(src)
        if m and m.group(1):
            vids.append({"embedUrl": "https://www.youtube.com/embed/" + m.group(1), "thumbnailUrl": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % m.group(1)})
        elif m:
            vids.append({"embedUrl": "https://player.vimeo.com/video/" + m.group(2)})
    vids += [{"contentUrl": absolute(url, v)} for v in p.videos]
    facts["videos"] = vids[:5]
    return facts, text


def detect_type(facts, existing_types, path, override):
    if override:
        return override, ["--type given"]
    main_types = [t for t in existing_types if t in TYPES and t not in ("Organization", "WebPage")]
    main_types += ["LocalBusiness" for t in existing_types if t in LOCAL_SUBTYPES]
    if main_types:
        return main_types[0], ["existing JSON-LD declares %s" % main_types[0]]
    ev, scores = [], dict.fromkeys(TYPES, 0)
    og = facts["og_type"]
    if og.startswith("article"):
        scores["Article"] += 3; ev.append("og:type article")
    if og.startswith("product"):
        scores["Product"] += 3; ev.append("og:type product")
    if og.startswith("video"):
        scores["VideoObject"] += 3; ev.append("og:type video")
    if og.startswith("profile"):
        scores["ProfilePage"] += 3; ev.append("og:type profile")
    hints = [(r"/(blog|news|articles?|posts?|insights|guides?)/", "Article"), (r"/(products?|p|shop|store|item)/", "Product"),
             (r"/(events?|webinars?)/", "Event"), (r"/(recipes?)/", "Recipe"), (r"/(jobs?|careers?|positions?)/.+", "JobPosting"),
             (r"/(locations?|stores?|branches|offices?)/", "LocalBusiness"), (r"/(authors?|team|people|profile)/", "ProfilePage"),
             (r"/(apps?|download|software)\b", "SoftwareApplication"), (r"/(faqs?|help)\b", "FAQPage")]
    for rx, t in hints:
        if re.search(rx, path, re.I):
            scores[t] += 2; ev.append("URL path suggests %s" % t)
    if facts["price"] and facts["availability"]:
        scores["Product"] += 2; ev.append("price and stock or buy button on the page")
    if facts["address"] and facts["telephone"]:
        scores["LocalBusiness"] += 2; ev.append("street address and phone on the page")
    if len(facts["ingredients"]) >= 3 and facts["instructions"]:
        scores["Recipe"] += 3; ev.append("ingredients and steps lists")
    if facts["date_published"] and facts["words"] > 300:
        scores["Article"] += 1; ev.append("publish date on a text page")
    if len(facts["faqs"]) >= 3 and max(scores.values()) == 0:
        scores["FAQPage"] += 1; ev.append("%d question-and-answer pairs" % len(facts["faqs"]))
    if facts["videos"] and max(scores.values()) == 0:
        scores["VideoObject"] += 1; ev.append("embedded video")
    best = max(scores, key=lambda t: scores[t])
    if scores[best] == 0:
        return ("Organization" if path in ("", "/") else "WebPage"), ["no strong signal; " + ("homepage" if path in ("", "/") else "generic page")]
    return best, ev


def fill(what):
    return "[FILL: %s]" % what


def rec(what):
    return "[FILL (recommended): %s]" % what


def build_graph(t, facts, url, origin, org_name, logo, same_as, local_type=None):
    org_id, site_id, page_id = origin + "/#organization", origin + "/#website", url + "#webpage"
    org = {"@type": "Organization", "@id": org_id, "name": org_name or facts["site_name"] or fill("organization name"), "url": origin + "/"}
    if logo or facts["logo"]:
        org["logo"] = logo or facts["logo"]
    if same_as or facts["same_as"]:
        org["sameAs"] = same_as or facts["same_as"]
    graph = [org]
    is_home = urllib.parse.urlsplit(url).path in ("", "/")
    if is_home:
        graph.append({"@type": "WebSite", "@id": site_id, "name": org["name"], "url": origin + "/", "publisher": {"@id": org_id}})
    page = {"@type": "ProfilePage" if t == "ProfilePage" else "FAQPage" if t == "FAQPage" else "WebPage", "@id": page_id, "url": url,
            "name": facts["title"] or facts["h1"]}
    if facts["description"]:
        page["description"] = facts["description"]
    graph.append(page)
    crumbs = facts["breadcrumbs"]
    if not crumbs and not is_home:
        parts = [x for x in urllib.parse.urlsplit(url).path.split("/") if x]
        crumbs = [{"name": "Home", "item": origin + "/"}] + [
            {"name": (facts["h1"] if i == len(parts) - 1 and facts["h1"] else parts[i].replace("-", " ").replace("_", " ").title()),
             "item": origin + "/" + "/".join(parts[:i + 1])} for i in range(len(parts))]
    if crumbs:
        graph.append({"@type": "BreadcrumbList", "@id": url + "#breadcrumb", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": c["name"], "item": c["item"]} for i, c in enumerate(crumbs)]})
        page["breadcrumb"] = {"@id": url + "#breadcrumb"}
    main = None
    name = facts["h1"] or facts["title"]
    if t in ("Article", "BlogPosting", "NewsArticle"):
        main = {"@type": t, "headline": name, "description": facts["description"] or None,
                "image": facts["image"] or rec("image URL, at least 1200 px wide"),
                "datePublished": facts["date_published"] or rec("publish date, ISO 8601"),
                "dateModified": facts["date_modified"] or facts["date_published"] or rec("last modified date, ISO 8601"),
                "author": {"@type": "Person", "name": facts["author"] or rec("author's name"), "url": rec("author profile URL")},
                "publisher": {"@id": org_id}, "mainEntityOfPage": {"@id": page_id}}
    elif t == "Product":
        offer = {"@type": "Offer", "url": url, "price": facts["price"] or fill("price as a number"),
                 "priceCurrency": facts["currency"] or fill("ISO 4217 currency code such as USD"),
                 "availability": facts["availability"] or rec("https://schema.org/InStock or OutOfStock")}
        main = {"@type": "Product", "name": name, "description": facts["description"] or None,
                "image": facts["image"] or rec("product image URL"), "brand": {"@type": "Brand", "name": org["name"]}, "offers": offer}
        if facts["rating"]:
            main["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": facts["rating"]["value"], "reviewCount": facts["rating"]["count"]}
    elif t == "LocalBusiness":
        addr = facts["address"] or {}
        main = {"@type": local_type or "LocalBusiness", "@id": url + "#localbusiness", "name": org["name"], "url": url,
                "image": facts["image"] or None, "telephone": facts["telephone"] or rec("phone with country code"),
                "address": {"@type": "PostalAddress",
                            "streetAddress": addr.get("streetAddress") or fill("street address"),
                            "addressLocality": addr.get("addressLocality") or fill("city"),
                            "addressRegion": addr.get("addressRegion") or fill("state or region"),
                            "postalCode": addr.get("postalCode") or fill("postal code"),
                            "addressCountry": "US" if addr.get("postalCode") else fill("two-letter country code such as US")},
                "openingHoursSpecification": rec("opening hours, e.g. [{\"@type\":\"OpeningHoursSpecification\",\"dayOfWeek\":[\"Monday\"],\"opens\":\"09:00\",\"closes\":\"17:00\"}]"),
                "parentOrganization": {"@id": org_id}}
        if facts["geo"]:
            main["geo"] = {"@type": "GeoCoordinates", **facts["geo"]}
    elif t == "SoftwareApplication":
        main = {"@type": "SoftwareApplication", "name": name, "description": facts["description"] or None,
                "applicationCategory": rec("e.g. BusinessApplication"), "operatingSystem": rec("e.g. Windows, macOS, Web"),
                "offers": {"@type": "Offer", "price": facts["price"] or fill("price as a number, 0 if free"),
                           "priceCurrency": facts["currency"] or fill("ISO 4217 currency code")},
                "aggregateRating": ({"@type": "AggregateRating", "ratingValue": facts["rating"]["value"], "ratingCount": facts["rating"]["count"]}
                                    if facts["rating"] else fill("aggregateRating from real reviews, or remove it (then no rich result)"))}
    elif t == "Event":
        main = {"@type": "Event", "name": name, "description": facts["description"] or None, "image": facts["image"] or None,
                "startDate": fill("start date and time, ISO 8601 with timezone"),
                "endDate": rec("end date and time, ISO 8601"), "eventStatus": "https://schema.org/EventScheduled",
                "eventAttendanceMode": rec("https://schema.org/OfflineEventAttendanceMode, OnlineEventAttendanceMode or MixedEventAttendanceMode"),
                "location": {"@type": "Place", "name": fill("venue name"), "address": fill("venue address")} if not facts["address"] else
                {"@type": "Place", "name": fill("venue name"), "address": {"@type": "PostalAddress", **{k: v for k, v in facts["address"].items() if k != "text"}}},
                "organizer": {"@id": org_id},
                "offers": {"@type": "Offer", "url": url, "price": facts["price"] or rec("ticket price, 0 if free"),
                           "priceCurrency": facts["currency"] or rec("ISO 4217 currency code"), "availability": "https://schema.org/InStock"}}
    elif t == "Recipe":
        main = {"@type": "Recipe", "name": name, "image": facts["image"] or fill("photo of the finished dish"),
                "description": facts["description"] or None, "author": {"@type": "Person", "name": facts["author"] or rec("author's name")},
                "datePublished": facts["date_published"] or None, "recipeIngredient": facts["ingredients"] or rec("ingredient list"),
                "recipeInstructions": [{"@type": "HowToStep", "text": s} for s in facts["instructions"]] or rec("steps"),
                "totalTime": rec("ISO 8601 duration such as PT45M")}
    elif t == "VideoObject":
        v = facts["videos"][0] if facts["videos"] else {}
        main = {"@type": "VideoObject", "name": name, "description": facts["description"] or rec("video description"),
                "thumbnailUrl": v.get("thumbnailUrl") or facts["image"] or fill("thumbnail URL"),
                "uploadDate": fill("upload date, ISO 8601 with timezone"), **{k: x for k, x in v.items() if k != "thumbnailUrl"}}
    elif t == "JobPosting":
        main = {"@type": "JobPosting", "title": name, "description": fill("the full job description as shown on the page (HTML allowed)"),
                "datePosted": facts["date_published"] or fill("date posted, ISO 8601"), "validThrough": rec("closing date, ISO 8601"),
                "hiringOrganization": {"@id": org_id}, "employmentType": rec("FULL_TIME, PART_TIME or CONTRACTOR"),
                "jobLocation": {"@type": "Place", "address": fill("PostalAddress of the workplace, or use jobLocationType TELECOMMUTE")}}
    elif t == "ProfilePage":
        page["mainEntity"] = {"@type": "Person", "name": name, "description": facts["description"] or None,
                              "image": facts["image"] or None, "sameAs": facts["same_as_content"] or None}
    elif t == "FAQPage":
        page["mainEntity"] = [{"@type": "Question", "name": f["question"], "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}}
                              for f in facts["faqs"]] or fill("visible questions and answers")
    if main:
        main = {k: v for k, v in main.items() if v is not None}
        main.setdefault("@id", url + "#" + t.lower())
        page["mainEntity"] = {"@id": main["@id"]}
        graph.append(main)
    return {"@context": "https://schema.org", "@graph": graph}


def merge_existing(gen, old, drop=()):
    """Fill placeholders from the page's existing markup, then keep any extra properties it already had."""
    if isinstance(old, list):
        old = next((x for x in old if isinstance(x, dict)), {})
    if not isinstance(old, dict):
        return gen
    for k, v in list(gen.items()):
        have = old.get(k)
        if have in (None, "", [], {}) or k in ("@id", "@type"):
            continue
        if isinstance(v, str) and v.startswith("[FILL") and not (isinstance(have, str) and have.startswith("[")):
            gen[k] = have
        elif isinstance(v, dict) and isinstance(have, (dict, list)):
            merge_existing(v, have)
    for k, have in old.items():
        if k not in gen and k not in ("@context", "@id") and k not in drop and have not in (None, "", [], {}):
            gen[k] = have
    return gen


def placeholders(node, path="$"):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += placeholders(v, "%s.%s" % (path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += placeholders(v, "%s[%d]" % (path, i))
    elif isinstance(node, str) and node.startswith("[FILL"):
        required = node.startswith("[FILL:")
        out.append({"path": path, "needs": node.split(":", 1)[1].strip()[:-1], "required": required})
    return out


def analyze(src_url, html, override, org_name, logo, same_as):
    p = PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    blocks, parse_errors = jsonld_blocks(p.jsonld)
    sp = urllib.parse.urlsplit(src_url)
    origin = "%s://%s" % (sp.scheme, sp.netloc)
    facts, text = extract_facts(p, src_url, blocks)
    issues, existing_types = validate(blocks, text, src_url)
    t, evidence = detect_type(facts, existing_types, sp.path, override)
    local_type = next((x for x in existing_types if x in LOCAL_SUBTYPES), None) if t == "LocalBusiness" else None
    graph = build_graph(t, facts, src_url, origin, org_name, logo, same_as, local_type)
    nodes = [n for b in blocks for path, n in walk(b) if re.match(r"^\$(\[\d+\])?(\.@graph\[\d+\])?$", path)]
    wanted = {t} | ({local_type} if local_type else set()) | ({"LocalBusiness"} | LOCAL_SUBTYPES if t == "LocalBusiness" else set())
    for gen in graph["@graph"]:
        gtypes = set(types_of(gen))
        match = next((n for n in nodes if set(types_of(n)) & (wanted if gtypes & wanted else gtypes)), None)
        if match is not None and gen["@type"] not in ("WebPage", "BreadcrumbList", "WebSite"):
            merge_existing(gen, match, drop=("aggregateRating", "review") if gtypes & SELF_SERVING else ())
    # Existing markup that already describes the main entity well is better fixed than replaced (it may be richer,
    # for example a ProductGroup with variants).
    covers = wanted | ({"ProductGroup"} if t == "Product" else set())
    current = next((n for n in nodes if set(types_of(n)) & covers), None)
    if current is None:
        recommendation = "add"
    elif not any(missing_required(current, x) for x in types_of(current)):
        recommendation = "keep_existing_and_fix"
    else:
        recommendation = "replace"
    gen_issues, _ = validate([graph], text, src_url)
    holes = placeholders(graph)
    to_confirm = []
    if facts["price_from_text"]:
        to_confirm.append("price %s %s was read from the page text; confirm it is this item's price and currency" % (facts["price"], facts["currency"]))
    if facts["rating"]:
        to_confirm.append("rating %s from %s reviews was read from the page text; confirm it comes from real, visible reviews" % (facts["rating"]["value"], facts["rating"]["count"]))
    if t == "LocalBusiness" and (facts["address"] or {}).get("postalCode"):
        to_confirm.append("address parsed as a US address (addressCountry US): %s" % ", ".join(v for k, v in facts["address"].items() if k != "text"))
    if facts["site_name"] == "" and not org_name:
        to_confirm.append("organization name not found; pass --org-name")
    return {
        "url": src_url, "status": "ok",
        "detected_type": t, "evidence": evidence, "recommendation": recommendation,
        "existing": {"jsonld_blocks": len(p.jsonld), "types": existing_types, "microdata": p.microdata, "rdfa": p.rdfa,
                     "issues": parse_errors + issues},
        "facts": {k: v for k, v in facts.items() if k not in ("words", "same_as_content")},
        "jsonld": graph,
        "placeholders": holes,
        "to_confirm": to_confirm,
        "generated_checks": [i for i in gen_issues if i["code"] != "PLACEHOLDER_TEXT"],
        "google_rich_result": None if t in ("WebPage", "Organization") else (t not in NO_RICH_RESULT),
        "rich_result_note": NO_RICH_RESULT.get(t),
        "ready": not any(h["required"] for h in holes) and not to_confirm,
        "snippet": '<script type="application/ld+json">\n%s\n</script>' % json.dumps(graph, indent=2, ensure_ascii=False),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", default=[], help="Live page (repeat or comma-separate, up to 10)")
    ap.add_argument("--html", help="Local HTML file for a page that is not live yet (use with --page-url)")
    ap.add_argument("--page-url", help="The URL the --html page will have")
    ap.add_argument("--type", choices=TYPES, help="Force the main entity type instead of detecting it")
    ap.add_argument("--org-name")
    ap.add_argument("--logo")
    ap.add_argument("--same-as", action="append", default=[], help="Official profile URL (repeatable)")
    args = ap.parse_args()
    jobs = []
    if args.html:
        if not args.page_url or not re.match(r"^https?://[^/\s]+", args.page_url):
            fail("INPUT_INVALID", "--html needs --page-url, the absolute URL the page will have.")
        try:
            with open(args.html, encoding="utf-8") as f:
                jobs.append((args.page_url, f.read()))
        except OSError as e:
            fail("HTML_UNREADABLE", str(e))
    urls = list(dict.fromkeys(u.strip() for a in args.url for u in a.split(",") if u.strip()))[:10]
    if not urls and not jobs:
        fail("INPUT_INVALID", "Pass --url or --html.")
    if any(not re.match(r"^https?://[^/\s]+", u) for u in urls):
        fail("INPUT_INVALID", "Each --url must be an absolute http(s) URL.")
    if args.logo and not re.match(r"^https?://", args.logo):
        fail("INPUT_INVALID", "--logo must be an absolute URL.")
    pages = []
    for u in urls:
        status, final, ctype, html = fetch(u)
        if status != 200 or "html" not in ctype.lower():
            pages.append({"url": u, "status": "unreachable", "http_status": status})
            continue
        jobs.append((final, html))
    for page_url, html in jobs:
        pages.append(analyze(page_url, html, args.type, args.org_name, args.logo, args.same_as))
    if all(pg["status"] != "ok" for pg in pages):
        fail("PAGES_UNREACHABLE", "None of the pages could be fetched as HTML.", pages=pages)
    json.dump({"status": "ok", "generated": datetime.date.today().isoformat(), "pages": pages}, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
