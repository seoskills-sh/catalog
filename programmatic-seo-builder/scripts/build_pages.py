#!/usr/bin/env python3
"""Programmatic SEO Builder: reference implementation.

Builds a programmatic page set from a dataset (CSV or JSON) and one template,
and holds back every page that fails a quality gate before anything ships:
missing or sparse data, duplicate slugs and titles, near-duplicate records,
and pages whose text is mostly shared template boilerplate. Writes the passing
pages as files with front matter, hub pages, related links, a manifest and a
sitemap, plus a staged rollout plan.

Auth:   none (local files only, no network).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 build_pages.py --data rows.csv --template page.md --out build/ \
       [--base-url https://example.com] [--priority-column volume] [--dry-run]
"""
from __future__ import annotations
import argparse, collections, csv, datetime, json, math, os, re, statistics, sys, unicodedata
from xml.sax.saxutils import escape

FIELD = re.compile(r"\{(\w+)(?:\|(\w+))?\}")
COND = re.compile(r"\{([?!])(\w+)\}(.*?)\{/\2\}", re.S)
COND_OPEN = re.compile(r"\{[?!](\w+)\}")
NEAR_DUP_MAX_ROWS = 1500
SITEMAP_LIMIT = 50000


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def slugify(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()


FILTERS = {"slug": slugify, "lower": str.lower, "upper": str.upper, "title": str.title}


def column_key(name):
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def cell(v):
    if v is None or v is False:
        return ""
    if v is True:
        return "Yes"
    if isinstance(v, list):
        return ", ".join(x for x in (cell(i) for i in v) if x)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def load_rows(path):
    try:
        if path.lower().endswith(".json"):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("rows") or data.get("data") or data.get("items") or []
            raw = [r for r in data if isinstance(r, dict)]
        else:
            with open(path, newline="", encoding="utf-8-sig") as f:
                raw = list(csv.DictReader(f))
    except (OSError, ValueError, csv.Error) as e:
        fail("DATA_UNREADABLE", "Could not read %s: %s" % (path, e))
    return [{column_key(k): cell(v) for k, v in r.items() if k is not None and column_key(k)} for r in raw]


def parse_template(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        fail("TEMPLATE_UNREADABLE", "Could not read %s: %s" % (path, e))
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)$", text, re.S)
    if not m:
        fail("TEMPLATE_INVALID", "The template needs a front matter block (--- lines) with at least slug and title.")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            meta[k.strip().lower()] = v
    return meta, m.group(2)


def render(text, row, slug_mode=False):
    for _ in range(6):  # resolve nested conditionals from the inside out
        new = COND.sub(lambda c: c.group(3) if bool(row.get(c.group(2))) == (c.group(1) == "?") else "", text)
        if new == text:
            break
        text = new

    def sub(m):
        col, flt = m.group(1), m.group(2)
        if col == "related" and not slug_mode:
            return m.group(0)
        val = row.get(col, "")
        if slug_mode or flt == "slug":
            return slugify(val)
        return FILTERS[flt](val) if flt else val
    return FIELD.sub(sub, text)


def make_slug(pattern, row):
    segs = [slugify(s) for s in render(pattern, row, slug_mode=True).split("/")]
    return "/" + "/".join(s for s in segs if s)


def fields_in(text):
    return {m.group(1) for m in FIELD.finditer(text)} | set(COND_OPEN.findall(text))


def plain_words(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_`|~=]+", " ", text)
    return re.findall(r"[^\W_][\w'-]*", text.lower())


def shingles(ws, n=3):
    return [tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)]


def number(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def front_matter(fields):
    lines = ["---"]
    for k, v in fields.items():
        if v is None or v == "" or v == []:
            continue
        lines.append("%s: %s" % (k, json.dumps(v, ensure_ascii=False) if not isinstance(v, bool) else str(v).lower()))
    return "\n".join(lines + ["---", ""])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="CSV or JSON file, one row per page")
    ap.add_argument("--template", required=True, help="Template file with front matter (slug, title, description, h1, hub, hub_title)")
    ap.add_argument("--out", help="Output folder (required unless --dry-run)")
    ap.add_argument("--base-url", help="Site origin, for canonicals, breadcrumbs and sitemap.xml")
    ap.add_argument("--priority-column", help="Numeric column (search volume, revenue) that orders the rollout")
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--related", type=int, default=4, help="Related links per page")
    ap.add_argument("--min-unique", type=float, default=0.30, help="Hold pages whose unique-text share is below this")
    ap.add_argument("--warn-unique", type=float, default=0.40)
    ap.add_argument("--min-completeness", type=float, default=0.60, help="Hold pages missing more of their data than this allows")
    ap.add_argument("--min-words", type=int, default=250, help="Warn below this many words")
    ap.add_argument("--dry-run", action="store_true", help="Measure and report; write nothing")
    ap.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty --out folder")
    args = ap.parse_args()

    if not args.dry_run and not args.out:
        fail("INPUT_INVALID", "Pass --out FOLDER, or --dry-run to only measure.")
    if args.out and not args.dry_run and os.path.isdir(args.out) and os.listdir(args.out) and not args.overwrite:
        fail("OUT_NOT_EMPTY", "%s already has files. Choose an empty folder or pass --overwrite." % args.out)
    base = (args.base_url or "").rstrip("/")
    if base and not re.match(r"^https?://[^/\s]+$", base):
        fail("INPUT_INVALID", "--base-url must be an origin such as https://example.com")

    meta, body = parse_template(args.template)
    if not meta.get("slug") or not meta.get("title"):
        fail("TEMPLATE_INVALID", "The template's front matter must set slug and title.")
    rows = load_rows(args.data)
    if not rows:
        fail("NO_ROWS", "The data file has no rows.")
    columns = sorted({k for r in rows for k in r})
    patterns = {k: meta.get(k, "") for k in ("slug", "title", "description", "h1", "hub", "hub_title")}
    used = set().union(*(fields_in(v) for v in patterns.values()), fields_in(body))
    unknown = sorted(used - set(columns) - {"related"})
    bad_filters = sorted({m.group(2) for t in [body, *patterns.values()] for m in FIELD.finditer(t) if m.group(2) and m.group(2) not in FILTERS})
    if unknown or bad_filters:
        fail("TEMPLATE_UNKNOWN_FIELD", "The template uses fields the data does not have.", unknown_fields=unknown,
             unknown_filters=bad_filters, data_columns=columns)
    if args.priority_column and column_key(args.priority_column) not in columns:
        fail("INPUT_INVALID", "--priority-column %s is not a data column." % args.priority_column, data_columns=columns)

    required = fields_in(patterns["slug"]) | fields_in(patterns["title"]) | fields_in(patterns["h1"])
    body_fields = sorted(fields_in(body) - {"related"})

    # 1. Render every row.
    pages = []
    for i, row in enumerate(rows):
        pg = {"row": i + 1, "slug": make_slug(patterns["slug"], row), "title": render(patterns["title"], row).strip(),
              "description": render(patterns["description"], row).strip(),
              "h1": render(patterns["h1"] or patterns["title"], row).strip(), "body": render(body, row).strip(),
              "hub": make_slug(patterns["hub"], row) if patterns["hub"] else None,
              "hub_title": render(patterns["hub_title"], row).strip() if patterns["hub_title"] else None,
              "holds": [], "warnings": [], "values": row}
        missing = sorted(f for f in required if not row.get(f))
        if missing:
            pg["holds"].append("MISSING_REQUIRED")
            pg["missing_fields"] = missing
        filled = [f for f in body_fields if row.get(f)]
        pg["completeness"] = round(len(filled) / len(body_fields), 2) if body_fields else 1.0
        if pg["completeness"] < args.min_completeness:
            pg["holds"].append("SPARSE_DATA")
        pg["words"] = len(plain_words(pg["body"]))
        if pg["words"] < args.min_words:
            pg["warnings"].append("SHORT_PAGE")
        if len(pg["title"]) > 60:
            pg["warnings"].append("TITLE_LONG")
        if not pg["description"]:
            pg["warnings"].append("NO_DESCRIPTION")
        elif len(pg["description"]) > 160:
            pg["warnings"].append("DESCRIPTION_LONG")
        if len(pg["slug"]) > 100:
            pg["warnings"].append("SLUG_LONG")
        pages.append(pg)

    # 2. Duplicates: the first row keeps a slug or title, later rows are held.
    for key, code in (("slug", "DUPLICATE_SLUG"), ("title", "DUPLICATE_TITLE")):
        seen = {}
        for pg in pages:
            k = pg[key].lower()
            if k in seen:
                pg["holds"].append(code)
                pg["duplicate_of_row"] = seen[k]
            else:
                seen[k] = pg["row"]

    # 3. Near-duplicate records: rows sharing more than 80% of their body data.
    if len(pages) <= NEAR_DUP_MAX_ROWS and len(body_fields) >= 3:
        vecs = [tuple(pg["values"].get(f, "").lower() for f in body_fields) for pg in pages]
        for j in range(len(pages)):
            for i in range(j):
                both = [k for k in range(len(body_fields)) if vecs[i][k] or vecs[j][k]]
                if both and sum(1 for k in both if vecs[i][k] == vecs[j][k]) / len(both) > 0.8:
                    if "NEAR_DUPLICATE_RECORD" not in pages[j]["holds"]:
                        pages[j]["holds"].append("NEAR_DUPLICATE_RECORD")
                        pages[j]["near_duplicate_of_row"] = pages[i]["row"]
                    break
        near_check = "done"
    else:
        near_check = "skipped (needs 3+ body fields and at most %d rows)" % NEAR_DUP_MAX_ROWS

    # 4. Unique-text share: 3-word sequences that appear on at least half the pages count as boilerplate.
    uniq_values = []
    if len(pages) >= 4:
        page_sh = [shingles(plain_words(pg["body"])) for pg in pages]
        df = collections.Counter(s for sh in page_sh for s in set(sh))
        cut = max(2, math.ceil(len(pages) * 0.5))
        for pg, sh in zip(pages, page_sh):
            share = round(1 - sum(1 for s in sh if df[s] >= cut) / len(sh), 2) if sh else 0.0
            pg["unique_share"] = share
            uniq_values.append(share)
            if share < args.min_unique:
                pg["holds"].append("BOILERPLATE_DOMINANT")
            elif share < args.warn_unique:
                pg["warnings"].append("LOW_UNIQUE_SHARE")
    passing = [pg for pg in pages if not pg["holds"]]
    for pg in pages:
        pg["status"] = "hold" if pg["holds"] else "pass"

    # 5. Related links among passing pages, by shared categorical values.
    cat_cols = [c for c in body_fields if 2 <= len({r.get(c) for r in rows if r.get(c)}) <= max(2, len(rows) // 2)]
    if patterns["hub"]:
        cat_cols = sorted(set(cat_cols) | fields_in(patterns["hub"]))
    buckets, position = collections.defaultdict(list), {}
    for idx, pg in enumerate(passing):
        for c in cat_cols:
            if pg["values"].get(c):
                key = (c, pg["values"][c].lower())
                position[(key, idx)] = len(buckets[key])
                buckets[key].append(idx)
    for idx, pg in enumerate(passing):
        score = collections.Counter()
        for c in cat_cols:
            key = (c, pg["values"].get(c, "").lower())
            members = buckets.get(key, [])
            if len(members) > 12:  # big buckets: take the neighbours in row order, so every page gets linked
                pos = position[(key, idx)]
                members = members[max(0, pos - 6):pos + 7]
            for other in members:
                if other != idx:
                    score[other] += 1
        picked = [o for o, _ in sorted(score.items(), key=lambda kv: (-kv[1], passing[kv[0]]["row"]))[: args.related]]
        for step in range(1, len(passing)):  # too few shared values: fill with the nearest rows, so no page is a dead end
            if len(picked) >= min(args.related, len(passing) - 1):
                break
            for o in (idx - step, idx + step):
                if 0 <= o < len(passing) and o not in picked and len(picked) < args.related:
                    picked.append(o)
        pg["related"] = [{"title": passing[o]["h1"], "slug": passing[o]["slug"]} for o in picked]

    # 6. Hubs, breadcrumbs, rollout order.
    hubs = collections.OrderedDict()
    for pg in passing:
        if pg["hub"]:
            hubs.setdefault(pg["hub"], {"slug": pg["hub"], "title": pg["hub_title"] or pg["hub"].rsplit("/", 1)[-1].replace("-", " ").title(), "pages": []})
            hubs[pg["hub"]]["pages"].append({"title": pg["h1"], "slug": pg["slug"]})
    prio = column_key(args.priority_column) if args.priority_column else None

    def order_key(pg):
        p = number(pg["values"].get(prio)) if prio else None
        return (-(p if p is not None else -1), -pg["completeness"], -pg["words"], pg["row"])
    for n, pg in enumerate(sorted(passing, key=order_key)):
        pg["batch"] = n // max(1, args.batch_size) + 1

    def breadcrumb(pg):
        if not base:
            return None
        items = [("Home", base + "/")]
        if pg.get("hub") and pg["hub"] in hubs:
            items.append((hubs[pg["hub"]]["title"], base + pg["hub"]))
        items.append((pg["h1"], base + pg["slug"]))
        return {"@context": "https://schema.org", "@type": "BreadcrumbList",
                "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": u} for i, (n, u) in enumerate(items)]}

    def page_text(pg):
        links = "\n".join("- [%s](%s)" % (r["title"], r["slug"]) for r in pg.get("related", []))
        text = pg["body"]
        if "{related}" in text:
            text = text.replace("{related}", links)
        elif links:
            text += "\n\n## Related\n\n" + links
        return text

    ext = os.path.splitext(args.template)[1] or ".md"
    notes = []
    written = 0
    today = datetime.date.today().isoformat()
    if not args.dry_run:
        def write(folder, slug, content):
            rel = slug.strip("/") or "index"
            path = os.path.join(args.out, folder, rel + ext)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        for pg in pages:
            fm = {"title": pg["title"], "description": pg["description"], "h1": pg["h1"], "slug": pg["slug"],
                  "canonical": base + pg["slug"] if base else None, "hub": pg["hub"], "batch": pg.get("batch"),
                  "hold_reasons": pg["holds"], "breadcrumb_jsonld": json.dumps(breadcrumb(pg)) if base else None}
            write("pages" if pg["status"] == "pass" else "hold", pg["slug"], front_matter(fm) + "\n" + page_text(pg) + "\n")
            written += 1
        for hub in hubs.values():
            listing = "\n".join("- [%s](%s)" % (p["title"], p["slug"]) for p in hub["pages"])
            write("hubs", hub["slug"], front_matter({"title": hub["title"], "slug": hub["slug"], "canonical": base + hub["slug"] if base else None})
                  + "\n# %s\n\n%s\n" % (hub["title"], listing))
            written += 1
        manifest = [{k: v for k, v in pg.items() if k not in ("body", "values")} for pg in pages]
        with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"generated": today, "pages": manifest, "hubs": list(hubs.values())}, f, indent=2, ensure_ascii=False)
        if base:
            urls = [base + pg["slug"] for pg in sorted(passing, key=lambda p: p.get("batch", 0))] + [base + h["slug"] for h in hubs.values()]
            with open(os.path.join(args.out, "sitemap.xml"), "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
                f.writelines("  <url><loc>%s</loc></url>\n" % escape(u) for u in urls[:SITEMAP_LIMIT])
            if len(urls) > SITEMAP_LIMIT:
                notes.append("sitemap.xml holds the first %d URLs; split the rest into more sitemap files." % SITEMAP_LIMIT)
                f.write("</urlset>\n")

    holds = collections.Counter(h for pg in pages for h in pg["holds"])
    warns = collections.Counter(w for pg in pages for w in pg["warnings"])
    report_pages = sorted(pages, key=lambda pg: (pg["status"] == "pass", pg["row"]))[:100]
    batches = collections.Counter(pg["batch"] for pg in passing)
    out = {
        "status": "ok", "generated": today, "dry_run": args.dry_run,
        "input": {"rows": len(rows), "columns": columns, "body_fields": body_fields, "required_fields": sorted(required)},
        "summary": {"pages": len(pages), "pass": len(passing), "hold": len(pages) - len(passing), "hubs": len(hubs),
                    "thin_hubs": sorted(h["slug"] for h in hubs.values() if len(h["pages"]) < 3),
                    "holds_by_reason": dict(holds), "warnings_by_code": dict(warns)},
        "gates": {"min_unique": args.min_unique, "warn_unique": args.warn_unique,
                  "min_completeness": args.min_completeness, "min_words": args.min_words},
        "uniqueness": ({"min": min(uniq_values), "median": statistics.median(uniq_values), "max": max(uniq_values)}
                       if uniq_values else None),
        "near_duplicate_check": near_check,
        "rollout": [{"batch": b, "pages": n} for b, n in sorted(batches.items())],
        "pages": [{"row": pg["row"], "slug": pg["slug"], "title": pg["title"], "status": pg["status"],
                   "hold_reasons": pg["holds"], "warnings": pg["warnings"], "words": pg["words"],
                   "unique_share": pg.get("unique_share"), "completeness": pg["completeness"],
                   "batch": pg.get("batch"), "related": len(pg.get("related", [])),
                   **({"missing_fields": pg["missing_fields"]} if "missing_fields" in pg else {}),
                   **({"duplicate_of_row": pg["duplicate_of_row"]} if "duplicate_of_row" in pg else {}),
                   **({"near_duplicate_of_row": pg["near_duplicate_of_row"]} if "near_duplicate_of_row" in pg else {})}
                  for pg in report_pages],
        "pages_listed": len(report_pages),
        "files": None if args.dry_run else {"out": os.path.abspath(args.out), "written": written,
                                            "manifest": "manifest.json", "sitemap": "sitemap.xml" if base else None},
    }
    if not uniq_values:
        notes.append("Unique-text share needs at least 4 pages, so it was not measured.")
    if notes:
        out["notes"] = notes
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
