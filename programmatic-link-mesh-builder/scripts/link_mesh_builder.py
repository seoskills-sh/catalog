#!/usr/bin/env python3
"""Programmatic Link Mesh Builder — reference implementation.

Models a generated page set as a directed graph, computes PageRank by power
iteration (std-lib), finds orphans and unreachable pages, and proposes a
contextual hub-spoke + related-entity internal-link plan that distributes
authority without over-linking. Guarantees every money page is reachable and
caps proposals against template link-spam.

Auth:   keyless (local crawl/sitemap). Optional: OPENAI_API_KEY with --embeddings.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 link_mesh_builder.py --edges edges.json [--pages pages.json] \
      [--money-pages money.json] [--home https://x.com/] [--max-outlinks 100]
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict, deque

OPENAI_EMBED = "https://api.openai.com/v1/embeddings"
TOKEN_SPLIT = re.compile(r"[/\-_.0-9]+")
STOP = {"", "www", "http", "https", "html", "php", "index", "page", "com", "net", "org"}
MAX_BACKOFF = 5


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def norm(u):
    return (u or "").strip()


def path_tokens(url):
    parts = urllib.parse.urlsplit(url)
    raw = (parts.path or "") + " " + (parts.query or "")
    toks = {t for t in TOKEN_SPLIT.split(raw.lower()) if t and t not in STOP and len(t) > 1}
    return toks


def section_of(url):
    parts = urllib.parse.urlsplit(url)
    segs = [s for s in (parts.path or "").split("/") if s]
    return segs[0] if segs else "_root"


def load_json_array(path, label):
    data = json.load(open(path))
    if not isinstance(data, list):
        fail("BAD_INPUT", "%s must be a JSON array." % label)
    return data


def load_edges(args):
    edges = []
    if args.edges:
        raw = load_json_array(args.edges, "--edges")
        for pair in raw:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                edges.append((norm(pair[0]), norm(pair[1])))
    if args.crawl:
        import csv
        with open(args.crawl, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            hl = [h.strip().lower() for h in header]
            si = next((i for i, h in enumerate(hl) if "source" in h or "from" in h), 0)
            di = next((i for i, h in enumerate(hl) if "destination" in h or "target" in h or "to" == h), 1)
            for row in reader:
                if len(row) > max(si, di):
                    s, d = norm(row[si]), norm(row[di])
                    if s and d:
                        edges.append((s, d))
    return edges


def fetch_sitemap_nodes(sitemap):
    try:
        req = urllib.request.Request(sitemap, headers={"User-Agent": "seoskills-link-mesh/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read(5000000).decode("utf-8", "ignore")
        return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    except Exception:
        fail("SITEMAP_UNREACHABLE", "Could not fetch sitemap.")


def pagerank(nodes, out_adj, damping, max_iter, eps):
    n = len(nodes)
    if n == 0:
        return {}, 0, True
    idx = {u: i for i, u in enumerate(nodes)}
    outdeg = [len(out_adj.get(u, ())) for u in nodes]
    dangling = [i for i in range(n) if outdeg[i] == 0]
    pr = [1.0 / n] * n
    converged = False
    iterations = 0
    for it in range(max_iter):
        iterations = it + 1
        dsum = 0.0
        for i in dangling:
            dsum += pr[i]
        base = (1.0 - damping) / n + damping * dsum / n
        new = [base] * n
        for u in nodes:
            i = idx[u]
            deg = outdeg[i]
            if deg == 0:
                continue
            share = damping * pr[i] / deg
            for t in out_adj[u]:
                j = idx.get(t)
                if j is not None:
                    new[j] += share
        delta = 0.0
        for i in range(n):
            delta += abs(new[i] - pr[i])
        pr = new
        if delta < eps:
            converged = True
            break
    return {u: pr[idx[u]] for u in nodes}, iterations, converged


def reachable_from(home, out_adj, nodes_set):
    if home not in nodes_set:
        return set()
    seen = {home}
    q = deque([home])
    while q:
        u = q.popleft()
        for v in out_adj.get(u, ()):
            if v in nodes_set and v not in seen:
                seen.add(v)
                q.append(v)
    return seen


def openai_embeddings(texts):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        fail("AUTH_MISSING_EMBEDDINGS", "--embeddings set but OPENAI_API_KEY is unset.")
    vectors = {}
    B = 256
    keys = list(texts.keys())
    for start in range(0, len(keys), B):
        batch = keys[start:start + B]
        body = json.dumps({"model": "text-embedding-3-small",
                           "input": [texts[k] for k in batch]}).encode("utf-8")
        req = urllib.request.Request(OPENAI_EMBED, data=body, method="POST")
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("Content-Type", "application/json")
        for attempt in range(MAX_BACKOFF + 1):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())["data"]
                for k, item in zip(batch, data):
                    vectors[k] = item["embedding"]
                break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    fail("AUTH_INVALID_EMBEDDINGS", "OpenAI rejected the API key (401).")
                if e.code == 429:
                    if attempt >= MAX_BACKOFF:
                        fail("RATE_LIMITED", "OpenAI embeddings quota exhausted.")
                    time.sleep(2 ** attempt)
                    continue
                if e.code >= 500 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                fail("REQUEST_FAILED", "OpenAI embeddings HTTP %s." % e.code)
            except Exception:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                fail("REQUEST_FAILED", "OpenAI embeddings request failed.")
        time.sleep(0.2)
    return vectors


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", default=None, help="JSON array of [source,dest] internal links.")
    ap.add_argument("--crawl", default=None, help="Crawler inlinks CSV (Source,Destination).")
    ap.add_argument("--sitemap", default=None, help="Sitemap URL (nodes only).")
    ap.add_argument("--pages", default=None, help="JSON array of all page URLs (nodes).")
    ap.add_argument("--money-pages", default=None, dest="money_pages")
    ap.add_argument("--home", default=None, help="Homepage / BFS root URL.")
    ap.add_argument("--damping", type=float, default=0.85)
    ap.add_argument("--max-iter", type=int, default=100, dest="max_iter")
    ap.add_argument("--eps", type=float, default=1e-6)
    ap.add_argument("--min-inlinks", type=int, default=1, dest="min_inlinks")
    ap.add_argument("--max-outlinks", type=int, default=100, dest="max_outlinks")
    ap.add_argument("--max-new-links-per-page", type=int, default=5, dest="max_new")
    ap.add_argument("--top-k-related", type=int, default=5, dest="top_k")
    ap.add_argument("--max-nodes", type=int, default=50000, dest="max_nodes")
    ap.add_argument("--max-cluster-compare", type=int, default=400, dest="max_cluster")
    ap.add_argument("--embeddings", action="store_true")
    ap.add_argument("--max-embeddings", type=int, default=1000, dest="max_embeddings")
    args = ap.parse_args()

    edges = load_edges(args)
    node_set = set()
    for s, d in edges:
        node_set.add(s)
        node_set.add(d)
    if args.pages:
        node_set.update(norm(u) for u in load_json_array(args.pages, "--pages") if norm(u))
    if args.sitemap:
        node_set.update(norm(u) for u in fetch_sitemap_nodes(args.sitemap) if norm(u))
    money = set()
    if args.money_pages:
        money = {norm(u) for u in load_json_array(args.money_pages, "--money-pages") if norm(u)}
        node_set.update(money)
    if not node_set:
        fail("BAD_INPUT", "No nodes: provide --edges, --crawl, --sitemap, or --pages.")
    if len(node_set) > args.max_nodes:
        fail("TOO_MANY_NODES", "Node count %d exceeds --max-nodes %d." % (len(node_set), args.max_nodes))

    nodes = sorted(node_set)
    out_adj = defaultdict(list)
    out_seen = defaultdict(set)
    in_deg = defaultdict(int)
    existing = set()
    for s, d in edges:
        if s in node_set and d in node_set and s != d and d not in out_seen[s]:
            out_seen[s].add(d)
            out_adj[s].append(d)
            in_deg[d] += 1
            existing.add((s, d))

    pr, iterations, converged = pagerank(nodes, out_adj, args.damping, args.max_iter, args.eps)

    # Home / BFS root.
    home = norm(args.home) if args.home else None
    if home not in node_set:
        home = next((u for u in nodes if urllib.parse.urlsplit(u).path in ("", "/")), None)
    if home is None:
        home = max(nodes, key=lambda u: in_deg.get(u, 0))
    reachable = reachable_from(home, out_adj, node_set)

    # Section clusters and their hubs (highest PageRank in section).
    clusters = defaultdict(list)
    for u in nodes:
        clusters[section_of(u)].append(u)
    hub_of = {}
    for sec, members in clusters.items():
        hub_of[sec] = max(members, key=lambda u: pr.get(u, 0))

    # Relatedness vectors.
    method = "url_token_jaccard"
    vectors = {}
    if args.embeddings:
        if len(nodes) > args.max_embeddings:
            method = "url_token_jaccard_fallback_over_embedding_cap"
        else:
            texts = {u: " ".join(sorted(path_tokens(u))) or u for u in nodes}
            vectors = openai_embeddings(texts)
            method = "openai_embedding_cosine"
    tok = {u: path_tokens(u) for u in nodes}

    # Proposal engine with link-spam caps.
    proposed_out = defaultdict(list)
    proposed_in = defaultdict(list)
    prop_seen = set()

    def can_add(src, dst):
        if src == dst or dst in out_seen[src] or (src, dst) in prop_seen:
            return False
        cur_out = len(out_adj[src]) + len(proposed_out[src])
        if cur_out >= args.max_outlinks:
            return False
        if len(proposed_out[src]) >= args.max_new:
            return False
        return True

    def add_link(src, dst, reason):
        if not can_add(src, dst):
            return False
        proposed_out[src].append({"to": dst, "reason": reason})
        proposed_in[dst].append({"from": src, "reason": reason})
        prop_seen.add((src, dst))
        return True

    # 1) Money-page reachability guarantee.
    unreachable_money = []
    reachable_sorted_hubs = sorted(
        (u for u in nodes if u in reachable),
        key=lambda u: pr.get(u, 0), reverse=True)
    for mp in sorted(money, key=lambda u: pr.get(u, 0)):
        if mp in reachable and in_deg.get(mp, 0) >= args.min_inlinks:
            continue
        linked = False
        for hub in reachable_sorted_hubs:
            if add_link(hub, mp, "money_page_reachability"):
                linked = True
                break
        if mp not in reachable and not linked:
            unreachable_money.append(mp)

    # 2) Orphan / low-inlink spokes get a hub link.
    for u in nodes:
        total_in = in_deg.get(u, 0) + len(proposed_in[u])
        if total_in >= args.min_inlinks:
            continue
        hub = hub_of.get(section_of(u))
        if hub and hub != u:
            add_link(hub, u, "hub_to_orphan_spoke")
        # spoke -> hub for authority flow / discovery
        if hub and hub != u:
            add_link(u, hub, "spoke_to_hub")

    # 3) Related-entity contextual links (within section clusters, capped).
    for sec, members in clusters.items():
        if len(members) < 2:
            continue
        pool = members[: args.max_cluster] if len(members) > args.max_cluster else members
        for u in pool:
            scored = []
            for v in pool:
                if v == u or v in out_seen[u]:
                    continue
                if method == "openai_embedding_cosine" and u in vectors and v in vectors:
                    sim = cosine(vectors[u], vectors[v])
                else:
                    sim = jaccard(tok[u], tok[v])
                if sim > 0:
                    scored.append((sim, v))
            scored.sort(reverse=True)
            for sim, v in scored[: args.top_k]:
                if not add_link(u, v, "related_entity"):
                    break

    # Assemble per-page report.
    orphans = 0
    pages_out = []
    for u in nodes:
        final_in = in_deg.get(u, 0) + len(proposed_in[u])
        is_orphan = in_deg.get(u, 0) < args.min_inlinks
        if is_orphan:
            orphans += 1
        pages_out.append({
            "url": u,
            "pagerank": round(pr.get(u, 0), 8),
            "inlinks": in_deg.get(u, 0),
            "outlinks": len(out_adj[u]),
            "is_money_page": u in money,
            "orphan": is_orphan,
            "reachable_from_home": u in reachable,
            "proposed_inlinks": proposed_in[u],
            "proposed_outlinks": proposed_out[u],
            "final_inlinks_after_plan": final_in,
        })
    pages_out.sort(key=lambda p: p["pagerank"], reverse=True)

    links_proposed = sum(len(v) for v in proposed_out.values())
    unreachable = [u for u in nodes if u not in reachable]
    out = {
        "status": "ok",
        "nodes": len(nodes),
        "edges": len(existing),
        "home": home,
        "pagerank_iterations": iterations,
        "pagerank_converged": converged,
        "relatedness_method": method,
        "orphans": orphans,
        "unreachable_pages": len(unreachable),
        "unreachable_money_pages": unreachable_money,
        "links_proposed": links_proposed,
        "max_outlinks_cap": args.max_outlinks,
        "pages": pages_out,
    }
    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
