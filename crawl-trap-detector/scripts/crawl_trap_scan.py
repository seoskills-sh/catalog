#!/usr/bin/env python3
"""Crawl-Trap & Facet Detector — reference implementation.

Bounded BFS crawl -> structural URL signatures -> combinatorial-explosion
classification. Output: JSON on stdout per ../references/output.schema.json.
Std-lib only.

Usage: python3 crawl_trap_scan.py --start https://example.com --max 3000
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request, urllib.error, urllib.robotparser
from collections import defaultdict, deque
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse

UA = "seoskills-crawltrap/1.0"
TRACKING = {"sessionid", "sid", "phpsessid", "jsessionid", "fbclid", "gclid", "msclkid"}
SORTING = {"sort", "order", "view", "page", "per_page", "orderby", "dir"}
DATEISH = re.compile(r"^(year|month|day|date|from|to|week|start|end)$", re.I)


def templatize(path: str) -> str:
    parts = []
    for seg in path.split("/"):
        if re.fullmatch(r"\d+", seg):
            parts.append("{n}")
        elif re.fullmatch(r"[0-9a-f]{8,}", seg) or re.search(r"\d", seg) and len(seg) > 12:
            parts.append("{id}")
        else:
            parts.append(seg)
    return "/".join(parts)


def signature(url):
    u = urlparse(url)
    keys = sorted(parse_qs(u.query).keys())
    return templatize(u.path) + ("?" + ",".join(keys) if keys else ""), keys


def get(url, timeout=15):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
                ctype = r.headers.get("Content-Type", "")
                if "text/html" not in ctype:
                    return r.status, ""
                return r.status, r.read(300000).decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, ""
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, ""
    return 0, ""


def title_h1(html):
    t = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I)
    h = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", html, re.I)
    return ((t.group(1) if t else "") + " " + (h.group(1) if h else "")).strip().lower()


def classify(keys):
    kl = {k.lower() for k in keys}
    if kl & TRACKING or any(k.startswith("utm_") for k in kl):
        return "SESSION_OR_TRACKING"
    if any(DATEISH.match(k) for k in kl):
        return "CALENDAR_LOOP"
    if len(keys) >= 3:
        return "FACETED_NAV"
    if kl & SORTING:
        return "SORT_ORDER_PAGINATION"
    return "GENERIC_PARAM_EXPLOSION"


CONTAINMENT = {
    "FACETED_NAV": "canonical to the unfaceted URL + robots disallow non-primary facet params",
    "CALENDAR_LOOP": "nofollow calendar navigation + robots disallow future-dated ranges",
    "SESSION_OR_TRACKING": "canonical to the param-stripped URL; never link internally with tracking params",
    "SORT_ORDER_PAGINATION": "canonical to default sort; expose only a clean paginated series",
    "GENERIC_PARAM_EXPLOSION": "review the offending params; canonicalize or robots-disallow if non-indexable",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--max", type=int, default=3000)
    ap.add_argument("--max-depth", type=int, default=6, dest="max_depth")
    ap.add_argument("--threshold", type=int, default=50)
    ap.add_argument("--no-robots", action="store_true")
    a = ap.parse_args()
    host = urlparse(a.start).netloc
    rp = urllib.robotparser.RobotFileParser()
    if not a.no_robots:
        try:
            rp.set_url(f"{urlparse(a.start).scheme}://{host}/robots.txt"); rp.read()
        except Exception:
            rp = None

    seen, groups, q = set(), defaultdict(lambda: {"urls": [], "keys": [], "samples": []}), deque([(a.start, 0)])
    hit_cap = False
    status = "ok"
    while q:
        if len(seen) >= a.max:
            hit_cap = True; break
        url, depth = q.popleft()
        n = urlunparse(urlparse(url)._replace(fragment=""))
        if n in seen or depth > a.max_depth:
            continue
        if rp and not a.no_robots and not rp.can_fetch(UA, url):
            continue
        seen.add(n)
        code, html = get(url)
        if code in (429, 503):
            status = "partial_rate_limited"; break
        sig, keys = signature(url)
        g = groups[sig]
        g["urls"].append(url); g["keys"] = keys
        if len(g["samples"]) < 8:
            g["samples"].append(title_h1(html))
        for m in re.finditer(r'href=["\']([^"\'#]+)["\']', html):
            nxt = urljoin(url, m.group(1))
            if urlparse(nxt).netloc == host and nxt.startswith("http"):
                q.append((nxt, depth + 1))
        time.sleep(0.2)

    traps = []
    for sig, g in groups.items():
        if len(g["urls"]) < a.threshold or not g["keys"]:
            continue
        uniq = len(set(g["samples"])) / max(len(g["samples"]), 1)
        traps.append({
            "signature": sig, "type": classify(g["keys"]), "param_keys": g["keys"],
            "url_count": len(g["urls"]), "content_uniqueness_estimate": round(uniq, 2),
            "waste_score": round(len(g["urls"]) * (1 - uniq), 1),
            "example_url": g["urls"][0], "containment": CONTAINMENT[classify(g["keys"])],
        })
    traps.sort(key=lambda t: t["waste_score"], reverse=True)
    json.dump({"status": status, "pages_crawled": len(seen), "hit_cap": hit_cap,
               "trap_count": len(traps), "traps": traps}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
