#!/usr/bin/env python3
"""Hreflang Network Validator — reference implementation.

Builds the hreflang graph across a URL cluster and reports broken/non-reciprocal
edges, invalid locales, and non-200/non-canonical targets.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 hreflang_validate.py --urls https://a.com/,https://a.com/fr/
       python3 hreflang_validate.py --sitemap https://a.com/sitemap.xml
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

ISO_LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2}|-[0-9]{3})?$")
UA = {"User-Agent": "seoskills-hreflang/1.0"}


def get(url, timeout=15):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read(500000).decode("utf-8", "ignore"), r.geturl()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, {}, "", url
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}, "", url
    return 0, {}, "", url


def parse_alternates(html, headers):
    alts = []
    head = html.split("</head>")[0] if "</head>" in html else html
    for m in re.finditer(r'<link\b[^>]*rel=["\']alternate["\'][^>]*>', head, re.I):
        tag = m.group(0)
        hl = re.search(r'hreflang=["\']([^"\']+)["\']', tag, re.I)
        hf = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        if hl and hf:
            alts.append((hl.group(1), hf.group(1)))
    link_header = headers.get("Link", "")
    for m in re.finditer(r'<([^>]+)>\s*;\s*rel="?alternate"?\s*;\s*hreflang="?([^";]+)"?', link_header, re.I):
        alts.append((m.group(2), m.group(1)))
    return alts


def canonical_of(html):
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else None


def norm(u):
    return u.split("#")[0].rstrip("/").lower()


def sitemap_urls(url):
    _, _, xml, _ = get(url)
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls"); ap.add_argument("--sitemap")
    ap.add_argument("--max-urls", type=int, default=2000, dest="max_urls")
    ap.add_argument("--expected", default="")
    a = ap.parse_args()
    urls = ([u.strip() for u in a.urls.split(",")] if a.urls else sitemap_urls(a.sitemap) if a.sitemap else [])
    urls = [u for u in urls if u][: a.max_urls]
    if not urls:
        json.dump({"status": "empty_input", "issues": []}, sys.stdout); return
    expected = [e.strip() for e in a.expected.split(",") if e.strip()]

    nodes = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for url, (status, headers, html, final) in zip(urls, ex.map(get, urls)):
            nodes[norm(url)] = {
                "url": url, "status": status, "unavailable": status in (0, 429, 503),
                "canonical": canonical_of(html), "alts": parse_alternates(html, headers),
            }

    issues = []
    declared = {k: {norm(h): (hl, h) for hl, h in v["alts"]} for k, v in nodes.items()}
    for k, node in nodes.items():
        if node["unavailable"]:
            continue
        locales = [hl for hl, _ in node["alts"]]
        if not node["alts"]:
            issues.append({"url": node["url"], "code": "NO_HREFLANG_DECLARED", "level": "info"})
            continue
        if not any(norm(h) == k for _, h in node["alts"]):
            issues.append({"url": node["url"], "code": "SELF_REFERENCE_MISSING", "level": "error"})
        seen = set()
        for hl, href in node["alts"]:
            if hl.lower() != "x-default" and not ISO_LANG.match(hl):
                issues.append({"url": node["url"], "code": "INVALID_LOCALE", "detail": hl, "level": "error"})
            if hl in seen:
                issues.append({"url": node["url"], "code": "DUPLICATE_LOCALE", "detail": hl, "level": "error"})
            seen.add(hl)
            t = nodes.get(norm(href))
            if t is None:
                continue  # target not in crawl set; cannot verify reciprocity here
            if t["unavailable"]:
                issues.append({"url": node["url"], "target": href, "code": "TARGET_UNVERIFIABLE", "level": "warn"})
                continue
            if t["status"] != 200:
                issues.append({"url": node["url"], "target": href, "code": "TARGET_NOT_200", "detail": t["status"], "level": "error"})
            if t["canonical"] and norm(t["canonical"]) != norm(href):
                issues.append({"url": node["url"], "target": href, "code": "NON_CANONICAL_TARGET", "level": "error"})
            if k not in declared.get(norm(href), {}):
                issues.append({"url": node["url"], "target": href, "code": "RETURN_TAG_MISSING", "level": "error"})
        for want in expected:
            if want not in locales:
                issues.append({"url": node["url"], "code": "MISSING_EXPECTED_LOCALE", "detail": want, "level": "warn"})

    json.dump({
        "status": "ok", "urls_checked": len(nodes),
        "issue_count": len(issues), "valid": len([i for i in issues if i.get("level") == "error"]) == 0,
        "issues": issues,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
