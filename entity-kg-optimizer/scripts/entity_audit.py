#!/usr/bin/env python3
"""Entity Knowledge-Graph Optimizer — reference implementation.

Auth:   KG_API_KEY (Google Knowledge Graph Search API) — optional; Wikidata is
        keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 entity_audit.py --brand brand.json [--qid Q123]
  brand.json: {"name":"Acme","domain":"acme.com","type_hint":"Organization",
               "expected_sameas":["https://linkedin.com/company/acme"]}
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.request, urllib.error, urllib.parse

UA = {"User-Agent": "seoskills-entity-optimizer/1.0"}
WEIGHTS = {"NOT_IN_KG": 15, "NOT_IN_WIKIDATA": 25, "MISSING_OFFICIAL_WEBSITE": 20,
           "WEAK_SAMEAS": 12, "MISSING_ENTITY_TYPE": 12, "THIN_DESCRIPTION": 6, "NO_CORROBORATION": 10}


def get(url, timeout=30):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return 200, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(2 ** attempt); continue
            return e.code, {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}
    return 429, {}


def domain_match(text, domain):
    return bool(text) and domain.lower().lstrip("www.") in text.lower()


def resolve_kg(name, type_hint, domain, key):
    if not key:
        return {"kg_status": "skipped_no_key"}
    q = urllib.parse.urlencode({"query": name, "types": type_hint or "", "key": key, "limit": 5})
    status, data = get(f"https://kgsearch.googleapis.com/v1/entities:search?{q}")
    if status == 429:
        return {"kg_status": "rate_limited"}
    if status != 200:
        return {"kg_status": "error"}
    for item in data.get("itemListElement", []):
        r = item.get("result", {})
        desc_url = r.get("detailedDescription", {}).get("url", "")
        if domain_match(r.get("url", ""), domain) or domain_match(desc_url, domain):
            return {"kg_status": "ok", "kg_entity": "recognized", "types": r.get("@type"),
                    "description": r.get("description"), "detailed": bool(desc_url),
                    "score": item.get("resultScore")}
    return {"kg_status": "ok", "kg_entity": "unrecognized"}


def resolve_wikidata(name, domain, qid):
    if not qid:
        status, data = get("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbsearchentities", "search": name, "language": "en", "format": "json", "limit": 7}))
        if status != 200:
            return {"wd_status": "error"}
        candidates = [c["id"] for c in data.get("search", [])]
    else:
        candidates = [qid]
    for qi in candidates:
        time.sleep(0.15)
        status, data = get("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbgetentities", "ids": qi, "format": "json", "props": "claims|descriptions"}))
        if status != 200:
            continue
        ent = data.get("entities", {}).get(qi, {})
        claims = ent.get("claims", {})
        website = ""
        for c in claims.get("P856", []):
            website = c.get("mainsnak", {}).get("datavalue", {}).get("value", "")
        if not qid and not domain_match(website, domain):
            continue
        desc = ent.get("descriptions", {}).get("en", {}).get("value", "")
        p31 = [c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id") for c in claims.get("P31", [])]
        refs = sum(len(c.get("references", [])) for cl in claims.values() for c in cl)
        return {"wd_status": "ok", "wd_entity": "recognized", "qid": qi,
                "has_official_website": domain_match(website, domain),
                "instance_of": [x for x in p31 if x], "description": desc, "reference_count": refs}
    return {"wd_status": "ok", "wd_entity": "unrecognized_or_ambiguous"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True); ap.add_argument("--qid")
    a = ap.parse_args()
    brand = json.load(open(a.brand))
    name, domain = brand["name"], brand["domain"]
    kg = resolve_kg(name, brand.get("type_hint"), domain, os.environ.get("KG_API_KEY"))
    wd = resolve_wikidata(name, domain, a.qid or brand.get("wikidata_qid"))

    if kg.get("kg_status") in ("error", "rate_limited", None) and wd.get("wd_status") == "error":
        json.dump({"status": "sources_unavailable", "kg": kg, "wikidata": wd}, sys.stdout); return

    findings = []
    def add(code, severity, rec):
        findings.append({"code": code, "severity": severity, "recommendation": rec})

    if kg.get("kg_status") == "ok" and kg.get("kg_entity") == "unrecognized":
        add("NOT_IN_KG", "high", f"Strengthen entity signals so Google's Knowledge Graph recognizes {name}: consistent NAP, Organization schema, and authoritative mentions.")
    if wd.get("wd_entity") in ("unrecognized_or_ambiguous", None):
        add("NOT_IN_WIKIDATA", "high", f"Create a Wikidata item for {name} with official website P856={domain} and a clear instance-of (P31). This is the highest-leverage AI-grounding fix.")
    elif not wd.get("has_official_website"):
        add("MISSING_OFFICIAL_WEBSITE", "high", f"Add official website (P856)={domain} to the Wikidata item {wd.get('qid')}.")
    if wd.get("wd_entity") == "recognized" and not wd.get("instance_of"):
        add("MISSING_ENTITY_TYPE", "medium", "Add an instance-of (P31) claim so the entity's type is unambiguous.")
    if brand.get("expected_sameas"):
        add("WEAK_SAMEAS", "medium", "Publish Organization schema on the site with sameAs linking: " + ", ".join(brand["expected_sameas"]) + "; and add these as identifiers on the Wikidata item.")
    desc = (wd.get("description") or kg.get("description") or "")
    if desc and len(desc.split()) < 10:
        add("THIN_DESCRIPTION", "low", "Expand the entity description with a precise, sourced one-liner of what the brand is and does.")
    if (wd.get("reference_count", 0) or 0) < 2 and not kg.get("detailed"):
        add("NO_CORROBORATION", "medium", "Earn coverage on ≥2 independent authoritative sources and cite them on the Wikidata item to corroborate the entity.")

    max_score = sum(WEIGHTS.values())
    lost = sum(WEIGHTS.get(f["code"], 0) for f in findings)
    grounding = round(100 * (max_score - lost) / max_score)
    json.dump({"status": "ok", "brand": name, "kg": kg, "wikidata": wd,
               "grounding_score": grounding, "finding_count": len(findings),
               "findings": sorted(findings, key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["severity"]])},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
