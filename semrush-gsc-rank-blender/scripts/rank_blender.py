#!/usr/bin/env python3
"""Semrush-GSC Rank Blender — reference implementation.

Joins Semrush tracked organic positions (third-party SERP scrape) with Search
Console's real per-query impression/click/position data (first-party truth) and
resolves the gap between them: one blended table where the tools agree, the queries
where they disagree beyond a threshold, and the real demand GSC reveals that Semrush
is not tracking at all.

Auth:   SEMRUSH_API_KEY  AND  GSC_OAUTH_TOKEN (or GCP_ACCESS_TOKEN) with
        webmasters.readonly. This std-lib reference uses a bearer access token
        (e.g. `gcloud auth print-access-token`); it cannot sign a service-account JWT.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 rank_blender.py --site sc-domain:example.com --domain example.com \
      --database us [--start 2026-08-15 --end 2026-09-11] [--delta-threshold 3] [--min-impr 20]
"""
from __future__ import annotations
import argparse, datetime, json, os, sys, time, urllib.parse
import urllib.request, urllib.error
from collections import defaultdict

SEMRUSH = "https://api.semrush.com/"
GSC = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
ROW_LIMIT = 25000


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def norm_kw(s):
    return " ".join((s or "").lower().split())


def default_dates():
    end = datetime.date.today() - datetime.timedelta(days=3)  # GSC data lag
    return (end - datetime.timedelta(days=27)).isoformat(), end.isoformat()


def fetch_semrush(domain, key, database, limit):
    params = urllib.parse.urlencode({
        "type": "domain_organic", "key": key, "domain": domain, "database": database,
        "display_limit": limit, "export_columns": "Ph,Po,Nq,Cp,Ur,Tr",
    })
    req = urllib.request.Request(SEMRUSH + "?" + params)
    text = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
                break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    fail("RATE_LIMITED", "Semrush API quota exhausted after backoff.")
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            fail("SEMRUSH_REQUEST_FAILED", "HTTP %s from Semrush." % e.code)
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            fail("SEMRUSH_UNREACHABLE", "Semrush API did not respond.")
    if text is None or text.startswith("ERROR"):
        # Semrush signals bad key / empty as a plain-text ERROR line.
        msg = (text or "").strip()[:80]
        if "NOTHING FOUND" in msg.upper():
            return {}
        fail("SEMRUSH_API_ERROR", "Semrush returned: %s" % (msg or "empty body"))
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return {}
    header = lines[0].split(";")
    idx = {name: i for i, name in enumerate(header)}
    need = ("Ph", "Po", "Ur")
    if not all(c in idx for c in need):
        fail("SEMRUSH_SCHEMA_UNEXPECTED", "Missing columns in Semrush response header: %s" % header)
    out = {}
    for line in lines[1:]:
        cols = line.split(";")
        if idx["Ph"] >= len(cols):
            continue
        kw = norm_kw(cols[idx["Ph"]])
        if not kw:
            continue

        def num(code, cast):
            j = idx.get(code)
            if j is None or j >= len(cols):
                return None
            try:
                return cast(cols[j])
            except (ValueError, TypeError):
                return None
        out[kw] = {"semrush_position": num("Po", lambda x: int(float(x))),
                   "volume": num("Nq", lambda x: int(float(x))),
                   "cpc": num("Cp", float),
                   "semrush_url": cols[idx["Ur"]] if idx["Ur"] < len(cols) else None}
    return out


def gsc_token():
    tok = os.environ.get("GSC_OAUTH_TOKEN") or os.environ.get("GCP_ACCESS_TOKEN")
    if not tok:
        fail("AUTH_MISSING_GSC_TOKEN",
             "Set GSC_OAUTH_TOKEN or GCP_ACCESS_TOKEN (bearer, webmasters.readonly).")
    return tok


