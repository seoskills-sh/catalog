#!/usr/bin/env python3
"""Redirect Chain Mapper — reference implementation.

Manually walks each URL's redirect chain (auto-follow disabled) and classifies
chains, loops, mixed codes, dead ends, and canonical mismatches.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 redirect_map.py --urls https://a.com/old,https://a.com/x
"""
from __future__ import annotations
import argparse, json, re, sys, time
import http.client
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

UA = "seoskills-redirect-mapper/1.0"
PERMANENT = {301, 308}
TEMPORARY = {302, 303, 307}


def request(url, method="HEAD", timeout=10):
    """Single request, redirects NOT followed. Returns (status, location, body_or_none)."""
    u = urlparse(url)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.netloc, timeout=timeout)
    try:
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        conn.request(method, path, headers={"User-Agent": UA, "Accept": "*/*"})
        resp = conn.getresponse()
        status = resp.status
        location = resp.getheader("Location")
        body = resp.read(200000).decode("utf-8", "ignore") if method == "GET" else None
        return status, location, body
    finally:
        conn.close()


def walk(start, max_hops, check_canonical):
    hops, issues, seen = [], set(), set()
    current, final_body = start, None
    for _ in range(max_hops + 1):
        if current in seen:
            issues.add("LOOP"); break
        seen.add(current)
        try:
            status, location, _ = request(current, "HEAD")
            if status in (405, 501):
                status, location, _ = request(current, "GET")
        except Exception:
            for attempt in range(3):
                try:
                    time.sleep(2 ** attempt)
                    status, location, _ = request(current, "GET")
                    break
                except Exception:
                    status, location = 0, None
            if status == 0:
                hops.append({"url": current, "status": 0, "location": None})
                issues.add("FETCH_FAILED"); break
        hops.append({"url": current, "status": status, "location": location})
        if status in (429, 503):
            issues.add("FETCH_FAILED"); break
        if 300 <= status < 400:
            if not location:
                issues.add("MALFORMED_REDIRECT"); break
            nxt = urljoin(current, location)
            if urlparse(current).scheme == "https" and urlparse(nxt).scheme == "http":
                issues.add("PROTOCOL_DOWNGRADE")
            current = nxt
            continue
        break  # non-3xx = final
    else:
        issues.add("TOO_LONG")

    final = hops[-1] if hops else {"url": start, "status": 0}
    hop_count = len(hops) - 1
    codes = {h["status"] for h in hops if 300 <= h["status"] < 400}
    if hop_count == 0 and final["status"] == 200 and not issues:
        issues.add("OK_DIRECT")
    if hop_count >= 2:
        issues.add("CHAIN")
    if codes & TEMPORARY:
        issues.add("TEMPORARY_REDIRECT")
    if codes & PERMANENT and codes & TEMPORARY:
        issues.add("MIXED_STATUS")
    if final["status"] >= 400 or final["status"] == 0:
        issues.add("ENDS_NON_200")

    if check_canonical and final["status"] == 200:
        try:
            _, _, body = request(final["url"], "GET")
            m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', body or "", re.I)
            if m and urljoin(final["url"], m.group(1)).rstrip("/") != final["url"].rstrip("/"):
                issues.add("CANONICAL_MISMATCH")
        except Exception:
            pass

    return {"input_url": start, "final_url": final["url"], "final_status": final["status"],
            "hop_count": hop_count, "hops": hops, "issues": sorted(issues)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", required=True)
    ap.add_argument("--max-hops", type=int, default=10, dest="max_hops")
    ap.add_argument("--no-canonical", action="store_true")
    a = ap.parse_args()
    urls = [u.strip() for u in a.urls.split(",") if u.strip()]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda u: walk(u, a.max_hops, not a.no_canonical), urls))
    flagged = [r for r in results if r["issues"] != ["OK_DIRECT"]]
    json.dump({"status": "ok", "count": len(results), "flagged": len(flagged),
               "results": results}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
