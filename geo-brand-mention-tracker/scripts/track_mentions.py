#!/usr/bin/env python3
"""GEO Brand Mention Tracker — reference implementation.

Auth:   OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / PERPLEXITY_API_KEY.
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 track_mentions.py --brand brand.json --prompts prompts.json \
       [--engines openai,anthropic,gemini,perplexity] [--facts facts.json] [--model openai=chat-latest]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error

# Default model per engine. Vendors retire models (Google shut down
# gemini-1.5-pro on 2025-09-29; Anthropic retired claude-3-5-sonnet on
# 2025-10-28), so --model ENGINE=ID swaps one without a code change.
MODELS = {"openai": "gpt-6.1-sol", "anthropic": "claude-sonnet-5-5",
          "gemini": "gemini-3.8-flash", "perplexity": "sonar"}
POS = re.compile(r"\b(best|recommend|top|leading|excellent|great|trusted|popular|ideal|go-to)\b", re.I)
NEG = re.compile(r"\b(avoid|worst|poor|caution|however|but|lacks?|limited|complaints?|not recommended|downside)\b", re.I)


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
                time.sleep(float(ra) if ra and ra.isdigit() else 2 ** attempt); continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt); continue
            return e.code, json.loads(e.read() or b"{}"), e.headers
        except Exception:
            if attempt < 3:
                time.sleep(2 ** attempt); continue
            return 0, {}, {}
    return 429, {}, {}


def call_engine(engine, prompt, model):
    """Returns (status, answer_text, citations).

    temperature=0 goes to Perplexity only: current OpenAI, Anthropic and Gemini
    models reject a non-default temperature or advise against it.
    """
    if engine == "openai":
        k = os.environ["OPENAI_API_KEY"]
        s, r, _ = post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {k}"},
                       {"model": model, "messages": [{"role": "user", "content": prompt}]})
        return s, (r.get("choices", [{}])[0].get("message", {}).get("content", "") if s == 200 else ""), []
    if engine == "anthropic":
        k = os.environ["ANTHROPIC_API_KEY"]
        # Thinking is on by default and counts toward max_tokens; keep text blocks only.
        s, r, _ = post("https://api.anthropic.com/v1/messages",
                       {"x-api-key": k, "anthropic-version": "2023-06-01"},
                       {"model": model, "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}]})
        text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text") if s == 200 else ""
        return s, text, []
    if engine == "gemini":
        k = os.environ["GEMINI_API_KEY"]
        s, r, _ = post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}",
                       {}, {"contents": [{"parts": [{"text": prompt}]}]})
        text = ""
        if s == 200:
            parts = r.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return s, text, []
    if engine == "perplexity":
        k = os.environ["PERPLEXITY_API_KEY"]
        s, r, _ = post("https://api.perplexity.ai/chat/completions", {"Authorization": f"Bearer {k}"},
                       {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]})
        text = r.get("choices", [{}])[0].get("message", {}).get("content", "") if s == 200 else ""
        return s, text, r.get("citations", []) if s == 200 else []
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


def mention_sentence(text, terms):
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if any(re.search(r"\b" + re.escape(t) + r"\b", sent, re.I) for t in terms):
            return sent.strip()
    return None


def sentiment(sentence):
    if not sentence:
        return None
    pos, neg = bool(POS.search(sentence)), bool(NEG.search(sentence))
    return "negative" if neg and not pos else "positive" if pos and not neg else "neutral"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True); ap.add_argument("--prompts", required=True)
    ap.add_argument("--engines", default="openai,anthropic,gemini,perplexity")
    ap.add_argument("--facts")
    ap.add_argument("--model", action="append", default=[], metavar="ENGINE=ID",
                    help="Override an engine's default model, e.g. --model openai=chat-latest.")
    a = ap.parse_args()
    brand = json.load(open(a.brand))
    prompts = json.load(open(a.prompts))
    models = parse_models(a.model)
    if models is None:
        json.dump({"status": "error", "error": {"code": "INVALID_MODEL_OVERRIDE",
                   "message": "Use --model ENGINE=MODEL_ID with ENGINE one of " + ", ".join(MODELS) + "."}},
                  sys.stdout); sys.exit(1)
    terms = [brand["name"]] + brand.get("aliases", [])
    domains = brand.get("domains", [])
    keymap = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
              "gemini": "GEMINI_API_KEY", "perplexity": "PERPLEXITY_API_KEY"}
    engines, skipped = [], []
    for e in [x.strip() for x in a.engines.split(",")]:
        (engines if os.environ.get(keymap[e]) else skipped).append(e)
    if skipped:
        skipped = [{"engine": e, "reason": "no_api_key"} for e in skipped]
    if not engines:
        json.dump({"status": "error", "error": {"code": "NO_ENGINE_CREDENTIALS"}}, sys.stdout); sys.exit(1)

    per_engine = {}
    for e in engines:
        cells, mentioned, linked, sents = [], 0, 0, {"positive": 0, "neutral": 0, "negative": 0}
        for p in prompts:
            status, text, citations = call_engine(e, p, models[e])
            if status == 429:
                cells.append({"prompt": p, "status": "rate_limited"}); continue
            if status != 200:
                cells.append({"prompt": p, "status": "engine_error", "code": status}); continue
            if not text:
                cells.append({"prompt": p, "status": "empty_answer", "mentioned": False}); continue
            is_ment = any(re.search(r"\b" + re.escape(t) + r"\b", text, re.I) for t in terms)
            is_link = any(d in text for d in domains) or any(any(d in c for d in domains) for c in citations)
            sent_txt = mention_sentence(text, terms) if is_ment else None
            sent = sentiment(sent_txt)
            if is_ment:
                mentioned += 1
                if sent:
                    sents[sent] += 1
            if is_link:
                linked += 1
            cells.append({"prompt": p, "status": "ok", "mentioned": is_ment, "linked": is_link,
                          "sentiment": sent, "evidence": sent_txt, "citations": citations[:5]})
            time.sleep(0.3)
        n = len(prompts)
        per_engine[e] = {"cells": cells, "presence_rate": round(mentioned / n, 3),
                         "link_rate": round(linked / n, 3), "sentiment": sents}
    json.dump({"status": "ok", "brand": brand["name"], "engines": list(per_engine.keys()),
               "models": {e: models[e] for e in per_engine}, "skipped_engines": skipped,
               "results": per_engine}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
