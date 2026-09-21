#!/usr/bin/env python3
"""Multi-Location NAP Consistency Crawler — reference implementation.

Auth:   keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 nap_crawl.py --locations locations.json [--citations citations.json]
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request, urllib.error

UA = {"User-Agent": "seoskills-nap/1.0"}
ADDR_ABBR = {"street": "st", "avenue": "ave", "boulevard": "blvd", "suite": "ste", "road": "rd",
             "drive": "dr", "lane": "ln", "north": "n", "south": "s", "east": "e", "west": "w"}
LEGAL = re.compile(r"\b(inc|llc|ltd|co|corp|company|incorporated)\b\.?", re.I)


def get(url, timeout=15):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def norm_name(n):
    return re.sub(r"\s+", " ", LEGAL.sub("", (n or "").lower())).strip(" .,")


def norm_addr(a):
    a = (a or "").lower()
    for full, ab in ADDR_ABBR.items():
        a = re.sub(r"\b" + full + r"\b", ab, a)
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", a)).strip()


def norm_phone(p, strict):
    return (p or "") if strict else re.sub(r"\D", "", p or "")


def extract_nap(html):
    # JSON-LD LocalBusiness / PostalAddress first
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>([\s\S]*?)</script>', html, re.I):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            addr = it.get("address", {})
            if isinstance(addr, dict) and (addr.get("streetAddress") or it.get("telephone")):
                street = " ".join(filter(None, [addr.get("streetAddress"), addr.get("addressLocality"),
                                                addr.get("addressRegion"), addr.get("postalCode")]))
                return {"name": it.get("name"), "address": street, "phone": it.get("telephone"), "source": "jsonld"}
    # regex fallback
    phone = re.search(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", html)
    return {"name": None, "address": None, "phone": phone.group(0) if phone else None, "source": "regex"} if phone else None


def check_source(url, canonical, strict):
    status, html = get(url)
    if status in (403, 429) or "captcha" in html.lower():
        return {"url": url, "extraction": "unverifiable"}
    if status != 200 or not html:
        return {"url": url, "extraction": "failed"}
    nap = extract_nap(html)
    if not nap:
        return {"url": url, "extraction": "failed"}
    fields = {}
    for f, normfn in (("name", norm_name), ("address", norm_addr),
                      ("phone", lambda x: norm_phone(x, strict))):
        cval = normfn(canonical.get(f))
        sval = normfn(nap.get(f)) if nap.get(f) else None
        fields[f] = {"status": "missing" if not sval else "match" if sval == cval else "mismatch",
                     "canonical": canonical.get(f), "found_raw": nap.get(f)}
    inconsistent = any(v["status"] == "mismatch" for v in fields.values())
    return {"url": url, "extraction": "ok", "source_type": nap["source"],
            "inconsistent": inconsistent, "fields": fields}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locations", required=True); ap.add_argument("--citations")
    ap.add_argument("--strict-phone", action="store_true", dest="strict")
    a = ap.parse_args()
    locations = json.load(open(a.locations))
    citations = json.load(open(a.citations)) if a.citations else {}

    results = []
    for loc in locations:
        canon = loc["canonical"]
        sources = list(loc.get("page_urls", [])) + list(citations.get(loc["id"], []))
        checks, matched, total = [], 0, 0
        for url in sources:
            c = check_source(url, canon, a.strict)
            checks.append(c)
            if c.get("extraction") == "ok":
                for v in c["fields"].values():
                    total += 1
                    if v["status"] == "match":
                        matched += 1
            time.sleep(0.25)
        missing = [u for u in citations.get(loc["id"], [])
                   if next((c for c in checks if c["url"] == u), {}).get("extraction") == "failed"]
        results.append({"location_id": loc["id"],
                        "consistency": round(matched / total, 2) if total else None,
                        "inconsistent_sources": [c for c in checks if c.get("inconsistent")],
                        "unverifiable_sources": [c["url"] for c in checks if c.get("extraction") == "unverifiable"],
                        "missing_citations": missing, "all_checks": checks})
    json.dump({"status": "ok", "locations_audited": len(results), "results": results}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
