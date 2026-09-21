#!/usr/bin/env python3
"""Screaming Frog Warehouse Loader — reference implementation.

Normalizes Screaming Frog SEO Spider CSV exports (internal_all, response_codes,
directives, all_inlinks) into a stable warehouse schema, computes a run-over-run
diff (new / changed / removed URLs) against the previous run's row-hash snapshot,
emits the target DDL + a MERGE plan, and OPTIONALLY streams the incremental rows
into BigQuery via tabledata.insertAll (idempotent insertIds).

Auth (only when --load): GCP_ACCESS_TOKEN + BQ_PROJECT + BQ_DATASET (BigQuery).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage:
  python3 warehouse_loader.py --export-dir ./sf_export --run-id 2026-09-15 \
      [--previous prev_snapshot.json] [--dialect bigquery] [--load] [--max-rows 200000]
"""
from __future__ import annotations
import argparse, csv, datetime, hashlib, json, os, sys, time
import urllib.request, urllib.error
from collections import defaultdict

# Logical warehouse column -> list of acceptable Screaming Frog header names.
FIELD_MAP = {
    "url": ["Address"],
    "status_code": ["Status Code"],
    "content_type": ["Content Type"],
    "indexability": ["Indexability"],
    "indexability_status": ["Indexability Status"],
    "title": ["Title 1"],
    "title_length": ["Title 1 Length"],
    "h1": ["H1-1"],
    "word_count": ["Word Count"],
    "canonical_url": ["Canonical Link Element 1"],
    "crawl_depth": ["Crawl Depth"],
    "inlinks_count": ["Inlinks"],
    "unique_inlinks": ["Unique Inlinks"],
    "response_time": ["Response Time"],
    "redirect_url": ["Redirect URL", "Redirect URI"],
    "meta_robots": ["Meta Robots 1", "Meta Robots"],
    "x_robots_tag": ["X-Robots-Tag 1", "X-Robots-Tag"],
}
INT_FIELDS = {"status_code", "title_length", "word_count", "crawl_depth", "inlinks_count", "unique_inlinks"}
FLOAT_FIELDS = {"response_time"}
# Fields that define semantic change (redirect/canonical/index state/on-page). ingested_at excluded.
HASH_FIELDS = ["status_code", "content_type", "indexability", "indexability_status", "title",
               "h1", "word_count", "canonical_url", "crawl_depth", "inlinks_count",
               "redirect_url", "meta_robots", "x_robots_tag"]
# Preferred source file per logical field (first present wins), by SF export basename.
SOURCE_PRIORITY = ["internal_all", "response_codes", "directives"]
SCHEMA_VERSION = 3

BQ_TYPE = {"url": "STRING", "status_code": "INT64", "content_type": "STRING", "indexability": "STRING",
           "indexability_status": "STRING", "title": "STRING", "title_length": "INT64", "h1": "STRING",
           "word_count": "INT64", "canonical_url": "STRING", "crawl_depth": "INT64", "inlinks_count": "INT64",
           "unique_inlinks": "INT64", "response_time": "FLOAT64", "redirect_url": "STRING",
           "meta_robots": "STRING", "x_robots_tag": "STRING", "row_hash": "STRING", "run_id": "STRING",
           "present": "BOOL", "ingested_at": "TIMESTAMP"}
