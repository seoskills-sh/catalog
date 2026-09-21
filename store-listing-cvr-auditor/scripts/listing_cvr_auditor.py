#!/usr/bin/env python3
"""Store Listing CVR Auditor — reference implementation.

Audits an App Store or Google Play listing (icon, screenshot sequence,
captions, subtitle, description, preview video, localization) against ASO
conversion heuristics and current store guidelines, then emits prioritized
CVR fixes and a statistically valid A/B experiment plan (variants, traffic
split, success metric, minimum sample from a normal-approximation power calc).

Auth:   Offline audit from --listing needs no key. --fetch-live requires
        APP_STORE_CONNECT_TOKEN (iOS) or GOOGLE_PLAY_ACCESS_TOKEN (Android).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 listing_cvr_auditor.py --listing listing.json --platform ios
"""
from __future__ import annotations
import argparse, json, math, os, sys, time
import urllib.request, urllib.error, urllib.parse

# Store guideline constants (2024/2025 App Store & Play).
GUIDE = {
    "ios": {"icon": (1024, 1024), "alpha_allowed": False, "subtitle_max": 30,
            "promo_max": 170, "shots_max": 10, "shots_reco": 5,
            "mechanism": "App Store Product Page Optimization", "max_treatments": 3,
            "success_metric": "product_page_conversion_rate"},
    "android": {"icon": (512, 512), "alpha_allowed": True, "subtitle_max": 80,
                "promo_max": 80, "shots_max": 8, "shots_reco": 4,
                "mechanism": "Play Store Listing Experiments", "max_treatments": 4,
                "success_metric": "store_listing_conversion_rate"},
}
Z_ALPHA = 1.959963985  # two-sided alpha = 0.05
Z_POWER = 0.841621234  # power = 0.80
APPS_URL = {"ios": "https://api.appstoreconnect.apple.com/v1/apps/{id}",
            "android": "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{id}"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        fail("INPUT_MISSING", "%s not found: %s" % (label, path))
    except (ValueError, OSError) as exc:
        fail("INPUT_INVALID", "%s not readable: %s" % (label, exc))


def fetch_live(platform, app_id, token):
    url = APPS_URL[platform].format(id=urllib.parse.quote(str(app_id)))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % token})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                fail("AUTH_EXPIRED", "Store API token expired or invalid.")
            if exc.code == 429:
                if attempt == 5:
                    fail("RATE_LIMITED", "Store API quota exhausted.")
                time.sleep(2 ** attempt)
                continue
            if exc.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            fail("LIVE_FETCH_FAILED", "Store API returned HTTP %s." % exc.code)
        except Exception as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            fail("LIVE_FETCH_FAILED", "Store API request error: %s" % exc)
    fail("LIVE_FETCH_FAILED", "Store API unreachable.")


def chk(cid, cat, status, weight, message, recommendation=None):
    return {"id": cid, "category": cat, "status": status, "weight": weight,
            "message": message, "recommendation": recommendation}


