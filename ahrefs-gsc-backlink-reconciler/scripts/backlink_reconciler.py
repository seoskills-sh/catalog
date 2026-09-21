#!/usr/bin/env python3
"""Ahrefs-GSC Backlink Reconciler — reference implementation.

Pulls referring domains from Ahrefs API v3, reads the Search Console "Top linking
sites" CSV export (GSC exposes NO links API, so a manual export is the honest
first-party source), optionally adds Semrush referring domains, normalizes every
host to its registrable domain, and reconciles the sources into one authoritative
link set with per-domain source attribution, coverage stats, gained/lost since the
last run, and discrepancies worth manual verification.

Auth:   AHREFS_API_TOKEN (Bearer). Optional: SEMRUSH_API_KEY.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 backlink_reconciler.py --target example.com \
      --gsc-links-csv top_linking_sites.csv [--previous prev.json] [--max-domains 5000]
"""
from __future__ import annotations
import argparse, csv, json, os, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import defaultdict

AHREFS = "https://api.ahrefs.com/v3/site-explorer/refdomains"
SEMRUSH = "https://api.semrush.com/analytics/v1/"

# Curated two-level public suffixes (heuristic; the full Public Suffix List is not a
# std-lib resource). registrable_domain() falls back to the last two labels otherwise.
TWO_LEVEL = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "net.uk", "sch.uk", "ltd.uk", "plc.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "id.au",
    "co.nz", "net.nz", "org.nz", "govt.nz", "ac.nz",
    "co.jp", "or.jp", "ne.jp", "go.jp", "ac.jp",
    "com.br", "net.br", "org.br", "gov.br",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
    "com.mx", "com.tr", "com.ar", "com.sg", "com.hk", "com.tw",
    "co.in", "net.in", "org.in", "gen.in", "firm.in",
    "co.za", "org.za", "co.kr", "or.kr", "com.pl", "com.ua", "co.il", "com.sa",
    "com.eg", "com.my", "com.ph", "com.vn", "co.id", "com.co", "com.pe", "com.ve",
    "com.ec", "com.gt", "co.th", "in.th", "com.pk", "com.bd", "com.ng", "com.gh",
}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def registrable_domain(host):
    h = (host or "").strip().lower()
    if not h:
        return ""
    if "//" in h:
        h = h.split("//", 1)[1]
    h = h.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in h:
        h = h.split("@", 1)[1]
    if ":" in h:
        h = h.split(":", 1)[0]
    h = h.strip(".")
    labels = [l for l in h.split(".") if l]
    if len(labels) <= 2:
        return ".".join(labels)
    last2 = ".".join(labels[-2:])
    if last2 in TWO_LEVEL:
        return ".".join(labels[-3:])
    return last2


def http_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return 200, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                fail("AUTH_INVALID_AHREFS_TOKEN", "Ahrefs rejected AHREFS_API_TOKEN (401).")
            if e.code == 403:
                fail("AHREFS_FORBIDDEN", "Token lacks access to this target or endpoint (403).")
            if e.code == 429:
                if attempt == 5:
                    return 429, {}
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, {"error_text": e.read().decode("utf-8", "replace")[:200]}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}
    return 429, {}


def fetch_ahrefs(target, token, limit):
    params = urllib.parse.urlencode({
        "target": target, "mode": "domain", "limit": limit, "order_by": "domain_rating:desc",
        "select": "domain,domain_rating,dofollow_links,linked_domains,first_seen,last_seen",
    })
    status, data = http_json(AHREFS + "?" + params, {"Authorization": "Bearer " + token,
                                                     "Accept": "application/json"})
    if status == 429:
        fail("RATE_LIMITED", "Ahrefs API quota exhausted after backoff.")
    if status != 200:
        fail("AHREFS_REQUEST_FAILED", "HTTP %s from Ahrefs." % status)
    out = {}
    for row in data.get("refdomains", data.get("domains", [])):
        dom = registrable_domain(row.get("domain", ""))
        if not dom:
            continue
        cur = out.get(dom)
        cand = {"domain_rating": row.get("domain_rating"),
                "dofollow_links": row.get("dofollow_links"),
                "first_seen": row.get("first_seen"), "last_seen": row.get("last_seen")}
        # collapse subdomains to the registrable domain, keeping the strongest signal
        if not cur or (cand["domain_rating"] or 0) > (cur["domain_rating"] or 0):
            out[dom] = cand
    return out


