#!/usr/bin/env python3
"""Heading Structure Auditor — reference implementation.

Auth:   keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 heading_audit.py --urls https://a.com/p,https://a.com/q
       python3 heading_audit.py --sitemap https://a.com/sitemap.xml
"""
from __future__ import annotations
import argparse, json, re, sys
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "seoskills-heading-audit/1.0"}
GENERIC = {"read more", "introduction", "click here", "untitled", "overview",
           "learn more", "more", "heading", "title"}


def get(url, timeout=12):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            if "text/html" not in r.headers.get("Content-Type", ""):
                return 0, ""
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def extract_headings(html):
    # drop nav/header/footer regions to reduce chrome-heading noise
    body = re.sub(r"<(nav|header|footer)[\s\S]*?</\1>", " ", html, flags=re.I)
    out = []
    for m in re.finditer(r"<h([1-6])\b[^>]*>([\s\S]*?)</h\1>", body, re.I):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        out.append({"level": int(m.group(1)), "text": text})
    return out


def audit(url, keyword):
    status, html = get(url)
    if status != 200 or not html:
        return {"url": url, "status": "unreachable", "http_status": status}
    headings = extract_headings(html)
    issues = []
    h1s = [h for h in headings if h["level"] == 1]
    if len(h1s) == 0:
        issues.append({"code": "NO_H1"})
    elif len(h1s) > 1:
        issues.append({"code": "MULTIPLE_H1", "count": len(h1s)})
    prev = None
    for h in headings:
        if prev is not None and h["level"] > prev + 1:
            issues.append({"code": "LEVEL_SKIP", "from": f"H{prev}", "to": f"H{h['level']}", "text": h["text"][:60]})
        prev = h["level"]
        if not h["text"]:
            issues.append({"code": "EMPTY_HEADING", "level": h["level"]})
        elif h["text"].lower() in GENERIC:
            issues.append({"code": "GENERIC_HEADING", "text": h["text"]})
    if keyword and h1s:
        toks = [t for t in re.split(r"\W+", keyword.lower()) if len(t) > 2]
        if not all(t in h1s[0]["text"].lower() for t in toks):
            issues.append({"code": "H1_KEYWORD_MISSING", "h1": h1s[0]["text"][:80], "keyword": keyword})
    score = max(0, 100 - sum({"NO_H1": 30, "MULTIPLE_H1": 20, "LEVEL_SKIP": 10, "EMPTY_HEADING": 8,
                              "GENERIC_HEADING": 5, "H1_KEYWORD_MISSING": 12}.get(i["code"], 5) for i in issues))
    grade = "pass" if score >= 80 else "needs_work" if score >= 50 else "fail"
    return {"url": url, "status": "ok", "heading_count": len(headings), "no_headings": not headings,
            "structure_score": score, "grade": grade, "issues": issues,
            "outline": [{"level": h["level"], "text": h["text"][:80]} for h in headings]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls"); ap.add_argument("--sitemap")
    ap.add_argument("--keywords"); ap.add_argument("--max-pages", type=int, default=2000, dest="max_pages")
    a = ap.parse_args()
    urls = [u.strip() for u in a.urls.split(",")] if a.urls else []
    if a.sitemap:
        _, xml = get(a.sitemap)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    urls = [u for u in urls if u][: a.max_pages]
    kw = json.load(open(a.keywords)) if a.keywords else {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda u: audit(u, kw.get(u)), urls))
    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: r["structure_score"])
    json.dump({"status": "ok", "pages_audited": len(results),
               "failing": len([r for r in ok if r["grade"] == "fail"]),
               "results": ok,
               "unreachable": [r for r in results if r.get("status") != "ok"]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
