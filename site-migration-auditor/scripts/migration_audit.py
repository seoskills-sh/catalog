#!/usr/bin/env python3
"""Site Migration Auditor — reference implementation.

Compares a pre-migration crawl (old URLs + their SEO signals) against the live
post-migration site. For every old URL it walks the redirect chain (auto-follow
DISABLED so each hop is observed), verifies a single 301 to a live canonical
equivalent, then diffs title / canonical / structured data / indexability to
score parity and quantify traffic-at-risk (joined from GSC clicks).

Auth:   none required for crawling. Optional GSC clicks via a local --gsc file,
        or live via --site + GSC_OAUTH_TOKEN (searchAnalytics, page dimension).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 migration_audit.py --old-crawl old.json [--new-crawl new.json] \
      [--gsc clicks.json] [--site sc-domain:example.com] [--max-urls 5000] \
      [--risk-threshold 70]
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.parse
import http.client
import urllib.request, urllib.error
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

UA = "seoskills-migration-auditor/1.0"
PERMANENT = {301, 308}
TEMPORARY = {302, 303, 307}
GSC_ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
ROW_LIMIT = 25000


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


class PageParser(HTMLParser):
    """Extracts the SEO signals a migration diff depends on."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.h1 = None
        self.canonical = None
        self.meta_robots = ""
        self.internal_links = 0
        self.jsonld_types = set()
        self._in_title = False
        self._in_h1 = False
        self._in_jsonld = False
        self._jsonld_buf = []

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and self.h1 is None:
            self._in_h1 = True
        elif tag == "link":
            rel = d.get("rel", "").lower()
            if "canonical" in rel and d.get("href") and self.canonical is None:
                self.canonical = d["href"]
        elif tag == "meta" and d.get("name", "").lower() == "robots":
            self.meta_robots = d.get("content", "").lower()
        elif tag == "a" and d.get("href"):
            self.internal_links += 1
        elif tag == "script" and d.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            try:
                self._collect(json.loads("".join(self._jsonld_buf)))
            except Exception:
                pass

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data
        if self._in_h1:
            self.h1 = (self.h1 or "") + data
        if self._in_jsonld:
            self._jsonld_buf.append(data)

    def _collect(self, node):
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                self.jsonld_types.add(t)
            elif isinstance(t, list):
                self.jsonld_types.update(x for x in t if isinstance(x, str))
            for v in node.values():
                self._collect(v)
        elif isinstance(node, list):
            for x in node:
                self._collect(x)


def _norm_title(s):
    return " ".join((s or "").split()).strip().lower()


