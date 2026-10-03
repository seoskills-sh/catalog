#!/usr/bin/env python3
"""Product Feed SEO Optimizer: reference implementation.

Audits a Google Merchant product feed for title quality, attribute
completeness, and GTIN/identifier validity (real GS1 mod-10 check digit),
generates recommended Product JSON-LD, cross-checks Merchant Center
disapprovals via the Merchant API (products v1), and rewrites titles to a
demand-informed pattern, returning a per-product diff plus schema fixes that
serve both organic and Shopping.

Auth:   keyless for the feed. Optional: MERCHANT_API_ACCESS_TOKEN (OAuth, content
        scope) with --merchant-id; an existing CONTENT_API_ACCESS_TOKEN also works.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 product_feed_optimizer.py --feed feed.tsv [--volumes volumes.json] \
      [--merchant-id 1234567] [--title-pattern "{brand} {title} {color} {size}"]
"""
from __future__ import annotations
import argparse, csv, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from collections import Counter

# Merchant API, products sub-API v1. It replaces the Content API for Shopping,
# which Google sunset on 2026-08-18: since 2026-09-01 its requests can fail with
# HTTP 410 Gone, and it shuts down fully in early 2027.
MERCHANT_API = "https://merchantapi.googleapis.com/products/v1/accounts/{mid}/products"
STATUS_PAGE_SIZE = 1000  # the API's maximum page size
REQUIRED = ["id", "title", "description", "link", "image_link", "availability", "price", "condition"]
APPAREL_HINTS = ("apparel", "clothing", "shoes", "footwear", "accessories")
APPAREL_REQUIRED = ["color", "size", "gender", "age_group"]
PROMO_RE = re.compile(r"(?i)\b(free shipping|best price|lowest price|on sale|sale|% ?off|hot deal|buy now|cheap)\b")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
MAX_BACKOFF = 5


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def gtin_valid(raw):
    """GS1 mod-10 check digit validation for GTIN-8/12/13/14."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) not in (8, 12, 13, 14):
        return False
    nums = [int(c) for c in digits]
    payload, check = nums[:-1], nums[-1]
    total = 0
    for i, d in enumerate(reversed(payload)):
        total += d * (3 if i % 2 == 0 else 1)
    computed = (10 - (total % 10)) % 10
    return computed == check


def load_feed(path, max_products):
    with open(path, "rb") as f:
        head = f.read(4096)
    text_head = head.decode("utf-8", "ignore").lstrip()
    products = []
    if text_head[:1] in ("[", "{"):
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("products") or data.get("items") or [data]
        for row in data:
            if isinstance(row, dict):
                products.append({str(k).lower(): ("" if v is None else str(v)) for k, v in row.items()})
    else:
        delim = "\t" if ("\t" in text_head or path.lower().endswith(".tsv")) else ","
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f, delimiter=delim)
            for row in reader:
                products.append({(k or "").strip().lower(): (v or "").strip() for k, v in row.items()})
    return products[:max_products]


def product_keys(product):
    """The ids a feed row may use for a Merchant API product: its offer id, and
    the product id in its resource name (accounts/1/products/en~US~sku1 gives
    en~US~sku1)."""
    keys = []
    if product.get("offerId"):
        keys.append(product["offerId"])
    name = product.get("name") or ""
    if "/products/" in name:
        keys.append(name.split("/products/", 1)[1])
    return keys


def disapproved_issues(product):
    """The product's item-level issues that disapprove it (severity DISAPPROVED)."""
    status = product.get("productStatus") or {}
    return [{"code": it.get("code"), "severity": it.get("severity"),
             "attribute": it.get("attribute"), "description": it.get("description")}
            for it in status.get("itemLevelIssues", [])
            if it.get("severity") == "DISAPPROVED"]


def fetch_disapprovals(merchant_id, max_pages):
    # The Merchant API takes the same OAuth scope (content) as the old Content
    # API, so a token minted for it keeps working.
    token = os.environ.get("MERCHANT_API_ACCESS_TOKEN") or os.environ.get("CONTENT_API_ACCESS_TOKEN")
    if not token:
        fail("AUTH_MISSING_MERCHANT_API", "--merchant-id set but MERCHANT_API_ACCESS_TOKEN is unset.")
    base = MERCHANT_API.format(mid=urllib.parse.quote(str(merchant_id), safe=""))
    by_id = {}
    page_token = None
    pages = 0
    while pages < max_pages:
        pages += 1
        params = {"pageSize": STATUS_PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        url = base + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + token)
        for attempt in range(MAX_BACKOFF + 1):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    fail("AUTH_MERCHANT_API_FORBIDDEN", "Merchant API token invalid or lacks access to merchant %s." % merchant_id)
                if e.code == 429:
                    if attempt >= MAX_BACKOFF:
                        fail("RATE_LIMITED", "Merchant API quota exhausted.")
                    time.sleep(2 ** attempt)
                    continue
                if e.code >= 500 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                fail("REQUEST_FAILED", "Merchant API HTTP %s." % e.code)
            except Exception:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                fail("REQUEST_FAILED", "Merchant API request failed.")
        else:
            fail("RATE_LIMITED", "Merchant API unavailable after retries.")
        for product in data.get("products", []):
            issues = disapproved_issues(product)
            if not issues:
                continue
            # One offer id can sit under several feed labels or languages
            # (en~US~sku1, en~CA~sku1), so merge their issues per key.
            for key in product_keys(product):
                merged = by_id.setdefault(key, [])
                merged.extend(i for i in issues if i not in merged)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.2)
    return by_id


