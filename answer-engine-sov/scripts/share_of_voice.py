#!/usr/bin/env python3
"""Answer Engine Share-of-Voice Reporter — reference implementation.

Auth:   OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / PERPLEXITY_API_KEY.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.
Shares the engine-call layer with geo-brand-mention-tracker.

Usage: python3 share_of_voice.py --brands brands.json --prompts prompts.json \
       [--engines openai,perplexity] [--weighting mention] [--model openai=chat-latest]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from collections import defaultdict

# Default model per engine. Vendors retire models (Google shut down
# gemini-1.5-pro on 2025-09-29; Anthropic retired claude-3-5-sonnet on
# 2025-10-28), so --model ENGINE=ID swaps one without a code change.
MODELS = {"openai": "gpt-6.1-sol", "anthropic": "claude-sonnet-5-5",
          "gemini": "gemini-3.8-flash", "perplexity": "sonar"}
KEYMAP = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
          "gemini": "GEMINI_API_KEY", "perplexity": "PERPLEXITY_API_KEY"}


def post(url, headers, payload, timeout=120):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return 200, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 5:
                    return 429, {}, e.headers
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else 2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, {}, e.headers
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}, {}
    return 429, {}, {}


def call_engine(engine, prompt, model):
    # temperature=0 goes to Perplexity only: current OpenAI, Anthropic and
    # Gemini models reject a non-default temperature or advise against it.
    if engine == "openai":
        s, r, _ = post("https://api.openai.com/v1/chat/completions",
                       {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                       {"model": model, "messages": [{"role": "user", "content": prompt}]})
        return s, (r.get("choices", [{}])[0].get("message", {}).get("content", "") if s == 200 else ""), []
    if engine == "anthropic":
        # Thinking is on by default and counts toward max_tokens; keep text blocks only.
        s, r, _ = post("https://api.anthropic.com/v1/messages",
                       {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                       {"model": model, "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}]})
        return s, ("".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text") if s == 200 else ""), []
    if engine == "gemini":
        k = os.environ["GEMINI_API_KEY"]
        s, r, _ = post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}",
                       {}, {"contents": [{"parts": [{"text": prompt}]}]})
        parts = r.get("candidates", [{}])[0].get("content", {}).get("parts", []) if s == 200 else []
        return s, "".join(p.get("text", "") for p in parts if not p.get("thought")), []
    if engine == "perplexity":
        s, r, _ = post("https://api.perplexity.ai/chat/completions",
                       {"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}"},
                       {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]})
        return s, (r.get("choices", [{}])[0].get("message", {}).get("content", "") if s == 200 else ""), (r.get("citations", []) if s == 200 else [])
    return 0, "", []


def parse_models(overrides):
    """MODELS with any --model ENGINE=ID overrides applied; None if one is malformed."""
    models = dict(MODELS)
    for o in overrides:
        engine, _, model = o.partition("=")
        if engine not in MODELS or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", model):
            return None
        models[engine] = model
    return models


def voice(brand, text, citations, weighting, text_lower):
    terms = [brand["name"]] + brand.get("aliases", [])
    # Case-insensitive: match lowercased terms against the lowercased answer.
    positions = [m.start() for t in terms for m in re.finditer(r"\b" + re.escape(t.lower()) + r"\b", text_lower)]
    mentioned = bool(positions)
    cited = any(d.lower() in text_lower for d in brand.get("domains", [])) or \
        any(any(d.lower() in c.lower() for d in brand.get("domains", [])) for c in citations)
    if weighting == "citation":
        return (1 if cited else 0, min(positions) if positions else None)
    if weighting == "first_mention":
        return (0, min(positions) if positions else None)  # resolved later (only earliest brand gets 1)
    return (1 if mentioned else 0, min(positions) if positions else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", required=True); ap.add_argument("--prompts", required=True)
    ap.add_argument("--engines", default="openai,anthropic,gemini,perplexity")
    ap.add_argument("--weighting", default="mention", choices=["mention", "citation", "first_mention"])
    ap.add_argument("--model", action="append", default=[], metavar="ENGINE=ID",
                    help="Override an engine's default model, e.g. --model openai=chat-latest.")
    a = ap.parse_args()
    brands = json.load(open(a.brands)); prompts = json.load(open(a.prompts))
    models = parse_models(a.model)
    if models is None:
        json.dump({"status": "error", "error": {"code": "INVALID_MODEL_OVERRIDE",
                   "message": "Use --model ENGINE=MODEL_ID with ENGINE one of " + ", ".join(MODELS) + "."}},
                  sys.stdout); sys.exit(1)
    engines, skipped = [], []
    for e in [x.strip() for x in a.engines.split(",")]:
        (engines if os.environ.get(KEYMAP[e]) else skipped).append(e)
    if not engines:
        json.dump({"status": "error", "error": {"code": "NO_ENGINE_CREDENTIALS"}}, sys.stdout); sys.exit(1)

    per_engine = {e: defaultdict(float) for e in engines}
    per_cluster = defaultdict(lambda: defaultdict(float))
    overall = defaultdict(float)
    failed = {e: defaultdict(int) for e in engines}
    answered, total = 0, 0
    for e in engines:
        for pr in prompts:
            total += 1
            status, text, citations = call_engine(e, pr["text"], models[e])
            if status != 200 or not text:
                # Count the failure, so a retired model or a bad key shows up
                # instead of silently shrinking that engine's sample.
                failed[e]["rate_limited" if status == 429 else "empty_answer" if status == 200
                          else f"engine_error_{status}"] += 1
                continue
            answered += 1
            tl = text.lower()
            scored = [(b["name"], *voice(b, text, citations, a.weighting, tl)) for b in brands]
            if a.weighting == "first_mention":
                present = [(nm, pos) for nm, _, pos in scored if pos is not None]
                winner = min(present, key=lambda x: x[1])[0] if present else None
                scored = [(nm, 1 if nm == winner else 0, pos) for nm, _, pos in scored]
            for nm, v, _pos in scored:
                per_engine[e][nm] += v
                per_cluster[pr.get("cluster", "default")][nm] += v
                overall[nm] += v
            time.sleep(0.3)

    def sov(counts):
        tot = sum(counts.values())
        return {nm: round(v / tot, 4) for nm, v in counts.items()} if tot else {nm: 0 for nm in counts}

    self_name = next((b["name"] for b in brands if b.get("is_self")), None)
    overall_sov = sov(overall)
    ranking = sorted(overall_sov.items(), key=lambda kv: kv[1], reverse=True)
    self_rank = next((i + 1 for i, (nm, _) in enumerate(ranking) if nm == self_name), None)

    json.dump({
        "status": "ok", "weighting": a.weighting, "engines": engines,
        "models": {e: models[e] for e in engines},
        "skipped_engines": [{"engine": e, "reason": "no_api_key"} for e in skipped],
        "answered_cells": answered, "total_cells": total,
        "failed_cells": {e: dict(c) for e, c in failed.items()},
        "per_engine_sov": {e: sov(c) for e, c in per_engine.items()},
        "per_cluster_sov": {c: sov(v) for c, v in per_cluster.items()},
        "overall_sov": overall_sov, "leaderboard": [nm for nm, _ in ranking],
        "self_brand": self_name, "self_rank": self_rank,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
