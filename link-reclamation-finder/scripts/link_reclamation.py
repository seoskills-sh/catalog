#!/usr/bin/env python3
"""Link Reclamation Finder — reference implementation.

Auth:   SERP_API_KEY (mentions) + DATAFORSEO_LOGIN/PASSWORD or BACKLINK_API_KEY
        (broken backlinks). Either source alone is usable.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 link_reclamation.py --brand brand.json
  brand.json: {"name":"Acme","domain":"acme.com","aliases":["Acme Inc"]}
"""
from __future__ import annotations
import argparse, base64, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from urllib.parse import urlparse

SERP = "https://serpapi.com/search.json"
DFS = "https://api.dataforseo.com/v3/backlinks/backlinks/live"
UA = {"User-Agent": "seoskills-reclamation/1.0"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def get(url, timeout=10, method="GET"):
    try:
        req = urllib.request.Request(url, headers=UA, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(300000).decode("utf-8", "ignore") if method == "GET" else ""
            return r.status, body, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, "", url
    except Exception:
        return 0, "", url


def serp_mentions(key, brand, domain):
    q = urllib.parse.urlencode({"engine": "google", "q": f'"{brand}" -site:{domain}', "num": 30, "api_key": key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(SERP + "?" + q, timeout=45) as r:
                d = json.loads(r.read())
                return 200, [o.get("link") for o in d.get("organic_results", []) if o.get("link")]
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


def inbound_backlinks(domain, hdr, limit=1000):
    body = json.dumps([{"target": domain, "mode": "as_is", "limit": limit,
                        "order_by": ["rank,desc"]}]).encode()
    req = urllib.request.Request(DFS, data=body, headers={"Content-Type": "application/json", **hdr})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = (json.loads(r.read()).get("tasks") or [{}])[0].get("result") or []
            return [(it.get("url_to"), it.get("url_from"), it.get("rank", 0) or 0)
                    for it in (res[0].get("items", []) if res else []) if it.get("url_to")]
    except urllib.error.HTTPError as e:
        if e.code == 402:
            return "payment"
        return None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--max", type=int, default=200)
    a = ap.parse_args()
    brand = json.load(open(a.brand))
    name, domain = brand["name"], brand["domain"].lower().lstrip("www.")
    serp_key = os.environ.get("SERP_API_KEY")
    login, pw = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if not serp_key and not (login and pw):
        fail("NO_SOURCES_AVAILABLE", "Provide SERP_API_KEY and/or DATAFORSEO credentials.")

    notes = {}
    unlinked = []
    if serp_key:
        st, pages = serp_mentions(serp_key, name, domain)
        if st == 429:
            notes["mentions"] = "rate_limited"
        else:
            terms = [name] + brand.get("aliases", [])
            for p in pages:
                status, html, _ = get(p)
                if status != 200 or not html:
                    continue
                mentioned = any(re.search(r"\b" + re.escape(t) + r"\b", html, re.I) for t in terms)
                linked = domain in " ".join(re.findall(r'href=["\']([^"\']+)["\']', html)).lower()
                if mentioned and not linked:
                    unlinked.append({"url": p, "action": "request a link on the existing mention"})
                time.sleep(0.2)
    else:
        notes["mentions"] = "skipped_no_serp_key"

    broken = []
    if login and pw:
        hdr = {"Authorization": "Basic " + base64.b64encode(f"{login}:{pw}".encode()).decode()}
        rows = inbound_backlinks(domain, hdr)
        if rows == "payment":
            notes["broken"] = "provider_payment_required"
        elif rows is None:
            notes["broken"] = "provider_error"
        else:
            from collections import defaultdict
            targets = defaultdict(lambda: {"refs": [], "rank": 0})
            for url_to, url_from, rank in rows:
                targets[url_to]["refs"].append(url_from)
                targets[url_to]["rank"] = max(targets[url_to]["rank"], rank)
            checked = 0
            for url_to, info in sorted(targets.items(), key=lambda kv: kv[1]["rank"], reverse=True):
                if checked >= 300:
                    break
                status, _, final = get(url_to, method="HEAD")
                checked += 1
                if status in (404, 410):
                    broken.append({"broken_target": url_to, "type": "broken_target",
                                   "referring_pages": info["refs"][:10], "authority": info["rank"],
                                   "action": "restore the URL or 301 it to the best equivalent"})
                elif 300 <= status < 400:
                    broken.append({"broken_target": url_to, "type": "redirected_target",
                                   "referring_pages": info["refs"][:10], "authority": info["rank"],
                                   "action": "ensure the redirect is a single 301 to a relevant page"})
                time.sleep(0.1)
    else:
        notes["broken"] = "skipped_no_backlink_provider"

    broken.sort(key=lambda b: b["authority"], reverse=True)
    json.dump({"status": "ok", "brand": name, "notes": notes,
               "unlinked_mention_count": len(unlinked), "broken_backlink_count": len(broken),
               "unlinked_mentions": unlinked[:a.max], "broken_backlinks": broken[:a.max]},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
