#!/usr/bin/env python3
"""On-Page Schema Coverage Auditor — reference implementation.

Auth:   keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 schema_coverage.py --urls https://a.com/p,https://a.com/q
"""
from __future__ import annotations
import argparse, json, os, re, sys
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "seoskills-schema-audit/1.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
CTS = json.load(open(os.path.join(HERE, "..", "references", "content_type_schema.json")))["types"]


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


def classify(url, html):
    h = html.lower()
    scores = {}
    if re.search(r'"@type"\s*:\s*"product"|itemprop=["\']price|add to cart', h):
        scores["product"] = 3
    if re.search(r'faq|frequently asked|<summary|itemprop=["\']acceptedanswer', h):
        scores["faq"] = 2
    if re.search(r'step\s*\d|<ol[^>]*>[\s\S]*<li|how to', h):
        scores["howto"] = 1
    if re.search(r'itemprop=["\']address|postaladdress|"streetaddress"', h):
        scores["localbusiness"] = 2
    if re.search(r'<article|"@type"\s*:\s*"(article|blogposting|newsarticle)"|datepublished', h):
        scores["article"] = 2
    if re.search(r'ingredients|recipe|cook time', h):
        scores["recipe"] = 2
    if not scores:
        return "webpage", 0.3
    best = max(scores, key=scores.get)
    conf = min(1.0, scores[best] / 3)
    return best, round(conf, 2)


def present_schema(html):
    types, blocks = set(), []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>([\s\S]*?)</script>', html, re.I):
        try:
            data = json.loads(m.group(1))
        except Exception:
            return types, blocks, True  # invalid jsonld flag
        for it in (data if isinstance(data, list) else [data]):
            if isinstance(it, dict):
                t = it.get("@type", "")
                for tt in ([t] if isinstance(t, str) else t):
                    types.add(str(tt).lower())
                blocks.append(it)
    return types, blocks, False


ISO4217 = re.compile(r"^[A-Z]{3}$")


def validate(block, t):
    issues = []
    if t == "product":
        offers = block.get("offers", {})
        if isinstance(offers, dict):
            cur = offers.get("priceCurrency")
            if cur and not ISO4217.match(str(cur)):
                issues.append({"code": "INVALID_VALUE", "property": "offers.priceCurrency", "value": cur})
        ar = block.get("aggregateRating", {})
        if isinstance(ar, dict) and ar.get("ratingValue"):
            try:
                v = float(ar["ratingValue"])
                if not (0 <= v <= 5):
                    issues.append({"code": "INVALID_VALUE", "property": "aggregateRating.ratingValue", "value": v})
            except Exception:
                issues.append({"code": "INVALID_VALUE", "property": "aggregateRating.ratingValue"})
    return issues


def audit(url, html, hint):
    detected, conf = (hint, 1.0) if hint else classify(url, html)
    spec = CTS.get(detected, CTS["webpage"])
    types, blocks, invalid = present_schema(html)
    issues = []
    if invalid:
        issues.append({"code": "INVALID_JSONLD"})
    for rt in spec["recommended_types"]:
        if rt.lower() not in types:
            issues.append({"code": "SCHEMA_MISSING", "type": rt})
        else:
            block = next((b for b in blocks if str(b.get("@type", "")).lower() == rt.lower()), {})
            for req in spec.get("required", {}).get(rt, []):
                if req not in block:
                    issues.append({"code": "REQUIRED_PROPERTY_MISSING", "type": rt, "property": req})
            for rec in spec.get("recommended", {}).get(rt, []):
                if rec not in block:
                    issues.append({"code": "RECOMMENDED_PROPERTY_MISSING", "type": rt, "property": rec, "severity": "info"})
            issues += validate(block, rt.lower())
    lost = spec.get("rich_results", []) if any(i["code"] in ("SCHEMA_MISSING", "REQUIRED_PROPERTY_MISSING") for i in issues) else []
    penalty = {"SCHEMA_MISSING": 30, "REQUIRED_PROPERTY_MISSING": 15, "INVALID_VALUE": 15,
               "INVALID_JSONLD": 20, "RECOMMENDED_PROPERTY_MISSING": 4}
    score = max(0, 100 - sum(penalty.get(i["code"], 5) for i in issues))
    return {"url": url, "status": "ok", "detected_type": detected, "classification_confidence": conf,
            "recommended_types": spec["recommended_types"], "present_types": sorted(types),
            "coverage_score": score, "grade": "pass" if score >= 80 else "needs_work" if score >= 50 else "fail",
            "lost_rich_results": lost, "issues": issues}


def get_and_audit(url, hint):
    status, html = get(url)
    if status != 200 or not html:
        return {"url": url, "status": "unreachable", "http_status": status}
    return audit(url, html, hint)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls"); ap.add_argument("--sitemap"); ap.add_argument("--hints")
    ap.add_argument("--max-pages", type=int, default=2000, dest="max_pages")
    a = ap.parse_args()
    urls = [u.strip() for u in a.urls.split(",")] if a.urls else []
    if a.sitemap:
        _, xml = get(a.sitemap)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    urls = [u for u in urls if u][: a.max_pages]
    hints = json.load(open(a.hints)) if a.hints else {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda u: get_and_audit(u, hints.get(u)), urls))
    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: r["coverage_score"])
    json.dump({"status": "ok", "pages_audited": len(results),
               "failing": len([r for r in ok if r["grade"] == "fail"]),
               "results": ok,
               "unreachable": [r for r in results if r.get("status") != "ok"]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
