#!/usr/bin/env python3
"""Programmatic Page Inventory Planner — reference implementation.

Demand-validate every {modifier} x {entity} combination BEFORE generating
programmatic pages. Fetches search volume + competition from DataForSEO
(Google Ads Search Volume) and, when connected, joins Google Search Console
to detect entities the target already ranks for. Scores each candidate page
for viability, projects traffic, and prunes zero-demand combos to prevent
index bloat.

Auth:   DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD (HTTP Basic).
        Optional: GSC_ACCESS_TOKEN (OAuth bearer, webmasters.readonly) with --site.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 page_inventory_planner.py --modifiers modifiers.json --entities entities.json \
      --pattern "{modifier} {entity}" [--site sc-domain:example.com] [--min-volume 20]
"""
from __future__ import annotations
import argparse, base64, json, math, os, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import OrderedDict

DFS_ENDPOINT = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"
GSC_ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
# Organic CTR by position (desktop, blended). Positions past 10 taper toward zero.
CTR = {1: 0.281, 2: 0.152, 3: 0.100, 4: 0.070, 5: 0.051, 6: 0.039, 7: 0.031,
       8: 0.025, 9: 0.021, 10: 0.018, 12: 0.012, 15: 0.008, 20: 0.005}
DFS_CHUNK = 700          # DataForSEO Google Ads accepts up to 1000 keywords/task; stay under.
MAX_BACKOFF = 5


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def ctr_for(position):
    """Nearest-defined-position CTR lookup for a projected rank."""
    if position <= 10:
        return CTR.get(position, CTR[10])
    for cut in (12, 15, 20):
        if position <= cut:
            return CTR[cut]
    return 0.003


def _basic_auth_header():
    login = os.environ.get("DATAFORSEO_LOGIN")
    pw = os.environ.get("DATAFORSEO_PASSWORD")
    if not login or not pw:
        fail("AUTH_MISSING_DATAFORSEO", "Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.")
    token = base64.b64encode(("%s:%s" % (login, pw)).encode("utf-8")).decode("ascii")
    return "Basic " + token


