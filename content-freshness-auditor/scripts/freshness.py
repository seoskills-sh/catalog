#!/usr/bin/env python3
"""Content Freshness Auditor — reference implementation.

Auth:   keyless page fetch; SERP_API_KEY only if --serp-recency.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 freshness.py --urls https://a.com/p,https://a.com/q [--serp-recency]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, datetime, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "seoskills-freshness/1.0"}
DEPRECATED = ["universal analytics", "google+", "amp ", "floc", "expanded text ads",
              "jquery mobile", "adobe flash", "internet explorer 11"]
CURRENCY_CUE = re.compile(r"(best|top|guide|update|latest|in|for)\s+.{0,20}(20\d{2})", re.I)
STAT_YEAR = re.compile(r"\b(in|as of|by|since)\s+(20\d{2})\b|(\d{1,3}(?:\.\d+)?%)[^.]{0,40}\b(20\d{2})\b", re.I)


def get(url, timeout=12):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def parse_date(html):
    for pat in [r'"dateModified"\s*:\s*"([^"]+)"', r'"datePublished"\s*:\s*"([^"]+)"',
                r'(?:updated|modified)[^0-9]{0,20}(20\d{2}-\d{2}-\d{2})']:
        m = re.search(pat, html, re.I)
        if m:
            try:
                return datetime.date.fromisoformat(m.group(1)[:10])
            except Exception:
                continue
    return None


def audit(url, current_year, traffic):
    status, html = get(url)
    if status != 200:
        return {"url": url, "status": "unreachable", "http_status": status}
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I))
    head = " ".join(text.split()[:120])
    title = (re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I) or [None, ""])[1]

    signals = []
    d = parse_date(html)
    age_days = (datetime.date.today() - d).days if d else None
    if age_days is not None and age_days > 365:
        signals.append({"type": "old_date", "detail": d.isoformat(), "age_days": age_days})

    for m in CURRENCY_CUE.finditer(title + " " + head):
        yr = int(m.group(2))
        if yr < current_year:
            signals.append({"type": "stale_year", "detail": m.group(0).strip(), "year": yr})
    for m in STAT_YEAR.finditer(text):
        yr = int(m.group(2) or m.group(4) or 0)
        if 0 < yr <= current_year - 2:
            signals.append({"type": "aging_statistic", "detail": m.group(0).strip()[:80], "year": yr})
    tl = text.lower()
    for term in DEPRECATED:
        if term in tl:
            signals.append({"type": "deprecated_reference", "detail": term.strip()})

    stale_year_n = sum(1 for s in signals if s["type"] == "stale_year")
    stat_n = sum(1 for s in signals if s["type"] == "aging_statistic")
    dep_n = sum(1 for s in signals if s["type"] == "deprecated_reference")
    score = min(100, (min((age_days or 0) / 365, 3) * 15) + stale_year_n * 20 + stat_n * 8 + dep_n * 15)
    score = round(score)
    cls = "stale" if score > 60 else "aging" if score >= 30 else "fresh"
    tclicks = (traffic or {}).get(url, 0)
    return {"url": url, "status": "ok", "date": d.isoformat() if d else None,
            "date_confidence": "low" if d is None else "normal", "age_days": age_days,
            "staleness_score": score, "class": cls, "signals": signals,
            "monthly_clicks": tclicks, "priority": round(tclicks * score / 100, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls"); ap.add_argument("--sitemap")
    ap.add_argument("--current-year", type=int, default=datetime.date.today().year, dest="year")
    ap.add_argument("--traffic")
    ap.add_argument("--serp-recency", action="store_true")
    a = ap.parse_args()
    urls = [u.strip() for u in a.urls.split(",")] if a.urls else []
    if a.sitemap:
        _, xml = get(a.sitemap)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    urls = [u for u in urls if u]
    traffic = json.load(open(a.traffic)) if a.traffic else {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda u: audit(u, a.year, traffic), urls))
    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: r["priority"], reverse=True)
    unreachable = [r for r in results if r.get("status") != "ok"]
    json.dump({"status": "ok",
               "serp_recency": "skipped_no_key" if a.serp_recency and not os.environ.get("SERP_API_KEY") else ("enabled" if a.serp_recency else "off"),
               "audited": len(results), "stale_or_aging": len([r for r in ok if r["class"] != "fresh"]),
               "results": ok, "unreachable": unreachable}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