def fetch_gsc(site, token, start, end, max_rows):
    url = GSC.format(site=urllib.parse.quote(site, safe=""))
    start_row, rows = 0, []
    while True:
        body = json.dumps({"startDate": start, "endDate": end, "dimensions": ["query", "page"],
                           "type": "web", "dataState": "final", "rowLimit": ROW_LIMIT,
                           "startRow": start_row}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"})
        page = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    page = json.loads(r.read() or b"{}").get("rows", [])
                    break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    fail("AUTH_INVALID_GSC_TOKEN", "GSC bearer token rejected (401).")
                if e.code == 403:
                    fail("AUTH_NO_SITE_ACCESS", "Identity is not a user on GSC property %s." % site)
                if e.code in (429, 500, 503):
                    if attempt == 5:
                        fail("RATE_LIMITED", "GSC API quota after retries.")
                    time.sleep(2 ** attempt); continue
                fail("GSC_REQUEST_FAILED", "HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:160]))
            except Exception:
                if attempt < 3:
                    time.sleep(2 ** attempt); continue
                fail("GSC_UNREACHABLE", "GSC API did not respond.")
        rows.extend(page or [])
        if not page or len(page) < ROW_LIMIT or len(rows) >= max_rows:
            return rows[:max_rows]
        start_row += ROW_LIMIT


def aggregate_gsc(rows):
    """Collapse query x page rows into one record per query (impression-weighted position)."""
    agg = defaultdict(lambda: {"clicks": 0, "impressions": 0, "pos_num": 0.0, "pages": defaultdict(int)})
    for row in rows:
        keys = row.get("keys") or ["", ""]
        q, page = norm_kw(keys[0]), (keys[1] if len(keys) > 1 else "")
        impr = row.get("impressions", 0) or 0
        a = agg[q]
        a["clicks"] += row.get("clicks", 0) or 0
        a["impressions"] += impr
        a["pos_num"] += (row.get("position", 0) or 0) * impr
        a["pages"][page] += row.get("clicks", 0) or 0
    out = {}
    for q, a in agg.items():
        impr = a["impressions"]
        top_page = max(a["pages"].items(), key=lambda kv: (kv[1], 1))[0] if a["pages"] else None
        out[q] = {"clicks": a["clicks"], "impressions": impr,
                  "gsc_position": round(a["pos_num"] / impr, 2) if impr else None,
                  "gsc_top_url": top_page}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--database", default="us")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--delta-threshold", type=float, default=3.0, dest="delta_threshold")
    ap.add_argument("--min-impr", type=int, default=20, dest="min_impr")
    ap.add_argument("--max-keywords", type=int, default=10000, dest="max_keywords")
    ap.add_argument("--max-rows", type=int, default=100000, dest="max_rows")
    a = ap.parse_args()

    skey = os.environ.get("SEMRUSH_API_KEY")
    if not skey:
        fail("AUTH_MISSING_SEMRUSH_KEY", "Set SEMRUSH_API_KEY.")
    token = gsc_token()
    start, end = (a.start, a.end) if (a.start and a.end) else default_dates()

    semrush = fetch_semrush(a.domain, skey, a.database, a.max_keywords)
    time.sleep(0.3)
    gsc_rows = fetch_gsc(a.site, token, start, end, a.max_rows)
    gsc = aggregate_gsc(gsc_rows)

    if not semrush and not gsc:
        json.dump({"status": "no_data", "site": a.site, "domain": a.domain,
                   "date_range": [start, end]}, sys.stdout)
        return

    common = [k for k in semrush if k in gsc]
    agreements, disagreements = [], []
    abs_deltas = []
    for kw in common:
        sp = semrush[kw]["semrush_position"]
        gp = gsc[kw]["gsc_position"]
        if sp is None or gp is None:
            continue
        delta = round(sp - gp, 2)  # positive = Semrush shows a worse (higher-number) rank than GSC
        abs_deltas.append(abs(delta))
        row = {"keyword": kw, "semrush_position": sp, "gsc_position": gp,
               "position_delta": delta, "impressions": gsc[kw]["impressions"],
               "clicks": gsc[kw]["clicks"], "volume": semrush[kw]["volume"],
               "semrush_url": semrush[kw]["semrush_url"], "gsc_top_url": gsc[kw]["gsc_top_url"],
               "url_mismatch": bool(semrush[kw]["semrush_url"] and gsc[kw]["gsc_top_url"]
                                    and semrush[kw]["semrush_url"] != gsc[kw]["gsc_top_url"])}
        if abs(delta) >= a.delta_threshold:
            row["direction"] = "semrush_optimistic" if delta < 0 else "semrush_pessimistic"
            row["impact"] = round(abs(delta) * gsc[kw]["impressions"], 1)
            disagreements.append(row)
        else:
            agreements.append(row)

    # Real demand GSC sees that Semrush is not tracking.
    untracked = []
    for kw, g in gsc.items():
        if kw in semrush:
            continue
        if g["impressions"] >= a.min_impr:
            untracked.append({"keyword": kw, "impressions": g["impressions"], "clicks": g["clicks"],
                              "gsc_position": g["gsc_position"], "gsc_top_url": g["gsc_top_url"]})

    # Semrush keywords with no GSC demand (phantom volume, wrong locale, or not truly ranking here).
    semrush_only = []
    for kw, s in semrush.items():
        if kw in gsc:
            continue
        semrush_only.append({"keyword": kw, "semrush_position": s["semrush_position"],
                             "volume": s["volume"], "semrush_url": s["semrush_url"]})

    disagreements.sort(key=lambda r: r["impact"], reverse=True)
    untracked.sort(key=lambda r: r["impressions"], reverse=True)
    semrush_only.sort(key=lambda r: (r["volume"] or 0), reverse=True)
    abs_deltas.sort()
    n_cmp = len(agreements) + len(disagreements)
    median_abs = abs_deltas[len(abs_deltas) // 2] if abs_deltas else None

    result = {
        "status": "ok",
        "site": a.site, "domain": a.domain, "database": a.database, "date_range": [start, end],
        "summary": {
            "semrush_keywords": len(semrush), "gsc_queries": len(gsc),
            "compared": n_cmp, "agreements": len(agreements), "disagreements": len(disagreements),
            "agreement_rate": round(len(agreements) / n_cmp, 3) if n_cmp else None,
            "median_abs_position_delta": median_abs,
            "untracked_demand_queries": len(untracked), "semrush_only_keywords": len(semrush_only),
            "delta_threshold": a.delta_threshold,
        },
        "disagreements": disagreements[:500],
        "untracked_demand": untracked[:500],
        "semrush_only": semrush_only[:500],
        "agreements_sample": agreements[:100],
    }
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