def audit(listing, g):
    checks = []
    # ---- Icon ----
    icon = listing.get("icon") or {}
    want_w, want_h = g["icon"]
    if icon.get("width") == want_w and icon.get("height") == want_h:
        checks.append(chk("icon_dimensions", "icon", "pass", 3, "Icon is %dx%d." % (want_w, want_h)))
    else:
        checks.append(chk("icon_dimensions", "icon", "fail", 3,
                          "Icon is %sx%s, expected %dx%d." % (icon.get("width"), icon.get("height"), want_w, want_h),
                          "Export the icon at exactly %dx%d." % (want_w, want_h)))
    if icon.get("has_text"):
        checks.append(chk("icon_no_text", "icon", "warn", 2, "Icon contains text.",
                          "Remove text from the icon; it is illegible at small sizes and hurts recognition."))
    else:
        checks.append(chk("icon_no_text", "icon", "pass", 2, "Icon has no baked-in text."))
    if icon.get("transparency") and not g["alpha_allowed"]:
        checks.append(chk("icon_no_alpha", "icon", "fail", 2, "Icon has transparency; App Store rejects alpha.",
                          "Flatten the icon onto an opaque background."))
    # ---- Screenshots ----
    shots = sorted(listing.get("screenshots") or [], key=lambda s: s.get("order", 99))
    n = len(shots)
    if n == 0:
        checks.append(chk("shots_present", "screenshots", "fail", 4, "No screenshots supplied.",
                          "Add at least %d screenshots." % g["shots_reco"]))
    else:
        if n >= g["shots_reco"]:
            checks.append(chk("shots_count", "screenshots", "pass", 3, "%d screenshots (>= %d recommended)." % (n, g["shots_reco"])))
        else:
            checks.append(chk("shots_count", "screenshots", "warn", 3,
                              "Only %d screenshots; %d+ convert better." % (n, g["shots_reco"]),
                              "Add screenshots up to the %d-slot maximum." % g["shots_max"]))
        first3 = shots[:3]
        missing_caps = [s.get("order") for s in first3 if not s.get("has_caption") and not s.get("caption")]
        if missing_caps:
            checks.append(chk("first_impression_captions", "screenshots", "fail", 4,
                              "First-impression screenshots %s have no caption." % missing_caps,
                              "The first 1-3 screenshots appear in search results; give each a benefit-led caption."))
        else:
            checks.append(chk("first_impression_captions", "screenshots", "pass", 4,
                              "First-impression screenshots are captioned."))
        long_caps = [s.get("order") for s in shots if (s.get("caption") and len(s["caption"]) > 45)]
        if long_caps:
            checks.append(chk("caption_length", "screenshots", "warn", 2,
                              "Captions on %s exceed ~45 chars." % long_caps,
                              "Tighten captions to one scannable benefit (<= 7 words)."))
        else:
            checks.append(chk("caption_length", "screenshots", "pass", 2, "Captions are concise."))
        orients = {s.get("orientation") for s in first3 if s.get("orientation")}
        if len(orients) > 1:
            checks.append(chk("orientation_consistency", "screenshots", "warn", 1,
                              "Mixed orientations in the first 3 screenshots.",
                              "Keep the first-impression set a single orientation."))
        else:
            checks.append(chk("orientation_consistency", "screenshots", "pass", 1, "Consistent first-set orientation."))
    if listing.get("preview_video"):
        checks.append(chk("preview_video", "screenshots", "pass", 2, "Preview video present."))
    else:
        checks.append(chk("preview_video", "screenshots", "warn", 2, "No preview video.",
                          "Add an app preview; it autoplays and lifts conversion."))
    # ---- Text ----
    sub = listing.get("subtitle") or ""
    if not sub:
        checks.append(chk("subtitle_present", "text", "warn", 3, "No subtitle/short description.",
                          "Add a benefit-led subtitle within %d chars." % g["subtitle_max"]))
    elif len(sub) > g["subtitle_max"]:
        checks.append(chk("subtitle_present", "text", "fail", 3,
                          "Subtitle is %d chars, max %d." % (len(sub), g["subtitle_max"]),
                          "Trim the subtitle to <= %d chars." % g["subtitle_max"]))
    else:
        checks.append(chk("subtitle_present", "text", "pass", 3, "Subtitle within length."))
    desc = listing.get("description") or ""
    if len(desc) < 100:
        checks.append(chk("description_frontload", "text", "warn", 2,
                          "Description is thin (%d chars)." % len(desc),
                          "Front-load the value proposition in the first 3 lines (above the fold)."))
    else:
        checks.append(chk("description_frontload", "text", "pass", 2, "Description has substance to front-load."))
    # ---- Localization ----
    locs = listing.get("locales_supported") or []
    if len(locs) <= 1:
        checks.append(chk("localization", "localization", "warn", 2,
                          "Only %d localization(s)." % len(locs),
                          "Add localizations to unlock more keyword space and higher regional CVR."))
    else:
        checks.append(chk("localization", "localization", "pass", 2, "%d localizations." % len(locs)))
    return checks, n


def score(checks):
    cats = {}
    got_all, tot_all = 0.0, 0.0
    for c in checks:
        val = 1.0 if c["status"] == "pass" else (0.5 if c["status"] == "warn" else 0.0)
        cat = c["category"]
        g, t = cats.get(cat, (0.0, 0.0))
        cats[cat] = (g + val * c["weight"], t + c["weight"])
        got_all += val * c["weight"]
        tot_all += c["weight"]
    out = {"overall": round(100 * got_all / tot_all) if tot_all else 0}
    for cat, (g, t) in cats.items():
        out[cat] = round(100 * g / t) if t else 0
    return out


