#!/usr/bin/env python3
"""Internal Anchor Text Auditor — reference implementation.

Auth:   keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 internal_anchors.py --start https://example.com [--keywords kw.json] [--brand Acme]
"""
from __future__ import annotations
import argparse, json, re, sys, time
import urllib.request, urllib.error, urllib.robotparser
from collections import defaultdict
from urllib.parse import urljoin, urlparse, urlunparse

UA = "seoskills-anchor-audit/1.0"
GENERIC = {"click here", "read more", "here", "this page", "link", "more", "learn more", "this"}


def get(url, timeout=12):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            if "text/html" not in r.headers.get("Content-Type", ""):
                return 0, ""
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except Exception:
        return 0, ""


def norm(u):
    return urlunparse(urlparse(u)._replace(fragment="")).rstrip("/")


def anchors(html, base):
    out = []
    for m in re.finditer(r'<a\b([^>]*?)href=["\']([^"\']+)["\']([^>]*)>([\s\S]*?)</a>', html, re.I):
        pre, href, post, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        if not text:
            alt = re.search(r'alt=["\']([^"\']*)["\']', inner, re.I)
            text = alt.group(1).strip() if alt else ""
        nofollow = "nofollow" in (pre + post).lower()
        out.append((norm(urljoin(base, href)), text, nofollow))
    return out


def classify(text, keyword, brands):
    t = text.strip().lower()
    if not t or re.match(r"^https?://", t):
        return "url_or_empty"
    if any(b.lower() in t for b in brands):
        return "branded"
    if t in GENERIC:
        return "generic"
    if keyword:
        kw = keyword.lower()
        if t == kw:
            return "exact_match"
        toks = [x for x in re.split(r"\W+", kw) if len(x) > 2]
        if toks and any(x in t for x in toks):
            return "partial_match"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--keywords"); ap.add_argument("--brand", default="")
    ap.add_argument("--max-pages", type=int, default=2000, dest="max_pages")
    ap.add_argument("--exact-ceiling", type=float, default=0.5, dest="ceiling")
    a = ap.parse_args()
    host = urlparse(a.start).netloc
    keywords = json.load(open(a.keywords)) if a.keywords else {}
    brands = [b.strip() for b in a.brand.split(",") if b.strip()]
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(f"{urlparse(a.start).scheme}://{host}/robots.txt"); rp.read()
    except Exception:
        rp = None

    from collections import deque
    seen, q = set(), deque([norm(a.start)])
    incoming = defaultdict(list)  # dest -> [(anchor_text, nofollow)]
    hit_cap = False
    while q:
        if len(seen) >= a.max_pages:
            hit_cap = True; break
        url = q.popleft()
        if url in seen:
            continue
        seen.add(url)
        if rp and not rp.can_fetch(UA, url):
            continue
        status, html = get(url)
        if status != 200:
            continue
        for dest, text, nofollow in anchors(html, url):
            if urlparse(dest).netloc == host:
                incoming[dest].append((text, nofollow))
                if dest not in seen:
                    q.append(dest)
        time.sleep(0.2)

    results = []
    for dest, links in incoming.items():
        followed = [(t, nf) for t, nf in links if not nf]
        dist = defaultdict(int)
        for t, nf in followed:
            dist[classify(t, keywords.get(dest), brands)] += 1
        total = sum(dist.values())
        if total == 0:
            continue
        exact_ratio = dist["exact_match"] / total
        generic_ratio = dist["generic"] / total
        flags = []
        if exact_ratio > a.ceiling and total >= 5:
            flags.append("OVER_OPTIMIZED")
        if generic_ratio > 0.4:
            flags.append("GENERIC_OVERUSE")
        if dist["url_or_empty"] > 0:
            flags.append("EMPTY_OR_URL_ANCHORS")
        if total < 2:
            flags.append("THIN_INTERNAL_LINKS")
        results.append({"destination": dest, "inbound_internal_links": len(links),
                        "followed": total, "nofollow": len(links) - total,
                        "distribution": dict(dist), "exact_match_ratio": round(exact_ratio, 2),
                        "generic_ratio": round(generic_ratio, 2), "flags": flags,
                        "classification": "no_keywords" if not keywords else "full"})
    order = {"OVER_OPTIMIZED": 0, "GENERIC_OVERUSE": 1, "THIN_INTERNAL_LINKS": 2}
    results.sort(key=lambda r: (min([order.get(f, 3) for f in r["flags"]] or [4]), -r["inbound_internal_links"]))
    json.dump({"status": "ok", "pages_crawled": len(seen), "hit_cap": hit_cap,
               "destinations_analyzed": len(results),
               "flagged": len([r for r in results if r["flags"]]), "results": results}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
