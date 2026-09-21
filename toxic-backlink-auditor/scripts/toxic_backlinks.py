#!/usr/bin/env python3
"""Toxic Backlink Auditor — reference implementation (DataForSEO Backlinks shape).

Auth:   DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD (Basic) or BACKLINK_API_KEY.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 toxic_backlinks.py --target example.com [--threshold 70]
"""
from __future__ import annotations
import argparse, base64, json, os, re, sys, time
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SIG = json.load(open(os.path.join(HERE, "..", "references", "toxicity_signals.json")))
ENDPOINT = "https://api.dataforseo.com/v3/backlinks/referring_domains/live"
COMMERCIAL_ANCHOR = re.compile(r"\b(buy|cheap|casino|loan|viagra|payday|porn|escort|pills|betting)\b", re.I)


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout); sys.exit(1)


def auth_header():
    login, pw = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if login and pw:
        return {"Authorization": "Basic " + base64.b64encode(f"{login}:{pw}".encode()).decode()}
    fail("AUTH_MISSING_BACKLINK_PROVIDER", "Set DATAFORSEO_LOGIN/PASSWORD or BACKLINK_API_KEY.")


def fetch(target, hdr, limit):
    body = json.dumps([{"target": target, "limit": limit, "order_by": ["rank,asc"]}]).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json", **hdr})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
                res = (data.get("tasks") or [{}])[0].get("result") or []
                return 200, (res[0].get("items", []) if res else [])
        except urllib.error.HTTPError as e:
            if e.code == 402:
                fail("PROVIDER_PAYMENT_REQUIRED", "Backlink provider requires credits.")
            if e.code == 429:
                if attempt == 5:
                    return 429, []
                time.sleep(2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, []
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, []
    return 429, []


def score_domain(it):
    w = SIG["weights"]
    reasons, score = [], 0
    rank = it.get("rank", 0) or 0
    tld = "." + (it.get("domain", "").rsplit(".", 1)[-1] if "." in it.get("domain", "") else "")
    backlinks = it.get("backlinks", 0) or 0
    dofollow = it.get("dofollow", 0) or 0
    anchor = (it.get("anchor") or "")
    if rank <= SIG["low_rank_threshold"]:
        score += w["low_rank"]; reasons.append("very_low_authority")
    if tld in SIG["spam_tlds"]:
        score += w["spam_tld"]; reasons.append(f"spam_tld({tld})")
    if backlinks >= SIG["sitewide_backlinks_threshold"]:
        score += w["sitewide"]; reasons.append("sitewide_footer_link")
    if COMMERCIAL_ANCHOR.search(anchor) and rank <= SIG["low_rank_threshold"] + 100:
        score += w["spam_anchor"]; reasons.append("spam_commercial_anchor")
    if backlinks > 0 and dofollow == 0:
        score += w["nofollow_only_farm"]; reasons.append("nofollow_only")
    score = min(100, score)
    cls = "high" if score >= 70 else "medium" if score >= 40 else "low"
    return score, cls, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--threshold", type=int, default=70)
    ap.add_argument("--max-domains", type=int, default=5000, dest="max_domains")
    a = ap.parse_args()
    hdr = auth_header()
    status, items = fetch(a.target, hdr, a.max_domains)
    if status == 429:
        fail("RATE_LIMITED", "Backlink API quota exhausted.")
    if status != 200:
        fail("REQUEST_FAILED", "HTTP %s" % status)

    scored, disavow, dofollow_n = [], [], 0
    for it in items:
        if not it.get("domain"):
            continue
        if it.get("lost_date"):
            continue  # already gone
        s, cls, reasons = score_domain(it)
        if (it.get("dofollow", 0) or 0) > 0:
            dofollow_n += 1
        entry = {"domain": it["domain"], "toxicity_score": s, "risk": cls,
                 "authority": it.get("rank", 0) or 0, "backlinks": it.get("backlinks", 0) or 0,
                 "reasons": reasons}
        scored.append(entry)
        if s >= a.threshold:
            disavow.append(f"domain:{it['domain']}")
    scored.sort(key=lambda x: x["toxicity_score"], reverse=True)
    n = len(scored)
    json.dump({"status": "ok", "advisory": True, "target": a.target,
               "domains_scored": n,
               "toxic_pct": round(len(disavow) / n, 3) if n else 0,
               "dofollow_ratio": round(dofollow_n / n, 3) if n else 0,
               "disavow_candidate_count": len(disavow),
               "disavow_file_lines": disavow[:2000],
               "scored_domains": scored[:1000]}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
