#!/usr/bin/env python3
"""Competitor Content Cadence Monitor — reference implementation.

Auth:   keyless. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only. Stateful: pass the prior run's snapshots as --previous.

Usage: python3 competitor_content.py --competitors competitors.json [--previous previous.json]
  competitors.json: [{"domain":"a.com"}, {"domain":"b.com","sitemap_url":"https://b.com/sitemap.xml"}]
"""
from __future__ import annotations
import argparse, json, re, sys, time, datetime
import urllib.request, urllib.error
from urllib.parse import urlparse

UA = {"User-Agent": "seoskills-content-monitor/1.0"}


def get(url, timeout=20):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.status, r.read(3000000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def discover_sitemaps(domain, given):
    if given:
        return [given]
    scheme = "https://"
    status, robots = get(scheme + domain + "/robots.txt")
    maps = re.findall(r"(?i)Sitemap:\s*(\S+)", robots)
    return maps or [scheme + domain + "/sitemap.xml"]


def parse_sitemap(url, cap, seen=None):
    """Recursively parse sitemap or sitemap index into {loc: lastmod}."""
    seen = seen if seen is not None else set()
    if url in seen or len(seen) > 50:
        return {}
    seen.add(url)
    status, xml = get(url)
    if status != 200 or not xml:
        return {}
    out = {}
    # sitemap index
    if "<sitemapindex" in xml:
        for child in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
            out.update(parse_sitemap(child, cap, seen))
            if len(out) >= cap:
                break
        return out
    for m in re.finditer(r"<url>([\s\S]*?)</url>", xml):
        block = m.group(1)
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
        lastmod = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block)
        if loc:
            out[loc.group(1)] = (lastmod.group(1)[:10] if lastmod else None)
        if len(out) >= cap:
            break
    return out


def topic_of(url, title):
    path = urlparse(url).path.lower()
    seg = [s for s in path.split("/") if s and not s.isdigit()]
    return (seg[0] if seg else "root")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--competitors", required=True); ap.add_argument("--previous")
    ap.add_argument("--no-sample", action="store_true")
    ap.add_argument("--max-urls", type=int, default=5000, dest="max_urls")
    a = ap.parse_args()
    competitors = json.load(open(a.competitors))
    prev = (json.load(open(a.previous)) if a.previous else {}).get("snapshots", {})

    results, snapshots = [], {}
    for comp in competitors:
        domain = comp["domain"]
        maps = discover_sitemaps(domain, comp.get("sitemap_url"))
        current = {}
        for m in maps:
            current.update(parse_sitemap(m, a.max_urls))
            if len(current) >= a.max_urls:
                break
        if not current:
            results.append({"domain": domain, "status": "sitemap_unreachable"})
            if domain in prev:
                snapshots[domain] = prev[domain]  # carry forward
            continue
        snapshots[domain] = current
        p = prev.get(domain)
        update_detection = any(v for v in current.values())
        if not p:
            results.append({"domain": domain, "status": "baseline", "url_count": len(current)})
            continue
        new = [u for u in current if u not in p]
        removed = [u for u in p if u not in current]
        updated = [u for u in current if u in p and current[u] and p.get(u) and current[u] > p[u]]
        topics = {}
        enriched = []
        if not a.no_sample:
            for u in new[:100]:
                st, html = get(u, timeout=10)
                title = (re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I) or [None, ""])[1].strip()
                dp = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
                t = topic_of(u, title)
                topics[t] = topics.get(t, 0) + 1
                enriched.append({"url": u, "title": title[:120], "date_published": dp.group(1) if dp else None, "topic": t})
                time.sleep(0.15)
        results.append({"domain": domain, "status": "ok", "url_count": len(current),
                        "update_detection": update_detection,
                        "new_count": len(new), "updated_count": len(updated), "removed_count": len(removed),
                        "new_urls": enriched or [{"url": u} for u in new[:100]],
                        "updated_urls": updated[:100], "removed_urls": removed[:100],
                        "topic_distribution": topics})
    json.dump({"status": "ok", "competitors_monitored": len(competitors),
               "results": results, "snapshots": snapshots}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
