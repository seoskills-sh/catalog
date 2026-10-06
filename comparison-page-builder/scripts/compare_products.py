#!/usr/bin/env python3
"""Comparison Page Builder: reference implementation.

Collects sourced facts for a "X vs Y", "X alternatives" or "best X" page from
each product's own website (homepage positioning, pricing plans and prices,
free plan and trial, feature lists, integration claims), lines the features
up into one matrix, finds what people compare each product with (Google
Autocomplete), and returns titles, target keywords and an outline. Every fact
carries the URL it came from and the date it was read.

Auth:   none (fetches the products' public pages and Google Autocomplete).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 compare_products.py --product "Pipedrive=https://www.pipedrive.com" \
       --product "Zoho CRM=https://www.zoho.com/crm/" [--type vs|alternatives|roundup] [--yours Pipedrive]
"""
from __future__ import annotations
import argparse, collections, datetime, json, re, sys, time
import urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; seoskills-comparison-builder/1.0; +https://seoskills.sh)"
SUGGEST = "https://suggestqueries.google.com/complete/search"
PRICE = re.compile(r"(?P<cur>[$€£₹¥]|USD|EUR|GBP|INR)\s?(?P<amt>\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\d)"
                   r"\s*(?P<per>(?:/|per|a|an)\s*(?:user|seat|member|editor|agent|contact)?\s*/?\s*(?:per\s+)?(?:month|mo|year|yr|annum|week|day))?", re.I)
PERIOD_PER_MONTH = {"mo": 1, "month": 1, "yr": 1 / 12, "year": 1 / 12, "annum": 1 / 12, "week": 52 / 12, "day": 365 / 12}
FREE_PLAN = re.compile(r"\b(free (plan|forever|tier|version)|\$0\b|free for (up to|teams|individuals)|forever free)\b", re.I)
TRIAL = re.compile(r"\b(\d{1,3})[- ]day (free )?trial\b|\bfree trial\b", re.I)
CUSTOM = re.compile(r"\b(contact (sales|us)|custom pricing|request a quote|talk to sales|get a quote)\b", re.I)
INTEGRATIONS = re.compile(r"(\d[\d,]*)\s*\+?\s*(native )?(integrations|apps|connectors|plugins|extensions)\b", re.I)
CUSTOMERS = re.compile(r"(?:trusted by|used by|loved by|join|over|more than)\s+(\d[\d,.]*\s*(?:k|m|million|thousand)?\+?)\s+"
                       r"(customers|companies|businesses|teams|users|organizations|brands)", re.I)
PAGE_HINTS = {"pricing": re.compile(r"pricing|plans|price", re.I), "features": re.compile(r"features|capabilities|product(?!s?/)|platform|tour", re.I),
              "integrations": re.compile(r"integrations?|apps?/|marketplace|app-store|connectors", re.I)}
