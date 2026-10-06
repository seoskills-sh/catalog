#!/usr/bin/env python3
"""Site Crawl Data Extractor: reference implementation.

Crawls a website (from start URLs, its sitemap, or both), politely and within
robots.txt, and extracts the same fields from every page into one table:
built-in SEO fields (title, meta description, H1, canonical, robots, dates,
author, word count, schema types, link and image counts, a text excerpt) plus
any custom fields defined on the command line: a regular expression, a JSON-LD
property path, a meta tag, the first element with a tag, class or id, and
optionally the page's HTML tables. Results go to a CSV or JSON Lines file.

Auth:   none. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 crawl_extract.py --start https://example.com/blog/ [--sitemap] [--include /blog/] [--exclude /tag/]
       [--field "price=jsonld:Product.offers.price"] [--field "sku=regex:SKU:\\s*(\\w+)"] [--tables] --out pages.csv
"""
from __future__ import annotations
import argparse, collections, csv, datetime, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UA = "seoskills-crawl-extractor/1.0 (+https://seoskills.sh)"
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|css|js|json|xml|txt|pdf|zip|gz|rar|mp4|mp3|mov|woff2?|ttf|eot|docx?|xlsx?|pptx?)$", re.I)
FIELD_KINDS = ("regex", "rawregex", "jsonld", "meta", "tag", "class", "id")


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), {k.lower(): v for k, v in r.headers.items()}, r.read(4_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, url, {}, ""
    except Exception:
        return 0, url, {}, ""


# ---------- robots.txt (Google-style matching: longest rule wins, Allow wins a tie) ----------

def parse_robots(text):
    groups, agents, rules, last, delay, sitemaps = [], [], [], False, None, []
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
        elif k == "crawl-delay" and "*" in agents:
            try:
                delay = float(v)
            except ValueError:
                pass
        elif k == "sitemap":
            sitemaps.append(v)
    if agents:
        groups.append((agents, rules))
    return groups, delay, sitemaps


def allowed(groups, path):
    chosen = [r for a, r in groups if any(x != "*" and x in UA.lower() for x in a)] or [r for a, r in groups if "*" in a]
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


class Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}
    CHROME = {"nav", "footer", "aside"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "br", "section", "article", "main", "header",
             "footer", "nav", "aside", "ul", "ol", "table", "dd", "dt", "blockquote", "pre", "figcaption", "form", "button", "label"}

    def __init__(self, want_tags, want_classes, want_ids, want_tables):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.canonical, self.lang, self.jsonld = "", {}, None, None, []
        self.links, self.images, self.text, self.main_text, self.times = [], 0, [], [], []
        self.tags_found, self.classes_found, self.ids_found = {}, {}, {}
        self.want_tags, self.want_classes, self.want_ids, self.want_tables = want_tags, want_classes, want_ids, want_tables
        self.tables, self._table, self._row, self._cell = [], None, None, None
        self._capture = []  # open captures: [kind, key, depth, parts]
        self._skip = self._chrome = self._main = 0
        self._in_title = self._in_jsonld = False
        self._jbuf, self.saw_main = [], False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        for cap in self._capture:
            cap[2] += 1
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        if tag == "title" and not self.title and not self._skip:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = a.get("href")
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld, self._jbuf = True, []
            return
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img":
            self.images += 1
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        if tag in self.BLOCK:  # text is joined without separators, so mark block edges with a space
            self.text.append(" ")
            self.main_text.append(" ")
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.CHROME:
            self._chrome += 1
        if tag in ("main", "article"):
            self._main += 1
            self.saw_main = True
        if tag in self.want_tags and tag not in self.tags_found:
            self._capture.append(["tag", tag, 1, []])
        for cls in a.get("class", "").split():
            if cls in self.want_classes and cls not in self.classes_found:
                self._capture.append(["class", cls, 1, []])
        if a.get("id") in self.want_ids and a.get("id") not in self.ids_found:
            self._capture.append(["id", a["id"], 1, []])
        if self.want_tables:
            if tag == "table" and self._table is None and len(self.tables) < 5:
                self._table = []
            elif tag == "tr" and self._table is not None:
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jbuf))
            self._in_jsonld = False
            return
        if tag in self.BLOCK:
            self.text.append(" ")
            self.main_text.append(" ")
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.CHROME and self._chrome:
            self._chrome -= 1
        if tag in ("main", "article") and self._main:
            self._main -= 1
        keep = []
        for cap in self._capture:
            cap[2] -= 1
            if cap[2] <= 0:
                text = re.sub(r"\s+", " ", "".join(cap[3])).strip()
                {"tag": self.tags_found, "class": self.classes_found, "id": self.ids_found}[cap[0]].setdefault(cap[1], text)
            else:
                keep.append(cap)
        self._capture = keep
        if self.want_tables:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
                self._cell = None
            elif tag == "tr" and self._row is not None and self._table is not None:
                if self._row and len(self._table) < 50:
                    self._table.append(self._row)
                self._row = None
            elif tag == "table" and self._table is not None:
                if self._table:
                    self.tables.append(self._table)
                self._table = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_jsonld:
            self._jbuf.append(data)
        elif not self._skip:
            self.text.append(data)
            if not self._chrome and (self._main or not self.saw_main):
                self.main_text.append(data)
            for cap in self._capture:
                cap[3].append(data)
            if self._cell is not None:
                self._cell.append(data)


