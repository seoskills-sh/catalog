#!/usr/bin/env python3
"""Audit Remediation Prioritizer — reference implementation (capstone).

Ingests raw findings from any audit source (the JSON emitted by the other native
audit skills, or a flat finding list), normalises heterogeneous shapes into one
canonical finding model, deduplicates and groups by type/template, scores each by
traffic-at-stake / impact / confidence / effort (RICE or ICE) using real GSC/GA4
signals where present, and outputs a sequenced remediation roadmap with owners,
effort, and score — plus ready-to-file ticket payloads (dry-run; never posted).

Auth:   none required. Optional --traffic map weights reach; JIRA export is a
        dry-run payload builder only (this script never POSTs tickets).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 remediation_prioritizer.py \
      --findings migration.json,indexation.json,bloat.json,canonical.json \
      [--traffic clicks.json] [--model rice] [--phases 3] [--jira-export]
"""
from __future__ import annotations
import argparse, json, os, sys, math
from collections import defaultdict

# type -> (owner, effort_points, impact 0.5..3). Prefix rules fill the gaps.
TYPE_META = {
    "broken_redirect": ("engineering", 3, 3.0),
    "redirect_chain": ("engineering", 2, 1.0),
    "temporary_redirect": ("engineering", 2, 1.5),
    "title_regression": ("content", 2, 2.0),
    "structured_data_loss": ("engineering", 3, 2.0),
    "noindex_regression": ("engineering", 1, 3.0),
    "parity_degraded": ("seo", 3, 1.5),
    "indexation_orphaned_earning": ("seo", 3, 3.0),
    "indexation_discovered_not_indexed": ("seo", 5, 2.0),
    "indexation_crawled_not_indexed": ("content", 5, 2.0),
    "indexation_submitted_not_indexed": ("content", 5, 1.5),
    "indexation_excluded_noindex": ("engineering", 1, 2.0),
    "indexation_excluded_canonical": ("seo", 2, 1.5),
    "indexation_crawl_error_in_sitemap": ("engineering", 2, 2.0),
    "bloat_remove_410": ("engineering", 2, 1.0),
    "bloat_noindex": ("engineering", 1, 1.0),
    "bloat_consolidate": ("content", 5, 1.5),
    "canonical_to_noindex": ("engineering", 2, 3.0),
    "canonical_to_redirect": ("engineering", 2, 2.0),
    "canonical_to_non200": ("engineering", 2, 2.0),
    "canonical_chain": ("engineering", 2, 1.5),
    "noindex_with_canonical": ("engineering", 1, 3.0),
    "canonicalized_but_in_sitemap": ("seo", 1, 1.0),
    "hreflang_conflict": ("engineering", 5, 1.5),
    "duplicate_no_canonical": ("seo", 3, 2.0),
}
PREFIX_OWNER = [("indexation_", "seo"), ("bloat_", "engineering"), ("canonical_", "engineering"),
                ("hreflang", "engineering"), ("redirect", "engineering"), ("duplicate", "seo")]


def fail(code, message):
    json.dump({"status": "error", "error": {"code": code, "message": message}}, sys.stdout)
    sys.exit(1)


def meta_for(ftype):
    if ftype in TYPE_META:
        return TYPE_META[ftype]
    owner = "seo"
    for pre, o in PREFIX_OWNER:
        if ftype.startswith(pre):
            owner = o
            break
    return (owner, 3, 2.0)


def _urls(v):
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return [v] if isinstance(v, str) else []


