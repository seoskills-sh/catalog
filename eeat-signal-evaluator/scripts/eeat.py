#!/usr/bin/env python3
"""E-E-A-T Signal Evaluator — reference implementation.

Auth:   keyless page fetch; KG_API_KEY optional for author entity (else Wikidata).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 eeat.py --url https://site.com/post [--topic "nutrition"] [--ymyl]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
RUBRIC = json.load(open(os.path.join(HERE, "..", "references", "eeat_rubric.json")))


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def get(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "seoskills-eeat/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(500000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def jsonld_blocks(html):
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>([\s\S]*?)</script>', html, re.I):
        try:
            out.append(json.loads(m.group(1)))
        except Exception:
            pass
    return out


def find_author(html, blocks):
    for b in blocks:
        items = b if isinstance(b, list) else [b]
        for it in items:
            a = it.get("author") if isinstance(it, dict) else None
            if isinstance(a, dict) and a.get("name"):
                return a["name"], a
            if isinstance(a, str):
                return a, {}
    m = re.search(r'rel=["\']author["\'][^>]*>([^<]+)<', html, re.I) or \
        re.search(r'class=["\'][^"\']*author[^"\']*["\'][^>]*>\s*([A-Z][a-z]+ [A-Z][a-z]+)', html)
    return (m.group(1).strip(), {}) if m else (None, {})


def kg_or_wikidata(name):
    key = os.environ.get("KG_API_KEY")
    try:
        if key:
            u = "https://kgsearch.googleapis.com/v1/entities:search?" + urllib.parse.urlencode(
                {"query": name, "key": key, "limit": 1, "types": "Person"})
            with urllib.request.urlopen(u, timeout=20) as r:
                d = json.loads(r.read())
                return "recognized" if d.get("itemListElement") else "not_found"
        u = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbsearchentities", "search": name, "language": "en", "format": "json", "limit": 1})
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "seoskills-eeat/1.0"}), timeout=20) as r:
            d = json.loads(r.read())
            return "recognized" if d.get("search") else "not_found"
    except Exception:
        return "unverifiable"


def count_cues(text, cues):
    tl = text.lower()
    return sum(len(re.findall(r"\b" + re.escape(c) + r"\b", tl)) for c in cues)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True); ap.add_argument("--topic", default="")
    ap.add_argument("--ymyl", action="store_true")
    ap.add_argument("--no-author-verify", action="store_true")
    a = ap.parse_args()
    status, html = get(a.url)
    if status == 0:
        fail("PAGE_UNREACHABLE", "Could not fetch the page.")
    if status >= 400:
        fail("PAGE_UNREACHABLE", "HTTP %s" % status, http_status=status)

    blocks = jsonld_blocks(html)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I))
    author, author_obj = find_author(html, blocks)
    date_mod = re.search(r'"dateModified"\s*:\s*"([^"]+)"', html)
    date_pub = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    outbound = re.findall(r'href=["\'](https?://[^"\']+)["\']', html)
    host = urlparse(a.url).netloc
    ext_auth = [u for u in outbound if urlparse(u).netloc != host and
                any(d in u for d in RUBRIC["authoritative_tlds"])]

    exp_cues = count_cues(text, RUBRIC["experience_cues"])
    has_credentials = bool(re.search(RUBRIC["credential_regex"], text, re.I))
    author_status = "not_checked" if a.no_author_verify or not author else kg_or_wikidata(author)

    missing = []
    # EXPERIENCE
    experience = min(100, exp_cues * 20 + (30 if "<img" in html.lower() else 0))
    if experience < 40:
        missing.append("first_hand_experience")
    # EXPERTISE
    expertise = (40 if author else 0) + (40 if has_credentials else 0) + (20 if author_obj.get("jobTitle") else 0)
    if not author:
        missing.append("author_identity")
    if not has_credentials:
        missing.append("author_credentials")
    # AUTHORITATIVENESS
    if author_status == "recognized":
        auth = 60 + min(40, len(ext_auth) * 10)
    elif author_status == "unverifiable":
        auth = min(100, 20 + len(ext_auth) * 10)
    else:
        auth = min(100, len(ext_auth) * 12)
        missing.append("authoritative_citations") if len(ext_auth) < 2 else None
    # TRUST
    trust = (25 if a.url.startswith("https") else 0) + (25 if date_mod else 0) + \
            (25 if ext_auth else 0) + (25 if any(k in html.lower() for k in ("/about", "/contact", "privacy")) else 0)
    if not date_mod:
        missing.append("freshness_datemodified")

    w = RUBRIC["ymyl_weights"] if a.ymyl else RUBRIC["default_weights"]
    overall = round(experience * w["experience"] + expertise * w["expertise"] +
                    auth * w["authoritativeness"] + trust * w["trust"])
    json.dump({"status": "ok", "url": a.url, "is_ymyl": a.ymyl,
               "author": author, "author_entity_status": author_status,
               "scores": {"experience": experience, "expertise": expertise,
                          "authoritativeness": auth, "trust": trust, "overall": overall},
               "authoritative_citations": len(ext_auth),
               "date_published": date_pub.group(1) if date_pub else None,
               "date_modified": date_mod.group(1) if date_mod else None,
               "missing_signals": [m for m in missing if m]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
