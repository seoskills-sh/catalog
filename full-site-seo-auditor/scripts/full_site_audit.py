#!/usr/bin/env python3
"""Full-Site SEO Auditor: reference implementation.

Crawls a site (from its sitemap, else by following internal links), checks every
page's crawlability, indexability, title and meta, headings, images, content
depth, structured data, HTTPS and mobile signals, checks robots.txt and the
sitemap, finds site-wide duplicates and unlinked pages, then scores each area
and ranks the fixes.

Auth:   none. --psi adds Google PageSpeed Insights for the homepage (keyless,
        small shared quota; set PSI_API_KEY for your own quota).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 full_site_audit.py --site https://example.com [--max-pages 50] [--psi]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

UA = "seoskills-full-site-auditor/1.0 (+https://seoskills.sh)"
MAX_BODY = 2_000_000
PSI = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
SEVERITY_WEIGHT = {"critical": 1.0, "high": 0.6, "medium": 0.3, "low": 0.1}
AREA_WEIGHT = {"crawlability": 20, "indexability": 20, "on_page": 25, "content": 15,
               "structured_data": 5, "security_mobile": 10, "performance": 5}

# code -> (area, severity, fix). One place for every check's meaning.
CHECKS = {
    "ROBOTS_BLOCKS_SITE": ("crawlability", "critical", "robots.txt disallows the whole site for Googlebot. Remove the blanket 'Disallow: /' unless the site must stay out of search."),
    "ROBOTS_MISSING": ("crawlability", "low", "Add a robots.txt (even 'User-agent: *' with no rules) and list the sitemap in it."),
    "SITEMAP_MISSING": ("crawlability", "medium", "Publish an XML sitemap and reference it in robots.txt with a 'Sitemap:' line."),
    "SITEMAP_NOT_IN_ROBOTS": ("crawlability", "low", "Add a 'Sitemap: <url>' line to robots.txt so crawlers find it without guessing."),
    "PAGE_BLOCKED_BY_ROBOTS": ("crawlability", "high", "This page is in the sitemap or linked internally but robots.txt blocks it. Unblock it, or drop it from the sitemap and links."),
    "HTTP_4XX": ("crawlability", "high", "The page returns a client error. Fix or redirect the URL, and update the links and sitemap entries that point to it."),
    "HTTP_5XX": ("crawlability", "critical", "The page returns a server error. Check the server logs; repeated 5xx responses make Google crawl less."),
    "UNREACHABLE": ("crawlability", "high", "The page timed out or the connection failed. Check hosting, firewalls and bot protection for crawler traffic."),
    "REDIRECT_CHAIN": ("crawlability", "medium", "More than one redirect hop. Point links and the sitemap at the final URL, and make each old URL redirect there in one hop."),
    "SITEMAP_URL_REDIRECTS": ("crawlability", "medium", "A sitemap URL redirects. List the final URL in the sitemap instead."),
    "SITEMAP_URL_NOT_200": ("crawlability", "high", "A sitemap URL does not return 200. Remove it from the sitemap or fix the page."),
    "NOINDEX": ("indexability", "high", "The page carries noindex (meta robots or X-Robots-Tag). Remove it if the page should rank."),
    "NOINDEX_IN_SITEMAP": ("indexability", "critical", "A noindex page is listed in the sitemap, sending Google mixed signals. Remove noindex or drop the URL from the sitemap."),
    "CANONICAL_MISSING": ("indexability", "low", "Add a self-referencing <link rel=\"canonical\"> so duplicate variants (parameters, trailing slashes) consolidate to this URL."),
    "CANONICAL_OTHER_URL": ("indexability", "medium", "The canonical points to a different URL, so this page will not rank itself. Confirm that is intended."),
    "CANONICAL_MULTIPLE": ("indexability", "high", "The page has more than one canonical tag; Google may ignore all of them. Keep one."),
    "TITLE_MISSING": ("on_page", "critical", "Add a unique <title> that leads with the page's main topic."),
    "TITLE_TOO_LONG": ("on_page", "low", "Shorten the title to about 60 characters so it does not truncate in results."),
    "TITLE_TOO_SHORT": ("on_page", "low", "The title is very short. Describe the page's topic and add a qualifier searchers use."),
    "TITLE_DUPLICATE": ("on_page", "high", "Several pages share this title. Give each page a title that names what is unique about it."),
    "META_MISSING": ("on_page", "medium", "Add a meta description (about 120 to 155 characters) that summarises the page and invites the click."),
    "META_TOO_LONG": ("on_page", "low", "Trim the meta description to about 155 characters so the key message is not cut off."),
    "META_DUPLICATE": ("on_page", "medium", "Several pages share this meta description. Write one per page."),
    "H1_MISSING": ("on_page", "medium", "Add one <h1> that states the page's topic."),
    "H1_MULTIPLE": ("on_page", "low", "The page has several <h1> elements. Keep one main heading and use <h2> for sections."),
    "IMG_ALT_MISSING": ("on_page", "low", "Add alt text to meaningful images (empty alt=\"\" is fine for decorative ones)."),
    "THIN_CONTENT": ("content", "medium", "The page has under 250 words of visible text. Expand it with what a searcher needs, or merge it into a stronger page."),
    "NO_INTERNAL_LINKS_OUT": ("content", "low", "The page links to no other page on the site. Add contextual links to related pages."),
    "NOT_LINKED_INTERNALLY": ("content", "medium", "No crawled page links here (an orphan within the crawl). Link to it from related pages and navigation."),
    "SCHEMA_MISSING": ("structured_data", "low", "No JSON-LD found. Add the schema type that matches the page (Organization, Article, Product, LocalBusiness, BreadcrumbList)."),
    "SCHEMA_INVALID_JSON": ("structured_data", "high", "A JSON-LD block does not parse, so search engines ignore it. Fix the JSON syntax."),
    "NOT_HTTPS": ("security_mobile", "critical", "The page is served over HTTP. Serve every page over HTTPS."),
    "HTTP_NOT_REDIRECTED": ("security_mobile", "high", "http:// does not redirect to https://. Add a site-wide 301 redirect to HTTPS."),
    "MIXED_CONTENT": ("security_mobile", "medium", "An HTTPS page loads resources over plain HTTP. Switch those URLs to HTTPS."),
    "VIEWPORT_MISSING": ("security_mobile", "high", "No mobile viewport meta tag. Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."),
    "LANG_MISSING": ("security_mobile", "low", "Add a lang attribute to <html> (for example lang=\"en\")."),
    "SLOW_RESPONSE": ("performance", "medium", "The HTML took over 1.5 seconds to arrive. Check server response time and caching."),
    "HEAVY_HTML": ("performance", "low", "The HTML document is over 500 KB. Trim inline scripts, styles and data."),
    "PSI_POOR": ("performance", "high", "PageSpeed Insights rates mobile performance under 50. Start with its top opportunities (images, render-blocking resources, JavaScript)."),
}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({"status": code, "to": urllib.parse.urljoin(req.full_url, newurl)})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=15):
    """GET following redirects; returns status, final URL, redirect chain, headers, body, seconds."""
    handler = _Redirects()
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    start = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(MAX_BODY)
            return {"status": r.status, "final_url": r.geturl(), "chain": handler.chain,
                    "headers": {k.lower(): v for k, v in r.headers.items()}, "body": body,
                    "seconds": round(time.monotonic() - start, 3)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final_url": e.geturl() or url, "chain": handler.chain,
                "headers": {k.lower(): v for k, v in (e.headers or {}).items()}, "body": b"",
                "seconds": round(time.monotonic() - start, 3)}
    except Exception as e:  # timeout, DNS, TLS
        return {"status": 0, "final_url": url, "chain": handler.chain, "headers": {}, "body": b"",
                "seconds": round(time.monotonic() - start, 3), "error": type(e).__name__}


# ---------- robots.txt (Google-style matching: longest rule wins, Allow wins ties) ----------

def parse_robots(text):
    groups, agents, rules, sitemaps, last_was_agent = [], [], [], [], False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (p.strip() for p in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent and agents:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if key in ("allow", "disallow"):
            rules.append((key, value))
        elif key == "sitemap":
            sitemaps.append(value)
    if agents:
        groups.append((agents, rules))
    return groups, sitemaps


def _rule_matches(pattern, path):
    if pattern == "":
        return False
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


def robots_allows(groups, agent, path):
    agent = agent.lower()
    chosen = [r for a, r in groups if any(x != "*" and x in agent for x in a)]
    if not chosen:
        chosen = [r for a, r in groups if "*" in a]
    best = None  # (length, is_allow)
    for rules in chosen:
        for kind, pattern in rules:
            if _rule_matches(pattern, path):
                cand = (len(pattern), kind == "allow")
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1]):
                    best = cand
    return True if best is None else best[1]


# ---------- sitemap discovery ----------

def sitemap_urls(start_urls, limit, max_files=15):
    seen_files, urls, queue = set(), [], deque(start_urls)
    while queue and len(seen_files) < max_files and len(urls) < limit:
        sm = queue.popleft()
        if sm in seen_files:
            continue
        seen_files.add(sm)
        res = fetch(sm)
        if res["status"] != 200 or not res["body"]:
            continue
        xml = res["body"].decode("utf-8", "ignore")
        locs = [html_unescape(l.strip()) for l in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml, re.I)]
        if re.search(r"<sitemapindex[\s>]", xml, re.I):
            queue.extend(locs)
        else:
            for loc in locs:
                if loc not in urls:
                    urls.append(loc)
                if len(urls) >= limit:
                    break
    return urls, sorted(seen_files)


def html_unescape(s):
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")


# ---------- page parsing ----------

class PageParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.in_title, self._svg = "", False, 0
        self.meta, self.canonicals, self.links, self.lang = {}, [], [], None
        self.h1, self.h2, self.imgs_no_alt, self.imgs = [], [], 0, 0
        self.jsonld, self._in_jsonld, self._buf = [], False, []
        self.text_words, self._skip, self._heading = 0, 0, None
        self.http_resources = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        elif tag == "svg":
            self._svg += 1
        elif tag == "title" and not self._svg and not self.title:
            self.in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel:
                self.canonicals.append(a.get("href", ""))
            if "stylesheet" in rel and a.get("href", "").startswith("http://"):
                self.http_resources += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img":
            self.imgs += 1
            if "alt" not in a:
                self.imgs_no_alt += 1
            if a.get("src", "").startswith("http://"):
                self.http_resources += 1
        elif tag == "script":
            if a.get("src", "").startswith("http://"):
                self.http_resources += 1
            if a.get("type", "").lower() == "application/ld+json":
                self._in_jsonld, self._buf = True, []
            else:
                self._skip += 1
        elif tag in ("h1", "h2"):
            self._heading, self._buf = tag, []
        if tag in self.SKIP and tag != "script":
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "svg" and self._svg:
            self._svg -= 1
        elif tag == "script":
            if self._in_jsonld:
                self.jsonld.append("".join(self._buf))
                self._in_jsonld = False
            elif self._skip:
                self._skip -= 1
        elif tag in ("h1", "h2") and self._heading == tag:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            (self.h1 if tag == "h1" else self.h2).append(text)
            self._heading = None
        if tag in self.SKIP and tag != "script" and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self._in_jsonld or self._heading:
            self._buf.append(data)
        if not self._skip and not self._in_jsonld:
            self.text_words += len(re.findall(r"[^\W\d_]{2,}", data))


# ---------- auditing ----------

def normalize(url):
    p = urllib.parse.urlsplit(url)
    path = p.path or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def audit_page(url, groups, in_sitemap):
    res = fetch(url)
    page = {"url": url, "status": res["status"], "final_url": res["final_url"], "seconds": res["seconds"],
            "redirects": len(res["chain"]), "issues": []}
    add = page["issues"].append
    path = urllib.parse.urlsplit(url).path or "/"
    if groups is not None and not robots_allows(groups, "Googlebot", path):
        add("PAGE_BLOCKED_BY_ROBOTS")
    if res["status"] == 0:
        add("UNREACHABLE")
        return page, None
    if len(res["chain"]) > 1:
        add("REDIRECT_CHAIN")
    if in_sitemap and res["chain"]:
        add("SITEMAP_URL_REDIRECTS")
    if res["status"] >= 500:
        add("HTTP_5XX")
    elif res["status"] >= 400:
        add("HTTP_4XX")
    if in_sitemap and res["status"] != 200:
        add("SITEMAP_URL_NOT_200")
    ctype = res["headers"].get("content-type", "")
    if res["status"] != 200 or "html" not in ctype:
        return page, None
    html = res["body"].decode("utf-8", "ignore")
    parser = PageParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    final = res["final_url"]
    title = re.sub(r"\s+", " ", parser.title).strip()
    meta = parser.meta.get("description", "").strip()
    robots_meta = (parser.meta.get("robots", "") + "," + parser.meta.get("googlebot", "")).lower()
    noindex = "noindex" in robots_meta or "noindex" in res["headers"].get("x-robots-tag", "").lower()
    internal = set()
    host = urllib.parse.urlsplit(final).netloc.lower()
    for href in parser.links:
        absu = urllib.parse.urljoin(final, href.strip())
        sp = urllib.parse.urlsplit(absu)
        if sp.scheme in ("http", "https") and sp.netloc.lower() == host:
            internal.add(normalize(absu))
    schema_types, schema_bad = [], 0
    for block in parser.jsonld:
        try:
            data = json.loads(block)
        except ValueError:
            schema_bad += 1
            continue
        for node in (data.get("@graph", [data]) if isinstance(data, dict) else data if isinstance(data, list) else []):
            if isinstance(node, dict):
                t = node.get("@type")
                schema_types.extend(t if isinstance(t, list) else [t] if t else [])
    canon = [urllib.parse.urljoin(final, c) for c in parser.canonicals if c]
    page.update({
        "title": title, "title_chars": len(title), "meta_description_chars": len(meta),
        "h1_count": len(parser.h1), "h1": parser.h1[:1], "words": parser.text_words,
        "canonical": canon[0] if canon else None, "noindex": noindex, "lang": parser.lang,
        "internal_links_out": len(internal - {normalize(final)}), "images": parser.imgs,
        "images_missing_alt": parser.imgs_no_alt, "schema_types": sorted(set(map(str, schema_types))),
        "bytes": len(res["body"]),
    })
    if not title:
        add("TITLE_MISSING")
    elif len(title) > 65:
        add("TITLE_TOO_LONG")
    elif len(title) < 15:
        add("TITLE_TOO_SHORT")
    if not meta:
        add("META_MISSING")
    elif len(meta) > 160:
        add("META_TOO_LONG")
    if not parser.h1:
        add("H1_MISSING")
    elif len(parser.h1) > 1:
        add("H1_MULTIPLE")
    if parser.imgs_no_alt:
        add("IMG_ALT_MISSING")
    if noindex:
        add("NOINDEX_IN_SITEMAP" if in_sitemap else "NOINDEX")
    if not canon:
        add("CANONICAL_MISSING")
    elif len(set(canon)) > 1:
        add("CANONICAL_MULTIPLE")
    elif normalize(canon[0]) != normalize(final):
        add("CANONICAL_OTHER_URL")
    if parser.text_words < 250 and not noindex:
        add("THIN_CONTENT")
    if not (internal - {normalize(final)}):
        add("NO_INTERNAL_LINKS_OUT")
    if schema_bad:
        add("SCHEMA_INVALID_JSON")
    elif not schema_types:
        add("SCHEMA_MISSING")
    if final.startswith("http://"):
        add("NOT_HTTPS")
    elif parser.http_resources:
        add("MIXED_CONTENT")
    if "viewport" not in parser.meta:
        add("VIEWPORT_MISSING")
    if not parser.lang:
        add("LANG_MISSING")
    if res["seconds"] > 1.5:
        add("SLOW_RESPONSE")
    if len(res["body"]) > 500_000:
        add("HEAVY_HTML")
    return page, {"title": title, "meta": meta, "internal": internal}


def run_psi(url):
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if os.environ.get("PSI_API_KEY"):
        params["key"] = os.environ["PSI_API_KEY"]
    res = fetch(PSI + "?" + urllib.parse.urlencode(params), timeout=90)
    if res["status"] != 200:
        return {"status": "unavailable", "http_status": res["status"],
                "note": "PageSpeed Insights quota or error; set PSI_API_KEY for a dedicated quota."}
    try:
        data = json.loads(res["body"])
    except ValueError:
        return {"status": "unavailable", "note": "unreadable PageSpeed Insights response"}
    lh = data.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    field = data.get("loadingExperience", {}).get("metrics", {})
    score = lh.get("categories", {}).get("performance", {}).get("score")
    return {
        "status": "ok", "url": url, "strategy": "mobile",
        "performance_score": round(score * 100) if isinstance(score, (int, float)) else None,
        "lab": {k: audits.get(a, {}).get("numericValue") for k, a in
                (("lcp_ms", "largest-contentful-paint"), ("cls", "cumulative-layout-shift"), ("tbt_ms", "total-blocking-time"))},
        "field_p75": {k: field.get(m, {}).get("percentile") for k, m in
                      (("lcp_ms", "LARGEST_CONTENTFUL_PAINT_MS"), ("inp_ms", "INTERACTION_TO_NEXT_PAINT"),
                       ("cls_x100", "CUMULATIVE_LAYOUT_SHIFT_SCORE"))} if field else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="Site root, e.g. https://example.com")
    ap.add_argument("--max-pages", type=int, default=50, dest="max_pages")
    ap.add_argument("--sitemap", help="Sitemap URL to use instead of discovering one")
    ap.add_argument("--psi", action="store_true", help="Add PageSpeed Insights for the homepage")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    site = args.site.rstrip("/")
    if not re.match(r"^https?://[^/]+", site):
        fail("INPUT_INVALID", "--site must be an absolute http(s) URL such as https://example.com")
    home = fetch(site + "/")
    if home["status"] == 0:
        fail("SITE_UNREACHABLE", "Could not reach %s (%s)." % (site, home.get("error", "connection failed")))
    warnings = []
    if home["status"] in (401, 403, 429, 503):
        warnings.append("The homepage answered HTTP %s to this crawler; the site may block automated requests, "
                        "so error findings may reflect bot protection rather than what Google sees." % home["status"])
    root = urllib.parse.urlsplit(home["final_url"])
    origin = "%s://%s" % (root.scheme, root.netloc)
    site_issues = []

    robots_res = fetch(origin + "/robots.txt")
    groups, robots_sitemaps = None, []
    if robots_res["status"] == 200 and robots_res["body"]:
        groups, robots_sitemaps = parse_robots(robots_res["body"].decode("utf-8", "ignore"))
        if not robots_allows(groups, "Googlebot", "/"):
            site_issues.append("ROBOTS_BLOCKS_SITE")
    else:
        site_issues.append("ROBOTS_MISSING")

    start = [args.sitemap] if args.sitemap else (robots_sitemaps or [origin + "/sitemap.xml"])
    urls, sitemap_files = sitemap_urls(start, max(args.max_pages, 5000))
    if not urls:
        site_issues.append("SITEMAP_MISSING")
    elif not robots_sitemaps and not args.sitemap:
        site_issues.append("SITEMAP_NOT_IN_ROBOTS")
    discovered_via = "sitemap" if urls else "crawl"
    complete = bool(urls) and len(urls) <= args.max_pages

    if root.scheme == "https":
        http_home = fetch("http://%s/" % root.netloc)
        if http_home["status"] and not http_home["final_url"].startswith("https://"):
            site_issues.append("HTTP_NOT_REDIRECTED")

    pages, extras = [], {}
    if urls:
        targets = urls[: args.max_pages]
        with ThreadPoolExecutor(max_workers=max(1, min(args.concurrency, 8))) as pool:
            for page, extra in pool.map(lambda u: audit_page(u, groups, True), targets):
                pages.append(page)
                if extra:
                    extras[page["url"]] = extra
    else:  # breadth-first crawl from the homepage
        queue, seen = deque([normalize(home["final_url"])]), set()
        while queue and len(pages) < args.max_pages:
            u = queue.popleft()
            if u in seen:
                continue
            seen.add(u)
            page, extra = audit_page(u, groups, False)
            pages.append(page)
            if extra:
                extras[u] = extra
                queue.extend(sorted(x for x in extra["internal"] if x not in seen and not re.search(r"\.(pdf|jpe?g|png|gif|webp|zip|xml)$", x, re.I)))
            time.sleep(0.15)

    # Site-wide checks: duplicates and pages nothing in the crawl links to.
    by_title, by_meta, linked = defaultdict(list), defaultdict(list), set()
    for url, ex in extras.items():
        if ex["title"]:
            by_title[ex["title"].lower()].append(url)
        if ex["meta"]:
            by_meta[ex["meta"].lower()].append(url)
        linked |= ex["internal"]
    for group, code in ((by_title, "TITLE_DUPLICATE"), (by_meta, "META_DUPLICATE")):
        for dupes in group.values():
            if len(dupes) > 1:
                for p in pages:
                    if p["url"] in dupes:
                        p["issues"].append(code)
    if complete and len(extras) > 1:
        for p in pages:
            if p["url"] in extras and normalize(p["url"]) not in linked and normalize(p.get("final_url", "")) not in linked \
                    and normalize(p["url"]) != normalize(home["final_url"]):
                p["issues"].append("NOT_LINKED_INTERNALLY")

    psi = run_psi(home["final_url"]) if args.psi else None
    if psi and psi.get("status") == "ok" and isinstance(psi.get("performance_score"), int) and psi["performance_score"] < 50:
        site_issues.append("PSI_POOR")

    # Score: each area starts at its weight and loses severity x share of pages affected.
    n = max(1, len(pages))
    counts = defaultdict(int)
    examples = defaultdict(list)
    for p in pages:
        for code in set(p["issues"]):
            counts[code] += 1
            if len(examples[code]) < 5:
                examples[code].append(p["url"])
    for code in site_issues:
        counts[code] = max(counts[code], n)
    penalty = defaultdict(float)
    for code, c in counts.items():
        area, sev, _ = CHECKS[code]
        penalty[area] += SEVERITY_WEIGHT[sev] * (c / n)
    areas = {a: round(w * max(0.0, 1.0 - penalty[a]), 1) for a, w in AREA_WEIGHT.items()}
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues = sorted(
        ({"code": code, "area": CHECKS[code][0], "severity": CHECKS[code][1],
          "pages_affected": (c if code not in site_issues else None), "site_wide": code in site_issues,
          "example_urls": examples.get(code, []), "fix": CHECKS[code][2]} for code, c in counts.items()),
        key=lambda i: (rank[i["severity"]], -(i["pages_affected"] or n)))
    quick = [i for i in issues if i["code"] in {
        "TITLE_MISSING", "META_MISSING", "H1_MISSING", "CANONICAL_MISSING", "VIEWPORT_MISSING", "LANG_MISSING",
        "SITEMAP_NOT_IN_ROBOTS", "NOINDEX_IN_SITEMAP", "SITEMAP_URL_REDIRECTS", "TITLE_DUPLICATE", "META_DUPLICATE",
        "IMG_ALT_MISSING", "ROBOTS_MISSING", "HTTP_NOT_REDIRECTED"}][:5]

    json.dump({
        "status": "ok",
        "site": origin,
        "pages_audited": len(pages),
        "discovered_via": discovered_via,
        "score": round(sum(areas.values())),
        "area_scores": areas,
        "area_max": AREA_WEIGHT,
        "issues": issues,
        "quick_wins": [{"code": q["code"], "fix": q["fix"]} for q in quick],
        "robots": {"found": groups is not None, "sitemaps_listed": robots_sitemaps,
                   "googlebot_allowed_root": None if groups is None else robots_allows(groups, "Googlebot", "/")},
        "sitemap": {"files_read": sitemap_files, "urls_found": len(urls), "fully_audited": complete},
        "psi": psi,
        "pages": pages,
        "warnings": warnings,
        "limits": {"max_pages": args.max_pages,
                   "note": "Findings cover the audited pages only; raise --max-pages for larger sites."},
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
