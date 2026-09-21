#!/usr/bin/env python3
"""Core Web Vitals CrUX Monitor — reference implementation.

Auth:   CRUX_API_KEY (Google Cloud API key with Chrome UX Report API enabled).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 crux_monitor.py --targets targets.json [--baseline baseline.json]
  targets.json: [{"url":"https://x.com/p"}, {"origin":"https://x.com"}]
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
TH = json.load(open(os.path.join(HERE, "..", "references", "thresholds.json")))["thresholds"]
ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={key}"
METRICS = ["largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"]
SHORT = {"largest_contentful_paint": "LCP", "interaction_to_next_paint": "INP", "cumulative_layout_shift": "CLS"}
DEFAULT_DELTA = {"LCP": 50, "INP": 50, "CLS": 0.02}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def rating(metric, p75):
    good, poor = TH[metric]["good"], TH[metric]["poor"]
    if p75 is None:
        return "insufficient_data"
    return "good" if p75 <= good else "poor" if p75 > poor else "needs_improvement"


def query(key, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(ENDPOINT.format(key=key), data=data,
                                headers={"Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return 200, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    fail("RATE_LIMITED", "CrUX quota exhausted.")
                time.sleep(2 ** attempt); continue
            return e.code, json.loads(e.read() or b"{}")
        except Exception as e:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {"error": {"message": str(e)}}
    return 0, {}


def one(key, target, ff):
    key_field = "url" if "url" in target else "origin"
    body = {key_field: target[key_field], "formFactor": ff, "metrics": METRICS}
    status, resp = query(key, body)
    data_level = key_field
    if status == 404 and key_field == "url":
        origin = target["url"].split("/", 3)[:3]
        body = {"origin": "/".join(origin), "formFactor": ff, "metrics": METRICS}
        status, resp = query(key, body)
        data_level = "origin_fallback"
    if status == 404:
        return {"target": target, "form_factor": ff, "status": "no_crux_data"}
    if status == 400:
        return {"target": target, "form_factor": ff, "status": "bad_target"}
    metrics = resp.get("record", {}).get("metrics", {})
    out, all_good = {}, True
    for m in METRICS:
        p75 = metrics.get(m, {}).get("percentiles", {}).get("p75")
        r = rating(m, p75)
        out[SHORT[m]] = {"p75": p75, "rating": r}
        if r != "good":
            all_good = False
    return {"target": target, "form_factor": ff, "status": "ok",
            "data_level": data_level, "metrics": out,
            "cwv_pass": all_good if all("rating" != "insufficient_data" for v in out.values() for _ in [0] if v["rating"] != "insufficient_data") and not any(v["rating"] == "insufficient_data" for v in out.values()) else "unknown"}


def apply_baseline(results, baseline, deltas):
    index = {(json.dumps(b["target"], sort_keys=True), b["form_factor"]): b for b in baseline or []}
    for r in results:
        if r["status"] != "ok":
            continue
        b = index.get((json.dumps(r["target"], sort_keys=True), r["form_factor"]))
        if not b or b.get("status") != "ok":
            continue
        for m, cur in r["metrics"].items():
            base = b["metrics"].get(m, {}).get("p75")
            if cur["p75"] is None or base is None:
                continue
            delta = round(cur["p75"] - base, 4)
            cur["delta_vs_baseline"] = delta
            cur["regressed"] = delta >= deltas.get(m, DEFAULT_DELTA[m])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--baseline")
    ap.add_argument("--form-factors", default="PHONE,DESKTOP", dest="ff")
    a = ap.parse_args()
    key = os.environ.get("CRUX_API_KEY")
    if not key:
        fail("AUTH_MISSING_API_KEY", "Set CRUX_API_KEY (Chrome UX Report API enabled).")
    targets = json.load(open(a.targets))
    baseline = json.load(open(a.baseline)) if a.baseline else None
    results = []
    for t in targets:
        for ff in [x.strip() for x in a.ff.split(",")]:
            results.append(one(key, t, ff))
            time.sleep(0.3)  # stay under per-minute quota
    apply_baseline(results, baseline, DEFAULT_DELTA)
    order = {"poor": 0, "needs_improvement": 1, "good": 2, "insufficient_data": 3}
    results.sort(key=lambda r: min([order.get(v["rating"], 4) for v in r.get("metrics", {}).values()] or [5]))
    json.dump({"status": "ok", "count": len(results), "results": results}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
