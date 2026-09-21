#!/usr/bin/env python3
"""Programmatic Template Auditor — reference implementation.

Samples pages emitted by ONE programmatic template, computes a 64-bit SimHash
over word-shingles for each, clusters near-duplicates by Hamming distance,
extracts the shared boilerplate shingle set, and quantifies the unique-token
budget each page actually adds. Flags thin pages, near-duplicate clusters, and
boilerplate-dominant doorway pages.

Auth:   keyless crawler. Optional: GSC_ACCESS_TOKEN with --site (clicks context).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 template_auditor.py --urls urls.json [--sitemap https://x.com/sitemap.xml \
      --url-pattern "/product/"] [--k 4] [--hamming-threshold 3] [--site sc-domain:x.com]
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "seoskills-template-auditor/1.0"}
GSC_ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
BLOCK_RE = re.compile(r"(?is)<(script|style|noscript|template|svg|nav|header|footer|form|aside)\b.*?</\1>")
TAG_RE = re.compile(r"(?s)<[^>]+>")
WORD_RE = re.compile(r"[a-z0-9]+")
BITS = 64
BITMASK = (1 << BITS) - 1
MAX_BACKOFF = 5


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def http_get(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "xml" not in ctype and ctype:
                return 0, ""
            return r.status, r.read(800000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def extract_text(html):
    body = BLOCK_RE.sub(" ", html)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    body = TAG_RE.sub(" ", body)
    body = re.sub(r"&[a-z#0-9]+;", " ", body)
    return body.lower()


def tokens_of(text):
    return WORD_RE.findall(text)


def shingles_of(tokens, k):
    if len(tokens) < k:
        return [" ".join(tokens)] if tokens else []
    return [" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)]


def hash64(s):
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(shingle_counts):
    """Charikar SimHash: weighted bit-vote across shingle hashes -> 64-bit fingerprint."""
    if not shingle_counts:
        return 0
    acc = [0] * BITS
    for sh, w in shingle_counts.items():
        h = hash64(sh)
        for b in range(BITS):
            if (h >> b) & 1:
                acc[b] += w
            else:
                acc[b] -= w
    fp = 0
    for b in range(BITS):
        if acc[b] > 0:
            fp |= (1 << b)
    return fp


def hamming(a, b):
    return bin((a ^ b) & BITMASK).count("1")


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def gsc_clicks_by_page(site, start, end):
    token = os.environ.get("GSC_ACCESS_TOKEN")
    if not token:
        fail("AUTH_MISSING_GSC", "--site set but GSC_ACCESS_TOKEN is unset.")
    url = GSC_ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    body = json.dumps({"startDate": start, "endDate": end, "dimensions": ["page"],
                       "rowLimit": 25000, "dataState": "final"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    for attempt in range(MAX_BACKOFF + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = json.loads(r.read()).get("rows", [])
                return {row["keys"][0]: row.get("clicks", 0) for row in rows}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                fail("AUTH_GSC_FORBIDDEN", "GSC token invalid or lacks access to %s." % site)
            if e.code in (429, 500, 503):
                if attempt >= MAX_BACKOFF:
                    return {}
                time.sleep(2 ** attempt)
                continue
            return {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {}
    return {}


def load_urls(args):
    urls = []
    if args.urls:
        urls = json.load(open(args.urls))
        if not isinstance(urls, list):
            fail("BAD_INPUT", "--urls file must be a JSON array.")
    elif args.sitemap:
        status, xml = http_get(args.sitemap)
        if status != 200 or not xml:
            fail("SITEMAP_UNREACHABLE", "Could not fetch sitemap (HTTP %s)." % status)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
        if args.url_pattern:
            pat = re.compile(args.url_pattern)
            urls = [u for u in urls if pat.search(u)]
    else:
        fail("BAD_INPUT", "Provide --urls or --sitemap.")
    urls = [u.strip() for u in urls if u and u.strip()]
    # Stable de-dupe.
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[: min(args.sample_size, args.max_sample)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default=None)
    ap.add_argument("--sitemap", default=None)
    ap.add_argument("--url-pattern", default=None, dest="url_pattern")
    ap.add_argument("--k", type=int, default=4, help="Shingle size in words.")
    ap.add_argument("--hamming-threshold", type=int, default=3, dest="hamming_threshold")
    ap.add_argument("--min-tokens", type=int, default=150, dest="min_tokens")
    ap.add_argument("--uniqueness-floor", type=float, default=0.20, dest="uniqueness_floor")
    ap.add_argument("--boilerplate-df", type=float, default=0.80, dest="boilerplate_df")
    ap.add_argument("--sample-size", type=int, default=50, dest="sample_size")
    ap.add_argument("--max-sample", type=int, default=300, dest="max_sample")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--site", default=None)
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="2026-08-28")
    args = ap.parse_args()

    urls = load_urls(args)
    if not urls:
        fail("NO_URLS", "No sample URLs after filtering.")
    clicks = gsc_clicks_by_page(args.site, args.start, args.end) if args.site else {}

    workers = max(1, min(args.workers, 8))
    fetched = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for url, (status, html) in zip(urls, ex.map(lambda u: (http_get(u)), urls)):
            fetched[url] = (status, html)

    pages = []          # per successfully-fetched page
    doc_freq = defaultdict(int)   # shingle -> number of pages containing it
    unreachable = []
    for url in urls:
        status, html = fetched[url]
        if status != 200 or not html:
            unreachable.append({"url": url, "http_status": status})
            continue
        toks = tokens_of(extract_text(html))
        shs = shingles_of(toks, args.k)
        counts = defaultdict(int)
        for sh in shs:
            counts[sh] += 1
        for sh in counts:
            doc_freq[sh] += 1
        pages.append({"url": url, "tokens": toks, "token_count": len(toks),
                      "shingle_counts": counts, "shingle_set": set(counts.keys()),
                      "fp": simhash(counts)})

    n = len(pages)
    if n == 0:
        fail("ALL_UNREACHABLE", "No sampled page could be fetched.", unreachable=unreachable)

    # Boilerplate = shingles present in >= boilerplate_df fraction of pages.
    df_cut = max(2, int(round(args.boilerplate_df * n))) if n > 1 else 1
    boilerplate = {sh for sh, df in doc_freq.items() if df >= df_cut}

    # Near-duplicate clustering by Hamming distance (union-find).
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if hamming(pages[i]["fp"], pages[j]["fp"]) <= args.hamming_threshold:
                uf.union(i, j)
    cluster_members = defaultdict(list)
    for i in range(n):
        cluster_members[uf.find(i)].append(i)

    cluster_id_map = {}
    dup_clusters = []
    cid = 0
    for root, members in cluster_members.items():
        if len(members) > 1:
            for m in members:
                cluster_id_map[m] = cid
            dup_clusters.append({
                "cluster_id": cid,
                "size": len(members),
                "urls": [pages[m]["url"] for m in members],
                "max_hamming_in_cluster": max(
                    (hamming(pages[a]["fp"], pages[b]["fp"])
                     for a in members for b in members if a < b), default=0),
            })
            cid += 1

    # Per-page uniqueness against boilerplate.
    results = []
    thin_count = duplicate_count = doorway_count = 0
    uniqueness_sum = budget_sum = 0.0
    for idx, p in enumerate(pages):
        pshs = p["shingle_set"]
        unique_shs = pshs - boilerplate
        uniq_ratio = (len(unique_shs) / len(pshs)) if pshs else 0.0
        unique_tokens = set()
        for sh in unique_shs:
            unique_tokens.update(sh.split(" "))
        budget = len(unique_tokens)
        is_thin = p["token_count"] < args.min_tokens
        in_cluster = idx in cluster_id_map
        is_doorway = (uniq_ratio < args.uniqueness_floor) and (len(boilerplate) > 0) and not is_thin
        if is_thin:
            thin_count += 1
        if in_cluster:
            duplicate_count += 1
        if is_doorway:
            doorway_count += 1
        uniqueness_sum += uniq_ratio
        budget_sum += budget
        flags = []
        if is_thin:
            flags.append("thin")
        if in_cluster:
            flags.append("near_duplicate")
        if is_doorway:
            flags.append("doorway")
        results.append({
            "url": p["url"],
            "token_count": p["token_count"],
            "simhash": "%016x" % p["fp"],
            "unique_shingles": len(unique_shs),
            "total_shingles": len(pshs),
            "content_uniqueness_ratio": round(uniq_ratio, 3),
            "unique_token_budget": budget,
            "near_duplicate_cluster": cluster_id_map.get(idx),
            "gsc_clicks": clicks.get(p["url"]) if args.site else None,
            "flags": flags,
        })

    avg_uniq = round(uniqueness_sum / n, 3)
    dup_share = round(duplicate_count / n, 3)
    thin_share = round(thin_count / n, 3)
    if thin_share >= 0.5 or (avg_uniq < args.uniqueness_floor and doorway_count >= n * 0.5):
        verdict = "doorway" if doorway_count >= n * 0.5 else "thin"
    elif dup_share >= 0.3:
        verdict = "duplicate_heavy"
    elif avg_uniq < args.uniqueness_floor:
        verdict = "boilerplate_dominant"
    else:
        verdict = "healthy"

    results.sort(key=lambda r: (r["content_uniqueness_ratio"], r["token_count"]))
    out = {
        "status": "ok",
        "pages_sampled": len(urls),
        "pages_analyzed": n,
        "shingle_size_k": args.k,
        "hamming_threshold": args.hamming_threshold,
        "boilerplate_shingles": len(boilerplate),
        "template_verdict": verdict,
        "avg_content_uniqueness_ratio": avg_uniq,
        "avg_unique_token_budget": round(budget_sum / n, 1),
        "thin_pages": thin_count,
        "near_duplicate_pages": duplicate_count,
        "doorway_pages": doorway_count,
        "duplicate_clusters": sorted(dup_clusters, key=lambda c: -c["size"]),
        "pages": results,
        "unreachable": unreachable,
    }
    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