def dfs_search_volume(keywords, location_code, language_code):
    """POST a chunk of keywords to DataForSEO; return {keyword: metrics} or raise via fail()."""
    header = _basic_auth_header()
    body = json.dumps([{"keywords": keywords, "location_code": location_code,
                        "language_code": language_code}]).encode("utf-8")
    req = urllib.request.Request(DFS_ENDPOINT, data=body, method="POST")
    req.add_header("Authorization", header)
    req.add_header("Content-Type", "application/json")
    for attempt in range(MAX_BACKOFF + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = json.loads(r.read())
                break
        except urllib.error.HTTPError as e:
            if e.code == 401:
                fail("AUTH_INVALID_DATAFORSEO", "DataForSEO rejected the credentials (401).")
            if e.code == 429:
                if attempt >= MAX_BACKOFF:
                    fail("RATE_LIMITED", "DataForSEO quota exhausted after retries.")
                time.sleep(2 ** attempt)
                continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            fail("REQUEST_FAILED", "DataForSEO HTTP %s." % e.code)
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            fail("REQUEST_FAILED", "DataForSEO request failed after retries.")
    else:
        fail("RATE_LIMITED", "DataForSEO unavailable after retries.")
    tasks = payload.get("tasks") or []
    if not tasks or tasks[0].get("status_code") not in (20000, 20100):
        msg = tasks[0].get("status_message") if tasks else "no tasks returned"
        fail("REQUEST_FAILED", "DataForSEO task error: %s" % msg)
    out = {}
    for item in (tasks[0].get("result") or []):
        kw = (item.get("keyword") or "").lower()
        out[kw] = {"search_volume": item.get("search_volume"),
                   "competition_index": item.get("competition_index"),
                   "competition": item.get("competition"),
                   "cpc": item.get("cpc")}
    return out


def gsc_existing_queries(site, start, end):
    """Optional: return the set of query strings the target already earns impressions for."""
    token = os.environ.get("GSC_ACCESS_TOKEN")
    if not token:
        fail("AUTH_MISSING_GSC", "--site set but GSC_ACCESS_TOKEN is unset.")
    url = GSC_ENDPOINT.format(site=urllib.parse.quote(site, safe=""))
    body = json.dumps({"startDate": start, "endDate": end, "dimensions": ["query"],
                       "rowLimit": 25000, "dataState": "final"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    for attempt in range(MAX_BACKOFF + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = json.loads(r.read()).get("rows", [])
                return {row["keys"][0].lower(): row.get("impressions", 0) for row in rows}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                fail("AUTH_GSC_FORBIDDEN", "GSC token invalid or lacks access to %s." % site)
            if e.code in (429, 500, 503):
                if attempt >= MAX_BACKOFF:
                    return {}  # non-fatal: proceed without the exists-signal
                time.sleep(2 ** attempt)
                continue
            return {}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {}
    return {}


def build_combos(modifiers, entities, pattern, max_combos):
    seen = OrderedDict()
    truncated = False
    for m in modifiers:
        for e in entities:
            try:
                kw = pattern.format(modifier=m, entity=e).strip()
            except (KeyError, IndexError):
                fail("BAD_PATTERN", "Pattern must use {modifier} and {entity} placeholders only.")
            kw = " ".join(kw.split()).lower()
            if not kw:
                continue
            if kw not in seen:
                if len(seen) >= max_combos:
                    truncated = True
                    continue
                seen[kw] = (m, e)
    return seen, truncated


def viability(volume, comp_index, cpc):
    """0-100 composite: demand (log volume), low competition, commercial value."""
    demand = min(1.0, math.log10(volume + 1) / 4.0)      # ~10k volume saturates demand
    ease = 1.0 - (float(comp_index) / 100.0 if comp_index is not None else 0.5)
    commercial = min(1.0, float(cpc or 0) / 8.0)
    return round(100 * (0.6 * demand + 0.25 * ease + 0.15 * commercial), 1)


def projected(volume, comp_index):
    """Estimate rank band from competition and project monthly organic clicks."""
    ci = comp_index if comp_index is not None else 50
    est_pos = 4 if ci <= 33 else 8 if ci <= 66 else 15
    return est_pos, int(round(volume * ctr_for(est_pos)))


def classify(kw, volume, comp_index, existing_impr, args):
    if volume is None:
        return "skip", "no_volume_data"
    if volume < args.min_volume:
        return "skip", "below_min_demand"     # prune to prevent index bloat
    if existing_impr and existing_impr > 0:
        return "exists", "already_ranking_in_gsc"
    if comp_index is not None and comp_index >= args.high_comp and volume < args.high_comp_min_volume:
        return "defer", "high_competition_low_reward"
    return "build", "validated_demand"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modifiers", required=True, help="JSON array of modifier strings.")
    ap.add_argument("--entities", required=True, help="JSON array of entity strings.")
    ap.add_argument("--pattern", default="{modifier} {entity}")
    ap.add_argument("--location-code", type=int, default=2840, dest="location_code")
    ap.add_argument("--language-code", default="en", dest="language_code")
    ap.add_argument("--min-volume", type=int, default=20, dest="min_volume")
    ap.add_argument("--high-comp", type=int, default=85, dest="high_comp")
    ap.add_argument("--high-comp-min-volume", type=int, default=500, dest="high_comp_min_volume")
    ap.add_argument("--max-combos", type=int, default=1000, dest="max_combos")
    ap.add_argument("--site", default=None, help="GSC property, e.g. sc-domain:example.com.")
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="2026-08-28")
    args = ap.parse_args()

    modifiers = json.load(open(args.modifiers))
    entities = json.load(open(args.entities))
    if not isinstance(modifiers, list) or not isinstance(entities, list):
        fail("BAD_INPUT", "modifiers and entities must each be a JSON array.")

    combos, truncated = build_combos(modifiers, entities, args.pattern, args.max_combos)
    if not combos:
        fail("BAD_INPUT", "No candidate combinations produced.")

    existing = gsc_existing_queries(args.site, args.start, args.end) if args.site else {}

    metrics = {}
    kw_list = list(combos.keys())
    for i in range(0, len(kw_list), DFS_CHUNK):
        chunk = kw_list[i:i + DFS_CHUNK]
        metrics.update(dfs_search_volume(chunk, args.location_code, args.language_code))
        time.sleep(0.3)

    inventory = []
    tallies = {"build": 0, "skip": 0, "defer": 0, "exists": 0}
    projected_total = 0
    no_data = 0
    for kw, (mod, ent) in combos.items():
        m = metrics.get(kw, {})
        vol = m.get("search_volume")
        ci = m.get("competition_index")
        cpc = m.get("cpc")
        if vol is None:
            no_data += 1
        existing_impr = existing.get(kw)
        decision, reason = classify(kw, vol, ci, existing_impr, args)
        est_pos, proj = projected(vol or 0, ci)
        if decision == "build":
            projected_total += proj
        tallies[decision] += 1
        inventory.append({
            "keyword": kw, "modifier": mod, "entity": ent,
            "search_volume": vol, "competition_index": ci, "cpc": cpc,
            "already_ranking": bool(existing_impr and existing_impr > 0),
            "viability_score": viability(vol or 0, ci, cpc),
            "projected_position": est_pos if vol else None,
            "projected_monthly_clicks": proj if (decision == "build") else 0,
            "decision": decision, "reason": reason,
        })

    inventory.sort(key=lambda r: (r["decision"] != "build", -r["viability_score"]))
    result = {
        "status": "ok",
        "candidates_evaluated": len(combos),
        "combinations_truncated": truncated,
        "demand_source": "dataforseo_google_ads",
        "exists_source": "gsc" if args.site else "none",
        "no_volume_data": no_data,
        "pages_to_build": tallies["build"],
        "pages_pruned": tallies["skip"],
        "pages_deferred": tallies["defer"],
        "already_ranking": tallies["exists"],
        "projected_monthly_clicks_total": projected_total,
        "inventory": inventory,
    }
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