STOP = set("a an the of to in for and or with your you our we on by at as is are be from that this it its all any each more".split())
GENERIC_FEATURE = re.compile(r"^(learn more|see (all|more)|read more|get started|sign up|log ?in|contact|pricing|home|blog|about|"
                             r"careers|privacy|terms|cookie|help|support|docs?|resources|company|partners?|login|try .* free|book a demo)$", re.I)


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read(4_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, url, "", ""
    except Exception:
        return 0, url, "", ""


class Page(HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "td", "th", "section", "article", "main", "header", "footer",
             "nav", "aside", "tr", "ul", "ol", "table", "dd", "dt", "summary", "details", "body", "button"}
    SKIP = {"script", "style", "noscript", "template", "svg", "select"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.links, self.segments = "", {}, [], []
        self._stack, self._buf = [], []
        self._skip = self._chrome = 0
        self._in_title = False
        self._link = None

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.segments.append((self._stack[-1] if self._stack else "body", text, self._chrome > 0))

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title" and not self.title:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        if tag == "a" and a.get("href"):
            self._link = [a["href"], ""]
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self._flush()
            if tag in ("nav", "footer"):
                self._chrome += 1
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._link is not None:
            self.links.append((self._link[0], re.sub(r"\s+", " ", self._link[1]).strip()))
            self._link = None
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK and tag in self._stack:
            self._flush()
            while self._stack:
                top = self._stack.pop()
                if top in ("nav", "footer") and self._chrome:
                    self._chrome -= 1
                if top == tag:
                    break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._buf.append(data)
            if self._link is not None:
                self._link[1] += data

    def close(self):
        super().close()
        self._flush()


def parse(html):
    p = Page()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    return p


def feature_terms(text):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9+-]*", text.lower()) if w not in STOP and len(w) > 2}


def find_pages(home_url, page):
    """Pick the pricing, features and integrations pages from the homepage's own links, preferring exact matches
    (a /pricing path or a link that just says "Pricing") over pages that merely mention the word."""
    host = urllib.parse.urlsplit(home_url).netloc.lower().removeprefix("www.")
    best = {}
    for href, text in page.links:
        url = urllib.parse.urljoin(home_url, href).split("#")[0]
        sp = urllib.parse.urlsplit(url)
        if sp.netloc.lower().removeprefix("www.") != host or not sp.path.strip("/") or len(sp.path) >= 80:
            continue
        last = sp.path.rstrip("/").rsplit("/", 1)[-1].lower()
        label = (text or "").strip().lower()
        for kind, rx in PAGE_HINTS.items():
            score = (3 if rx.fullmatch(last) or last.startswith(kind) else 0) + (2 if rx.fullmatch(label) else 0) \
                + (1 if rx.search(sp.path) else 0) - sp.path.count("/") * 0.1
            if score >= 1 and score > best.get(kind, (0, ""))[0]:
                best[kind] = (score, url)
    return {k: v[1] for k, v in best.items()}


def extract_prices(segments):
    """Price points with the nearest preceding heading as the plan name."""
    plans, last_heading = [], ""
    for tag, text, chrome in segments:
        if chrome:
            continue
        if tag in ("h2", "h3", "h4", "h5") and len(text) <= 25 and not PRICE.search(text) and "?" not in text:
            last_heading = text
        for m in PRICE.finditer(text):
            raw = m.group("amt")
            amt = float(raw.replace(",", "") if re.search(r",\d{3}(\.|$)", raw) else raw.replace(",", "."))
            if amt == 0 and "0" not in m.group(0):
                continue
            per = (m.group("per") or "").lower()
            unit = next((u for u in PERIOD_PER_MONTH if re.search(r"\b%s\b" % u, per)), None)
            plans.append({"plan": last_heading, "amount": amt, "currency": m.group("cur").upper().replace("$", "USD").replace("€", "EUR").replace("£", "GBP").replace("₹", "INR").replace("¥", "JPY"),
                          "period": unit, "per_seat": bool(re.search(r"user|seat|member|editor|agent", per)),
                          "monthly_equivalent": round(amt * PERIOD_PER_MONTH[unit], 2) if unit else None, "text": text[:140]})
    seen, out = set(), []
    for pl in plans:
        k = (pl["plan"], pl["amount"], pl["period"])
        if k not in seen:
            seen.add(k)
            out.append(pl)
    return out[:30]


def extract_features(segments):
    items = []
    for tag, text, chrome in segments:
        if chrome or tag not in ("li", "h3", "h4", "dt", "td"):
            continue
        n = len(text.split())
        if 2 <= n <= 14 and not GENERIC_FEATURE.match(text) and not PRICE.search(text) and not re.search(r"https?://|@", text):
            items.append(text.strip(" -•*✓✔"))
    return list(dict.fromkeys(i for i in items if i))[:80]


def stem(w):
    for suf in ("ing", "ies", "es", "s", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def mentions(feature, segments_by_page):
    """Where a product's own pages mention every word of a checklist feature in one passage."""
    need = {stem(w) for w in feature_terms(feature)}
    for kind, segs in segments_by_page.items():
        for _, text, chrome in segs:
            have = {stem(w) for w in feature_terms(text)}
            if need and need <= have and len(text) <= 400:
                return {"page": kind, "text": text[:160]}
    return None


def autocomplete(q, hl="en", gl="us"):
    url = SUGGEST + "?" + urllib.parse.urlencode({"client": "firefox", "hl": hl, "gl": gl, "q": q})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=10) as r:
            return [s for s in json.loads(r.read().decode("utf-8", "ignore"))[1] if isinstance(s, str)]
    except Exception:
        return []


def research(name, url, today):
    status, final, ctype, html = fetch(url)
    if status != 200 or "html" not in ctype.lower():
        return {"name": name, "url": url, "status": "unreachable", "http_status": status}
    home = parse(html)
    pages = {k: v for k, v in find_pages(final, home).items() if v.rstrip("/") != final.rstrip("/")}
    origin = "%s://%s" % urllib.parse.urlsplit(final)[:2]
    blocked = {}
    for kind, paths in (("pricing", ("/pricing", "/en/pricing", "/plans")), ("features", ("/features", "/en/features"))):
        for path in paths if kind not in pages else ():  # common paths when the homepage's links are built by JavaScript
            st = fetch(origin + path, timeout=10)[0]
            if st == 200:
                pages[kind] = origin + path
                break
            if st in (401, 403, 429):
                blocked[kind] = st
                break
    facts, sources = {}, {"homepage": final}
    texts = {"homepage": " ".join(t for _, t, c in home.segments if not c)}
    parsed = {"homepage": home}
    for kind, purl in pages.items():
        st, fin, ct, body = fetch(purl)
        if st == 200 and "html" in ct.lower():
            parsed[kind] = parse(body)
            texts[kind] = " ".join(t for _, t, c in parsed[kind].segments if not c)
            sources[kind] = fin
        elif st in (401, 403, 429):
            blocked[kind] = st
        time.sleep(0.3)
    facts["positioning"] = {"title": home.title.strip(), "description": home.meta.get("description") or home.meta.get("og:description") or "",
                            "h1": next((t for tag, t, _ in home.segments if tag == "h1"), ""), "source": final}
    price_page = parsed.get("pricing")
    prices = extract_prices(price_page.segments) if price_page else []
    all_text = " ".join(texts.values())
    trial = TRIAL.search(texts.get("pricing", "") + " " + texts["homepage"])
    paid = [p for p in prices if p["amount"] > 0 and p["monthly_equivalent"]]
    facts["pricing"] = {
        "source": sources.get("pricing"), "plans": prices,
        "lowest_paid_monthly": min((p["monthly_equivalent"] for p in paid), default=None),
        "currency": paid[0]["currency"] if paid else None,
        "free_plan": bool(FREE_PLAN.search(texts.get("pricing", "") + " " + texts["homepage"])),
        "free_trial_days": int(trial.group(1)) if trial and trial.group(1) else (True if trial else None),
        "custom_pricing": bool(CUSTOM.search(texts.get("pricing", ""))),
        "found_in_html": bool(prices), "blocked_status": blocked.get("pricing"),
    }
    feats = extract_features((parsed.get("features") or home).segments)
    facts["features"] = {"source": sources.get("features") or final, "items": feats}
    m = INTEGRATIONS.search(texts.get("integrations", "") + " " + texts["homepage"])
    facts["integrations_claim"] = {"text": m.group(0), "source": sources.get("integrations") or final} if m else None
    m = CUSTOMERS.search(all_text)
    facts["customers_claim"] = {"text": m.group(0), "source": final} if m else None
    return {"name": name, "url": url, "status": "ok", "read": today, "pages": sources, "facts": facts,
            "_segments": {k: v.segments for k, v in parsed.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", action="append", required=True, help='"Name=https://homepage" (2 to 8; repeat)')
    ap.add_argument("--type", choices=["vs", "alternatives", "roundup"], help="Page type (default: vs for 2 products, alternatives for more)")
    ap.add_argument("--subject", help="For alternatives pages: the product people want alternatives to (default: the first)")
    ap.add_argument("--yours", help="Your product's name, if it is one of them (adds a disclosure)")
    ap.add_argument("--feature", action="append", default=[], help="Feature to check on every product's pages (repeat or comma-separate)")
    ap.add_argument("--country", default="us")
    ap.add_argument("--language", default="en")
    args = ap.parse_args()
    products = []
    for p in args.product:
        if "=" not in p:
            fail("INPUT_INVALID", 'Each --product must look like "Name=https://example.com".')
        name, url = (x.strip() for x in p.split("=", 1))
        if not name or not re.match(r"^https?://[^/\s]+", url):
            fail("INPUT_INVALID", 'Bad --product "%s"; use "Name=https://example.com".' % p)
        products.append((name, url))
    if not 2 <= len(products) <= 8:
        fail("INPUT_INVALID", "Pass between 2 and 8 --product values.")
    page_type = args.type or ("vs" if len(products) == 2 else "alternatives")
    subject = args.subject or products[0][0]
    today = datetime.date.today().isoformat()

    research_out = [research(n, u, today) for n, u in products]
    ok = [r for r in research_out if r["status"] == "ok"]
    if len(ok) < 2:
        fail("TOO_FEW_PRODUCTS", "Fewer than 2 product sites could be read.", products=research_out)

    # Checklist: does each product's own site mention each feature? Not found means not found on the pages read, not missing.
    checklist = [x.strip() for a in args.feature for x in a.split(",") if x.strip()]
    check_rows = [{"feature": ft, "products": {r["name"]: mentions(ft, r["_segments"]) for r in ok}} for ft in checklist]
    # Feature matrix: line up similar feature phrases across products.
    rows = []
    for r in ok:
        for f in r["facts"]["features"]["items"]:
            t = feature_terms(f)
            if not t:
                continue
            row = next((x for x in rows if len(t & x["terms"]) / len(t | x["terms"]) >= 0.5), None)
            if row is None:
                row = {"feature": f, "terms": t, "found": {}}
                rows.append(row)
            row["found"].setdefault(r["name"], f)
    matrix = [{"feature": x["feature"], "found_on": {r["name"]: x["found"].get(r["name"]) for r in ok}, "products": len(x["found"])}
              for x in sorted(rows, key=lambda x: (-len(x["found"]), x["feature"]))]
    shared = [m for m in matrix if m["products"] == len(ok)]
    differs = [m for m in matrix if 1 <= m["products"] < len(ok)]

    # What people compare these products with, and the questions they ask.
    compared, questions = collections.Counter(), []
    names = [n for n, _ in products]
    for n in names:
        for s in autocomplete("%s vs" % n.lower(), args.language, args.country):
            other = s.lower().split(" vs ", 1)[1].strip() if " vs " in s.lower() else ""
            if other:
                compared[other] += 1
        time.sleep(0.2)
    pair = "%s vs %s" % (names[0], names[1])
    for q in (["is %s better than %s" % (names[0].lower(), names[1].lower()), pair.lower(), "%s alternatives" % subject.lower()]):
        questions += autocomplete(q, args.language, args.country)
        time.sleep(0.2)
    year = datetime.date.today().year
    if page_type == "vs":
        keywords = [pair, "%s vs %s" % (names[1], names[0]), "%s or %s" % (names[0], names[1]), "%s vs %s pricing" % (names[0], names[1]),
                    "is %s better than %s" % (names[0], names[1])]
        titles = ["%s vs %s: Pricing, Features, Verdict" % (names[0], names[1]), "%s vs %s (%d): Which Is Better?" % (names[0], names[1], year),
                  "%s vs %s: An Honest Comparison" % (names[0], names[1])]
        outline = ["Quick verdict: who each one is best for", "Comparison table", "Pricing compared (as of %s)" % today,
                   "Feature by feature: the differences that matter", "What they have in common", "Integrations", "Pros and cons of each",
                   "Alternatives worth considering", "FAQ", "How we compared (sources and date)"]
    else:
        keywords = ["%s alternatives" % subject, "best %s alternatives" % subject, "%s competitors" % subject,
                    "apps like %s" % subject, "%s alternative free" % subject]
        titles = ["%d Best %s Alternatives (%d)" % (len(ok) - 1, subject, year), "%s Alternatives: %d Options Compared" % (subject, len(ok) - 1),
                  "Best %s Alternatives, by Use Case" % subject]
        if page_type == "roundup":
            keywords = ["best %s" % args.subject] if args.subject else keywords
        outline = ["Why people look for an alternative to %s" % subject, "Quick picks by use case", "Comparison table",
                   *["%s: best for ..." % n for n in names if n != subject], "Pricing compared (as of %s)" % today,
                   "How to choose", "FAQ", "How we compared (sources and date)"]
    titles = [t for t in titles if len(t) <= 60] or titles[:1]
    out = {
        "status": "ok", "type": page_type, "subject": subject, "read": today,
        "products": [{k: v for k, v in r.items() if k != "_segments"} for r in research_out],
        "pricing_summary": [{"name": r["name"], "lowest_paid_monthly": r["facts"]["pricing"]["lowest_paid_monthly"],
                             "currency": r["facts"]["pricing"]["currency"], "free_plan": r["facts"]["pricing"]["free_plan"],
                             "free_trial_days": r["facts"]["pricing"]["free_trial_days"], "custom_pricing": r["facts"]["pricing"]["custom_pricing"],
                             "source": r["facts"]["pricing"]["source"], "found_in_html": r["facts"]["pricing"]["found_in_html"]} for r in ok],
        "feature_checklist": check_rows,
        "feature_matrix": {"shared": shared[:30], "differs": differs[:60], "rows": len(matrix)},
        "people_also_compare": [{"query_suffix": k, "count": c} for k, c in compared.most_common(15)],
        "questions": list(dict.fromkeys(questions))[:20],
        "page_plan": {"target_keywords": keywords, "title_options": titles, "outline": outline,
                      "schema": "Article (author, datePublished, dateModified)" + (" plus ItemList of the products" if page_type != "vs" else ""),
                      "disclosure_needed": bool(args.yours and any(args.yours.lower() == n.lower() for n in names))},
        "verification_needed": [r["name"] + (": the pricing page refused automated requests (HTTP %s); read prices manually" % r["facts"]["pricing"]["blocked_status"]
                                              if r["facts"]["pricing"]["blocked_status"] else ": no prices in the served HTML (no pricing page found, or prices load by JavaScript)")
                                for r in ok if not r["facts"]["pricing"]["found_in_html"]]
                               + [r["name"] + ": no feature list found" for r in ok if not r["facts"]["features"]["items"]],
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