def jsonld_nodes(blocks):
    nodes = []
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
                nodes.append(n)
                stack.extend(v for v in n.values() if isinstance(v, (dict, list)))
    return nodes


def types_of(n):
    t = n.get("@type")
    return [t] if isinstance(t, str) else [x for x in t if isinstance(x, str)] if isinstance(t, list) else []


def jsonld_path(nodes, spec):
    """Type.prop.prop: the first node of that @type (or * for any) that has the whole path."""
    typ, _, path = spec.partition(".")
    for n in nodes:
        if typ != "*" and typ not in types_of(n):
            continue
        cur = n
        for part in path.split(".") if path else []:
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur is not None and path:
            if isinstance(cur, list):
                cur = cur[0] if len(cur) == 1 else cur
            return cur if isinstance(cur, (str, int, float, bool)) else json.dumps(cur, ensure_ascii=False)
    return None


def parse_fields(specs):
    fields = []
    for spec in specs:
        name, sep, rest = spec.partition("=")
        kind, sep2, arg = rest.partition(":")
        if not sep or not sep2 or not re.fullmatch(r"[A-Za-z_][\w-]*", name) or kind not in FIELD_KINDS or not arg:
            fail("INPUT_INVALID", 'Bad --field "%s". Use name=kind:argument with kind one of %s.' % (spec, ", ".join(FIELD_KINDS)))
        if kind in ("regex", "rawregex"):
            try:
                re.compile(arg)
            except re.error as e:
                fail("INPUT_INVALID", 'The regex in --field "%s" does not compile: %s' % (spec, e))
        fields.append((name, kind, arg))
    return fields


