#!/usr/bin/env python3
"""TF-IDF Entity Gap Analyzer — reference implementation.

Auth:   SERP_API_KEY (required); NL_API_KEY (optional, else local TF-IDF).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 entity_gap.py --query "best crm" --url https://site.com/crm
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, time, urllib.request, urllib.error, urllib.parse
from collections import Counter
from urllib.parse import urlparse

SERP = "https://serpapi.com/search.json"
NL = "https://language.googleapis.com/v1/documents:analyzeEntities?key={key}"
STOP = set("the a an and or but of to in on for with is are was were be been by at from as it this that these those you your we our they their he she his her its i".split())


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def http_get(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "seoskills-entity-gap/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "text/html" not in r.headers.get("Content-Type", ""):
                return None
            return r.read(400000).decode("utf-8", "ignore")
    except Exception:
        return None


def main_text(html):
    if not html:
        return ""
    html = re.sub(r"<(script|style|nav|header|footer|aside)[\s\S]*?</\1>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    return [w for w in re.findall(r"[a-z][a-z\-']{2,}", text.lower()) if w not in STOP]


def serp_top(key, query, n):
    q = urllib.parse.urlencode({"engine": "google", "q": query, "num": n, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(SERP + "?" + q, timeout=45) as r:
                d = json.loads(r.read())
                return [o["link"] for o in d.get("organic_results", []) if o.get("link")][:n]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    fail("RATE_LIMITED", "SERP quota exhausted.")
                time.sleep(2 ** attempt); continue
            fail("REQUEST_FAILED", "SERP HTTP %s" % e.code)
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            fail("REQUEST_FAILED", "SERP unreachable")


def nl_entities(text, key):
    body = json.dumps({"document": {"type": "PLAIN_TEXT", "content": text[:100000]},
                       "encodingType": "UTF8"}).encode()
    req = urllib.request.Request(NL.format(key=key), data=body, headers={"Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
                return {e["name"].lower(): e.get("salience", 0) for e in d.get("entities", [])}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(2 ** attempt); continue
            return None
        except Exception:
            return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True); ap.add_argument("--url", required=True)
    ap.add_argument("--competitors", type=int, default=10)
    a = ap.parse_args()
    serp_key = os.environ.get("SERP_API_KEY")
    if not serp_key:
        fail("AUTH_MISSING_SERP_KEY", "Set SERP_API_KEY.")
    nl_key = os.environ.get("NL_API_KEY")
    backend = "google_nl" if nl_key else "tfidf"
    target_host = urlparse(a.url).netloc.lower().lstrip("www.")

    urls = [u for u in serp_top(serp_key, a.query, a.competitors + 3)
            if urlparse(u).netloc.lower().lstrip("www.") != target_host][: a.competitors]
    target_text = main_text(http_get(a.url))
    docs, skipped, mixed = [], [], False
    for u in urls:
        t = main_text(http_get(u))
        if len(t.split()) < 200:
            skipped.append({"url": u, "reason": "thin_or_unreachable"}); continue
        docs.append((u, t))
        time.sleep(0.2)
    if len(docs) < 3:
        fail("INSUFFICIENT_CORPUS", "Fewer than 3 competitor pages could be profiled.")

    # profile each doc -> {term: weight}
    def profile(text):
        if backend == "google_nl" and nl_key:
            ents = nl_entities(text, nl_key)
            if ents is not None:
                return ents, "google_nl"
        # TF-IDF fallback
        return {w: c for w, c in Counter(tokens(text)).most_common(60)}, "tfidf"

    corpus_terms = Counter()
    doc_weight = {}
    for u, t in docs:
        prof, used = profile(t)
        if used != backend:
            mixed = True
        doc_weight[u] = prof
        for term in prof:
            corpus_terms[term] += 1

    tgt_prof, _ = profile(target_text) if target_text else ({}, "tfidf")
    tgt_terms = set(tgt_prof.keys())

    gaps, covered = [], []
    for term, doc_count in corpus_terms.items():
        coverage = doc_count / len(docs)
        mean_w = sum(dw.get(term, 0) for dw in doc_weight.values()) / len(docs)
        entry = {"term": term, "coverage": round(coverage, 2), "mean_weight": round(mean_w, 4),
                 "priority": round(coverage * mean_w, 5)}
        if coverage >= 0.4 and term not in tgt_terms:
            gaps.append(entry)
        elif term in tgt_terms and coverage >= 0.4:
            covered.append(term)
    gaps.sort(key=lambda g: g["priority"], reverse=True)
    json.dump({"status": "ok", "query": a.query, "target_url": a.url, "entity_backend": backend,
               "mixed_backend": mixed, "competitors_profiled": len(docs),
               "target_thin": len(target_text.split()) < 200, "gap_count": len(gaps),
               "gaps": gaps[:60], "covered_well": covered[:40], "skipped_competitors": skipped},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