def audit_title(p, title_max, title_optimal):
    title = p.get("title", "")
    brand = p.get("brand", "")
    issues = []
    ln = len(title)
    if not title:
        issues.append({"code": "TITLE_MISSING"})
        return 0, issues
    if ln > title_max:
        issues.append({"code": "TITLE_TOO_LONG", "length": ln, "limit": title_max})
    elif ln > title_optimal:
        issues.append({"code": "TITLE_OVER_OPTIMAL", "length": ln, "optimal": title_optimal, "severity": "info"})
    letters = [c for c in title if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.6:
        issues.append({"code": "PROMOTIONAL_CAPS"})
    if PROMO_RE.search(title):
        issues.append({"code": "PROMOTIONAL_TEXT"})
    if brand and brand.lower() not in title[:max(1, len(brand) + 15)].lower():
        issues.append({"code": "BRAND_NOT_FRONTLOADED", "brand": brand})
    words = re.findall(r"[a-z0-9]+", title.lower())
    common = [w for w, c in Counter(words).items() if c >= 3 and len(w) > 2]
    if common:
        issues.append({"code": "KEYWORD_STUFFING", "tokens": common[:5]})
    penalty = {"TITLE_TOO_LONG": 25, "TITLE_OVER_OPTIMAL": 8, "PROMOTIONAL_CAPS": 20,
               "PROMOTIONAL_TEXT": 20, "BRAND_NOT_FRONTLOADED": 15, "KEYWORD_STUFFING": 20}
    score = max(0, 100 - sum(penalty.get(i["code"], 5) for i in issues))
    return score, issues


def identifier_status(p):
    g = p.get("gtin", "")
    valid_g = gtin_valid(g) if g else False
    has_mpn = bool(p.get("mpn"))
    has_brand = bool(p.get("brand"))
    if g and not valid_g:
        return "invalid_gtin", valid_g
    if valid_g:
        return "gtin_valid", valid_g
    if has_brand and has_mpn:
        return "brand_mpn", valid_g
    if str(p.get("identifier_exists", "")).lower() in ("false", "no", "0"):
        return "identifier_exempt", valid_g
    return "missing_identifier", valid_g


def attribute_coverage(p):
    required = list(REQUIRED)
    cat = (p.get("google_product_category", "") + " " + p.get("product_type", "")).lower()
    if any(h in cat for h in APPAREL_HINTS):
        required += APPAREL_REQUIRED
    # identifier requirement: gtin OR mpn (with brand)
    missing = [a for a in required if not p.get(a)]
    if not (p.get("gtin") or p.get("mpn")):
        missing.append("gtin_or_mpn")
        required.append("gtin_or_mpn")
    coverage = round(1 - len(missing) / len(required), 3) if required else 1.0
    return coverage, missing, required


def build_jsonld(p):
    offer = {"@type": "Offer",
             "price": p.get("price", "").split(" ")[0] if p.get("price") else None,
             "priceCurrency": (p.get("price", "").split(" ")[1] if len(p.get("price", "").split(" ")) > 1 else None),
             "availability": "https://schema.org/InStock" if p.get("availability", "").lower() in ("in stock", "in_stock") else "https://schema.org/OutOfStock",
             "itemCondition": "https://schema.org/NewCondition" if p.get("condition", "new").lower() == "new" else "https://schema.org/UsedCondition"}
    if p.get("link"):
        offer["url"] = p.get("link")
    node = {"@context": "https://schema.org/", "@type": "Product",
            "name": p.get("title", ""),
            "image": p.get("image_link", "") or None,
            "description": p.get("description", "") or None,
            "sku": p.get("id", "") or None,
            "mpn": p.get("mpn", "") or None,
            "brand": {"@type": "Brand", "name": p.get("brand", "")} if p.get("brand") else None,
            "offers": {k: v for k, v in offer.items() if v is not None}}
    if gtin_valid(p.get("gtin", "")):
        node["gtin"] = re.sub(r"\D", "", p.get("gtin", ""))
    return {k: v for k, v in node.items() if v is not None}


def rewrite_title(p, pattern, volumes, title_max, title_optimal):
    brand = p.get("brand", "")
    title = p.get("title", "")
    # Avoid brand duplication when the title already leads with the brand.
    brand_field = "" if (brand and title.lower().startswith(brand.lower())) else brand
    fields = {"brand": brand_field, "title": title,
              "color": p.get("color", ""), "size": p.get("size", ""),
              "material": p.get("material", ""), "gender": p.get("gender", ""),
              "category": (p.get("product_type", "").split(">")[-1].strip() if p.get("product_type") else "")}
    try:
        base = pattern.format(**{k: fields.get(k, "") for k in fields})
    except (KeyError, IndexError):
        base = fields["brand"] + " " + fields["title"]
    base = re.sub(r"\s+", " ", base).strip(" -|,")
    # Demand-informed front-loading: pick the highest-volume phrase that the product matches.
    lead = None
    if volumes:
        hay = " ".join(v for v in fields.values() if v).lower() + " " + p.get("title", "").lower()
        matches = [(vol, term) for term, vol in volumes.items() if term.lower() in hay]
        if matches:
            matches.sort(reverse=True)
            lead = matches[0][1]
    new_title = base
    if lead and lead.lower() not in base[:title_optimal].lower():
        candidate = lead + " " + base
        new_title = re.sub(r"\s+", " ", candidate).strip()
    # Truncate at a word boundary to the Merchant limit.
    if len(new_title) > title_max:
        cut = new_title[:title_max].rsplit(" ", 1)[0]
        new_title = cut if cut else new_title[:title_max]
    return new_title, lead


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", required=True, help="Product feed: TSV/CSV or JSON.")
    ap.add_argument("--volumes", default=None, help="JSON map term -> monthly volume.")
    ap.add_argument("--title-pattern", default="{brand} {title} {color} {size}", dest="pattern")
    ap.add_argument("--title-max", type=int, default=150, dest="title_max")
    ap.add_argument("--title-optimal", type=int, default=70, dest="title_optimal")
    ap.add_argument("--merchant-id", default=None, dest="merchant_id")
    ap.add_argument("--max-products", type=int, default=100000, dest="max_products")
    ap.add_argument("--max-status-pages", type=int, default=40, dest="max_status_pages")
    args = ap.parse_args()

    products = load_feed(args.feed, args.max_products)
    if not products:
        fail("EMPTY_FEED", "No products parsed from the feed.")
    volumes = {}
    if args.volumes:
        volumes = {str(k): (v if isinstance(v, (int, float)) else 0) for k, v in json.load(open(args.volumes)).items()}
    disapprovals = fetch_disapprovals(args.merchant_id, args.max_status_pages) if args.merchant_id else {}

    results = []
    invalid_gtins = missing_ids = disapproved = 0
    title_sum = cov_sum = 0.0
    for p in products:
        pid = p.get("id", "") or p.get("offer_id", "")
        t_score, t_issues = audit_title(p, args.title_max, args.title_optimal)
        id_status, gv = identifier_status(p)
        if id_status == "invalid_gtin":
            invalid_gtins += 1
        if id_status == "missing_identifier":
            missing_ids += 1
        coverage, missing, _req = attribute_coverage(p)
        new_title, lead = rewrite_title(p, args.pattern, volumes, args.title_max, args.title_optimal)
        dis = disapprovals.get(pid) or disapprovals.get(p.get("offer_id", "")) or []
        if dis:
            disapproved += 1
        title_sum += t_score
        cov_sum += coverage
        results.append({
            "id": pid,
            "title": p.get("title", ""),
            "title_score": t_score,
            "title_issues": t_issues,
            "rewritten_title": new_title,
            "title_changed": new_title != p.get("title", ""),
            "demand_lead_keyword": lead,
            "gtin": p.get("gtin", "") or None,
            "gtin_valid": gv,
            "identifier_status": id_status,
            "attribute_coverage": coverage,
            "missing_attributes": missing,
            "schema_status": "recommended",
            "recommended_jsonld": build_jsonld(p),
            "merchant_disapprovals": dis,
        })

    n = len(results)
    results.sort(key=lambda r: (r["attribute_coverage"], r["title_score"]))
    out = {
        "status": "ok",
        "products_audited": n,
        "demand_informed": bool(volumes),
        "disapprovals_source": "merchant_api" if args.merchant_id else "none",
        "invalid_gtins": invalid_gtins,
        "missing_identifiers": missing_ids,
        "products_disapproved": disapproved,
        "avg_title_score": round(title_sum / n, 1),
        "avg_attribute_coverage": round(cov_sum / n, 3),
        "products": results,
    }
    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
