#!/usr/bin/env python3
"""ASO Keyword Field Optimizer — reference implementation.

Packs the iOS 100-char keyword field (and validates subtitle overlap) per
locale: tokenizes title/subtitle/keyword-field, strips cross-field and
stop-word waste, dedupes across localizations, and greedily fills the highest
opportunity terms without repeating any word already indexed elsewhere.

Auth:   ASO_API_KEY (OPTIONAL, only for --enrich volume/difficulty lookups).
Input:  --metadata local JSON (per-locale listing + candidate terms).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 keyword_field_optimizer.py --metadata listing.json [--enrich]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse

ASO_BASE = "https://api.asokeyword.io/v1/keywords"
KW_LIMIT = 100  # Apple iOS keyword field hard cap (characters)

# Apple ignores these in the keyword field; including them wastes characters.
STOP_WORDS = {
    "a", "an", "and", "the", "or", "for", "with", "your", "you", "of", "to",
    "in", "on", "at", "by", "is", "it", "app", "apps", "free", "best", "new",
    "get", "my", "our", "this", "that", "from", "can", "will", "are", "be",
}
TOKEN_RE = re.compile(r"[^a-z0-9]+")


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        fail("INPUT_MISSING", "%s file not found: %s" % (label, path))
    except (ValueError, OSError) as exc:
        fail("INPUT_INVALID", "%s not readable: %s" % (label, exc))


def singular(tok):
    """Collapse naive plurals so cat/cats do not both consume the field."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") and not tok.endswith("us"):
        return tok[:-1]
    return tok


def tokens_of(text):
    out = []
    for raw in TOKEN_RE.split((text or "").lower()):
        if raw:
            out.append(singular(raw))
    return out


def content_tokens(text):
    """Indexable, de-stopworded tokens for title/subtitle coverage."""
    return {t for t in tokens_of(text) if t not in STOP_WORDS}


def combinations(n):
    """Apple recombines indexed words: unigrams + unordered bigrams."""
    return n + (n * (n - 1)) // 2


def enrich(term, country, key, timeout=30):
    q = urllib.parse.urlencode({"term": term, "country": country})
    req = urllib.request.Request(ASO_BASE + "?" + q, headers={"Authorization": "Bearer %s" % key})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
                return data.get("volume"), data.get("difficulty")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                if attempt == 5:
                    return None, None
                time.sleep(2 ** attempt)
                continue
            if exc.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None, None
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None, None
    return None, None


def opportunity(volume, difficulty):
    if not isinstance(volume, (int, float)):
        return None
    diff = difficulty if isinstance(difficulty, (int, float)) else 50.0
    return round(volume / (diff + 10.0), 4)


def build_candidates(loc, country, do_enrich, key, budget):
    """Return {token: best_opportunity_or_None}, first-seen order, and source."""
    cand, order, seen = {}, {}, 0
    used_api, used_meta = False, False
    raw = list(loc.get("candidates", []))
    # Retain current keyword field words as unranked candidates (do not lose them).
    for kw in [x for x in re.split(r"[,\n]", loc.get("current_keywords", "")) if x.strip()]:
        raw.append({"term": kw.strip()})
    lookups = 0
    for c in raw:
        term = c.get("term", "")
        vol, diff = c.get("volume"), c.get("difficulty")
        if vol is not None:
            used_meta = True
        if do_enrich and vol is None and lookups < budget:
            vol, diff = enrich(term, country, key)
            lookups += 1
            if vol is not None:
                used_api = True
            time.sleep(0.2)
        opp = opportunity(vol, diff)
        for tok in tokens_of(term):
            if tok not in order:
                seen += 1
                order[tok] = seen
            if opp is not None and (cand.get(tok) is None or opp > cand[tok]):
                cand[tok] = opp
            else:
                cand.setdefault(tok, None)
    src = "aso_api" if used_api else ("metadata" if used_meta else "none")
    return cand, order, src, lookups