def fetch_semrush(target, key, limit):
    params = urllib.parse.urlencode({
        "key": key, "type": "backlinks_refdomains", "target": target, "target_type": "root_domain",
        "display_limit": limit, "export_columns": "domain,domain_ascore,backlinks_num,first_seen",
    })
    req = urllib.request.Request(SEMRUSH + "?" + params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
                break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return {}, "semrush_http_%s" % e.code
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return {}, "semrush_unreachable"
    else:
        return {}, "semrush_rate_limited"
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines or text.startswith("ERROR"):
        return {}, "semrush_error:%s" % (text[:60] if text else "empty")
    header = lines[0].split(";")
    idx = {name: i for i, name in enumerate(header)}
    out = {}
    for line in lines[1:]:
        cols = line.split(";")
        if "domain" not in idx or idx["domain"] >= len(cols):
            continue
        dom = registrable_domain(cols[idx["domain"]])
        if not dom:
            continue

        def g(name):
            j = idx.get(name)
            return cols[j] if j is not None and j < len(cols) else None
        out[dom] = {"domain_ascore": g("domain_ascore"), "backlinks_num": g("backlinks_num")}
    return out, None


def read_gsc_csv(path):
    """Parse the GSC 'Top linking sites' export. Column names vary by locale/version."""
    host_keys = ("site", "linking site", "domain", "linking sites", "sites")
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = [h for h in (reader.fieldnames or [])]
            hcol = None
            for h in headers:
                if h and h.strip().lower() in host_keys:
                    hcol = h
                    break
            if hcol is None and headers:
                hcol = headers[0]  # GSC puts the site host in the first column
            ccol = next((h for h in headers if h and ("linking" in h.lower() and "page" in h.lower())), None)
            for row in reader:
                dom = registrable_domain(row.get(hcol, ""))
                if not dom:
                    continue
                cnt = None
                if ccol:
                    raw = (row.get(ccol) or "").replace(",", "").strip()
                    if raw.isdigit():
                        cnt = int(raw)
                prev = out.get(dom, {"linking_pages": 0})
                out[dom] = {"linking_pages": (prev["linking_pages"] or 0) + (cnt or 0)}
    except OSError as e:
        fail("GSC_CSV_UNREADABLE", "Cannot read --gsc-links-csv: %s" % e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--gsc-links-csv", dest="gsc_csv")
    ap.add_argument("--previous")
    ap.add_argument("--use-semrush", action="store_true")
    ap.add_argument("--max-domains", type=int, default=5000, dest="max_domains")
    ap.add_argument("--verify-min-dr", type=int, default=30, dest="verify_min_dr",
                    help="Ahrefs-only domains at/above this DR are flagged for manual verification")
    a = ap.parse_args()

    token = os.environ.get("AHREFS_API_TOKEN")
    if not token:
        fail("AUTH_MISSING_AHREFS_TOKEN", "Set AHREFS_API_TOKEN (Bearer) for Ahrefs API v3.")

    ahrefs = fetch_ahrefs(a.target, token, a.max_domains)

    semrush, semrush_note = {}, "not_requested"
    if a.use_semrush:
        key = os.environ.get("SEMRUSH_API_KEY")
        if not key:
            semrush_note = "skipped_no_key"
        else:
            semrush, err = fetch_semrush(a.target, key, a.max_domains)
            semrush_note = err or "ok"
            time.sleep(0.3)

    gsc = {}
    gsc_note = "skipped_no_csv"
    if a.gsc_csv:
        gsc = read_gsc_csv(a.gsc_csv)
        gsc_note = "ok" if gsc else "empty_export"

    all_domains = set(ahrefs) | set(gsc) | set(semrush)
    authoritative = []
    for dom in sorted(all_domains):
        sources = []
        if dom in ahrefs:
            sources.append("ahrefs")
        if dom in gsc:
            sources.append("gsc")
        if dom in semrush:
            sources.append("semrush")
        entry = {"domain": dom, "sources": sources,
                 "domain_rating": ahrefs.get(dom, {}).get("domain_rating"),
                 "gsc_linking_pages": gsc.get(dom, {}).get("linking_pages"),
                 "first_seen": ahrefs.get(dom, {}).get("first_seen")}
        authoritative.append(entry)

    ahrefs_only = sorted(set(ahrefs) - set(gsc)) if gsc else []
    gsc_only = sorted(set(gsc) - set(ahrefs)) if gsc else []
    both = sorted(set(ahrefs) & set(gsc)) if gsc else []

    # Discrepancies worth a human look.
    verify = []
    if gsc:
        for dom in gsc_only:
            verify.append({"domain": dom, "flag": "in_gsc_not_ahrefs",
                           "note": "Google sees this link; Ahrefs index misses it. Confirm it is live."})
        for dom in ahrefs_only:
            dr = ahrefs.get(dom, {}).get("domain_rating") or 0
            if dr >= a.verify_min_dr:
                verify.append({"domain": dom, "flag": "high_dr_not_in_gsc", "domain_rating": dr,
                               "note": "High-authority ref domain absent from GSC export; may be nofollow, "
                                       "disallowed, or not yet counted by Google."})

    # Stateful gained/lost vs the previous authoritative set.
    prev_set = set()
    if a.previous:
        try:
            prev_set = set(json.load(open(a.previous)).get("authoritative_domains", []))
        except (OSError, ValueError) as e:
            fail("PREVIOUS_SNAPSHOT_INVALID", "Cannot read --previous: %s" % e)
    first_run = not prev_set
    gained = sorted(all_domains - prev_set) if prev_set else []
    lost = sorted(prev_set - all_domains) if prev_set else []

    n_all = len(all_domains) or 1
    coverage = {
        "total_authoritative": len(all_domains),
        "ahrefs_domains": len(ahrefs), "gsc_domains": len(gsc), "semrush_domains": len(semrush),
        "overlap_ahrefs_gsc": len(both),
        "overlap_pct": round(len(both) / n_all, 3) if gsc else None,
        "ahrefs_unique_pct": round(len(ahrefs_only) / n_all, 3) if gsc else None,
        "gsc_unique_pct": round(len(gsc_only) / n_all, 3) if gsc else None,
    }

    result = {
        "status": "baseline" if first_run else "ok",
        "target": a.target,
        "reconciliation": gsc_note if not gsc else "reconciled",
        "sources": {"ahrefs": "ok", "gsc": gsc_note, "semrush": semrush_note},
        "coverage": coverage,
        "ahrefs_only": ahrefs_only[:1000],
        "gsc_only": gsc_only[:1000],
        "both": both[:1000],
        "verify_candidates": sorted(verify, key=lambda x: -(x.get("domain_rating") or 0))[:500],
        "gained_domains": gained[:1000],
        "lost_domains": lost[:1000],
        "authoritative_sample": authoritative[:500],
        "authoritative_domains": sorted(all_domains),
    }
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
