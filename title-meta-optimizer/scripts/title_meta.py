#!/usr/bin/env python3
"""Title & Meta Pixel Optimizer — reference implementation.

Auth:   keyless crawl; optional GOOGLE_APPLICATION_CREDENTIALS for GSC priority.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 title_meta.py --urls https://a.com/p,https://a.com/q
"""
from __future__ import annotations
import argparse, json, re, sys
import urllib.request, urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "seoskills-title-meta/1.0"}
# Proportional-font pixel width approximation at ~20px (Arial-like), narrow/wide char sets.
NARROW = set("ijltfIftr.,:;'|!")
WIDE = set("mwMW@")


def px_width(text):
    w = 0.0
    for ch in text:
        w += 5.0 if ch in NARROW else 12.0 if ch in WIDE else 7.5 if ch.isupper() else 7.0
    return round(w)


def get(url, timeout=12):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            if "text/html" not in r.headers.get("Content-Type", ""):
                return 0, ""
            return r.status, r.read(200000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def extract(html):
    t = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I)
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
    clean = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip() if s else ""
    return clean(t.group(1) if t else ""), clean(m.group(1) if m else "")


def audit(url, keyword, tbudget, mbudget):
    status, html = get(url)
    if status != 200 or not html:
        return {"url": url, "status": "unreachable", "http_status": status}
    title, meta = extract(html)
    tpx, mpx = px_width(title), px_width(meta)
    issues = []
    if not title:
        issues.append({"code": "TITLE_MISSING", "severity": "critical"})
    else:
        if tpx > tbudget:
            issues.append({"code": "TITLE_TRUNCATED", "severity": "warning", "estimated_px": tpx})
        elif tpx < 200:
            issues.append({"code": "TITLE_TOO_SHORT", "severity": "info", "estimated_px": tpx})
    if not meta:
        issues.append({"code": "META_MISSING", "severity": "warning"})
    elif mpx > mbudget:
        issues.append({"code": "META_TRUNCATED", "severity": "info", "estimated_px": mpx})
    if keyword and title:
        toks = [t for t in re.split(r"\W+", keyword.lower()) if len(t) > 2]
        if not all(t in title.lower() for t in toks):
            issues.append({"code": "KEYWORD_NOT_IN_TITLE", "severity": "warning", "keyword": keyword})
        elif px_width(title[:title.lower().find(toks[0])]) > 30:
            issues.append({"code": "KEYWORD_NOT_FRONT_LOADED", "severity": "info", "keyword": keyword})
    return {"url": url, "status": "ok", "title": title, "title_px": tpx, "title_chars": len(title),
            "meta": meta, "meta_px": mpx, "meta_chars": len(meta), "issues": issues}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls"); ap.add_argument("--sitemap")
    ap.add_argument("--keywords"); ap.add_argument("--gsc")
    ap.add_argument("--title-budget", type=int, default=580, dest="tb")
    ap.add_argument("--meta-budget", type=int, default=920, dest="mb")
    a = ap.parse_args()
    urls = [u.strip() for u in a.urls.split(",")] if a.urls else []
    if a.sitemap:
        _, xml = get(a.sitemap)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    urls = [u for u in urls if u]
    kw = json.load(open(a.keywords)) if a.keywords else {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda u: audit(u, kw.get(u), a.tb, a.mb), urls))
    ok = [r for r in results if r.get("status") == "ok"]
    # duplicate detection
    tmap, mmap = defaultdict(list), defaultdict(list)
    for r in ok:
        if r["title"]:
            tmap[r["title"].lower()].append(r["url"])
        if r["meta"]:
            mmap[r["meta"].lower()].append(r["url"])
    for r in ok:
        if r["title"] and len(tmap[r["title"].lower()]) > 1:
            r["issues"].append({"code": "DUPLICATE_TITLE", "severity": "warning",
                                "shared_with": [u for u in tmap[r["title"].lower()] if u != r["url"]][:5]})
        if r["meta"] and len(mmap[r["meta"].lower()]) > 1:
            r["issues"].append({"code": "DUPLICATE_META", "severity": "info",
                                "shared_with": [u for u in mmap[r["meta"].lower()] if u != r["url"]][:5]})
    ok.sort(key=lambda r: len(r["issues"]), reverse=True)
    json.dump({"status": "ok", "priority_basis": "none",
               "pages_audited": len(results), "with_issues": len([r for r in ok if r["issues"]]),
               "note": "pixel widths are estimates; verify against live rendering",
               "results": ok,
               "unreachable": [r for r in results if r.get("status") != "ok"]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