def normalize(obj, source):
    """Detect an audit output shape and emit canonical findings."""
    out = []
    if isinstance(obj, list):
        for it in obj:
            out += _generic_item(it, source)
        return out
    if not isinstance(obj, dict):
        return out

    # Site Migration Auditor
    if "at_risk" in obj and isinstance(obj.get("at_risk"), list) and \
            any("redirect_class" in r for r in obj["at_risk"] if isinstance(r, dict)):
        fatal = {"BROKEN_404", "REDIRECT_TO_404", "REDIRECT_TO_ERROR", "LOOP", "FETCH_FAILED"}
        for r in obj["at_risk"]:
            if not isinstance(r, dict):
                continue
            flags = r.get("parity_flags", [])
            rc = r.get("redirect_class", "")
            if rc in fatal:
                ft = "broken_redirect"
            elif "CANONICAL_DRIFT" in flags:
                ft = "canonical_drift"
            elif "STRUCTURED_DATA_LOST" in flags:
                ft = "structured_data_loss"
            elif "TITLE_CHANGED" in flags:
                ft = "title_regression"
            elif "NOINDEX_ON_TARGET" in flags:
                ft = "noindex_regression"
            elif rc == "CHAIN":
                ft = "redirect_chain"
            elif rc == "TEMPORARY_REDIRECT":
                ft = "temporary_redirect"
            else:
                ft = "parity_degraded"
            out.append(mk(ft, _urls(r.get("old_url")), source,
                          traffic=float(r.get("clicks_at_risk", 0) or 0),
                          evidence="redirect=%s parity=%s flags=%s" % (rc, r.get("parity_score"), flags)))
        return out

    # Indexation Coverage Auditor
    if "gaps" in obj and isinstance(obj.get("gaps"), list):
        ledger_clicks = {}
        for row in obj.get("ledger", []) or []:
            if isinstance(row, dict) and row.get("url"):
                ledger_clicks[row["url"]] = float(row.get("clicks", 0) or 0)
        for g in obj["gaps"]:
            if not isinstance(g, dict):
                continue
            cause = g.get("cause", "unknown")
            urls = g.get("example_urls", [])
            traffic = sum(ledger_clicks.get(u, 0) for u in urls)
            out.append(mk("indexation_" + cause, urls, source,
                          traffic=traffic, count=g.get("count", len(urls)),
                          evidence=g.get("likely_fix", "")))
        return out

    # Canonicalization Auditor
    if "conflicts" in obj and isinstance(obj.get("conflicts"), list):
        for c in obj["conflicts"]:
            if not isinstance(c, dict):
                continue
            ctypes = c.get("conflict_types", []) or ["canonical_generic"]
            head = str(ctypes[0]).lower()
            # canonical_/noindex_/hreflang_ heads already name their template; others get a canonical_ prefix.
            ft = head if head.startswith(("canonical", "noindex", "hreflang")) else "canonical_" + head
            out.append(mk(ft, _urls(c.get("url")), source,
                          evidence=" ".join(c.get("why", []))[:400] or "canonical conflict",
                          extra={"corrected_canonical": c.get("corrected_canonical")}))
        for d in obj.get("duplicate_clusters", []) or []:
            if isinstance(d, dict):
                out.append(mk("duplicate_no_canonical", d.get("urls", []), source,
                              evidence="choose canonical: %s" % d.get("suggested_canonical", "")))
        return out

    # Index Bloat Pruning Auditor
    if "results" in obj and isinstance(obj.get("results"), list) and \
            any("action" in r for r in obj["results"] if isinstance(r, dict)):
        for r in obj["results"]:
            if not isinstance(r, dict):
                continue
            action = r.get("action")
            if action in (None, "keep", "already_gone"):
                continue
            out.append(mk("bloat_" + action, _urls(r.get("url")), source,
                          traffic=float(r.get("clicks", 0) or 0),
                          evidence="; ".join(r.get("reasons", [])) + (" -> %s" % r.get("consolidation_target") if r.get("consolidation_target") else "")))
        return out

    # Generic {findings:[...]}
    if "findings" in obj and isinstance(obj["findings"], list):
        for it in obj["findings"]:
            out += _generic_item(it, source)
        return out
    return out


def _generic_item(it, source):
    if not isinstance(it, dict):
        return []
    ft = str(it.get("type") or it.get("finding_type") or "generic").lower()
    urls = it.get("urls") or _urls(it.get("url"))
    sev = it.get("severity")
    traffic = float(it.get("clicks") or it.get("traffic") or it.get("clicks_at_risk") or 0)
    return [mk(ft, urls, source, traffic=traffic, severity=sev,
               evidence=str(it.get("evidence") or it.get("detail") or it.get("message") or ""))]


