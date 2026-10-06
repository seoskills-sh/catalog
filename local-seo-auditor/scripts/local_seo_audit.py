#!/usr/bin/env python3
"""Local SEO Auditor: reference implementation.

Audits a local business's search presence in four areas and scores each: the
website's local signals (name, address and phone on the page, LocalBusiness
markup, click to call, map, hours, location pages and whether they are
near-copies), and, from files the user exports, the Google Business Profile,
the reviews and the citations, each compared with the business's canonical
name, address and phone. Areas without data are reported as not assessed,
never as zero.

Auth:   none. Website checks fetch the site; the profile, review and citation
        data come from CSV or JSON files the user provides.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 local_seo_audit.py --site https://example.com \
       [--gbp profiles.csv] [--reviews reviews.csv] [--citations citations.csv]
"""
from __future__ import annotations
import argparse, collections, csv, datetime, json, re, sys
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UA = "seoskills-local-seo-auditor/1.0 (+https://seoskills.sh)"
LOCAL_TYPES = {"LocalBusiness", "Restaurant", "Dentist", "Physician", "MedicalClinic", "MedicalBusiness", "Store", "AutoRepair",
               "AutoDealer", "HomeAndConstructionBusiness", "Plumber", "Electrician", "HVACBusiness", "RoofingContractor",
               "GeneralContractor", "LegalService", "Attorney", "HealthAndBeautyBusiness", "HairSalon", "BeautySalon",
               "DaySpa", "FoodEstablishment", "CafeOrCoffeeShop", "Bakery", "BarOrPub", "LodgingBusiness", "Hotel",
               "ProfessionalService", "RealEstateAgent", "FinancialService", "AccountingService", "InsuranceAgency",
               "VeterinaryCare", "ChildCare", "SportsActivityLocation", "ExerciseGym", "AutomotiveBusiness",
               "EmergencyService", "Optician", "Pharmacy", "Locksmith", "MovingCompany", "HousePainter", "ClothingStore"}
INDEX_PATH = re.compile(r"/(locations?|stores?|offices?|branches|clinics?|find-us|store-locator|our-locations|service-areas?"
                        r"|areas-we-serve|near-me)/?$", re.I)
CHILD_PATH = re.compile(r"/(locations?|stores?|offices?|branches|clinics?|service-areas?|areas-we-serve)/[^/?#]+", re.I)
CONTACT_PATH = re.compile(r"/(contact(-us)?|find-us|visit(-us)?|directions)/?$", re.I)
SKIP_PATH = re.compile(r"/(blog|news|careers?|jobs|privacy|terms|legal|login|account|cart|checkout|search|tag|category|press|wp-)"
                       r"|\.(pdf|jpe?g|png|gif|zip)$", re.I)
PHONE_TEXT = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-](\d{4})(?![\d-])")
US_ADDRESS = re.compile(r"(\d{1,6}[A-Za-z0-9 .#'-]{2,60}(?:,\s*(?:suite|ste|unit|apt|#|floor|fl)\.?\s*[A-Za-z0-9-]+)?),"
                        r"\s*([A-Za-z .'-]{2,40}),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", re.I)
HOURS = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\b[^\n]{0,40}?\b\d{1,2}(:\d{2})?\s?(am|pm|a\.m\.|p\.m\.)"
                   r"|\b\d{1,2}:\d{2}\s?(-|\u2013|to)\s?\d{1,2}:\d{2}\b|\bopen 24 hours\b", re.I)
MAPS = re.compile(r"google\.[a-z.]+/maps|maps\.google\.|goo\.gl/maps|maps\.app\.goo\.gl|g\.page/|maps\.apple\.com|bing\.com/maps", re.I)
SERVICE_AREA = re.compile(r"\b(serving|we serve|service areas?|areas we serve|we come to you|on-site service|mobile service)\b", re.I)
CORE_DIRECTORIES = {"Google Business Profile": r"google", "Apple Business Connect": r"apple", "Bing Places": r"bing",
                    "Yelp": r"yelp", "Facebook": r"facebook"}
ABBR = {"street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr", "lane": "ln", "court": "ct",
        "place": "pl", "parkway": "pkwy", "highway": "hwy", "suite": "ste", "building": "bldg", "floor": "fl",
        "north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne", "northwest": "nw", "southeast": "se",
        "southwest": "sw", "apartment": "apt", "square": "sq", "terrace": "ter", "circle": "cir", "center": "ctr",
        "centre": "ctr", "plaza": "plz", "expressway": "expy", "freeway": "fwy", "trail": "trl", "way": "way"}
