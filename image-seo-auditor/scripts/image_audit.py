#!/usr/bin/env python3
"""Image SEO Auditor — reference implementation.

Auth:   keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 image_audit.py --urls https://a.com/p [--check-bytes]
"""
from __future__ import annotations
import argparse, json, re, sys
import urllib.request, urllib.error
from urllib.parse import urljoin, urlparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "seoskills-image-audit/1.0"}
GENERIC_ALT = {"image", "photo", "picture", "img", "logo", "icon", "banner"}
LEGACY = ("jpg", "jpeg", "png", "gif")


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            if "text/html" not in r.headers.get("Content-Type", ""):
                return 0, ""
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except Exception:
        return 0, ""


def head_size(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception:
        return None


def ext_of(src):
    path = urlparse(src).path.lower()
    m = re.search(r"\.([a-z0-9]+)(?:$|\?)", path)
    return m.group(1) if m else ""


def parse_images(html, base):
    imgs = []
    # collect <picture> format hints
    picture_formats = set()
    for pm in re.finditer(r"<picture[\s\S]*?</picture>", html, re.I):
        for sm in re.finditer(r'<source[^>]+type=["\']image/([a-z0-9]+)["\']', pm.group(0), re.I):
            picture_formats.add(sm.group(1).lower())
    for m in re.finditer(r"<img\b([^>]*)>", html, re.I):
        tag = m.group(1)
        def attr(name):
            mm = re.search(name + r'=["\']([^"\']*)["\']', tag, re.I)
            return mm.group(1) if mm else None
        src = attr("src") or attr("data-src") or ""
        if not src:
            continue
        srcset = attr("srcset") or ""
        fmts = {ext_of(src)} | {ext_of(s.split()[0]) for s in srcset.split(",") if s.strip()} | picture_formats
        imgs.append({
            "src": urljoin(base, src),
            "alt_present": bool(re.search(r'\balt=', tag, re.I)),
            "alt": attr("alt"),
            "width": attr("width"), "height": attr("height"),
            "loading": (attr("loading") or "").lower(),
            "formats": [f for f in fmts if f],
        })
    return imgs


def audit_image(img, index, total, check_size):
    issues = []
    alt = img["alt"]
    if not img["alt_present"]:
        issues.append({"code": "ALT_MISSING", "severity": "critical"})
    elif alt == "":
        if index < total * 0.6:  # likely content image
            issues.append({"code": "ALT_EMPTY_NON_DECORATIVE", "severity": "info"})
    else:
        al = alt.strip().lower()
        fname = urlparse(img["src"]).path.rsplit("/", 1)[-1].lower()
        if al in GENERIC_ALT or al == fname or len(al.split()) > 12:
            issues.append({"code": "ALT_GENERIC", "severity": "warning", "alt": alt[:60]})
    fname = urlparse(img["src"]).path.rsplit("/", 1)[-1]
    if re.match(r"^(img[_-]?\d+|dsc[_-]?\d+|[0-9a-f]{16,}|\d+)\.", fname, re.I):
        issues.append({"code": "NON_DESCRIPTIVE_FILENAME", "severity": "info", "filename": fname})
    if any(f in LEGACY for f in img["formats"]) and not ({"webp", "avif"} & set(img["formats"])):
        issues.append({"code": "LEGACY_FORMAT", "severity": "info", "formats": img["formats"]})
    if not (img["width"] and img["height"]):
        issues.append({"code": "MISSING_DIMENSIONS", "severity": "warning"})
    if index >= 2 and img["loading"] != "lazy":
        issues.append({"code": "NO_LAZY_LOADING", "severity": "info", "heuristic": True})
    if check_size:
        sz = head_size(img["src"])
        if sz is None:
            img["size_unknown"] = True
        else:
            img["bytes"] = sz
            if sz > check_size * 1024:
                issues.append({"code": "OVERSIZE", "severity": "warning", "kb": round(sz / 1024)})
    return issues


def audit_page(url, check_size):
    status, html = get(url)
    if status != 200 or not html:
        return {"url": url, "status": "unreachable", "http_status": status}
    imgs = parse_images(html, url)
    per_image = []
    alts = Counter(i["alt"].strip().lower() for i in imgs if i.get("alt"))
    for idx, img in enumerate(imgs):
        issues = audit_image(img, idx, len(imgs), check_size)
        if img.get("alt") and alts[img["alt"].strip().lower()] > 1:
            issues.append({"code": "DUPLICATE_ALT", "severity": "info"})
        if issues:
            per_image.append({"src": img["src"], "issues": issues})
    total_issues = sum(len(p["issues"]) for p in per_image)
    score = max(0, 100 - total_issues * 4 - sum(1 for p in per_image for i in p["issues"] if i.get("severity") == "critical") * 6)
    return {"url": url, "status": "ok", "image_count": len(imgs),
            "images_with_issues": len(per_image), "image_score": score,
            "images": per_image}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls"); ap.add_argument("--sitemap")
    ap.add_argument("--check-bytes", action="store_true", dest="check_bytes")
    ap.add_argument("--oversize-kb", type=int, default=200, dest="oversize")
    ap.add_argument("--max-pages", type=int, default=1000, dest="max_pages")
    a = ap.parse_args()
    urls = [u.strip() for u in a.urls.split(",")] if a.urls else []
    if a.sitemap:
        _, xml = get(a.sitemap)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    urls = [u for u in urls if u][: a.max_pages]
    size = a.oversize if a.check_bytes else 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda u: audit_page(u, size), urls))
    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: r["image_score"])
    json.dump({"status": "ok", "pages_audited": len(results),
               "results": ok,
               "unreachable": [r for r in results if r.get("status") != "ok"]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