PG_TYPE = {"INT64": "bigint", "STRING": "text", "FLOAT64": "double precision",
           "BOOL": "boolean", "TIMESTAMP": "timestamptz"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def norm_url(u):
    u = (u or "").strip()
    if "#" in u:
        u = u.split("#", 1)[0]
    return u


def coerce(field, raw):
    v = (raw or "").strip()
    if v == "":
        return None
    if field in INT_FIELDS:
        try:
            return int(float(v))
        except ValueError:
            return None
    if field in FLOAT_FIELDS:
        try:
            return round(float(v), 3)
        except ValueError:
            return None
    return v


def discover(export_dir):
    """Map SF basenames -> path, tolerating the tool's varied filename spellings."""
    found = {}
    try:
        names = os.listdir(export_dir)
    except OSError as e:
        fail("EXPORT_DIR_UNREADABLE", "Cannot read --export-dir: %s" % e)
    for fn in names:
        low = fn.lower()
        if not low.endswith(".csv"):
            continue
        key = low[:-4].replace(" ", "_")
        if "internal" in key and "all" in key:
            found["internal_all"] = os.path.join(export_dir, fn)
        elif "response" in key and "code" in key:
            found["response_codes"] = os.path.join(export_dir, fn)
        elif "directive" in key:
            found["directives"] = os.path.join(export_dir, fn)
        elif "inlink" in key:
            found["all_inlinks"] = os.path.join(export_dir, fn)
    return found


def read_table(path, max_rows):
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        for i, r in enumerate(reader):
            if i >= max_rows:
                return headers, rows, True
            rows.append(r)
    return headers, rows, False


def pick(row, headers, names):
    for n in names:
        if n in headers and (row.get(n) or "").strip() != "":
            return row.get(n)
    return None


def normalize(sources, max_rows):
    """Merge SF exports into one row per URL following SOURCE_PRIORITY."""
    merged = {}
    truncated = False
    per_file_headers = {}
    for base in SOURCE_PRIORITY:
        path = sources.get(base)
        if not path:
            continue
        headers, rows, trunc = read_table(path, max_rows)
        per_file_headers[base] = headers
        truncated = truncated or trunc
        for r in rows:
            url = norm_url(pick(r, headers, FIELD_MAP["url"]))
            if not url:
                continue
            rec = merged.setdefault(url, {"url": url})
            for field, names in FIELD_MAP.items():
                if field == "url" or field in rec:
                    continue
                val = coerce(field, pick(r, headers, names))
                if val is not None:
                    rec[field] = val
    return merged, truncated, per_file_headers


def enrich_inlinks(sources, merged, max_rows):
    """From the All Inlinks export, count distinct linking sources + top anchors per destination."""
    path = sources.get("all_inlinks")
    if not path:
        return False
    headers, rows, _ = read_table(path, max_rows * 4)
    dest_h = next((h for h in ("Destination", "To") if h in headers), None)
    src_h = next((h for h in ("Source", "From") if h in headers), None)
    anchor_h = next((h for h in ("Anchor", "Anchor Text") if h in headers), None)
    if not dest_h or not src_h:
        return False
    srcs = defaultdict(set)
    anchors = defaultdict(lambda: defaultdict(int))
    for r in rows:
        dst = norm_url(r.get(dest_h))
        src = norm_url(r.get(src_h))
        if not dst or not src:
            continue
        srcs[dst].add(src)
        if anchor_h:
            a = (r.get(anchor_h) or "").strip()
            if a:
                anchors[dst][a] += 1
    for url, rec in merged.items():
        if url in srcs:
            rec["unique_inlinks"] = len(srcs[url])
            top = sorted(anchors[url].items(), key=lambda x: x[1], reverse=True)[:5]
            rec["top_inbound_anchors"] = [{"anchor": a, "count": c} for a, c in top]
    return True


def row_hash(rec):
    payload = "|".join("%s=%s" % (f, rec.get(f, "")) for f in HASH_FIELDS)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def build_ddl(dialect, table_fqn):
    cols = list(BQ_TYPE.keys())
    if dialect == "bigquery":
        lines = ["  `%s` %s" % (c, BQ_TYPE[c]) for c in cols]
        return ("CREATE TABLE IF NOT EXISTS `%s` (\n%s\n)\n"
                "PARTITION BY DATE(ingested_at)\n"
                "CLUSTER BY url, present;") % (table_fqn, ",\n".join(lines))
    lines = ["  %s %s" % (c, PG_TYPE[BQ_TYPE[c]]) for c in cols]
    return ("CREATE TABLE IF NOT EXISTS %s (\n%s\n);\n"
            "CREATE INDEX IF NOT EXISTS idx_%s_url ON %s (url);") % (
        table_fqn, ",\n".join(lines), table_fqn.replace(".", "_"), table_fqn)


def merge_sql(dialect, table_fqn):
    """Illustrative upsert the agent runs to fold the run into a current-state view."""
    if dialect == "bigquery":
        return ("MERGE `%s_current` T USING (SELECT * EXCEPT(rn) FROM (SELECT *, "
                "ROW_NUMBER() OVER (PARTITION BY url ORDER BY ingested_at DESC) rn FROM `%s`) WHERE rn=1) S "
                "ON T.url=S.url WHEN MATCHED THEN UPDATE SET T.row_hash=S.row_hash, T.present=S.present "
                "WHEN NOT MATCHED THEN INSERT ROW;") % (table_fqn, table_fqn)
    return ("INSERT INTO %s_current AS T SELECT DISTINCT ON (url) * FROM %s "
            "ORDER BY url, ingested_at DESC ON CONFLICT (url) DO UPDATE "
            "SET row_hash=EXCLUDED.row_hash, present=EXCLUDED.present;") % (table_fqn, table_fqn)


def bq_request(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                fail("AUTH_INVALID_TOKEN", "GCP_ACCESS_TOKEN rejected (401).")
            if e.code == 409:
                return 409, {}
            if e.code == 429 or e.code >= 500:
                if attempt == 5:
                    return e.code, {}
                time.sleep(2 ** attempt); continue
            body_txt = e.read().decode("utf-8", "replace")[:300]
            return e.code, {"error_text": body_txt}
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}
    return 429, {}


def ensure_table(project, dataset, table, token):
    base = "https://bigquery.googleapis.com/bigquery/v2/projects/%s/datasets/%s/tables" % (project, dataset)
    schema = {"fields": [{"name": c, "type": BQ_TYPE[c]} for c in BQ_TYPE]}
    body = {"tableReference": {"projectId": project, "datasetId": dataset, "tableId": table},
            "schema": schema,
            "timePartitioning": {"type": "DAY", "field": "ingested_at"}}
    st, _ = bq_request("POST", base, token, body)
    return st in (200, 409)


def stream_insert(project, dataset, table, token, rows, run_id, max_rows):
    url = ("https://bigquery.googleapis.com/bigquery/v2/projects/%s/datasets/%s/tables/%s/insertAll"
           % (project, dataset, table))
    loaded, failed, batch = 0, 0, []
    capped = rows[:max_rows]
    for rec in capped:
        insert_id = "%s:%s" % (rec["row_hash"], run_id)
        batch.append({"insertId": insert_id, "json": rec})
        if len(batch) >= 500:
            loaded, failed = _flush(url, token, batch, loaded, failed)
            batch = []
            time.sleep(0.2)  # pace against streaming-insert quota
    if batch:
        loaded, failed = _flush(url, token, batch, loaded, failed)
    return loaded, failed, len(capped) < len(rows)


def _flush(url, token, batch, loaded, failed):
    st, resp = bq_request("POST", url, token, {"skipInvalidRows": False,
                                               "ignoreUnknownValues": False, "rows": batch})
    if st == 200 and not resp.get("insertErrors"):
        return loaded + len(batch), failed
    errs = resp.get("insertErrors", [])
    return loaded + (len(batch) - len(errs)), failed + len(errs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--previous")
    ap.add_argument("--run-id", default=datetime.date.today().isoformat())
    ap.add_argument("--dialect", choices=["bigquery", "postgres"], default="bigquery")
    ap.add_argument("--table", default="seo_wh.crawl_pages")
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--max-rows", type=int, default=200000, dest="max_rows")
    ap.add_argument("--sample", type=int, default=25, help="rows echoed inline in output")
    a = ap.parse_args()

    sources = discover(a.export_dir)
    if "internal_all" not in sources:
        fail("REQUIRED_EXPORT_MISSING", "internal_all export not found in --export-dir; it is mandatory.",
             discovered=list(sources.keys()))

    merged, truncated, headers = normalize(sources, a.max_rows)
    if not merged:
        json.dump({"status": "no_data", "run_id": a.run_id, "reason": "no URL rows parsed",
                   "sources": list(sources.keys())}, sys.stdout)
        return
    inlinks_used = enrich_inlinks(sources, merged, a.max_rows)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    current_hashes = {}
    rows_out = []
    for url, rec in merged.items():
        rec["row_hash"] = row_hash(rec)
        rec["run_id"] = a.run_id
        rec["present"] = True
        rec["ingested_at"] = now
        current_hashes[url] = rec["row_hash"]
        rows_out.append(rec)

    prev = {}
    if a.previous:
        try:
            prev = json.load(open(a.previous)).get("row_hashes", {})
        except (OSError, ValueError) as e:
            fail("PREVIOUS_SNAPSHOT_INVALID", "Cannot read --previous: %s" % e)

    first_run = not prev
    new_urls = [u for u in current_hashes if u not in prev]
    changed = [u for u in current_hashes if u in prev and prev[u] != current_hashes[u]]
    removed = [u for u in prev if u not in current_hashes]
    unchanged = [u for u in current_hashes if u in prev and prev[u] == current_hashes[u]]

    # Incremental payload = new + changed rows, plus tombstones for removed URLs.
    changed_set = set(new_urls) | set(changed)
    by_url = {r["url"]: r for r in rows_out}
    to_load = [by_url[u] for u in changed_set]
    for u in removed:
        to_load.append({"url": u, "row_hash": hashlib.sha1(("TOMBSTONE:" + u).encode()).hexdigest(),
                        "run_id": a.run_id, "present": False, "ingested_at": now, "status_code": None})

    ddl = build_ddl(a.dialect, a.table)
    dml = merge_sql(a.dialect, a.table)

    load_report = {"attempted": False, "target": a.dialect}
    if a.load:
        if a.dialect == "postgres":
            fail("LOAD_UNSUPPORTED_STDLIB",
                 "Postgres load needs a driver (not std-lib). Emit DDL/MERGE and run via your DB tool.")
        token = os.environ.get("GCP_ACCESS_TOKEN")
        project = os.environ.get("BQ_PROJECT")
        dataset = os.environ.get("BQ_DATASET")
        if not (token and project and dataset):
            fail("AUTH_MISSING_WAREHOUSE_CREDS",
                 "Set GCP_ACCESS_TOKEN, BQ_PROJECT and BQ_DATASET to --load into BigQuery.")
        table_id = a.table.split(".")[-1]
        if not ensure_table(project, dataset, table_id, token):
            fail("WAREHOUSE_TABLE_ERROR", "Could not create/verify BigQuery table %s." % table_id)
        loaded, failed, load_trunc = stream_insert(project, dataset, table_id, token, to_load,
                                                    a.run_id, a.max_rows)
        load_report = {"attempted": True, "target": "bigquery", "rows_loaded": loaded,
                       "rows_failed": failed, "load_truncated": load_trunc,
                       "table": "%s.%s.%s" % (project, dataset, table_id)}

    def slim(r):
        return {k: r.get(k) for k in ("url", "status_code", "indexability", "row_hash", "present")}

    result = {
        "status": "baseline" if first_run else "ok",
        "run_id": a.run_id,
        "schema_version": SCHEMA_VERSION,
        "dialect": a.dialect,
        "truncated": truncated,
        "sources_used": {k: os.path.basename(v) for k, v in sources.items()},
        "inlinks_enriched": inlinks_used,
        "totals": {"urls_normalized": len(current_hashes), "columns": len(BQ_TYPE)},
        "diff": {"new": len(new_urls), "changed": len(changed),
                 "removed": len(removed), "unchanged": len(unchanged),
                 "new_urls": new_urls[:200], "changed_urls": changed[:200], "removed_urls": removed[:200]},
        "incremental_rows": len(to_load),
        "ddl": ddl,
        "merge_sql": dml,
        "load": load_report,
        "sample_rows": [slim(r) for r in rows_out[:a.sample]],
        "row_hashes": current_hashes,
    }
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