NAME_NOISE = {"llc", "inc", "co", "ltd", "corp", "pllc", "pc", "the", "company", "corporation", "incorporated"}
# Points per area and what each finding costs (the script's weighting, not a Google formula).
AREA_MAX = {"website": 40, "gbp": 25, "reviews": 20, "citations": 15}
FINDINGS = {
    "NO_LOCAL_SCHEMA": ("website", "high", 8, "Add LocalBusiness JSON-LD (or the closest subtype) with name, address, phone, hours and geo to each location page, or the homepage for a single location."),
    "ORG_NOT_LOCALBUSINESS": ("website", "medium", 3, "The markup uses Organization with an address; use LocalBusiness or a subtype so it describes a place customers can visit."),
    "NAP_MISSING": ("website", "high", 8, "Show the business name, full address and phone as text on the page (not only in an image or map)."),
    "SCHEMA_NAP_MISMATCH": ("website", "high", 6, "Make the phone and address in the JSON-LD match the ones printed on the page."),
    "SCHEMA_INCOMPLETE": ("website", "low", 3, "Add the missing LocalBusiness properties: telephone, openingHoursSpecification, geo and url."),
    "DUPLICATE_SCHEMA_ID": ("website", "medium", 3, "Give each location its own @id (for example the page URL plus #localbusiness)."),
    "NO_CLICK_TO_CALL": ("website", "medium", 4, "Wrap the phone number in a tel: link so mobile visitors can call in one tap."),
    "NO_MAP": ("website", "low", 2, "Add a map or a directions link for each location."),
    "NO_HOURS": ("website", "medium", 4, "Print the opening hours on the page and in openingHoursSpecification."),
    "TITLE_NO_CITY": ("website", "medium", 3, "Put the city (and the main service) in each location page's title."),
    "H1_NO_CITY": ("website", "low", 2, "Put the city in each location page's H1."),
    "LOCATION_PAGES_NEAR_DUPLICATE": ("website", "high", 8, "The location pages are mostly the same text with the city swapped. Add facts unique to each place: staff, parking, landmarks, local reviews, photos, services offered there."),
    "MISSING_LOCATION_PAGES": ("website", "high", 6, "Some profiles have no matching page on the site. Give every location its own page and link the profile to it."),
    "GBP_NAME_MISMATCH": ("gbp", "high", 6, "Make the profile name the real-world business name, exactly as on the website and signage."),
    "GBP_ADDRESS_MISMATCH": ("gbp", "high", 6, "Make the profile address match the website address exactly."),
    "GBP_PHONE_MISMATCH": ("gbp", "high", 6, "Use the same primary phone on the profile and the website."),
    "GBP_WEBSITE_MISMATCH": ("gbp", "medium", 4, "Point the profile's website link at this site, ideally the matching location page, and make sure it loads."),
    "GBP_NO_HOURS": ("gbp", "medium", 3, "Add regular opening hours to the profile."),
    "GBP_NO_ADDITIONAL_CATEGORIES": ("gbp", "low", 2, "Add the secondary categories that describe services you really offer."),
    "GBP_CATEGORY_NOT_ON_SITE": ("gbp", "medium", 3, "The website never names the profile's primary category. Add a page or section about that service."),
    "FEW_REVIEWS": ("reviews", "medium", 5, "Ask every customer for a review at the end of the job; do not offer incentives or filter who gets asked."),
    "LOW_RATING": ("reviews", "high", 6, "Read the low-rated reviews for recurring problems and fix those first; reply to each one."),
    "REVIEW_GAP": ("reviews", "medium", 4, "No new reviews recently. Build review requests into the normal customer follow-up."),
    "LOW_RESPONSE_RATE": ("reviews", "medium", 4, "Reply to every review, starting with the negative ones."),
    "CITATION_MISMATCH": ("citations", "high", 10, "Correct the listings whose name, address or phone differ from the canonical details."),
    "CORE_DIRECTORY_MISSING": ("citations", "medium", 1, "Claim and complete the missing core listings."),
    "DUPLICATE_LISTING": ("citations", "medium", 1, "Merge or remove duplicate listings on the same directory."),
}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read(3_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, url, "", ""
    except Exception:
        return 0, url, "", ""


class PageParser(HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "section", "article", "main", "header",
             "footer", "nav", "aside", "address", "br", "tr", "ul", "ol", "table", "dd", "dt", "form", "body"}
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.h1, self.links, self.iframes, self.jsonld = "", "", [], [], []
        self.parts, self.main_parts, self.addresses = [], [], []
        self._skip = self._main = self._chrome = 0
        self._in_title = self._in_h1 = self._in_jsonld = False
        self._addr = None
        self._jbuf = []
        self.saw_main = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title" and not self.title:
            self._in_title = True
        elif tag == "h1" and not self.h1:
            self._in_h1 = True
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld, self._jbuf = True, []
            return
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])
        elif tag == "address":
            self._addr = []
        if tag in self.SKIP:
            self._skip += 1
        if tag in ("main", "article"):
            self._main += 1
            self.saw_main = True
        if tag in ("nav", "footer", "header", "aside"):
            self._chrome += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
            if self._addr is not None and tag != "address":
                self._addr.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jbuf))
            self._in_jsonld = False
            return
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in ("main", "article") and self._main:
            self._main -= 1
        if tag in ("nav", "footer", "header", "aside") and self._chrome:
            self._chrome -= 1
        if tag == "address" and self._addr is not None:
            self.addresses.append(re.sub(r"\s+", " ", re.sub(r"(\s*\n\s*)+", ", ", "".join(self._addr))).strip(", "))
            self._addr = None
        if tag in self.BLOCK:
            self.parts.append("\n")
            if self._addr is not None:
                self._addr.append("\n")

    def handle_data(self, data):
        if self._in_jsonld:
            self._jbuf.append(data)
            return
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if self._in_h1:
            self.h1 += data
        self.parts.append(data)
        if self._addr is not None:
            self._addr.append(data)
        if not self._chrome and (self._main or not self.saw_main):
            self.main_parts.append(data)

    def text(self):
        return re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))