def optimize_locale(loc, global_placed, country, do_enrich, key, budget):
    covered = content_tokens(loc.get("title", "")) | content_tokens(loc.get("subtitle", ""))
    brand = content_tokens(loc.get("title", ""))
    cand, order, src, lookups = build_candidates(loc, country, do_enrich, key, budget)

    ranked = sorted(cand.keys(), key=lambda t: (-(cand[t] if cand[t] is not None else 0.0), order[t]))
    placed, dropped, captured = [], [], 0.0
    field = ""
    for tok in ranked:
        if tok in STOP_WORDS:
            dropped.append({"token": tok, "reason": "stopword"})
        elif tok in covered:
            dropped.append({"token": tok, "reason": "in_title_subtitle"})
        elif tok in global_placed:
            dropped.append({"token": tok, "reason": "duplicate_across_locale"})
        else:
            addition = (len(tok) + (1 if field else 0))
            if len(field) + addition <= KW_LIMIT:
                field = (field + "," + tok) if field else tok
                placed.append(tok)
                global_placed.add(tok)
                captured += cand[tok] if cand[tok] is not None else 0.0
            else:
                dropped.append({"token": tok, "reason": "over_budget"})

    before_tokens = covered | {t for t in tokens_of(loc.get("current_keywords", "")) if t not in STOP_WORDS}
    after_tokens = covered | set(placed)
    coverage = {
        "indexable_tokens_before": len(before_tokens),
        "indexable_tokens_after": len(after_tokens),
        "est_combinations_before": combinations(len(before_tokens)),
        "est_combinations_after": combinations(len(after_tokens)),
        "combinations_gained": combinations(len(after_tokens)) - combinations(len(before_tokens)),
    }
    result = {
        "locale": loc.get("locale", "unknown"),
        "priority": loc.get("priority", 99),
        "optimized_keyword_field": field,
        "char_count": len(field),
        "char_budget_remaining": KW_LIMIT - len(field),
        "tokens_placed": placed,
        "tokens_dropped": dropped,
        "opportunity_captured": round(captured, 4),
        "opportunity_source": src,
        "coverage": coverage,
        "brand_tokens_excluded": sorted(brand),
    }
    return result, before_tokens, after_tokens, lookups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--country", default="us")
    ap.add_argument("--enrich", action="store_true", help="Look up missing volume/difficulty via ASO_API_KEY.")
    ap.add_argument("--max-lookups", type=int, default=60, dest="max_lookups")
    args = ap.parse_args()

    key = os.environ.get("ASO_API_KEY")
    if args.enrich and not key:
        fail("AUTH_MISSING_ASO_KEY", "Set ASO_API_KEY to use --enrich, or drop the flag for offline mode.")

    meta = load_json(args.metadata, "metadata")
    locales = meta.get("locales", [])
    if not locales:
        fail("INPUT_INVALID", "metadata.locales is empty; supply at least one locale block.")

    ordered = sorted(locales, key=lambda l: l.get("priority", 99))
    global_placed, results = set(), []
    tot_before, tot_after, budget = set(), set(), args.max_lookups
    dup_removed, sources = 0, set()
    for loc in ordered:
        res, before, after, used = optimize_locale(loc, global_placed, args.country, args.enrich, key, budget)
        budget -= used
        results.append(res)
        tot_before |= before
        tot_after |= after
        sources.add(res["opportunity_source"])
        dup_removed += sum(1 for d in res["tokens_dropped"] if d["reason"] == "duplicate_across_locale")

    warnings = []
    status = "ok"
    if sources == {"none"} or (len(sources) == 1 and "none" in sources):
        status = "insufficient"
        warnings.append("No volume/difficulty data available; tokens ranked by input order, not opportunity.")
    over = [r["locale"] for r in results if r["char_count"] > KW_LIMIT]
    if over:
        warnings.append("Keyword field exceeded 100 chars for: %s (should not happen)." % ", ".join(over))

    summary = {
        "locales_optimized": len(results),
        "total_unique_tokens_before": len(tot_before),
        "total_unique_tokens_after": len(tot_after),
        "total_combinations_gained": combinations(len(tot_after)) - combinations(len(tot_before)),
        "cross_locale_duplicates_removed": dup_removed,
    }
    json.dump({
        "status": status,
        "app_id": meta.get("app_id"),
        "keyword_char_limit": KW_LIMIT,
        "locales": results,
        "summary": summary,
        "warnings": warnings,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