def _token_jaccard(a, b):
    ta, tb = set(_norm_title(a).split()), set(_norm_title(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def head(url, timeout):
    """One request, redirects NOT followed. Returns (status, location) or raises."""
    u = urlparse(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.netloc, timeout=timeout)
    try:
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        conn.request("HEAD", path, headers={"User-Agent": UA, "Accept": "*/*"})
        r = conn.getresponse()
        r.read()
        return r.status, r.getheader("Location")
    finally:
        conn.close()


def walk(url, max_hops, timeout):
    """Walk the redirect chain. Returns (hops, final_url, final_status, flags)."""
    hops, flags, seen = [], set(), set()
    current = url
    for _ in range(max_hops + 1):
        if current in seen:
            flags.add("LOOP")
            break
        seen.add(current)
        status, location = None, None
        for attempt in range(4):
            try:
                status, location = head(current, timeout)
                if status in (405, 501):  # some servers reject HEAD
                    status, location = head(current, timeout)
                break
            except Exception:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                status = 0
        hops.append({"url": current, "status": status, "location": location})
        if status in (0, 429, 503):
            flags.add("FETCH_FAILED")
            break
        if 300 <= status < 400:
            if not location:
                flags.add("MALFORMED_REDIRECT")
                break
            nxt = urljoin(current, location)
            if urlparse(current).scheme == "https" and urlparse(nxt).scheme == "http":
                flags.add("PROTOCOL_DOWNGRADE")
            current = nxt
            continue
        break
    else:
        flags.add("TOO_LONG")
    final = hops[-1] if hops else {"url": url, "status": 0}
    return hops, final["url"], final["status"], flags


def classify_redirect(hops, final_status, flags):
    """Deterministic single verdict for the redirect behaviour."""
    hop_count = len(hops) - 1
    codes = {h["status"] for h in hops if 300 <= h["status"] < 400}
    if "LOOP" in flags or "TOO_LONG" in flags:
        return "LOOP"
    if "FETCH_FAILED" in flags or final_status == 0:
        return "FETCH_FAILED"
    if final_status in (404, 410):
        return "BROKEN_404" if hop_count == 0 else "REDIRECT_TO_404"
    if final_status >= 400:
        return "REDIRECT_TO_ERROR"
    if hop_count == 0 and final_status == 200:
        return "PERSISTED_200"  # URL unchanged and live
    if hop_count >= 2:
        return "CHAIN"
    if codes & TEMPORARY:
        return "TEMPORARY_REDIRECT"
    if codes & PERMANENT and final_status == 200:
        return "OK_301"
    return "OTHER"


def get_html(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if "text/html" not in r.headers.get("Content-Type", ""):
                    return ""
                return r.read(500000).decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return ""
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return ""
    return ""


def audit_one(rec, new_index, args):
    old_url = rec.get("url")
    hops, final_url, final_status, flags = walk(old_url, args.max_hops, args.timeout)
    rclass = classify_redirect(hops, final_status, flags)

    parity_flags = []
    score = 100
    fatal = {"BROKEN_404", "REDIRECT_TO_404", "REDIRECT_TO_ERROR", "LOOP", "FETCH_FAILED"}
    if rclass in fatal:
        parity_flags.append("REDIRECT_" + rclass)
        score = 0
    else:
        if rclass == "CHAIN":
            parity_flags.append("REDIRECT_CHAIN")
            score -= 15
        if rclass == "TEMPORARY_REDIRECT":
            parity_flags.append("TEMPORARY_REDIRECT_SHOULD_BE_301")
            score -= 15
        if rclass == "PROTOCOL_DOWNGRADE" or "PROTOCOL_DOWNGRADE" in flags:
            parity_flags.append("PROTOCOL_DOWNGRADE")
            score -= 10

    new_sig = None
    if final_status == 200 and rclass not in fatal:
        new_sig = new_index.get(final_url.rstrip("/"))
        if new_sig is None:
            html = get_html(final_url, args.timeout)
            if html:
                p = PageParser()
                try:
                    p.feed(html)
                except Exception:
                    pass
                new_sig = {"title": p.title, "canonical": urljoin(final_url, p.canonical) if p.canonical else None,
                           "robots": p.meta_robots, "jsonld_types": sorted(p.jsonld_types),
                           "internal_links": p.internal_links}
        if new_sig:
            # Title parity.
            jac = _token_jaccard(rec.get("title"), new_sig.get("title"))
            if rec.get("title") and jac < 0.6:
                parity_flags.append("TITLE_CHANGED")
                score -= 15
            # Canonical must self-reference the live URL.
            canon = (new_sig.get("canonical") or "").rstrip("/")
            if canon and canon != final_url.rstrip("/"):
                parity_flags.append("CANONICAL_DRIFT")
                score -= 15
            # Structured-data preservation.
            old_types = set(rec.get("jsonld_types") or rec.get("structured_data") or [])
            new_types = set(new_sig.get("jsonld_types") or [])
            if old_types and not old_types.issubset(new_types):
                parity_flags.append("STRUCTURED_DATA_LOST")
                score -= 15
            # Indexability regression.
            if "noindex" in (new_sig.get("robots") or ""):
                parity_flags.append("NOINDEX_ON_TARGET")
                score -= 20
            # Internal-link collapse (only when a new crawl supplied both sides).
            old_in = rec.get("internal_links")
            new_in = new_sig.get("internal_links")
            if isinstance(old_in, int) and isinstance(new_in, int) and old_in >= 10 and new_in < old_in * 0.4:
                parity_flags.append("INTERNAL_LINKS_COLLAPSED")
                score -= 10
        else:
            parity_flags.append("TARGET_UNVERIFIABLE")
    score = max(0, min(100, score))

    clicks = args.clicks.get(old_url, args.clicks.get(old_url.rstrip("/"), 0))
    at_risk = clicks if (score < args.risk_threshold or rclass in fatal) else 0
    return {
        "old_url": old_url,
        "final_url": final_url,
        "final_status": final_status,
        "hop_count": len(hops) - 1,
        "hops": hops,
        "redirect_class": rclass,
        "parity_score": score,
        "parity_flags": parity_flags,
        "clicks": round(clicks, 1),
        "clicks_at_risk": round(at_risk, 1),
    }


def load_map(path):
    if not path:
        return {}
    if not os.path.exists(path):
        fail("INPUT_FILE_MISSING", "File not found: %s" % path)
    data = json.load(open(path))
    if isinstance(data, dict):
        return data
    fail("INPUT_INVALID", "%s must be a JSON object url->clicks." % path)


def fetch_gsc_clicks(site, start, end, timeout):
    token = os.environ.get("GSC_OAUTH_TOKEN")
    if not token:
        fail("AUTH_MISSING_CREDENTIALS",
             "Set GSC_OAUTH_TOKEN (OAuth access token, webmasters.readonly) or pass --gsc file.")
    url = GSC_ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    out, start_row = {}, 0
    while True:
        body = json.dumps({"startDate": start, "endDate": end, "dimensions": ["page"],
                           "dataState": "final", "rowLimit": ROW_LIMIT, "startRow": start_row}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"})
        rows = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    rows = json.loads(r.read()).get("rows", [])
                break
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    fail("AUTH_NO_SITE_ACCESS", "Token has no access to GSC property %s." % site)
                if e.code in (429, 500, 503) and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                fail("RATE_LIMITED" if e.code == 429 else "REQUEST_FAILED",
                     "GSC HTTP %s" % e.code)
            except Exception:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                fail("REQUEST_FAILED", "GSC request failed.")
        for row in rows or []:
            out[row["keys"][0]] = row.get("clicks", 0)
        if not rows or len(rows) < ROW_LIMIT:
            return out
        start_row += ROW_LIMIT


def default_dates():
    import datetime
    end = datetime.date.today() - datetime.timedelta(days=3)
    return (end - datetime.timedelta(days=27)).isoformat(), end.isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-crawl", required=True, dest="old_crawl")
    ap.add_argument("--new-crawl", dest="new_crawl")
    ap.add_argument("--gsc")
    ap.add_argument("--site")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--max-urls", type=int, default=5000, dest="max_urls")
    ap.add_argument("--max-hops", type=int, default=10, dest="max_hops")
    ap.add_argument("--risk-threshold", type=int, default=70, dest="risk_threshold")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()

    if not os.path.exists(args.old_crawl):
        fail("INPUT_FILE_MISSING", "Old crawl not found: %s" % args.old_crawl)
    old = json.load(open(args.old_crawl))
    if isinstance(old, dict) and "results" in old:
        old = old["results"]
    if not isinstance(old, list) or not old:
        fail("INPUT_INVALID", "--old-crawl must be a non-empty JSON list of page records.")
    records = [r for r in old if isinstance(r, dict) and r.get("url")]
    if len(records) > args.max_urls:
        records = records[:args.max_urls]

    new_index = {}
    if args.new_crawl and os.path.exists(args.new_crawl):
        nc = json.load(open(args.new_crawl))
        nc = nc["results"] if isinstance(nc, dict) and "results" in nc else nc
        for r in nc if isinstance(nc, list) else []:
            if isinstance(r, dict) and r.get("url"):
                new_index[r["url"].rstrip("/")] = {
                    "title": r.get("title"),
                    "canonical": r.get("canonical"),
                    "robots": (r.get("robots") or r.get("meta_robots") or ""),
                    "jsonld_types": r.get("jsonld_types") or r.get("structured_data") or [],
                    "internal_links": r.get("internal_links"),
                }

    if args.gsc:
        args.clicks = load_map(args.gsc)
    elif args.site:
        start, end = (args.start, args.end) if args.start and args.end else default_dates()
        args.clicks = fetch_gsc_clicks(args.site, start, end, args.timeout)
    else:
        args.clicks = {}

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        results = list(ex.map(lambda r: audit_one(r, new_index, args), records))

    dist = {}
    for r in results:
        dist[r["redirect_class"]] = dist.get(r["redirect_class"], 0) + 1
    at_risk = sorted([r for r in results if r["clicks_at_risk"] > 0 or r["parity_score"] < args.risk_threshold],
                     key=lambda r: (r["clicks_at_risk"], 100 - r["parity_score"]), reverse=True)
    scored = [r["parity_score"] for r in results]
    summary = {
        "urls_audited": len(results),
        "redirect_distribution": dist,
        "avg_parity_score": round(sum(scored) / len(scored), 1) if scored else 0,
        "clean_301_pct": round(100 * dist.get("OK_301", 0) / len(results), 1) if results else 0,
        "broken_or_lost": sum(dist.get(k, 0) for k in
                              ("BROKEN_404", "REDIRECT_TO_404", "REDIRECT_TO_ERROR", "LOOP", "FETCH_FAILED")),
        "total_clicks_at_risk": round(sum(r["clicks_at_risk"] for r in results), 1),
        "has_traffic_data": bool(args.clicks),
    }
    json.dump({"status": "ok", "summary": summary, "at_risk": at_risk[:200], "results": results},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
