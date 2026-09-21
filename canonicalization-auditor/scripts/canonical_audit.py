#!/usr/bin/env python3
"""Canonicalization Auditor — reference implementation.

Crawls a site to collect, per URL, its rel=canonical, hreflang set, redirect
target, robots directives (meta + X-Robots-Tag), and sitemap membership, then
detects the contradictions that make Google ignore a canonical hint:
canonical-to-noindex, canonical-to-redirect, canonical chains, noindex+canonical
on the same page, canonicalized-away-but-in-sitemap, duplicate clusters with no
chosen canonical, and non-reciprocal / invalid hreflang. Each conflict is
returned with the corrected canonical decision and why the hint is discarded.

Auth:   none (crawler). Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage:
  python3 canonical_audit.py --start https://example.com \
      [--sitemap https://example.com/sitemap.xml] [--max-urls 2000]
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, os, sys, time, re
import http.client
import urllib.request, urllib.error
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

UA = "seoskills-canonical-auditor/1.0"
LANG_RE = re.compile(r"^([a-z]{2,3})(-[a-z]{2,4})?$")


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


def norm(u):
    if not u:
        return u
    p = urlparse(u.strip())
    return urlunparse(p._replace(fragment="")).rstrip("/") or (p.scheme + "://" + p.netloc)


class DocParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonical = None
        self.meta_robots = ""
        self.hreflang = []        # (lang, href)
        self.links = []
        self.title = None
        self._in_title = False
        self._skip = 0
        self._text = []

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "link":
            rel = d.get("rel", "").lower()
            href = d.get("href")
            if "canonical" in rel and href and self.canonical is None:
                self.canonical = href
            if "alternate" in rel and d.get("hreflang") and href:
                self.hreflang.append((d["hreflang"].strip().lower(), href))
        elif tag == "meta" and d.get("name", "").lower() == "robots":
            self.meta_robots = d.get("content", "").lower()
        elif tag == "a" and d.get("href"):
            self.links.append(d["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data
        if not self._skip:
            s = data.strip()
            if s:
                self._text.append(s)

    def content_hash(self):
        blob = " ".join(self._text).lower()
        blob = re.sub(r"\s+", " ", blob)
        return hashlib.sha1(blob.encode("utf-8", "ignore")).hexdigest() if blob else None


PROBED = {}
PROBE_BUDGET = [0]


def _raw_get(url, timeout):
    """GET with redirects DISABLED. Returns (status, location, headers, body)."""
    u = urlparse(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.netloc, timeout=timeout)
    try:
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        conn.request("GET", path, headers={"User-Agent": UA, "Accept": "text/html"})
        r = conn.getresponse()
        status = r.status
        location = r.getheader("Location")
        xrobots = (r.getheader("X-Robots-Tag") or "").lower()
        ctype = r.getheader("Content-Type", "")
        body = r.read(600000).decode("utf-8", "ignore") if "text/html" in ctype else ""
        return status, location, xrobots, body
    finally:
        conn.close()


def probe(url, timeout, allow_budget=True):
    n = norm(url)
    if n in PROBED:
        return PROBED[n]
    if allow_budget and PROBE_BUDGET[0] <= 0:
        return {"url": n, "status": None, "unknown": True}
    if allow_budget:
        PROBE_BUDGET[0] -= 1
    status, location, xrobots, body = None, None, "", ""
    for attempt in range(4):
        try:
            status, location, xrobots, body = _raw_get(n, timeout)
            break
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            status = 0
    rec = {"url": n, "status": status, "location": norm(urljoin(n, location)) if location else None,
           "canonical": None, "robots": xrobots, "hreflang": [], "content_hash": None, "title": None,
           "links": []}
    if body and status and 200 <= status < 300:
        p = DocParser()
        try:
            p.feed(body)
        except Exception:
            pass
        rec["canonical"] = norm(urljoin(n, p.canonical)) if p.canonical else None
        rec["robots"] = (xrobots + " " + p.meta_robots).strip()
        rec["hreflang"] = [(lang, norm(urljoin(n, href))) for lang, href in p.hreflang]
        rec["content_hash"] = p.content_hash()
        rec["title"] = (p.title or "").strip()
        rec["links"] = p.links
    PROBED[n] = rec
    time.sleep(0.1)
    return rec


def is_noindex(rec):
    return "noindex" in (rec.get("robots") or "")


def resolve_redirect(rec, timeout, max_hops=5):
    cur = rec
    for _ in range(max_hops):
        if not cur or cur.get("status") is None:
            return None
        if 300 <= (cur.get("status") or 0) < 400 and cur.get("location"):
            cur = probe(cur["location"], timeout)
            continue
        return cur
    return cur


def parse_sitemap(src, timeout, max_urls):
    urls = set()
    try:
        if os.path.exists(src):
            raw = open(src, "rb").read()
        else:
            with urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": UA}), timeout=timeout) as r:
                raw = r.read(20 * 1024 * 1024)
        if raw[:2] == b"\x1f\x8b" or src.endswith(".gz"):
            raw = gzip.decompress(raw)
        root = ET.fromstring(raw)
    except Exception:
        return urls
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() == "loc" and el.text:
            t = el.text.strip()
            if t.endswith(".xml") or t.endswith(".xml.gz"):
                if len(urls) < max_urls:
                    urls |= parse_sitemap(t, timeout, max_urls)
            else:
                urls.add(norm(t))
            if len(urls) >= max_urls:
                break
    return urls


def crawl(start, max_urls, max_depth, timeout):
    host = urlparse(start).netloc
    seen, order, q = set(), [], deque([(norm(start), 0)])
    while q and len(seen) < max_urls:
        url, depth = q.popleft()
        if url in seen or depth > max_depth:
            continue
        seen.add(url)
        rec = probe(url, timeout, allow_budget=False)  # crawl always fetches
        order.append(url)
        if rec.get("status") and 200 <= rec["status"] < 300:
            for href in rec.get("links", []):
                nxt = norm(urljoin(url, href))
                if urlparse(nxt).netloc == host and nxt.startswith("http") and nxt not in seen:
                    q.append((nxt, depth + 1))
    return order


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--url-list", dest="url_list")
    ap.add_argument("--sitemap")
    ap.add_argument("--max-urls", type=int, default=2000, dest="max_urls")
    ap.add_argument("--max-depth", type=int, default=6, dest="max_depth")
    ap.add_argument("--max-probe", type=int, default=1000, dest="max_probe")
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()

    if not args.start and not args.url_list:
        fail("INPUT_INVALID", "Provide --start (crawl root) or --url-list (file of URLs).")
    PROBE_BUDGET[0] = args.max_probe

    if args.url_list:
        if not os.path.exists(args.url_list):
            fail("INPUT_FILE_MISSING", "URL list not found: %s" % args.url_list)
        data = json.load(open(args.url_list)) if args.url_list.endswith(".json") else \
            [l.strip() for l in open(args.url_list) if l.strip()]
        pages = [norm(u) for u in data]
        for u in pages:
            probe(u, args.timeout, allow_budget=False)
    else:
        pages = crawl(args.start, args.max_urls, args.max_depth, args.timeout)

    sitemap = parse_sitemap(args.sitemap, args.timeout, args.max_urls) if args.sitemap else set()

    # duplicate content clusters (each self-canonical)
    by_hash = defaultdict(list)
    for u in pages:
        rec = PROBED.get(u, {})
        if rec.get("status") and 200 <= rec["status"] < 300 and rec.get("content_hash"):
            by_hash[rec["content_hash"]].append(u)

    dup_clusters = []
    for h, members in by_hash.items():
        if len(members) < 2:
            continue
        self_canon = [u for u in members if (PROBED[u].get("canonical") or u) == u]
        if len(self_canon) >= 2:
            primary = min(self_canon, key=lambda u: (len(u), u))  # shortest URL = likely canonical
            dup_clusters.append({"content_hash": h[:12], "urls": members,
                                 "self_canonical": self_canon, "suggested_canonical": primary})

    conflicts = []
    type_counts = defaultdict(int)
    for u in pages:
        rec = PROBED.get(u)
        if not rec or not rec.get("status") or not (200 <= rec["status"] < 300):
            continue
        declared = rec.get("canonical")
        types, why, hreflang_issues = [], [], []
        corrected = declared or u

        # --- canonical signal conflicts ---
        if declared and declared != u:
            tgt = probe(declared, args.timeout)
            tstate = tgt.get("status")
            if tstate is None:
                types.append("CANONICAL_TARGET_UNVERIFIED")
                why.append("Canonical target could not be fetched within the probe budget.")
            elif 300 <= (tstate or 0) < 400:
                types.append("CANONICAL_TO_REDIRECT")
                final = resolve_redirect(tgt, args.timeout)
                corrected = final["url"] if final and final.get("status") and 200 <= final["status"] < 300 else u
                why.append("Canonical points to a redirecting URL; Google distrusts it and may pick its own canonical. Point directly at %s." % corrected)
            elif tstate >= 400 or tstate == 0:
                types.append("CANONICAL_TO_NON200")
                corrected = u
                why.append("Canonical points to a %s URL; a canonical to a non-200 page is ignored. Self-canonicalize or target a live equivalent." % tstate)
            else:
                if is_noindex(tgt):
                    types.append("CANONICAL_TO_NOINDEX")
                    corrected = u
                    why.append("Canonical points to a noindex page: contradictory consolidation, Google may drop the whole cluster. Remove the noindex or re-point the canonical.")
                tcanon = tgt.get("canonical")
                if tcanon and tcanon != declared:
                    types.append("CANONICAL_CHAIN")
                    corrected = tcanon
                    why.append("Canonical chain (%s -> %s -> %s); chains are collapsed unpredictably. Point directly at %s." % (u, declared, tcanon, tcanon))

        # --- same-page contradictions ---
        if is_noindex(rec) and declared and declared != u:
            types.append("NOINDEX_WITH_CANONICAL")
            why.append("noindex and rel=canonical to another URL on the same page are contradictory; Google tends to honour noindex and ignore the canonical, deindexing the page.")
        if declared and declared != u and norm(u) in sitemap:
            types.append("CANONICALIZED_BUT_IN_SITEMAP")
            why.append("This URL canonicalizes elsewhere yet is listed in the sitemap; sitemaps should contain only canonical URLs. Remove it or make it self-canonical.")

        # --- hreflang conflicts ---
        hl = rec.get("hreflang")
        if hl:
            langs = [lang for lang, _ in hl]
            if "x-default" not in langs:
                hreflang_issues.append({"issue": "MISSING_XDEFAULT", "detail": "No x-default alternate declared."})
            for lang, href in hl:
                if lang != "x-default" and not LANG_RE.match(lang):
                    hreflang_issues.append({"issue": "INVALID_LANG_CODE", "lang": lang, "href": href,
                                            "detail": "Invalid hreflang code; the whole cluster annotation may be dropped."})
                alt = probe(href, args.timeout)
                ast = alt.get("status")
                if ast is None:
                    continue
                if not (200 <= (ast or 0) < 300):
                    hreflang_issues.append({"issue": "ALTERNATE_NON_200", "lang": lang, "href": href, "status": ast,
                                            "detail": "hreflang alternate does not return 200; it must point to an indexable page."})
                    continue
                back = {h2 for _, h2 in alt.get("hreflang", [])}
                if norm(u) not in back:
                    hreflang_issues.append({"issue": "NON_RECIPROCAL", "lang": lang, "href": href,
                                            "detail": "Alternate does not link back to this URL; non-reciprocal hreflang is ignored."})
                if is_noindex(alt) or (alt.get("canonical") and alt["canonical"] != alt["url"]):
                    hreflang_issues.append({"issue": "ALTERNATE_NON_CANONICAL", "lang": lang, "href": href,
                                            "detail": "Alternate is noindex or canonicalizes elsewhere; hreflang must reference indexable, self-canonical URLs."})
            if hreflang_issues:
                types.append("HREFLANG_CONFLICT")

        if types:
            for t in types:
                type_counts[t] += 1
            conflicts.append({
                "url": u,
                "conflict_types": types,
                "canonical_declared": declared,
                "canonical_self": (declared == u) or (declared is None),
                "robots": rec.get("robots") or "",
                "in_sitemap": norm(u) in sitemap,
                "corrected_canonical": corrected,
                "hreflang_issues": hreflang_issues,
                "why": why,
            })

    for c in dup_clusters:
        type_counts["DUPLICATE_NO_CANONICAL"] += 1

    conflicts.sort(key=lambda c: len(c["conflict_types"]), reverse=True)
    summary = {
        "pages_crawled": len(pages),
        "pages_probed_total": len(PROBED),
        "conflicts_found": len(conflicts),
        "duplicate_clusters": len(dup_clusters),
        "probe_budget_exhausted": PROBE_BUDGET[0] <= 0,
        "sitemap_urls": len(sitemap),
        "conflict_type_counts": dict(type_counts),
    }
    json.dump({"status": "ok", "summary": summary, "conflicts": conflicts[:2000],
               "duplicate_clusters": dup_clusters[:500]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