def min_sample(p, mde_rel):
    p1 = p
    p2 = p * (1 + mde_rel)
    if p2 >= 1:
        p2 = 0.999
    delta = p2 - p1
    if delta <= 0:
        return None
    n = ((Z_ALPHA + Z_POWER) ** 2) * (p1 * (1 - p1) + p2 * (1 - p2)) / (delta ** 2)
    return int(math.ceil(n))


def experiment_plan(listing, checks, g, platform, mde_rel):
    fails = [c for c in checks if c["status"] != "pass"]
    fails.sort(key=lambda c: (0 if c["status"] == "fail" else 1, -c["weight"]))
    element = fails[0]["id"] if fails else "first_impression_captions"
    baseline = listing.get("baseline_cvr")
    assumed = baseline is None
    p = baseline if baseline is not None else 0.30
    per = min_sample(p, mde_rel)
    n_var = 2  # control + 1 treatment by default
    split = round(1.0 / n_var, 3)
    plan = {
        "element": element,
        "platform": platform,
        "mechanism": g["mechanism"],
        "variants": [{"name": "control", "change": "current listing"},
                     {"name": "treatment_1", "change": "fix: %s" % element}],
        "max_treatments_supported": g["max_treatments"],
        "traffic_split": [split, split],
        "success_metric": g["success_metric"],
        "baseline_cvr": round(p, 4),
        "baseline_assumed": assumed,
        "min_detectable_effect_rel": mde_rel,
        "min_sample_per_variant": per,
        "min_sample_total": per * n_var if per else None,
        "notes": [
            "Sample size from a two-proportion normal approximation (alpha=0.05 two-sided, power=0.80).",
            "%s allows up to %d treatment(s) against control; test one element at a time." % (g["mechanism"], g["max_treatments"]),
        ],
    }
    if assumed:
        plan["notes"].append("baseline_cvr assumed at 0.30 — supply the real store CVR for an accurate sample size.")
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", required=True)
    ap.add_argument("--platform", choices=["ios", "android"], default=None)
    ap.add_argument("--mde", type=float, default=0.10, help="Minimum detectable effect, relative (default 0.10).")
    ap.add_argument("--fetch-live", action="store_true", dest="fetch_live")
    args = ap.parse_args()

    listing = load_json(args.listing, "listing")
    platform = args.platform or listing.get("platform") or "ios"
    if platform not in GUIDE:
        fail("INPUT_INVALID", "platform must be ios or android, got %s." % platform)
    g = GUIDE[platform]

    if args.fetch_live:
        token = os.environ.get("APP_STORE_CONNECT_TOKEN") if platform == "ios" else os.environ.get("GOOGLE_PLAY_ACCESS_TOKEN")
        if not token:
            var = "APP_STORE_CONNECT_TOKEN" if platform == "ios" else "GOOGLE_PLAY_ACCESS_TOKEN"
            fail("AUTH_MISSING_STORE_TOKEN",
                 "Set %s (bearer minted from APP_STORE_CONNECT_KEY_ID/ISSUER_ID/PRIVATE_KEY) for --fetch-live." % var)
        live = fetch_live(platform, listing.get("app_id"), token)
        listing.setdefault("_live_meta", live)  # merged reference; offline fields still drive the audit

    checks, n_shots = audit(listing, g)
    scores = score(checks)
    prioritized = [c for c in checks if c["status"] != "pass"]
    prioritized.sort(key=lambda c: (0 if c["status"] == "fail" else 1, -c["weight"]))
    for c in prioritized:
        c["impact"] = round(c["weight"] * (1.0 if c["status"] == "fail" else 0.5), 2)

    status = "ok"
    warnings = []
    if n_shots == 0 and not (listing.get("icon")):
        status = "insufficient"
        warnings.append("Listing has neither icon nor screenshot metadata; audit is incomplete — supply image metadata.")

    plan = experiment_plan(listing, checks, g, platform, args.mde)
    json.dump({
        "status": status,
        "app_id": listing.get("app_id"),
        "platform": platform,
        "scores": scores,
        "checks": checks,
        "prioritized_fixes": prioritized,
        "experiment_plan": plan,
        "warnings": warnings,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