# ---------- normalization ----------

def norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def norm_name(n):
    n = re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"['\u2019]", "", (n or "").lower()).replace("&", " and "))
    return " ".join(w for w in n.split() if w not in NAME_NOISE)


def norm_street(s):
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower().replace("#", " ste "))
    return " ".join(ABBR.get(w, w) for w in s.split())


def parse_address(text):
    if isinstance(text, dict):
        street = text.get("streetAddress") or text.get("street") or ""
        if isinstance(street, list):
            street = ", ".join(street)
        return {"street": str(street), "city": str(text.get("addressLocality") or text.get("city") or ""),
                "region": str(text.get("addressRegion") or text.get("region") or ""),
                "postal": str(text.get("postalCode") or text.get("postal") or "")}
    m = US_ADDRESS.search(text or "")
    if m:
        return {"street": m.group(1).strip(), "city": m.group(2).strip(), "region": m.group(3).upper(), "postal": m.group(4)}
    return {"street": (text or "").strip(), "city": "", "region": "", "postal": ""} if text else None


def compare_address(a, b):
    """match, partial (same place, a unit or detail differs), mismatch, or unknown."""
    if not a or not b or not a.get("street") or not b.get("street"):
        return "unknown"
    if a.get("postal") and b.get("postal") and a["postal"][:5] != b["postal"][:5]:
        return "mismatch"
    sa, sb = norm_street(a["street"]), norm_street(b["street"])
    if sa == sb:
        return "match"
    na, nb = sa.split()[:1], sb.split()[:1]
    if na != nb:  # different street numbers
        return "mismatch"
    core = lambda s: re.sub(r"\b(ste|unit|apt|fl|bldg)\b.*$", "", s).strip()
    return "partial" if core(sa) == core(sb) or sa in sb or sb in sa else "mismatch"


def nap_vs(listing, page):
    """Check a profile or listing against everything a page (or the whole site) shows: any matching phone or address counts."""
    out = {}
    if listing.get("phone") and page["phones"]:
        out["phone"] = "match" if norm_phone(listing["phone"]) in page["phones"] else "mismatch"
    if listing.get("address") and page["addresses"]:
        results = [compare_address(listing["address"], a) for a in page["addresses"]]
        out["address"] = "match" if "match" in results else "partial" if "partial" in results else "mismatch"
    names = [s["name"] for s in page["schema"] if s.get("name")]
    if listing.get("name") and names:
        out["name"] = "match" if any(norm_name(n) == norm_name(listing["name"]) for n in names) else "mismatch"
    return out


def compare_nap(canon, other):
    out = {}
    if canon.get("name") and other.get("name"):
        out["name"] = "match" if norm_name(canon["name"]) == norm_name(other["name"]) else "mismatch"
    if canon.get("phone") and other.get("phone"):
        out["phone"] = "match" if norm_phone(canon["phone"]) == norm_phone(other["phone"]) else "mismatch"
    if canon.get("address") and other.get("address"):
        out["address"] = compare_address(canon["address"], other["address"])
    return out


# ---------- input files ----------