def mk(ftype, urls, source, traffic=0.0, count=None, evidence="", severity=None, extra=None):
    return {"finding_type": ftype, "urls": urls, "source": source,
            "traffic_at_stake": round(float(traffic or 0), 1),
            "count": count if count is not None else max(len(urls), 1),
            "evidence": evidence, "severity_hint": severity, "extra": extra or {}}


def confidence_of(f, has_traffic_file):
    c = 0.5
    if f["traffic_at_stake"] > 0:
        c += 0.3
    if f["finding_type"] in TYPE_META:
        c += 0.2
    if not f["urls"]:
        c -= 0.1
    return max(0.3, min(1.0, c))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", required=True, help="comma-separated JSON files")
    ap.add_argument("--traffic")
    ap.add_argument("--effort-map", dest="effort_map")
    ap.add_argument("--owner-map", dest="owner_map")
    ap.add_argument("--impact-map", dest="impact_map")
    ap.add_argument("--model", choices=["rice", "ice"], default="rice")
    ap.add_argument("--phases", type=int, default=3)
    ap.add_argument("--jira-export", action="store_true", dest="jira_export")
    args = ap.parse_args()

    files = [p.strip() for p in args.findings.split(",") if p.strip()]
    if not files:
        fail("INPUT_INVALID", "--findings needs at least one JSON file.")
    findings, sources = [], []
    for path in files:
        if not os.path.exists(path):
            fail("INPUT_FILE_MISSING", "Findings file not found: %s" % path)
        try:
            obj = json.load(open(path))
        except Exception as e:
            fail("INPUT_INVALID", "%s is not valid JSON: %s" % (path, e))
        src = os.path.basename(path)
        got = normalize(obj, src)
        sources.append({"file": src, "findings": len(got)})
        findings += got

    if not findings:
        json.dump({"status": "insufficient", "summary": {
            "findings_ingested": 0, "unique_findings": 0, "sources": sources,
            "reason": "No recognizable findings in the supplied files."},
            "roadmap": [], "findings": [], "tickets": []}, sys.stdout, indent=2)
        return

    # optional traffic override map
    traffic_map = {}
    if args.traffic:
        if not os.path.exists(args.traffic):
            fail("INPUT_FILE_MISSING", "Traffic file not found: %s" % args.traffic)
        traffic_map = {k: float(v) for k, v in json.load(open(args.traffic)).items()}
    has_traffic_file = bool(traffic_map)

    def load_map(path, cast):
        if not path:
            return {}
        if not os.path.exists(path):
            fail("INPUT_FILE_MISSING", "Map file not found: %s" % path)
        return {k: cast(v) for k, v in json.load(open(path)).items()}
    effort_over = load_map(args.effort_map, float)
    owner_over = load_map(args.owner_map, str)
    impact_over = load_map(args.impact_map, float)

    # dedup identical (type, sorted urls)
    seen, deduped = {}, []
    for f in findings:
        key = (f["finding_type"], tuple(sorted(f["urls"])))
        if key in seen:
            g = seen[key]
            g["traffic_at_stake"] = round(max(g["traffic_at_stake"], f["traffic_at_stake"]), 1)
            g["dupe_count"] = g.get("dupe_count", 1) + 1
            if f["source"] not in g["sources"]:
                g["sources"].append(f["source"])
            continue
        f["sources"] = [f["source"]]
        f["dupe_count"] = 1
        seen[key] = f
        deduped.append(f)

    # score each finding
    scored = []
    for f in deduped:
        owner, effort, impact = meta_for(f["finding_type"])
        owner = owner_over.get(f["finding_type"], owner)
        effort = effort_over.get(f["finding_type"], effort)
        impact = impact_over.get(f["finding_type"], impact)
        if f.get("severity_hint") in ("high", "critical"):
            impact = max(impact, 3.0)
        elif f.get("severity_hint") == "low":
            impact = min(impact, 1.0)
        # traffic override from the map, joined over the finding's URLs
        tmap = sum(traffic_map.get(u, 0) for u in f["urls"]) if has_traffic_file else 0
        traffic = max(f["traffic_at_stake"], tmap)
        reach = traffic if traffic > 0 else float(f["count"])
        conf = confidence_of(f, has_traffic_file)
        effort = max(0.5, float(effort))
        if args.model == "rice":
            score = (reach * impact * conf) / effort
        else:  # ice
            impact10 = impact * (10.0 / 3.0)
            ease10 = max(1.0, 10.0 - (effort - 1) * (9.0 / 7.0))
            score = (impact10 * (conf * 10.0) * ease10) / 100.0
        f.update({"owner": owner, "effort": effort, "impact": round(impact, 2),
                  "confidence": round(conf, 2), "reach": round(reach, 1),
                  "traffic_at_stake": round(traffic, 1), "score": round(score, 2)})
        scored.append(f)

    scored.sort(key=lambda x: (x["score"], x["traffic_at_stake"]), reverse=True)

    # group into templates
    templates = defaultdict(lambda: {"finding_type": None, "count": 0, "url_count": 0,
                                      "traffic_at_stake": 0.0, "score": 0.0, "owner": None,
                                      "effort": 0, "impact": 0.0, "example_urls": [], "fix": ""})
    for f in scored:
        t = templates[f["finding_type"]]
        t["finding_type"] = f["finding_type"]
        t["count"] += 1
        t["url_count"] += len(f["urls"])
        t["traffic_at_stake"] += f["traffic_at_stake"]
        t["score"] += f["score"]
        t["owner"] = f["owner"]
        t["effort"] = f["effort"]
        t["impact"] = f["impact"]
        if len(t["example_urls"]) < 5:
            t["example_urls"] += f["urls"][:5 - len(t["example_urls"])]
        if not t["fix"] and f["evidence"]:
            t["fix"] = f["evidence"][:300]

    tmpl_list = sorted(templates.values(), key=lambda x: (x["score"], x["traffic_at_stake"]), reverse=True)
    for t in tmpl_list:
        t["score"] = round(t["score"], 2)
        t["traffic_at_stake"] = round(t["traffic_at_stake"], 1)

    # sequence into phases (equal rank chunks)
    n = len(tmpl_list)
    phases = max(1, args.phases)
    chunk = max(1, math.ceil(n / phases))
    roadmap, tickets = [], []
    for i, t in enumerate(tmpl_list):
        phase = min(phases, i // chunk + 1)
        entry = {"phase": phase, "order": i + 1, **t}
        roadmap.append(entry)
        tickets.append({
            "title": "[SEO] %s (%d URLs)" % (t["finding_type"].replace("_", " ").title(), t["url_count"]),
            "body": "Type: %s\nOwner: %s\nEffort: %s pts\nImpact: %s\nTraffic at stake: %s\nScore: %s\nFix: %s\nExamples: %s" % (
                t["finding_type"], t["owner"], t["effort"], t["impact"], t["traffic_at_stake"],
                t["score"], t["fix"] or "see finding", ", ".join(t["example_urls"][:5])),
            "labels": ["seo-audit", t["finding_type"], "owner:" + str(t["owner"])],
            "owner": t["owner"], "estimate_points": t["effort"], "score": t["score"], "phase": phase,
        })

    export_mode = "dry_run"  # this script never POSTs; creating tickets requires explicit user approval.
    jira_ready = bool(args.jira_export and os.environ.get("JIRA_BASE_URL") and os.environ.get("JIRA_TOKEN"))

    summary = {
        "findings_ingested": len(findings),
        "unique_findings": len(deduped),
        "duplicates_merged": len(findings) - len(deduped),
        "sources": sources,
        "model": args.model,
        "total_traffic_at_stake": round(sum(f["traffic_at_stake"] for f in scored), 1),
        "has_traffic_data": has_traffic_file or any(f["traffic_at_stake"] > 0 for f in scored),
        "template_count": n,
        "phases": phases,
        "export_mode": export_mode,
        "jira_credentials_present": jira_ready,
    }
    json.dump({"status": "ok", "summary": summary, "roadmap": roadmap,
               "findings": scored, "tickets": tickets}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
