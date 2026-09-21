#!/usr/bin/env python3
"""Multi-Location Landing Page Auditor — reference implementation.

Auth:   keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 local_page_audit.py --pages pages.json [--gbp gbp.json]
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "seoskills-local-audit/1.0"}
REQUIRED = ["name", "address", "telephone", "geo_or_map", "openingHours"]
LOCAL_TYPES = ("localbusiness", "store", "restaurant", "professionalservice", "medicalbusiness",
               "autorepair", "dentist", "attorney", "homeandconstructionbusiness")


def get(url, timeout=12):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def local_schema(html):
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>([\s\S]*?)</script>', html, re.I):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        for it in (data if isinstance(data, list) else [data]):
            if not isinstance(it, dict):
                continue
            t = it.get("@type", "")
            t = " ".join(t) if isinstance(t, list) else str(t)
            if any(lt in t.lower() for lt in LOCAL_TYPES):
                return it
    return None


def main_text(html):
    html = re.sub(r"<(script|style|nav|header|footer)[\s\S]*?</\1>", " ", html, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def shingles(text, k=5):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return set(tuple(words[i:i + k]) for i in range(max(0, len(words) - k + 1)))


def audit_one(url, gbp):
    status, html = get(url)
    if status != 200 or not html:
        return {"url": url, "status": "unreachable", "http_status": status}
    sch = local_schema(html)
    missing = []
    if not sch:
        missing = list(REQUIRED)
    else:
        if not sch.get("name"):
            missing.append("name")
        addr = sch.get("address")
        if not (isinstance(addr, dict) and addr.get("streetAddress")):
            missing.append("address")
        if not sch.get("telephone"):
            missing.append("telephone")
        if not (sch.get("geo") or sch.get("hasMap")):
            missing.append("geo_or_map")
        if not (sch.get("openingHours") or sch.get("openingHoursSpecification")):
            missing.append("openingHours")
    has_map = bool(re.search(r"google\.com/maps/embed|<iframe[^>]+maps|staticmap", html, re.I)) or bool(sch and (sch.get("geo") or sch.get("hasMap")))
    text = main_text(html)
    words = len(text.split())
    nap_parity = "not_checked"
    if gbp and url in gbp:
        want = gbp[url]
        got_name = (sch or {}).get("name", "")
        got_phone = re.sub(r"\D", "", (sch or {}).get("telephone", "") or "")
        want_phone = re.sub(r"\D", "", want.get("phone", ""))
        nap_parity = "match" if got_name and want.get("name", "").lower() in got_name.lower() and got_phone == want_phone else \
            "missing" if not got_name else "mismatch"
    return {"url": url, "status": "ok", "has_local_schema": bool(sch), "missing_schema_fields": missing,
            "has_map": has_map, "nap_parity": nap_parity, "word_count": words,
            "_shingles": shingles(text), "text_extractable": words > 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", required=True); ap.add_argument("--gbp")
    ap.add_argument("--min-unique", type=int, default=150, dest="min_unique")
    ap.add_argument("--value")
    a = ap.parse_args()
    pages = json.load(open(a.pages))
    gbp = json.load(open(a.gbp)) if a.gbp else None
    value = json.load(open(a.value)) if a.value else {}
    if not pages:
        json.dump({"status": "error", "error": {"code": "NO_PAGES_FOUND"}}, sys.stdout); sys.exit(1)

    with ThreadPoolExecutor(max_workers=5) as ex:
        raw = list(ex.map(lambda u: audit_one(u, gbp), pages))
    ok = [r for r in raw if r.get("status") == "ok"]
    # cross-page duplication
    for r in ok:
        best_sim, best_url = 0.0, None
        for other in ok:
            if other is r or not other.get("_shingles") or not r.get("_shingles"):
                continue
            inter = len(r["_shingles"] & other["_shingles"])
            union = len(r["_shingles"] | other["_shingles"]) or 1
            sim = inter / union
            if sim > best_sim:
                best_sim, best_url = sim, other["url"]
        r["max_similarity"] = round(best_sim, 2)
        r["most_similar_url"] = best_url
        r["duplicate"] = best_sim >= 0.8
        r["thin"] = r["word_count"] < a.min_unique
        score = 100
        score -= len(r["missing_schema_fields"]) * 12
        score -= 0 if r["has_map"] else 12
        score -= {"mismatch": 20, "missing": 12, "match": 0, "not_checked": 0}[r["nap_parity"]]
        score -= 25 if r["duplicate"] else 0
        score -= 15 if r["thin"] else 0
        r["page_score"] = max(0, score)
        r["grade"] = "pass" if r["page_score"] >= 80 else "needs_work" if r["page_score"] >= 50 else "fail"
        r["value"] = value.get(r["url"], 0)
        del r["_shingles"]
    ok.sort(key=lambda r: (r["grade"] != "fail", -r.get("value", 0), r["page_score"]))
    unreachable = [r for r in raw if r.get("status") != "ok"]
    json.dump({"status": "ok", "pages_audited": len(raw),
               "failing": len([r for r in ok if r["grade"] == "fail"]),
               "results": ok, "unreachable": unreachable}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