def read_table(path):
    try:
        if path.lower().endswith(".json"):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("rows") or data.get("items") or data.get("locations") or data.get("reviews") or [data]
            rows = [r for r in data if isinstance(r, dict)]
        else:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
    except (OSError, ValueError, csv.Error) as e:
        fail("FILE_UNREADABLE", "Could not read %s: %s" % (path, e))
    return [{re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_"): ("" if v is None else v) for k, v in r.items() if k} for r in rows]


def pick(row, *keys):
    for k in keys:
        for col, v in row.items():
            if (col == k or col.startswith(k)) and v not in ("", None):
                return v
    return ""


def gbp_profiles(path):
    out = []
    for r in read_table(path):
        street = pick(r, "address_line_1", "address", "street")
        line2 = pick(r, "address_line_2")
        hours = {k: v for k, v in r.items() if "hours" in k and v and "special" not in k}
        if isinstance(r.get("hours"), (dict, list)):
            hours = {"hours": r["hours"]}
        out.append({
            "store_code": str(pick(r, "store_code", "location_id", "id")),
            "name": str(pick(r, "business_name", "name", "title")),
            "address": {"street": ", ".join(x for x in (str(street), str(line2)) if x), "city": str(pick(r, "locality", "city")),
                        "region": str(pick(r, "administrative_area", "state", "region")), "postal": str(pick(r, "postal_code", "zip"))} if street else None,
            "phone": str(pick(r, "primary_phone", "phone")),
            "website": str(pick(r, "website", "url")),
            "primary_category": str(pick(r, "primary_category", "category")),
            "additional_categories": str(pick(r, "additional_categories")),
            "hours": hours,
        })
    return out


def parse_day(s):
    """ISO dates first; then US-style month/day/year and written-out months."""
    s = str(s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    try:
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(s.split("T")[0].strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------- website ----------

def jsonld_nodes(raw_blocks):
    nodes = []
    for raw in raw_blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, list):
                stack.extend(n)
            elif isinstance(n, dict):
                nodes.append(n)
                stack.extend(v for v in n.values() if isinstance(v, (dict, list)))
    return nodes


def types_of(n):
    t = n.get("@type")
    return {t} if isinstance(t, str) else {x for x in t if isinstance(x, str)} if isinstance(t, list) else set()


def sitemap_urls(origin, limit=3000):
    status, _, _, robots = fetch(origin + "/robots.txt")
    queue = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots) if status == 200 else []
    queue = queue or [origin + "/sitemap.xml"]
    urls, seen = [], set()
    while queue and len(seen) < 6 and len(urls) < limit:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        st, _, _, body = fetch(sm)
        if st != 200:
            continue
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            continue
        locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
        if root.tag.endswith("sitemapindex"):
            queue.extend(locs)
        else:
            urls.extend(locs)
    return urls[:limit]


def shingle_share(texts):
    """Unique-text share per page: 3-word sequences on at least half the pages count as boilerplate."""
    words = [re.findall(r"[a-z0-9']+", t.lower()) for t in texts]
    sh = [[tuple(w[i:i + 3]) for i in range(len(w) - 2)] for w in words]
    df = collections.Counter(s for x in sh for s in set(x))
    cut = max(2, -(-len(texts) // 2))
    return [round(1 - sum(1 for s in x if df[s] >= cut) / len(x), 2) if x else 0.0 for x in sh]


def audit_page(url, kind):
    status, final, ctype, html = fetch(url)
    if status != 200 or "html" not in ctype.lower():
        return {"url": url, "kind": kind, "status": status, "reachable": False}
    p = PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = p.text()
    phones = {norm_phone(h[4:]) for h in p.links if h.lower().startswith("tel:")}
    phones |= {"".join(m.groups()) for m in PHONE_TEXT.finditer(text)}
    phones = sorted(x for x in phones if len(x) >= 7)
    flat = re.sub(r"(\s*\n\s*)+", ", ", text)  # "7 Carmine St" and "New York, NY 10014" on separate lines read as one address
    found = p.addresses + [m.group(0) for m in US_ADDRESS.finditer(flat)]
    addresses = []
    for a in (parse_address(x) for x in found):
        if a and a.get("postal") and not any(compare_address(a, b) == "match" for b in addresses):
            addresses.append(a)
    nodes = jsonld_nodes(p.jsonld)
    local = []
    for n in nodes:
        ts = types_of(n)
        if ts & LOCAL_TYPES or ("Organization" in ts and n.get("address")):
            addr = n.get("address")
            addr = addr[0] if isinstance(addr, list) and addr else addr
            local.append({"types": sorted(ts), "id": n.get("@id"), "name": n.get("name"), "telephone": n.get("telephone"),
                          "address": parse_address(addr) if isinstance(addr, (dict, str)) else None,
                          "has_hours": bool(n.get("openingHoursSpecification") or n.get("openingHours")),
                          "has_geo": bool(n.get("geo")), "has_url": bool(n.get("url"))})
    return {
        "url": final, "kind": kind, "status": status, "reachable": True, "title": p.title.strip(), "h1": re.sub(r"\s+", " ", p.h1).strip(),
        "phones": phones, "addresses": addresses, "schema": local,
        "click_to_call": any(h.lower().startswith("tel:") for h in p.links),
        "map": any(MAPS.search(x) for x in p.iframes + p.links),
        "hours": bool(HOURS.search(text)) or any(s["has_hours"] for s in local),
        "service_area_text": bool(SERVICE_AREA.search(text)),
        "main_text": re.sub(r"\s+", " ", "".join(p.main_parts)),
        "links": p.links,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="Website origin, e.g. https://example.com")
    ap.add_argument("--gbp", help="Google Business Profile export (CSV or JSON), one row per location")
    ap.add_argument("--reviews", help="Reviews export (CSV or JSON): date, rating, optional reply and platform")
    ap.add_argument("--citations", help="Citations list (CSV or JSON): source, name, address, phone, url")
    ap.add_argument("--name", help="Canonical business name, if it differs from the profile")
    ap.add_argument("--phone", help="Canonical phone")
    ap.add_argument("--address", help="Canonical address, e.g. '500 Congress Ave, Austin, TX 78701'")
    ap.add_argument("--max-pages", type=int, default=25)
    args = ap.parse_args()
    if not re.match(r"^https?://[^/\s]+", args.site):
        fail("INPUT_INVALID", "--site must be an absolute URL such as https://example.com")
    sp = urllib.parse.urlsplit(args.site)
    origin = "%s://%s" % (sp.scheme, sp.netloc)
    host = sp.netloc.lower().removeprefix("www.")
    today = datetime.date.today()
    findings, notes = [], []

    def add(code, detail, affected=None, total=None, pages=None):
        area, sev, weight, fix = FINDINGS[code]
        share = (affected / total) if affected is not None and total else 1.0
        findings.append({"area": area, "code": code, "severity": sev, "detail": detail, "fix": fix,
                         "pages": pages or [], "cost": round(weight * share, 2)})

    profiles = gbp_profiles(args.gbp) if args.gbp else []

    # 1. Website: the homepage, local-looking pages it links to, matching sitemap URLs and the profiles' website links.
    home = audit_page(origin + "/", "home")
    if not home["reachable"]:
        fail("SITE_UNREACHABLE", "The homepage did not load (HTTP %s)." % home["status"])
    same_site = lambda u: urllib.parse.urlsplit(u).netloc.lower().removeprefix("www.") == host
    path_of = lambda u: urllib.parse.urlsplit(u).path
    home_links = {urllib.parse.urljoin(home["url"], h).split("#")[0].rstrip("/") for h in home["links"]}
    candidates = [u for u in home_links if same_site(u)]
    candidates += [u for u in sitemap_urls(origin) if same_site(u) and any(rx.search(path_of(u)) for rx in (INDEX_PATH, CHILD_PATH, CONTACT_PATH))]
    gbp_urls = [pr["website"] for pr in profiles if pr["website"].startswith("http")]
    queue = {"location": [u for u in gbp_urls if same_site(u)] + [u for u in candidates if CHILD_PATH.search(path_of(u))],
             "index": [u for u in candidates if INDEX_PATH.search(path_of(u))],
             "contact": [u for u in candidates if CONTACT_PATH.search(path_of(u))]}
    pages, done = [home], {home["url"].rstrip("/")}

    def take(kind):
        while queue[kind]:
            u = queue[kind].pop(0).split("#")[0]
            if u.rstrip("/") in done:
                continue
            done.add(u.rstrip("/"))
            pg = audit_page(u, kind)
            pages.append(pg)
            if kind == "index" and pg["reachable"]:
                # Pages a locations index links to (and the homepage does not) are the location pages.
                for h in pg["links"]:
                    child = urllib.parse.urljoin(pg["url"], h).split("#")[0]
                    if same_site(child) and child.rstrip("/") not in home_links and child.rstrip("/") != pg["url"].rstrip("/") \
                            and not SKIP_PATH.search(path_of(child)) and path_of(child).count("/") >= 1:
                        queue["location"].append(child)
            return True
        return False

    for kind in ("index", "contact"):  # one index and one contact page first, then location pages
        if len(pages) < args.max_pages:
            take(kind)
    while len(pages) < args.max_pages and any(queue.values()):
        take("location") or take("index") or take("contact")
    ok = [pg for pg in pages if pg["reachable"]]
    loc_pages = [pg for pg in ok if pg["kind"] == "location" and (pg["phones"] or pg["addresses"] or pg["schema"] or pg["url"] in gbp_urls)]
    for pg in ok:
        if pg["kind"] == "location" and pg not in loc_pages:
            pg["kind"] = "other"
    nap_pages = loc_pages or [pg for pg in ok if pg["kind"] == "index" and (pg["phones"] or pg["addresses"])] \
        or [pg for pg in ok if pg["kind"] in ("home", "contact")]

    # Canonical NAP: flags first, then a single profile, then the site's own markup and text.
    site_schema = [s for pg in ok for s in pg["schema"]]
    phone_counts = collections.Counter(ph for pg in ok for ph in pg["phones"])
    canon = {"name": args.name or (profiles[0]["name"] if len(profiles) == 1 else "") or next((s["name"] for s in site_schema if s.get("name")), ""),
             "phone": args.phone or (profiles[0]["phone"] if len(profiles) == 1 else "") or next((s["telephone"] for s in site_schema if s.get("telephone")), "")
             or (phone_counts.most_common(1)[0][0] if phone_counts else ""),
             "address": parse_address(args.address) if args.address else (profiles[0]["address"] if len(profiles) == 1 and profiles[0]["address"] else None)
             or next((s["address"] for s in site_schema if s.get("address") and s["address"].get("street")), None)
             or next((a for pg in ok for a in pg["addresses"]), None)}
    canon_source = "flags" if (args.name or args.phone or args.address) else "profile" if len(profiles) == 1 else "website"
    site_addresses = []
    for pg in ok:
        for a in pg["addresses"]:
            if not any(compare_address(a, b) in ("match", "partial") for b in site_addresses):
                site_addresses.append(a)
    if canon_source == "website" and len(site_addresses) > 1:
        canon["address"] = None
        canon["phone"] = ""
    if canon_source == "website" and len(site_addresses) > 1 and not profiles:
        notes.append("The site lists %d different addresses, so there is no single canonical address. Pass --gbp with one row per "
                     "location to compare profiles and citations location by location." % len(site_addresses))
    service_area = not any(pg["addresses"] for pg in ok) and not any(s.get("address") for s in site_schema) \
        and not any(pr["address"] for pr in profiles)
    if service_area:
        notes.append("No street address found on the site or profiles, so it is treated as a service-area business and address checks are skipped.")

    # 2. Website checks.
    if not site_schema:
        add("NO_LOCAL_SCHEMA", "No LocalBusiness markup on any audited page.")
    elif all("Organization" in s["types"] and not set(s["types"]) & LOCAL_TYPES for s in site_schema):
        add("ORG_NOT_LOCALBUSINESS", "Only Organization markup with an address was found.")
    missing_schema = [pg["url"] for pg in loc_pages if not pg["schema"]]
    if site_schema and missing_schema:
        add("NO_LOCAL_SCHEMA", "%d of %d location pages have no LocalBusiness markup." % (len(missing_schema), len(loc_pages)),
            len(missing_schema), len(loc_pages), missing_schema[:5])
    no_nap = [pg["url"] for pg in nap_pages if not pg["phones"] or (not pg["addresses"] and not service_area)]
    if no_nap:
        add("NAP_MISSING", "%d page(s) that should show the business details lack a phone or address in the text." % len(no_nap),
            len(no_nap), len(nap_pages), no_nap[:5])
    mismatch, incomplete = [], []
    for pg in ok:
        for s in pg["schema"]:
            tel = norm_phone(s.get("telephone") or "")
            bad_tel = tel and pg["phones"] and tel not in pg["phones"]
            bad_addr = s.get("address") and s["address"].get("postal") and pg["addresses"] and \
                all(compare_address(s["address"], a) == "mismatch" for a in pg["addresses"])
            if bad_tel or bad_addr:
                mismatch.append(pg["url"])
            if not (s.get("telephone") and s["has_hours"] and s["has_geo"] and s["has_url"]) or (not s.get("address") and not service_area):
                incomplete.append(pg["url"])
    if mismatch:
        add("SCHEMA_NAP_MISMATCH", "The markup's phone or address differs from the text on %d page(s)." % len(set(mismatch)),
            len(set(mismatch)), len(ok), sorted(set(mismatch))[:5])
    if incomplete:
        add("SCHEMA_INCOMPLETE", "LocalBusiness markup lacks address, telephone, hours, geo or url on %d page(s)." % len(set(incomplete)),
            len(set(incomplete)), len(ok), sorted(set(incomplete))[:5])
    ids = collections.defaultdict(set)
    for pg in loc_pages:
        for s in pg["schema"]:
            if s.get("id"):
                ids[s["id"]].add(pg["url"])
    dup_ids = {i: sorted(u) for i, u in ids.items() if len(u) > 1}
    if dup_ids:
        add("DUPLICATE_SCHEMA_ID", "%d @id value(s) are shared by several location pages." % len(dup_ids), pages=[u for us in dup_ids.values() for u in us][:5])
    if not any(pg["click_to_call"] for pg in ok):
        add("NO_CLICK_TO_CALL", "No tel: link on any audited page.")
    if not service_area:
        no_map = [pg["url"] for pg in nap_pages if not pg["map"]]
        if no_map:
            add("NO_MAP", "%d page(s) have no map or directions link." % len(no_map), len(no_map), len(nap_pages), no_map[:5])
    no_hours = [pg["url"] for pg in nap_pages if not pg["hours"]]
    if no_hours:
        add("NO_HOURS", "%d page(s) show no opening hours." % len(no_hours), len(no_hours), len(nap_pages), no_hours[:5])
    no_city_title, no_city_h1 = [], []
    for pg in loc_pages:
        own = next((s["address"] for s in pg["schema"] if s.get("address") and s["address"].get("city")), None) or \
            next((a for a in pg["addresses"] if a.get("city")), None)
        city = (own or {}).get("city", "").lower()
        if city:
            if city not in pg["title"].lower():
                no_city_title.append(pg["url"])
            if city not in pg["h1"].lower():
                no_city_h1.append(pg["url"])
    if no_city_title:
        add("TITLE_NO_CITY", "%d location page title(s) omit the city." % len(no_city_title), len(no_city_title), len(loc_pages), no_city_title[:5])
    if no_city_h1:
        add("H1_NO_CITY", "%d location page H1(s) omit the city." % len(no_city_h1), len(no_city_h1), len(loc_pages), no_city_h1[:5])
    shares = shingle_share([pg["main_text"] for pg in loc_pages]) if len(loc_pages) >= 3 else []
    for pg, share in zip(loc_pages, shares):
        pg["unique_share"] = share
    thin = [pg["url"] for pg in loc_pages if pg.get("unique_share", 1) < 0.3]
    if thin:
        add("LOCATION_PAGES_NEAR_DUPLICATE", "%d of %d location pages are under 30%% unique text." % (len(thin), len(loc_pages)),
            len(thin), len(loc_pages), thin[:5])
    if len(loc_pages) >= args.max_pages - 1:
        notes.append("The page cap (--max-pages %d) was reached; raise it to audit every location page." % args.max_pages)

    # 3. Google Business Profile, one row per location.
    gbp_out = None
    whole_site = {"phones": list(phone_counts), "addresses": site_addresses, "schema": site_schema}
    if profiles:
        site_text = " ".join(pg["main_text"] for pg in ok).lower()
        rows = []
        unmatched_pages = 0
        for pr in profiles:
            page = None
            if pr["website"]:
                page = next((pg for pg in ok if pg["url"].split("?")[0].rstrip("/") == pr["website"].split("?")[0].rstrip("/")), None)
            page = page or next((pg for pg in ok if norm_phone(pr["phone"]) and norm_phone(pr["phone"]) in pg["phones"]), None)
            page = page or next((pg for pg in ok if pr["address"] and any(compare_address(pr["address"], a) in ("match", "partial") for a in pg["addresses"])), None)
            # Compare against the matched page's details, or the whole site's when no page carries them.
            ref = page if page and (page["phones"] or page["addresses"]) else whole_site
            check = nap_vs(pr, ref)
            web_ok = bool(pr["website"]) and same_site(pr["website"])
            if web_ok and page is None and pr["website"]:
                st = fetch(pr["website"])[0]
                web_ok = st == 200
            row = {"store_code": pr["store_code"], "name": pr["name"], "matched_page": page["url"] if page else None,
                   "nap_check": check, "website_ok": web_ok, "hours": bool(pr["hours"]),
                   "primary_category": pr["primary_category"], "additional_categories": pr["additional_categories"]}
            cat_words = [w for w in re.findall(r"[a-z]+", pr["primary_category"].lower()) if len(w) > 3 and w not in ("service", "services", "store", "shop")]
            row["category_on_site"] = (not cat_words) or any(w[:-1] in site_text if w.endswith("s") else w in site_text for w in cat_words)
            rows.append(row)
            if page is None and len(profiles) > 1:
                unmatched_pages += 1
        n = len(rows)
        for code, test, what in [("GBP_NAME_MISMATCH", lambda r: r["nap_check"].get("name") == "mismatch", "name"),
                                 ("GBP_ADDRESS_MISMATCH", lambda r: r["nap_check"].get("address") == "mismatch", "address"),
                                 ("GBP_PHONE_MISMATCH", lambda r: r["nap_check"].get("phone") == "mismatch", "phone"),
                                 ("GBP_WEBSITE_MISMATCH", lambda r: not r["website_ok"], "website link"),
                                 ("GBP_NO_HOURS", lambda r: not r["hours"], "hours"),
                                 ("GBP_NO_ADDITIONAL_CATEGORIES", lambda r: not r["additional_categories"], "additional categories"),
                                 ("GBP_CATEGORY_NOT_ON_SITE", lambda r: not r["category_on_site"], "category")]:
            hit = [r["store_code"] or r["name"] for r in rows if test(r)]
            if hit:
                add(code, "%d of %d profile(s) affected (%s)." % (len(hit), n, what), len(hit), n, hit[:5])
        shared = collections.Counter(r["matched_page"] for r in rows if r["matched_page"])
        sharing = sum(c for c in shared.values() if c > 1)
        if unmatched_pages or sharing:
            parts = (["%d of %d profiles match no audited page" % (unmatched_pages, n)] if unmatched_pages else []) + \
                (["%d of %d profiles share one page with another location" % (sharing, n)] if sharing else [])
            add("MISSING_LOCATION_PAGES", "; ".join(parts) + ".", unmatched_pages + sharing, n)
        gbp_out = {"profiles": rows}

    # 4. Reviews.
    rev_out = None
    if args.reviews:
        revs = []
        table = read_table(args.reviews)
        reply_col = next((c for c in (table[0] if table else {}) if re.match(r"^(reply|response|owner_response|owner_reply|responded|replied)", c)), None)
        for r in table:
            day = parse_day(pick(r, "date", "created", "time", "published", "review_date"))
            try:
                rating = float(str(pick(r, "rating", "stars", "star_rating", "score")).split("/")[0])
            except ValueError:
                rating = None
            reply = str(r.get(reply_col, "")).strip().lower() if reply_col else None
            revs.append({"date": day, "rating": rating, "replied": None if reply is None else reply not in ("", "no", "false", "0", "none", "n"),
                         "platform": str(pick(r, "platform", "source", "site") or "unknown")})
        rated = [x["rating"] for x in revs if x["rating"] is not None]
        dated = sorted(x["date"] for x in revs if x["date"])
        known = [x for x in revs if x["replied"] is not None]
        reply_cols = reply_col is not None
        rev_out = {"count": len(revs), "average": round(sum(rated) / len(rated), 2) if rated else None,
                   "last_30_days": sum(1 for d in dated if (today - d).days <= 30),
                   "last_90_days": sum(1 for d in dated if (today - d).days <= 90),
                   "days_since_last": (today - dated[-1]).days if dated else None,
                   "response_rate": round(sum(1 for x in known if x["replied"]) / len(known), 2) if reply_cols and known else None,
                   "by_rating": dict(sorted(collections.Counter(int(r) for r in rated).items())),
                   "by_platform": dict(collections.Counter(x["platform"] for x in revs))}
        if len(revs) < 10:
            add("FEW_REVIEWS", "%d reviews in the export." % len(revs))
        if rev_out["average"] is not None and rev_out["average"] < 4.0:
            add("LOW_RATING", "Average rating %.2f." % rev_out["average"])
        if rev_out["days_since_last"] is not None and rev_out["days_since_last"] > 30:
            add("REVIEW_GAP", "The newest review is %d days old." % rev_out["days_since_last"])
        if rev_out["response_rate"] is not None and rev_out["response_rate"] < 0.5:
            add("LOW_RESPONSE_RATE", "%d%% of reviews have an owner reply." % round(rev_out["response_rate"] * 100))
        if not dated:
            notes.append("The reviews file has no readable dates, so recency was not measured.")

    # 5. Citations.
    cit_out = None
    if args.citations:
        listings = []
        for r in read_table(args.citations):
            addr_text = ", ".join(str(x) for x in (pick(r, "address", "street"), pick(r, "city", "locality"),
                                                   " ".join(str(y) for y in (pick(r, "state", "region"), pick(r, "zip", "postal")) if y)) if x)
            listings.append({"source": str(pick(r, "source", "directory", "site", "platform")), "name": str(pick(r, "name", "business_name")),
                             "phone": str(pick(r, "phone")), "address": parse_address(addr_text) if addr_text else None, "url": str(pick(r, "url", "link"))})
        mism = []
        for li in listings:
            target = canon
            if len(profiles) > 1:
                target = next((pr for pr in profiles if norm_phone(pr["phone"]) and norm_phone(pr["phone"]) == norm_phone(li["phone"])), None) or \
                    next((pr for pr in profiles if pr["address"] and li["address"] and compare_address(pr["address"], li["address"]) != "mismatch"), canon)
            check = compare_nap(target, li)
            if target is canon and not canon["phone"] and li["phone"] and phone_counts:
                check["phone"] = "match" if norm_phone(li["phone"]) in phone_counts else "mismatch"
            if target is canon and not canon["address"] and li["address"] and site_addresses:
                check["address"] = "match" if any(compare_address(a, li["address"]) in ("match", "partial") for a in site_addresses) else "mismatch"
            bad = {k: v for k, v in check.items() if v == "mismatch"}
            li["check"] = check
            if bad:
                mism.append({"source": li["source"], "fields": sorted(bad), "listing": {k: li[k] for k in ("name", "phone", "address")}})
        seen = collections.Counter(re.sub(r"[^a-z]", "", li["source"].lower()) for li in listings if li["source"])
        dups = sorted(s for s, c in seen.items() if c > 1)
        blob = " ".join((li["source"] + " " + li["url"]).lower() for li in listings)
        core_missing = [name for name, rx in CORE_DIRECTORIES.items() if not re.search(rx, blob) and not (profiles and name == "Google Business Profile")]
        cit_out = {"listings": len(listings), "consistent": len(listings) - len(mism), "mismatches": mism[:50],
                   "duplicates": dups, "core_missing": core_missing}
        if mism:
            add("CITATION_MISMATCH", "%d of %d listings differ from the canonical details." % (len(mism), len(listings)), len(mism), len(listings),
                [m["source"] for m in mism][:5])
        for name in core_missing:
            add("CORE_DIRECTORY_MISSING", "%s is not in the citations list." % name)
        for d in dups:
            add("DUPLICATE_LISTING", "More than one listing on %s." % d)

    # 6. Score each assessed area; leave the others out of the total.
    assessed = {"website": True, "gbp": bool(profiles), "reviews": bool(args.reviews), "citations": bool(args.citations)}
    area_scores = {}
    for area, mx in AREA_MAX.items():
        if not assessed[area]:
            area_scores[area] = {"score": None, "max": mx, "assessed": False,
                                 "reason": "pass --%s to assess this area" % {"gbp": "gbp", "reviews": "reviews", "citations": "citations"}.get(area, area)}
            continue
        lost = sum(f["cost"] for f in findings if f["area"] == area)
        area_scores[area] = {"score": round(max(0.0, mx - lost), 1), "max": mx, "assessed": True}
    got = sum(a["score"] for a in area_scores.values() if a["assessed"])
    cap = sum(a["max"] for a in area_scores.values() if a["assessed"])
    findings.sort(key=lambda f: (SEVERITY_RANK[f["severity"]], -f["cost"]))
    json.dump({
        "status": "ok", "site": origin, "checked": today.isoformat(),
        "business": {"type": "service_area" if service_area else "storefront", "profiles": len(profiles),
                     "location_pages": len(loc_pages), "canonical_nap": {**canon, "source": canon_source}},
        "score": round(got / cap * 100) if cap else None, "area_scores": area_scores,
        "findings": findings,
        "website": {"pages_audited": len(ok), "pages": [{k: v for k, v in pg.items() if k not in ("main_text", "links")} for pg in pages]},
        "gbp": gbp_out, "reviews": rev_out, "citations": cit_out, "notes": notes,
    }, sys.stdout, indent=2, default=str)


if __name__ == "__main__":
    main()