def extract(url, html, headers, status, final, fields, text_chars, want_tables):
    p = Page({a.lower() for _, k, a in fields if k == "tag"} | {"h1"}, {a for _, k, a in fields if k == "class"},
             {a for _, k, a in fields if k == "id"}, want_tables)
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    nodes = jsonld_nodes(p.jsonld)
    main = re.sub(r"\s+", " ", "".join(p.main_text)).strip()
    full = re.sub(r"\s+", " ", "".join(p.text)).strip()
    host = urllib.parse.urlsplit(final).netloc.lower()
    internal = sum(1 for h in p.links if urllib.parse.urlsplit(urllib.parse.urljoin(final, h)).netloc.lower() == host)
    dates = sorted({d[:10] for n in nodes for k in ("datePublished", "dateModified") for d in [n.get(k)] if isinstance(d, str) and re.match(r"\d{4}-\d{2}-\d{2}", d)}
                   | {p.meta.get(k, "")[:10] for k in ("article:published_time", "article:modified_time") if re.match(r"\d{4}-\d{2}-\d{2}", p.meta.get(k, ""))})
    author = next((n["author"].get("name") if isinstance(n.get("author"), dict) else n.get("author") for n in nodes
                   if isinstance(n.get("author"), (dict, str))), None) or p.meta.get("author")
    row = {
        "url": url, "status": status, "final_url": final, "title": p.title.strip(), "meta_description": p.meta.get("description", "").strip(),
        "h1": p.tags_found.get("h1"), "canonical": urllib.parse.urljoin(final, p.canonical) if p.canonical else None,
        "robots": ",".join(x for x in (p.meta.get("robots", ""), headers.get("x-robots-tag", "")) if x), "lang": p.lang,
        "published": dates[0] if dates else None, "modified": dates[-1] if dates else None, "author": author if isinstance(author, str) else None,
        "words": len(re.findall(r"[A-Za-z0-9]+", main)), "schema_types": "|".join(sorted({t for n in nodes for t in types_of(n)})),
        "internal_links": internal, "external_links": len(p.links) - internal, "images": p.images,
        "excerpt": main[:text_chars],
    }
    for name, kind, arg in fields:
        val = None
        if kind == "regex":
            m = re.search(arg, full)
            val = (m.group(1) if m and m.groups() else m.group(0)) if m else None
        elif kind == "rawregex":
            m = re.search(arg, html)
            val = (m.group(1) if m and m.groups() else m.group(0)) if m else None
        elif kind == "jsonld":
            val = jsonld_path(nodes, arg)
        elif kind == "meta":
            val = p.meta.get(arg.lower())
        elif kind == "tag":
            val = p.tags_found.get(arg.lower())
        elif kind == "class":
            val = p.classes_found.get(arg)
        elif kind == "id":
            val = p.ids_found.get(arg)
        row[name] = val.strip() if isinstance(val, str) else val
    if want_tables:
        row["tables"] = p.tables
    return row, p.links


