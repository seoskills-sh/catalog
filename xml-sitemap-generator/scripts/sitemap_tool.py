#!/usr/bin/env python3
"""XML Sitemap Generator and Auditor: reference implementation.

audit:    finds a site's sitemaps (robots.txt, then common paths), follows
          sitemap indexes, checks every file against the sitemap protocol
          (size, URL count, namespace, URL format, duplicates, lastmod) and
          fetches a sample of the listed URLs to find ones that should not be
          there: errors, redirects, noindex, canonicalized elsewhere, or
          blocked by robots.txt.
generate: builds clean sitemap files from a crawl of the site or a URL list,
          keeping only indexable, self-canonical, 200 pages, with lastmod only
          where the page itself states a real modification date.

Auth:   none. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 sitemap_tool.py audit --site https://example.com [--sitemap URL] [--sample 200]
       python3 sitemap_tool.py generate --site https://example.com --out sitemaps/ [--urls urls.txt] [--max-pages 500] [--gzip]
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, datetime, email.utils, gzip, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from xml.sax.saxutils import escape

UA = "seoskills-sitemap-tool/1.0 (+https://seoskills.sh)"
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
MAX_URLS, MAX_BYTES = 50000, 52428800
W3C = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}))?)?)?$")
COMMON = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml", "/sitemap.xml.gz"]
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|css|js|json|xml|txt|zip|gz|rar|mp4|mp3|mov|woff2?|ttf|eot)$", re.I)
SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
FILE_CHECKS = {
    "SITEMAP_UNREACHABLE": ("critical", "The sitemap URL does not return 200. Fix the URL in robots.txt and Search Console, or serve the file."),
    "NOT_XML": ("critical", "The file is not well-formed XML, so search engines cannot read any URL in it."),
    "WRONG_NAMESPACE": ("high", "Declare xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\" on the urlset or sitemapindex element."),
    "TOO_MANY_URLS": ("high", "A sitemap file may list at most 50,000 URLs. Split it and list the parts in a sitemap index."),
    "TOO_LARGE": ("high", "A sitemap file may be at most 50 MB uncompressed. Split it."),
    "EMPTY_SITEMAP": ("medium", "The file lists no URLs. Remove it from the index or fill it."),
    "NESTED_INDEX": ("medium", "A sitemap index lists another index. The protocol defines an index as a list of sitemaps, so point the top index straight at the sitemap files."),
    "NOT_IN_ROBOTS": ("low", "Add a Sitemap: line to robots.txt so every crawler can find the sitemap."),
}
URL_CHECKS = {
    "INVALID_URL": ("high", "Use absolute, fully qualified URLs (scheme and host)."),
    "CROSS_HOST": ("medium", "URLs on another host need that host's robots.txt to point to this sitemap; otherwise list them in that host's own sitemap."),
    "OUTSIDE_SITEMAP_PATH": ("low", "Google says a sitemap covers only URLs under its own folder unless it is submitted in Search Console. Submit it there, or move the file to the site root."),
    "SCHEME_MISMATCH": ("medium", "List the https:// version of each URL, the one the site redirects to."),
    "DUPLICATE_URL": ("low", "List each URL once."),
    "URL_TOO_LONG": ("low", "Keep URLs under 2,048 characters."),
    "UNESCAPED_CHARACTERS": ("medium", "Escape spaces and special characters in URLs (for example %20)."),
    "LASTMOD_INVALID": ("medium", "Use a W3C datetime for lastmod, such as 2026-10-06 or 2026-10-06T09:30:00+00:00."),
    "LASTMOD_FUTURE": ("medium", "A lastmod in the future is wrong; set it to when the page last changed."),
    "LASTMOD_ALL_SAME": ("medium", "Every URL has the same lastmod, which usually means the generation time. Google uses lastmod only when it is consistently accurate, so use each page's real modification date or leave it out."),
    "PRIORITY_CHANGEFREQ": ("info", "Google ignores priority and changefreq; they can stay, but they do nothing for Google."),
}
SAMPLE_CHECKS = {
    "NON_200": ("high", "Remove URLs that return errors, or fix the pages."),
    "NO_RESPONSE": ("medium", "These URLs did not answer (timeout or connection error), even on a retry. Recheck them before removing anything."),
    "REDIRECTED": ("medium", "List the final URL instead of the one that redirects."),
    "NOINDEX": ("high", "Remove noindex pages from the sitemap, or drop the noindex if they should rank."),
    "NON_CANONICAL": ("medium", "List only canonical URLs; these pages name a different URL as canonical."),
    "BLOCKED_BY_ROBOTS": ("high", "These URLs are disallowed in robots.txt. Unblock them, or remove them from the sitemap."),
}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append(code)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=20, limit=60_000_000):
    handler = _Redirects()
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*;q=0.8", "Accept-Encoding": "gzip"})
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(limit)
            if r.headers.get("Content-Encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            return {"status": r.status, "final": r.geturl(), "redirects": handler.chain,
                    "headers": {k.lower(): v for k, v in r.headers.items()}, "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final": url, "redirects": handler.chain, "headers": {k.lower(): v for k, v in (e.headers or {}).items()}, "body": b""}
    except Exception:
        return {"status": 0, "final": url, "redirects": handler.chain, "headers": {}, "body": b""}


# ---------- robots.txt (Google-style matching: longest rule wins, Allow wins a tie) ----------

def parse_robots(text):
    groups, agents, rules, sitemaps, last = [], [], [], [], False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (p.strip() for p in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if not last and agents:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.lower())
            last = True
            continue
        last = False
        if k in ("allow", "disallow"):
            rules.append((k, v))
        elif k == "sitemap":
            sitemaps.append(v)
    if agents:
        groups.append((agents, rules))
    return groups, sitemaps


def allowed(groups, path, agent="googlebot"):
    chosen = [r for a, r in groups if any(x != "*" and x in agent for x in a)] or [r for a, r in groups if "*" in a]
    best = None
    for rules in chosen:
        for kind, pat in rules:
            if not pat:
                continue
            rx = re.escape(pat).replace(r"\*", ".*")
            rx = rx[:-2] + "$" if rx.endswith(r"\$") else rx
            if re.match(rx, path):
                cand = (len(pat), kind == "allow")
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1]):
                    best = cand
    return True if best is None else best[1]


def path_of(url):
    sp = urllib.parse.urlsplit(url)
    return (sp.path or "/") + ("?" + sp.query if sp.query else "")


class PageInfo(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.robots, self.canonical, self.links, self.dates = "", None, [], []
        self._jsonld, self._buf = False, []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("robots", "googlebot"):
                self.robots += "," + a.get("content", "").lower()
            elif name in ("article:modified_time", "og:updated_time"):
                self.dates.append(a.get("content", ""))
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = a.get("href", "")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._jsonld, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "script" and self._jsonld:
            self.dates += re.findall(r'"dateModified"\s*:\s*"([^"]+)"', "".join(self._buf))
            self._jsonld = False

    def handle_data(self, data):
        if self._jsonld:
            self._buf.append(data)


def norm(url):
    sp = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((sp.scheme.lower(), sp.netloc.lower(), sp.path or "/", sp.query, "")).rstrip("/")


def inspect(url, groups):
    """Fetch one URL and classify it for sitemap inclusion."""
    r = fetch(url, limit=3_000_000)
    if r["status"] in (0, 429, 500, 502, 503, 504):  # transient failures get one retry before they count
        time.sleep(1.5)
        r = fetch(url, limit=3_000_000)
    info = {"url": url, "status": r["status"], "final": r["final"], "redirected": bool(r["redirects"]), "issues": []}
    if r["status"] != 200:
        info["issues"].append("NO_RESPONSE" if r["status"] == 0 else "NON_200")
        return info, None
    if r["redirects"] and norm(r["final"]) != norm(url):
        info["issues"].append("REDIRECTED")
    p = PageInfo()
    ctype = r["headers"].get("content-type", "")
    if "html" in ctype:
        try:
            p.feed(r["body"].decode("utf-8", "ignore"))
        except Exception:
            pass
    robots = p.robots + "," + r["headers"].get("x-robots-tag", "").lower()
    if "noindex" in robots or re.search(r"(^|[,\s])none([,\s]|$)", robots):
        info["issues"].append("NOINDEX")
    if p.canonical:
        canon = urllib.parse.urljoin(r["final"], p.canonical)
        info["canonical"] = canon
        if norm(canon) != norm(r["final"]):
            info["issues"].append("NON_CANONICAL")
    if not allowed(groups, path_of(url)):
        info["issues"].append("BLOCKED_BY_ROBOTS")
    lastmod = None
    for d in p.dates:
        d = d.strip()
        if W3C.match(d) or re.match(r"^\d{4}-\d{2}-\d{2}", d):
            lastmod = d if W3C.match(d) else d[:10]
            break
    if not lastmod and r["headers"].get("last-modified"):
        try:
            lm = email.utils.parsedate_to_datetime(r["headers"]["last-modified"])
            if (datetime.datetime.now(datetime.timezone.utc) - lm).total_seconds() > 86400:  # a "now" header is the render time, not a change
                lastmod = lm.date().isoformat()
        except (TypeError, ValueError):
            pass
    info["lastmod"] = lastmod
    return info, p


def discover(origin, explicit):
    r = fetch(origin + "/robots.txt")
    robots_text = r["body"].decode("utf-8", "ignore") if r["status"] == 200 else ""
    groups, listed = parse_robots(robots_text)
    roots = [explicit] if explicit else listed
    if not roots:
        for path in COMMON:
            probe = fetch(origin + path, limit=2_000_000)
            if probe["status"] == 200 and probe["body"].lstrip()[:200].lower().find(b"<") != -1 and (b"urlset" in probe["body"][:3000] or b"sitemapindex" in probe["body"][:3000]):
                roots = [origin + path]
                break
    return groups, listed, roots, r["status"] == 200


def audit(args):
    sp = urllib.parse.urlsplit(args.site)
    origin = "%s://%s" % (sp.scheme, sp.netloc)
    host = sp.netloc.lower().removeprefix("www.")
    groups, listed, roots, robots_found = discover(origin, args.sitemap)
    if not roots:
        fail("NO_SITEMAP", "No sitemap found in robots.txt or at the common paths (%s). Run generate to build one." % ", ".join(COMMON),
             robots_txt_found=robots_found)
    files, issues = [], collections.defaultdict(lambda: {"count": 0, "examples": []})
    urls, seen_urls, lastmods = [], collections.Counter(), []
    queue, visited = [(u, False) for u in roots], set()

    def add(code, example):
        issues[code]["count"] += 1
        if len(issues[code]["examples"]) < 5:
            issues[code]["examples"].append(example)

    today = datetime.date.today().isoformat()
    while queue and len(visited) < args.max_files:
        sm, from_index = queue.pop(0)
        if sm in visited:
            continue
        visited.add(sm)
        r = fetch(sm)
        entry = {"url": sm, "status": r["status"], "type": None, "urls": 0, "bytes": len(r["body"])}
        files.append(entry)
        if r["status"] != 200:
            add("SITEMAP_UNREACHABLE", sm)
            continue
        try:
            root = ET.fromstring(r["body"])
        except ET.ParseError:
            add("NOT_XML", sm)
            continue
        tag = root.tag.split("}")[-1]
        entry["type"] = tag
        if not root.tag.startswith("{%s}" % NS):
            add("WRONG_NAMESPACE", sm)
        if len(r["body"]) > MAX_BYTES:
            add("TOO_LARGE", sm)
        child = lambda el, name: next((c for c in el if c.tag.split("}")[-1] == name), None)
        locs = [(child(e, "loc"), e) for e in list(root)]
        if tag == "sitemapindex":
            if from_index:
                add("NESTED_INDEX", sm)
            for loc, _ in locs:
                if loc is not None and loc.text:
                    queue.append((loc.text.strip(), True))
            entry["urls"] = len(locs)
            continue
        entry["urls"] = len(locs)
        sm_dir = urllib.parse.urlsplit(sm).path.rsplit("/", 1)[0] + "/"
        if len(locs) > MAX_URLS:
            add("TOO_MANY_URLS", sm)
        if not locs:
            add("EMPTY_SITEMAP", sm)
        for loc, el in locs:
            u = (loc.text or "").strip() if loc is not None else ""
            us = urllib.parse.urlsplit(u)
            if not us.scheme or not us.netloc:
                add("INVALID_URL", u or "(empty loc)")
                continue
            if us.netloc.lower().removeprefix("www.") != host:
                add("CROSS_HOST", u)
            elif not urllib.parse.urlsplit(u).path.startswith(sm_dir):
                add("OUTSIDE_SITEMAP_PATH", "%s in %s" % (u, sm))
            if us.scheme != sp.scheme:
                add("SCHEME_MISMATCH", u)
            if len(u) > 2048:
                add("URL_TOO_LONG", u[:120])
            if re.search(r"[\s<>\"{}|\\^`]", u):
                add("UNESCAPED_CHARACTERS", u)
            seen_urls[u] += 1
            if seen_urls[u] == 2:
                add("DUPLICATE_URL", u)
            lm = child(el, "lastmod")
            if lm is not None and lm.text:
                v = lm.text.strip()
                lastmods.append(v)
                if not W3C.match(v):
                    add("LASTMOD_INVALID", "%s %s" % (u, v))
                elif v[:10] > today:
                    add("LASTMOD_FUTURE", "%s %s" % (u, v))
            if any(c.tag.split("}")[-1] in ("priority", "changefreq") for c in el):
                issues["PRIORITY_CHANGEFREQ"]["count"] += 1
            if seen_urls[u] == 1:
                urls.append(u)
    if len(lastmods) > 10 and len(set(lastmods)) == 1:
        add("LASTMOD_ALL_SAME", lastmods[0])
    if listed == [] and roots:
        add("NOT_IN_ROBOTS", roots[0])

    # Sample the listed URLs, spread evenly across the list.
    step = max(1, len(urls) // max(1, args.sample))
    sample = urls[::step][: args.sample]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for info, _ in pool.map(lambda u: inspect(u, groups), sample):
            results.append(info)
    sample_counts = collections.Counter(code for r in results for code in r["issues"])
    clean = sum(1 for r in results if not r["issues"])
    findings = []
    for code, data in issues.items():
        sev, fix = (FILE_CHECKS.get(code) or URL_CHECKS.get(code))
        findings.append({"code": code, "severity": sev, "count": data["count"], "examples": data["examples"], "fix": fix, "scope": "listed"})
    for code, c in sample_counts.items():
        sev, fix = SAMPLE_CHECKS[code]
        findings.append({"code": code, "severity": sev, "count": c, "share_of_sample": round(c / len(results), 3) if results else 0,
                         "examples": ["%s (%s)" % (r["url"], r["status"]) for r in results if code in r["issues"]][:5], "fix": fix, "scope": "sample"})
    findings.sort(key=lambda f: (SEVERITY[f["severity"]], -f["count"]))
    return {"status": "ok", "mode": "audit", "site": origin, "checked": today,
            "robots_txt": {"found": robots_found, "sitemaps_listed": listed},
            "sitemaps": files, "urls_listed": len(urls),
            "sample": {"size": len(results), "clean": clean, "clean_share": round(clean / len(results), 3) if results else None,
                       "estimated_clean_urls": round(clean / len(results) * len(urls)) if results else None},
            "lastmod": {"with_lastmod": len(lastmods), "distinct_values": len(set(lastmods))},
            "findings": findings,
            "sample_results": [r for r in results if r["issues"]][:100]}


def read_url_list(path):
    rows = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                parts = [p.strip() for p in re.split(r"[,\t]", line.strip()) if p.strip()]
                if parts and re.match(r"^https?://", parts[0]):
                    rows.append((parts[0], parts[1] if len(parts) > 1 and W3C.match(parts[1]) else None))
    except OSError as e:
        fail("FILE_UNREADABLE", str(e))
    return rows


def generate(args):
    sp = urllib.parse.urlsplit(args.site)
    origin = "%s://%s" % (sp.scheme, sp.netloc)
    if not args.out:
        fail("INPUT_INVALID", "generate needs --out FOLDER.")
    if os.path.isdir(args.out) and os.listdir(args.out) and not args.overwrite:
        fail("OUT_NOT_EMPTY", "%s already has files. Choose an empty folder or pass --overwrite." % args.out)
    groups, listed, _, robots_found = discover(origin, None)
    given_lastmod = {}
    if args.urls:
        rows = read_url_list(args.urls)
        if not rows:
            fail("NO_URLS", "The --urls file has no absolute http(s) URLs.")
        candidates = [u for u, _ in rows][: args.max_pages]
        given_lastmod = {u: lm for u, lm in rows if lm}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            inspected = list(pool.map(lambda u: inspect(u, groups), candidates))
    else:
        host = urllib.parse.urlsplit(fetch(origin + "/")["final"]).netloc.lower()
        queue, seen, inspected = [origin + "/"], {norm(origin + "/")}, []
        while queue and len(inspected) < args.max_pages:
            batch, queue = queue[:4], queue[4:]
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda u: inspect(u, groups), batch))
            for info, page in results:
                inspected.append((info, page))
                if page is None:
                    continue
                for href in page.links:
                    u = urllib.parse.urljoin(info["final"], href).split("#")[0]
                    us = urllib.parse.urlsplit(u)
                    if us.scheme not in ("http", "https") or us.netloc.lower() != host or SKIP_EXT.search(us.path):
                        continue
                    if us.query and not args.keep_query:
                        continue
                    if norm(u) not in seen and allowed(groups, path_of(u)):
                        seen.add(norm(u))
                        queue.append(u)
            time.sleep(0.2)
    keep, excluded = [], collections.defaultdict(list)
    seen_final = set()
    for info, _ in inspected:
        if info["issues"]:
            for code in info["issues"]:
                excluded[code].append(info["url"])
            continue
        if norm(info["final"]) in seen_final:
            excluded["DUPLICATE_URL"].append(info["url"])
            continue
        seen_final.add(norm(info["final"]))
        keep.append((info["final"], given_lastmod.get(info["url"]) or info.get("lastmod")))
    if not keep:
        fail("NOTHING_TO_INCLUDE", "No URL passed the checks (200, indexable, self-canonical, allowed by robots.txt).",
             excluded={k: v[:10] for k, v in excluded.items()})
    os.makedirs(args.out, exist_ok=True)
    chunks = [keep[i:i + MAX_URLS] for i in range(0, len(keep), MAX_URLS)]
    names = ["sitemap.xml"] if len(chunks) == 1 else ["sitemap-%d.xml" % (i + 1) for i in range(len(chunks))]
    written = []
    for name, chunk in zip(names, chunks):
        body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="%s">' % NS]
        for u, lm in chunk:
            body.append("  <url><loc>%s</loc>%s</url>" % (escape(u), "<lastmod>%s</lastmod>" % escape(lm) if lm else ""))
        body.append("</urlset>\n")
        data = "\n".join(body).encode("utf-8")
        fname = name + (".gz" if args.gzip else "")
        with open(os.path.join(args.out, fname), "wb") as f:
            f.write(gzip.compress(data) if args.gzip else data)
        written.append({"file": fname, "urls": len(chunk), "bytes": len(data)})
    index = None
    if len(chunks) > 1:
        base = (args.base or origin).rstrip("/")
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<sitemapindex xmlns="%s">' % NS]
        lines += ["  <sitemap><loc>%s/%s</loc></sitemap>" % (escape(base), w["file"]) for w in written]
        lines.append("</sitemapindex>\n")
        with open(os.path.join(args.out, "sitemap_index.xml"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        index = "sitemap_index.xml"
    main_file = index or written[0]["file"]
    return {"status": "ok", "mode": "generate", "site": origin, "checked": datetime.date.today().isoformat(),
            "source": "url_list" if args.urls else "crawl", "inspected": len(inspected), "included": len(keep),
            "with_lastmod": sum(1 for _, lm in keep if lm),
            "excluded": {k: {"count": len(v), "examples": v[:5]} for k, v in excluded.items()},
            "files": written, "index": index, "out": os.path.abspath(args.out),
            "robots_txt_line": "Sitemap: %s/%s" % ((args.base or origin).rstrip("/"), main_file),
            "robots_txt_already_lists": listed,
            "notes": (["The crawl stopped at --max-pages %d; raise it to cover the whole site." % args.max_pages]
                      if not args.urls and len(inspected) >= args.max_pages else [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["audit", "generate"])
    ap.add_argument("--site", required=True, help="Site origin, e.g. https://example.com")
    ap.add_argument("--sitemap", help="audit: a sitemap URL to check instead of discovering it")
    ap.add_argument("--sample", type=int, default=200, help="audit: listed URLs to fetch and check")
    ap.add_argument("--max-files", type=int, default=50, help="audit: sitemap files to read")
    ap.add_argument("--out", help="generate: output folder")
    ap.add_argument("--urls", help="generate: a file of URLs (one per line, optional lastmod after a comma) instead of crawling")
    ap.add_argument("--max-pages", type=int, default=500, help="generate: pages to crawl or check")
    ap.add_argument("--keep-query", action="store_true", help="generate: keep URLs with query strings when crawling")
    ap.add_argument("--gzip", action="store_true", help="generate: write .xml.gz files")
    ap.add_argument("--base", help="generate: the public folder URL the files will be served from (default: the site root)")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    if not re.match(r"^https?://[^/\s]+", args.site):
        fail("INPUT_INVALID", "--site must be an absolute URL such as https://example.com")
    out = audit(args) if args.mode == "audit" else generate(args)
    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
