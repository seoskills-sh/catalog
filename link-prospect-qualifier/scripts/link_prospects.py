#!/usr/bin/env python3
"""Link Prospect Qualifier — reference implementation.

Auth:   SERP_API_KEY (discovery). Optional DATAFORSEO_LOGIN/PASSWORD for
        competitor-backlink discovery + authority.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 link_prospects.py --topic "sustainable packaging" [--competitors a.com,b.com]
"""
from __future__ import annotations
import argparse, base64, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from urllib.parse import urlparse

SERP = "https://serpapi.com/search.json"
DFS = "https://api.dataforseo.com/v3/backlinks/referring_domains/live"
SPAM_TLDS = {".xyz", ".top", ".loan", ".click", ".tk", ".ml", ".gq"}
DEFAULT_FOOTPRINTS = ['"{topic}" "write for us"', '"{topic}" resources', '"{topic}" inurl:links',
                      '"{topic}" "guest post"', '{topic} blog']


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def domain_of(u):
    return urlparse(u if "://" in u else "http://" + u).netloc.lower().lstrip("www.")


def serp(key, q):
    url = SERP + "?" + urllib.parse.urlencode({"engine": "google", "q": q, "num": 20, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                d = json.loads(r.read())
                return 200, [(o.get("link"), o.get("title", ""), o.get("snippet", ""))
                             for o in d.get("organic_results", []) if o.get("link")]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, []
                time.sleep(2 ** attempt); continue
            return e.code, []
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, []
    return 429, []


def competitor_domains(comp, hdr):
    body = json.dumps([{"target": comp, "limit": 500, "order_by": ["rank,desc"]}]).encode()
    req = urllib.request.Request(DFS, data=body, headers={"Content-Type": "application/json", **hdr})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = (json.loads(r.read()).get("tasks") or [{}])[0].get("result") or []
            return {it["domain"]: it.get("rank", 0) or 0 for it in (res[0].get("items", []) if res else []) if it.get("domain")}
    except Exception:
        return {}


def relevance(topic, text):
    toks = set(t for t in re.split(r"\W+", topic.lower()) if len(t) > 2)
    words = set(re.split(r"\W+", (text or "").lower()))
    return round(len(toks & words) / len(toks), 2) if toks else 0.0


def contact_hint(domain):
    for path in ("/contact", "/write-for-us", "/contribute"):
        try:
            u = "https://" + domain + path
            req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": "seoskills-prospect/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status < 400:
                    return u
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--competitors", default="")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--max", type=int, default=200)
    a = ap.parse_args()
    serp_key = os.environ.get("SERP_API_KEY")
    competitors = [c.strip() for c in a.competitors.split(",") if c.strip()]
    if not serp_key and not competitors:
        fail("NO_DISCOVERY_SOURCE", "Provide SERP_API_KEY and/or --competitors.")
    exclude = {domain_of(x) for x in a.exclude.split(",") if x.strip()}

    candidates = {}  # domain -> {relevance, authority, text, links_to_competitor}
    basis = "heuristic"
    # SERP discovery
    if serp_key:
        for fp in DEFAULT_FOOTPRINTS:
            st, rows = serp(serp_key, fp.format(topic=a.topic))
            if st == 429:
                fail("RATE_LIMITED", "SERP quota exhausted.", partial=len(candidates))
            for link, title, snippet in rows:
                d = domain_of(link)
                if d and d not in exclude:
                    c = candidates.setdefault(d, {"relevance": 0, "authority": 0, "links_to_competitor": False})
                    c["relevance"] = max(c["relevance"], relevance(a.topic, title + " " + snippet))
                    c["authority"] = max(c["authority"], 30)  # heuristic proxy
            time.sleep(0.4)
    # Backlink discovery
    login, pw = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    comp_sets = {}
    if competitors and login and pw:
        hdr = {"Authorization": "Basic " + base64.b64encode(f"{login}:{pw}".encode()).decode()}
        basis = "backlink_provider"
        for comp in competitors:
            comp_sets[comp] = competitor_domains(comp, hdr)
            for d, rank in comp_sets[comp].items():
                if d in exclude:
                    continue
                c = candidates.setdefault(d, {"relevance": 0.3, "authority": 0, "links_to_competitor": True})
                c["authority"] = max(c["authority"], rank)
                c["links_to_competitor"] = True
            time.sleep(0.3)

    qualified, disq = [], 0
    for d, c in candidates.items():
        tld = "." + d.rsplit(".", 1)[-1] if "." in d else ""
        spam = tld in SPAM_TLDS
        is_q = c["relevance"] >= 0.3 and not spam
        if not is_q:
            disq += 1
            continue
        pri = round(c["authority"] * max(c["relevance"], 0.1) * (1.3 if c["links_to_competitor"] else 1.0), 1)
        qualified.append({"domain": d, "relevance": c["relevance"], "authority": c["authority"],
                          "authority_metric": "domain_rank" if basis == "backlink_provider" else "heuristic",
                          "links_to_competitor": c["links_to_competitor"], "spam_risk": "high" if spam else "low",
                          "priority": pri})
    qualified.sort(key=lambda x: x["priority"], reverse=True)
    qualified = qualified[:a.max]
    for q in qualified[:50]:  # contact hint only for the top slice (bounded)
        q["contact_hint"] = contact_hint(q["domain"])
    status = "ok" if qualified else "no_prospects"
    json.dump({"status": status, "topic": a.topic, "scoring_basis": basis,
               "qualified_count": len(qualified), "disqualified_count": disq,
               "prospects": qualified}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
