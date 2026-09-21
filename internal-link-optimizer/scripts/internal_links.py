#!/usr/bin/env python3
"""Internal Link Opportunity Mapper — reference implementation.

Auth:   keyless crawl; OPENAI_API_KEY optional for embeddings (else TF-IDF).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 internal_links.py --start https://example.com --max 1000 --threshold 0.75
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, time, urllib.request, urllib.error, urllib.robotparser
from collections import defaultdict, deque, Counter
from urllib.parse import urljoin, urlparse, urlunparse

UA = "seoskills-internal-links/1.0"
STOP = set("the a an and or but of to in on for with is are was were be by at from as it this that these those".split())


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def get(url, timeout=15):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            if "text/html" not in r.headers.get("Content-Type", ""):
                return 0, ""
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except Exception:
        return 0, ""


def main_text(html):
    html = re.sub(r"<(script|style|nav|header|footer|aside)[\s\S]*?</\1>", " ", html, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def title_of(html):
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I)
    return (m.group(1).strip() if m else "")


def norm(u):
    return urlunparse(urlparse(u)._replace(fragment="")).rstrip("/")


def tfidf_vectors(texts):
    docs = [[w for w in re.findall(r"[a-z][a-z\-']{2,}", t.lower()) if w not in STOP] for t in texts]
    df = Counter()
    for d in docs:
        df.update(set(d))
    N = len(docs)
    vecs = []
    for d in docs:
        tf = Counter(d)
        vec = {w: (c / len(d)) * math.log(N / (1 + df[w])) for w, c in tf.items()} if d else {}
        vecs.append(vec)
    return vecs


def cosine(a, b):
    if isinstance(a, dict):
        common = set(a) & set(b)
        num = sum(a[w] * b[w] for w in common)
        da = math.sqrt(sum(v * v for v in a.values())); db = math.sqrt(sum(v * v for v in b.values()))
        return num / (da * db) if da and db else 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)); db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


def openai_embed(texts, key):
    import urllib.request as R
    out = []
    for i in range(0, len(texts), 96):
        batch = [t[:8000] for t in texts[i:i + 96]]
        body = json.dumps({"model": "text-embedding-3-small", "input": batch}).encode()
        req = R.Request("https://api.openai.com/v1/embeddings", data=body,
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        for attempt in range(6):
            try:
                with R.urlopen(req, timeout=60) as r:
                    out += [d["embedding"] for d in json.loads(r.read())["data"]]
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 5:
                    time.sleep(2 ** attempt); continue
                return None
            except Exception:
                if attempt < 3:
                    time.sleep(2 ** attempt); continue
                return None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--max", type=int, default=1000)
    ap.add_argument("--threshold", type=float, default=0.75)
    ap.add_argument("--max-recs", type=int, default=200, dest="max_recs")
    a = ap.parse_args()
    host = urlparse(a.start).netloc
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(f"{urlparse(a.start).scheme}://{host}/robots.txt"); rp.read()
    except Exception:
        rp = None

    pages, links_out, inbound, q, seen = {}, defaultdict(set), defaultdict(int), deque([a.start]), set()
    hit_cap = False
    while q:
        if len(pages) >= a.max:
            hit_cap = True; break
        url = norm(q.popleft())
        if url in seen:
            continue
        seen.add(url)
        if rp and not rp.can_fetch(UA, url):
            continue
        code, html = get(url)
        if code != 200:
            continue
        text = main_text(html)
        if len(text.split()) < 100:
            continue
        pages[url] = {"title": title_of(html), "text": text}
        for m in re.finditer(r'href=["\']([^"\'#]+)["\']', html):
            nxt = norm(urljoin(url, m.group(1)))
            if urlparse(nxt).netloc == host and nxt.startswith("http"):
                links_out[url].add(nxt)
                inbound[nxt] += 1
                q.append(nxt)
        time.sleep(0.2)

    if len(pages) < 5:
        fail("INSUFFICIENT_PAGES", "Fewer than 5 embeddable pages.")

    urls = list(pages)
    texts = [pages[u]["title"] + ". " + pages[u]["text"] for u in urls]
    key = os.environ.get("OPENAI_API_KEY")
    backend = "tfidf"
    vecs = None
    if key:
        vecs = openai_embed(texts, key)
        backend = "openai" if vecs else "tfidf"
    if vecs is None:
        vecs = tfidf_vectors(texts)

    recs, orphans = [], [u for u in urls if inbound.get(u, 0) == 0 and u != norm(a.start)]
    for i in range(len(urls)):
        for j in range(i + 1, len(urls)):
            ui, uj = urls[i], urls[j]
            if uj in links_out[ui] or ui in links_out[uj]:
                continue
            sim = cosine(vecs[i], vecs[j])
            if sim < a.threshold:
                continue
            # link from lower-deficit (source) to higher-deficit (target)
            src, tgt = (ui, uj) if inbound[ui] >= inbound[uj] else (uj, ui)
            src_words = set(re.findall(r"[a-z][a-z\-']{2,}", pages[src]["text"].lower()))
            anchor_src = [w for w in re.findall(r"[a-z][a-z\-']{2,}", pages[tgt]["title"].lower()) if w in src_words and w not in STOP]
            recs.append({"from": src, "to": tgt, "cosine": round(sim, 3),
                         "target_inbound_links": inbound[tgt],
                         "priority": round(sim * (1 / (1 + inbound[tgt])), 4),
                         "anchor_suggestion": " ".join(anchor_src[:4]) or pages[tgt]["title"][:60],
                         "anchor_confidence": "high" if anchor_src else "low"})
    recs.sort(key=lambda r: r["priority"], reverse=True)
    json.dump({"status": "ok", "embedding_backend": backend, "pages_crawled": len(pages),
               "hit_cap": hit_cap, "recommendation_count": len(recs),
               "recommendations": recs[:a.max_recs], "orphan_pages": orphans[:100]},
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