def sitemap_urls(origin, extra, limit):
    queue, urls, seen = list(extra) or [origin + "/sitemap.xml"], [], set()
    while queue and len(seen) < 25 and len(urls) < limit:
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
    return urls[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="append", default=[], help="Start URL (repeatable); its host is the one crawled")
    ap.add_argument("--sitemap", action="store_true", help="Also seed from the site's sitemaps")
    ap.add_argument("--urls-file", help="Extract exactly these URLs (one per line) instead of crawling")
    ap.add_argument("--include", action="append", default=[], help="Only keep URLs whose path matches this regex (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], help="Skip URLs whose path matches this regex (repeatable)")
    ap.add_argument("--field", action="append", default=[], help='Custom field "name=kind:arg"; kinds: regex, rawregex, jsonld (Type.path), meta, tag, class, id')
    ap.add_argument("--tables", action="store_true", help="Also extract up to 5 HTML tables per page")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--delay", type=float, default=0.5, help="Seconds between requests (robots.txt Crawl-delay wins if larger)")
    ap.add_argument("--text-chars", type=int, default=600, help="Characters of main text to keep per page")
    ap.add_argument("--keep-query", action="store_true", help="Follow links with query strings")
    ap.add_argument("--out", help="Write every row to this .csv or .jsonl file")
    args = ap.parse_args()
    fields = parse_fields(args.field)
    if args.urls_file:
        try:
            with open(args.urls_file, encoding="utf-8-sig") as f:
                listed = [l.strip().split(",")[0] for l in f if re.match(r"^https?://", l.strip())]
        except OSError as e:
            fail("FILE_UNREADABLE", str(e))
        if not listed:
            fail("INPUT_INVALID", "The --urls-file has no absolute http(s) URLs.")
        starts = listed
    else:
        starts = [s.strip() for s in args.start if s.strip()]
    if not starts or any(not re.match(r"^https?://[^/\s]+", s) for s in starts):
        fail("INPUT_INVALID", "Pass --start with absolute http(s) URLs, or --urls-file.")
    if args.out and not args.out.lower().endswith((".csv", ".jsonl")):
        fail("INPUT_INVALID", "--out must end in .csv or .jsonl.")
    sp = urllib.parse.urlsplit(starts[0])
    origin = "%s://%s" % (sp.scheme, sp.netloc)
    host = sp.netloc.lower()
    robots_cache = {}

    def robots_for(u):
        o = "%s://%s" % urllib.parse.urlsplit(u)[:2]
        if o not in robots_cache:
            st, _, _, txt = fetch(o + "/robots.txt")
            robots_cache[o] = parse_robots(txt) if st == 200 else ([], None, [])
        return robots_cache[o]
    _, crawl_delay, robots_sitemaps = robots_for(origin + "/")
    delay = max(args.delay, crawl_delay or 0)
    inc = [re.compile(x) for x in args.include]
    exc = [re.compile(x) for x in args.exclude]

    def wanted(u):
        us = urllib.parse.urlsplit(u)
        if us.scheme not in ("http", "https") or SKIP_EXT.search(us.path):
            return False
        if us.query and not args.keep_query and not args.urls_file:
            return False
        path = us.path or "/"
        if inc and not any(r.search(path) for r in inc):
            return False
        return not any(r.search(path) for r in exc)

    queue = collections.deque()
    seen = set()

    def push(u):
        u = u.split("#")[0]
        key = u.rstrip("/")
        if key not in seen:
            seen.add(key)
            queue.append(u)
    for s in starts:
        push(s)
    if args.sitemap and not args.urls_file:
        for u in sitemap_urls(origin, robots_sitemaps, args.max_pages * 5):
            if urllib.parse.urlsplit(u).netloc.lower() == host and wanted(u):
                push(u)
    rows, skipped, statuses = [], collections.Counter(), collections.Counter()
    while queue and len(rows) < args.max_pages:
        u = queue.popleft()
        path = (urllib.parse.urlsplit(u).path or "/") + ("?" + urllib.parse.urlsplit(u).query if urllib.parse.urlsplit(u).query else "")
        if not allowed(robots_for(u)[0], path):
            skipped["robots_disallowed"] += 1
            continue
        status, final, headers, html = fetch(u)
        statuses[status] += 1
        time.sleep(delay)
        if status != 200 or "html" not in headers.get("content-type", "html"):
            rows.append({"url": u, "status": status, "final_url": final})
            continue
        row, links = extract(u, html, headers, status, final, fields, args.text_chars, args.tables)
        rows.append(row)
        if not args.urls_file:
            for h in links:
                nu = urllib.parse.urljoin(final, h)
                if urllib.parse.urlsplit(nu).netloc.lower() == host and wanted(nu):
                    push(nu)
    if args.out:  # every row, failures included, so the file accounts for each URL
        columns = list(dict.fromkeys(k for r in rows for k in r if k != "tables"))
        with open(args.out, "w", newline="", encoding="utf-8") as out_f:
            if args.out.lower().endswith(".jsonl"):
                for r in rows:
                    out_f.write(json.dumps(r, ensure_ascii=False) + "\n")
            else:
                writer = csv.DictWriter(out_f, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for r in rows:
                    writer.writerow({k: r.get(k) for k in columns})
    ok = [r for r in rows if r.get("status") == 200 and "title" in r]
    custom = [name for name, _, _ in fields]
    fill = {name: round(sum(1 for r in ok if r.get(name) not in (None, "")) / len(ok), 2) if ok else 0 for name in ["title", "meta_description", "canonical", "published", "author"] + custom}
    notes = []
    if queue and len(rows) >= args.max_pages:
        notes.append("Stopped at --max-pages %d with %d more URLs queued." % (args.max_pages, len(queue)))
    if ok and all(r["words"] < 50 for r in ok):
        notes.append("Every page has under 50 words of text in its HTML: the site may render content with JavaScript, which this crawler does not run.")
    json.dump({
        "status": "ok", "origin": origin, "crawled": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": len(rows), "ok_pages": len(ok), "statuses": {str(k): v for k, v in statuses.items()}, "skipped": dict(skipped),
        "delay_seconds": delay, "fields": ["url", "status", "final_url", "title", "meta_description", "h1", "canonical", "robots", "lang", "published",
                                           "modified", "author", "words", "schema_types", "internal_links", "external_links", "images", "excerpt"] + custom,
        "fill_rates": fill, "out": os.path.abspath(args.out) if args.out else None,
        "rows": rows[:25], "rows_in_output": min(25, len(rows)), "notes": notes,
    }, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
