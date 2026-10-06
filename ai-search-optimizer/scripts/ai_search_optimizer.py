#!/usr/bin/env python3
"""AI Search Optimizer: reference implementation.

Measures how easy a page is for AI search engines (ChatGPT search, Perplexity,
Claude, Gemini, Google AI Overviews and AI Mode, Copilot) to extract and cite:
whether it answers up front, how quotable its facts are, its authority and
freshness signals and its entity markup, whether its robots meta lets Google
quote it, and which AI crawlers the site's robots.txt lets in. Returns scores,
findings, and the exact passages to rewrite.

Auth:   none (fetches the page, /robots.txt and /llms.txt).
Output: JSON on stdout per ../references/output.schema.json. Std-lib only.

Usage: python3 ai_search_optimizer.py --url https://example.com/guide \
       [--query "what is a crawl budget"] [--query "how to fix crawl budget"]
"""
from __future__ import annotations
import argparse, datetime, json, re, sys
import urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

UA = "seoskills-ai-search-optimizer/1.0 (+https://seoskills.sh)"
# Crawler tokens and what a robots.txt block means, per each vendor's own docs.
AI_CRAWLERS = [
    ("OAI-SearchBot", "OpenAI", "search", "Blocking it keeps the site out of ChatGPT search answers."),
    ("ChatGPT-User", "OpenAI", "user", "Fetches a page when a user asks; OpenAI says robots.txt may not apply, so a block is not a reliable opt-out."),
    ("GPTBot", "OpenAI", "training", "Training only; blocking it does not remove the site from ChatGPT search."),
    ("Claude-SearchBot", "Anthropic", "search", "Blocking it can reduce the site's visibility in Claude's search results."),
    ("Claude-User", "Anthropic", "user", "Blocking it stops Claude retrieving the page when a user asks about it."),
    ("ClaudeBot", "Anthropic", "training", "Training only; blocking it keeps future content out of Claude's training data."),
    ("PerplexityBot", "Perplexity", "search", "Blocking it keeps the site out of Perplexity's search results."),
    ("Perplexity-User", "Perplexity", "user", "Fetches a page when a user asks; Perplexity says it generally ignores robots.txt."),
    ("Googlebot", "Google", "search", "AI Overviews and AI Mode use Google Search's index; blocking Googlebot removes the site from both."),
    ("Google-Extended", "Google", "training", "Limits Gemini training and grounding in Google's other AI products; it does not affect Search or AI Overviews."),
    ("Bingbot", "Microsoft", "search", "Copilot answers draw on Bing's index; blocking Bingbot removes the site from them."),
    ("Applebot-Extended", "Apple", "training", "Training only (Apple Intelligence); regular Applebot still crawls for Siri and Spotlight."),
    ("CCBot", "Common Crawl", "training", "Training datasets used by many models; no direct effect on live AI answers."),
]
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "section", "article", "main",
              "header", "footer", "nav", "aside", "blockquote", "summary", "dd", "dt", "figcaption", "tr",
              "ul", "ol", "table", "dl", "details", "pre", "form", "body"}
PARA_TAGS = {"p", "div", "section", "article", "main", "body", "blockquote", "dd", "figcaption", "td", "form"}
STRUCTURE_TAGS = {"ul", "ol", "table", "dl", "details"}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "select", "button"}
QUESTION = re.compile(r"^(what|how|why|when|where|which|who|can|could|does|do|is|are|should|will|would)\b|\?\s*$", re.I)
SUMMARY_HEAD = re.compile(r"\b(tl;?dr|summary|key takeaways|in short|at a glance|the short answer|quick answer)\b", re.I)
DEF_VERB = re.compile(r"\s(is|are|refers to|means)\s(a|an|the)\s", re.I)
SUBJECT_STOP = {"it", "this", "that", "there", "here", "these", "those", "they", "we", "you", "i", "he", "she", "what",
                "which", "who", "if", "when", "while", "because", "although", "since", "so", "and", "but", "or", "as",
                "our", "your", "my", "their", "its"}
STAT = re.compile(r"\d[\d,.]*\s?(%|percent|per cent|x\b|times\b|million|billion|thousand|ms\b|seconds?|minutes?|hours?"
                  r"|days?|weeks?|months?|years?|users|people|customers|sites|pages|visits|clicks|searches|queries)"
                  r"|[$€£¥₹]\s?\d|\b\d{1,3}(,\d{3})+\b|\b\d+ (in|out of) \d+\b", re.I)
BOILERPLATE = re.compile(r"©|copyright|trademark|licensed under|all rights reserved|cookie", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
VISIBLE_DATE = re.compile(r"\b(updated|modified|reviewed|published)\b[^.\n]{0,24}?"
                          r"(\d{4}-\d{2}-\d{2}|[a-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2} [a-z]{3,9}\.? \d{4})", re.I)
SHARE = re.compile(r"/intent/|sharer|sharearticle|/share\b|/submit\?|mailto:|whatsapp", re.I)
STOP = {"the", "a", "an", "of", "to", "in", "for", "and", "or", "is", "are", "what", "how", "why", "do", "does", "can",
        "i", "my", "on", "with", "best", "vs", "your", "you", "it", "be", "should", "which", "who", "when", "where"}


def fail(code, message, **extra):
    json.dump({"status": "error", "error": {"code": code, "message": message}, **extra}, sys.stdout)
    sys.exit(1)


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(3_000_000).decode("utf-8", "ignore")
            return r.status, r.geturl(), r.headers, body
    except urllib.error.HTTPError as e:
        return e.code, url, e.headers, ""
    except Exception:
        return 0, url, {}, ""


class PageParser(HTMLParser):
    """Splits the page into text segments at block boundaries, so div-built pages read like p-built ones.
    Each segment knows its enclosing block tag and whether it sits in the main content or in site chrome."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.segments, self.meta, self.jsonld, self.times, self.links = [], {}, [], [], []
        self.title, self.byline, self.saw_main = "", False, False
        self.structures = []  # (main, chrome) per list/table
        self._stack, self._buf, self._jbuf = [], [], []
        self._skip = self._svg = self._main = self._chrome = 0
        self._in_title = self._in_jsonld = False
        self._header_chrome = []

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.segments.append({"tag": self._stack[-1] if self._stack else "body", "text": text,
                                  "main": self._main > 0, "chrome": self._chrome > 0})

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "svg":
            self._svg += 1
        if tag == "title" and not self._svg and not self.title:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld, self._jbuf = True, []
            return
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        marker = " ".join([a.get("class", ""), a.get("itemprop", ""), a.get("rel", ""), a.get("id", "")]).lower()
        if re.search(r"\b(author|byline)\b", marker):
            self.byline = True
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "br":
            self._flush()
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in ("main", "article"):
                self._main += 1
                self.saw_main = True
            if tag in ("nav", "footer", "aside"):
                self._chrome += 1
            if tag == "header":
                outside = self._main == 0
                self._header_chrome.append(outside)
                self._chrome += outside
            if tag in STRUCTURE_TAGS:
                self.structures.append((self._main > 0, self._chrome > 0))
            self._stack.append(tag)
        if tag == "a" and a.get("href"):
            self.links.append((a["href"], self._main > 0, self._chrome > 0))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "svg" and self._svg:
            self._svg -= 1
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jbuf))
            self._in_jsonld = False
            return
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in BLOCK_TAGS and tag in self._stack:
            self._flush()
            while self._stack:
                top = self._stack.pop()
                if top in ("main", "article") and self._main:
                    self._main -= 1
                if top in ("nav", "footer", "aside") and self._chrome:
                    self._chrome -= 1
                if top == "header" and self._header_chrome:
                    self._chrome -= self._header_chrome.pop()
                if top == tag:
                    break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_jsonld:
            self._jbuf.append(data)
        elif not self._skip and not self._svg:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def jsonld_nodes(blocks):
    nodes = []
    for b in blocks:
        try:
            data = json.loads(b)
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if isinstance(n.get("@graph"), list):
                    stack.extend(n["@graph"])
                nodes.append(n)
            elif isinstance(n, list):
                stack.extend(n)
    return nodes


def types_of(node):
    t = node.get("@type")
    return [t] if isinstance(t, str) else [x for x in t if isinstance(x, str)] if isinstance(t, list) else []


def parse_date(s):
    s = (s or "").strip()
    try:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"([a-z]{3,9})\.? (\d{1,2}),? (\d{4})", s, re.I) or re.match(r"(\d{1,2}) ([a-z]{3,9})\.? (\d{4})", s, re.I)
        if m:
            day, mon = (m.group(2), m.group(1)) if m.group(1).isalpha() else (m.group(1), m.group(2))
            month = MONTHS.get(mon[:3].lower())
            return datetime.date(int(m.group(3)), month, int(day)) if month else None
    except ValueError:
        return None
    return None


def words(text):
    return re.findall(r"[^\W\d_][\w'-]*", text or "")


def stem(w):
    w = w.lower()
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def terms(text):
    return {stem(w) for w in words(text) if w.lower() not in STOP}


def is_definition(sentence):
    """'Crawl budget is the number of ...': a short noun-phrase subject, then is/are/means + article."""
    m = DEF_VERB.search(sentence)
    if not m or not sentence[:1].isupper():
        return False
    subject = [w.strip(",:;\"'").lower() for w in sentence[:m.start()].split()]
    return 1 <= len(subject) <= 6 and not set(subject) & SUBJECT_STOP


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(words(s)) >= 6]


def site_key(host):
    labels = host.lower().split(":")[0].removeprefix("www.").split(".")
    if len(labels) >= 3 and labels[-2] in {"co", "com", "org", "net", "ac", "gov", "edu"} and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def robots_directives(meta, headers):
    vals = [meta.get("robots", ""), meta.get("googlebot", "")]
    vals += [v for k, v in (headers.items() if hasattr(headers, "items") else []) if k.lower() == "x-robots-tag"]
    joined = ",".join(vals).lower()
    snippet = re.search(r"max-snippet\s*:\s*(-?\d+)", joined)
    return {"noindex": "noindex" in joined or "none" in re.split(r"[,\s]+", joined),
            "nosnippet": "nosnippet" in joined,
            "max_snippet": int(snippet.group(1)) if snippet else None}


# ---------- robots.txt (Google-style matching: longest rule wins, Allow wins a tie) ----------

def parse_robots(text):
    groups, agents, rules, last_agent = [], [], [], False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (p.strip() for p in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if not last_agent and agents:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.split("/")[0].strip().lower())
            last_agent = True
        else:
            last_agent = False
            if k in ("allow", "disallow"):
                rules.append((k, v))
    if agents:
        groups.append((agents, rules))
    return groups


def allowed(groups, agent, path):
    agent = agent.lower()
    chosen = [r for a, r in groups if agent in a]
    if not chosen:
        chosen = [r for a, r in groups if "*" in a]
    best = None
    for rules in chosen:
        for kind, pat in rules:
            if not pat:
                continue
            rx = re.escape(pat).replace(r"\*", ".*")
            rx = rx[:-2] + "$" if rx.endswith(r"\$") else rx
            if re.match(rx, path):
                cand = (len(pat), kind == "allow")
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1]):
                    best = cand
    return True if best is None else best[1]


def domain_checks(origin, paths):
    status, _, _, robots = fetch(origin + "/robots.txt")
    groups = parse_robots(robots) if status == 200 else []
    crawlers = []
    for name, vendor, kind, impact in AI_CRAWLERS:
        blocked = [p for p in paths if not allowed(groups, name, p)]
        crawlers.append({"crawler": name, "vendor": vendor, "purpose": kind, "allowed": not blocked,
                         "blocked_paths": blocked, "what_blocking_means": impact})
    l_status, _, l_headers, l_body = fetch(origin + "/llms.txt")
    l_type = l_headers.get("Content-Type", "") if hasattr(l_headers, "get") else ""
    has_llms = l_status == 200 and l_body.lstrip().startswith("#") and "html" not in l_type.lower()
    blocked = [c["crawler"] for c in crawlers if not c["allowed"] and c["purpose"] == "search"]
    return {"robots_txt_found": status == 200, "search_crawlers_blocked": blocked, "ai_crawlers": crawlers,
            "llms_txt_found": has_llms}


def analyze(url, queries, today):
    status, final, headers, html = fetch(url)
    ctype = headers.get("Content-Type", "") if hasattr(headers, "get") else ""
    if status != 200 or "html" not in ctype.lower():
        return {"url": url, "status": "unreachable", "http_status": status}
    p = PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass

    def is_content(seg_main, seg_chrome):
        return not seg_chrome and (seg_main or not p.saw_main)

    content = [s for s in p.segments if is_content(s["main"], s["chrome"])]
    h1 = next((s["text"] for s in p.segments if s["tag"] == "h1"), "")
    h1_at = next((i for i, s in enumerate(content) if s["tag"] == "h1"), -1)
    heads = [s["text"] for s in content if s["tag"] in ("h2", "h3", "h4")]
    paras = [s["text"] for s in content if s["tag"] in PARA_TAGS or s["tag"] == "li"]
    after_h1 = [s for s in content[h1_at + 1:] if len(words(s["text"])) >= 15]
    first_para = next((s["text"] for s in after_h1 if s["tag"] == "p"), "") \
        or next((s["text"] for s in after_h1 if s["tag"] in PARA_TAGS), "")
    body_text = "\n".join(s["text"] for s in content)
    total_words = len(words(body_text))
    nodes = jsonld_nodes(p.jsonld)
    all_types = {t for n in nodes for t in types_of(n)}
    own = site_key(urllib.parse.urlsplit(final).netloc)
    cites = set()
    for href, in_main, in_chrome in p.links:
        full = urllib.parse.urljoin(final, href)
        host = urllib.parse.urlsplit(full).netloc
        if is_content(in_main, in_chrome) and full.startswith("http") and host and site_key(host) != own and not SHARE.search(full):
            cites.add(full.split("#")[0])
    directives = robots_directives(p.meta, headers)

    findings, recs, rewrite = [], [], []

    def flag(code, priority, action):
        findings.append(code)
        recs.append((priority, action))

    # 0. Eligibility: Google's own controls for AI Overviews and AI Mode.
    if directives["noindex"]:
        flag("NOINDEX", "critical", "The page is noindex, so no search engine or AI answer built on a search index can use it. Remove noindex if it should be found.")
    if directives["nosnippet"]:
        flag("NOSNIPPET", "critical", "nosnippet stops Google quoting this page in results, AI Overviews and AI Mode. Remove it, or use data-nosnippet on the parts to hide.")
    elif directives["max_snippet"] is not None and 0 <= directives["max_snippet"] < 160:
        flag("MAX_SNIPPET_LIMIT", "high", "max-snippet:%d caps how much Google can quote, including in AI features. Raise it or use -1." % directives["max_snippet"])

    # 1. Extractability: an answer up front, question-led sections, short paragraphs, lists and tables.
    fp_words = len(words(first_para))
    answer_first = 25 <= fp_words <= 90
    q_heads = [h for h in heads if QUESTION.search(h.strip())]
    long_paras = [x for x in paras if len(words(x)) > 120]
    structured = sum(1 for m, c in p.structures if is_content(m, c))
    has_summary = any(SUMMARY_HEAD.search(h) for h in heads)
    ext = (6 if answer_first else 2 if first_para else 0) + min(5, len(q_heads) * 2) \
        + (4 if not long_paras else 2 if len(long_paras) <= 2 else 0) + min(3, structured) + (2 if has_summary else 0)
    if not answer_first:
        what = "is %d words long" % fp_words if first_para else "was not found"
        flag("NO_ANSWER_FIRST", "high", "The opening paragraph %s. Put a 40 to 60 word direct answer right under the H1, before any background." % what)
        if first_para:
            rewrite.append({"kind": "answer_first", "current": first_para[:700]})
    if heads and not q_heads:
        flag("NO_QUESTION_HEADINGS", "medium", "Phrase key section headings as the questions people ask, each answered in the first sentence below it.")
        rewrite.extend({"kind": "question_heading", "current": h} for h in heads[:5])
    if long_paras:
        flag("LONG_PARAGRAPHS", "medium", "Split the %d paragraph(s) over 120 words; AI answers lift short, self-contained passages." % len(long_paras))
        rewrite.extend({"kind": "split_paragraph", "current": lp[:700]} for lp in long_paras[:3])
    if not structured:
        flag("NO_LISTS_OR_TABLES", "medium", "Turn steps into a numbered list and comparisons into a table.")
    if total_words >= 800 and not has_summary:
        flag("NO_SUMMARY", "low", "Add a short \"Key takeaways\" list near the top of this long page.")

    # 2. Quotability: self-contained factual sentences with figures, definitions, cited sources.
    sents = [s for seg in content if seg["tag"] not in ("h1", "h2", "h3", "h4", "h5", "h6")
             for s in sentences(seg["text"]) if not BOILERPLATE.search(s)]
    stats = [s for s in sents if STAT.search(s)]
    defs = [s for s in sents if is_definition(s)]
    quot = min(10, len(stats) * 2) + min(6, len(defs) * 3) + (4 if cites else 0)
    if not stats:
        flag("NO_STATISTICS", "medium", "Add specific figures (counts, percentages, prices, timings) with their source; AI answers quote numbers.")
    if not defs:
        flag("NO_DEFINITION", "low", "Include a one-sentence definition of the main term (\"X is a ...\") near the top.")
    if stats and not cites:
        flag("STATS_WITHOUT_SOURCES", "medium", "Link each figure to its original source.")

    # 3. Authority: a named author, the organization behind the page, outbound citations.
    author_node = any(n.get("author") for n in nodes)
    author_meta = bool(p.meta.get("author") or p.meta.get("article:author"))
    org = bool(all_types & {"Organization", "Corporation", "LocalBusiness", "NewsMediaOrganization", "OnlineStore"}
               or any(n.get("publisher") for n in nodes))
    auth = (8 if author_node else 5 if (author_meta or p.byline) else 0) + (6 if org else 0) + min(6, len(cites))
    if not (author_node or author_meta or p.byline):
        flag("NO_AUTHOR", "high", "Show a named author with relevant credentials, and add `author` (a Person) to the page's JSON-LD.")
    elif not author_node:
        flag("AUTHOR_NOT_MARKED_UP", "low", "The author is shown but not in JSON-LD; add `author` as a Person with name and url.")
    if not org:
        flag("NO_ORGANIZATION", "medium", "Add Organization JSON-LD (name, url, logo, sameAs) so engines tie the content to your brand.")

    # 4. Freshness: machine-readable and visible dates, and how old the newest one is.
    candidates = [parse_date(n.get(k)) for n in nodes for k in ("dateModified", "datePublished") if isinstance(n.get(k), str)]
    candidates += [parse_date(p.meta.get(k, "")) for k in ("article:modified_time", "article:published_time", "og:updated_time")]
    candidates += [parse_date(t) for t in p.times]
    visible = VISIBLE_DATE.search("\n".join(s["text"] for s in p.segments if not s["chrome"]))
    if visible:
        candidates.append(parse_date(visible.group(2)))
    dates = [d for d in candidates if d and d <= today]
    newest = max(dates) if dates else None
    age = (today - newest).days if newest else None
    has_modified = any(isinstance(n.get("dateModified"), str) for n in nodes)
    fresh = (10 if age is not None and age <= 180 else 6 if age is not None and age <= 365 else 2 if age is not None else 0) \
        + (5 if has_modified else 0) + (5 if visible else 0)
    if age is None:
        flag("NO_DATES", "high", "Publish datePublished and dateModified in JSON-LD and show a visible \"Updated\" date.")
    else:
        if age > 365:
            flag("STALE", "high", "The newest date on the page is %d days old. Refresh the facts, then update dateModified and the visible date." % age)
        if not has_modified:
            flag("NO_DATE_MODIFIED", "medium", "Add dateModified to the page's JSON-LD and change it only when the content really changes.")
        if not visible:
            flag("NO_VISIBLE_DATE", "low", "Show the last-updated date on the page, not only in the markup.")

    # 5. Entity clarity: a typed page, sameAs links to the brand's profiles, breadcrumbs.
    same_as = sum(len(n["sameAs"]) if isinstance(n.get("sameAs"), list) else 1 if n.get("sameAs") else 0 for n in nodes)
    page_types = all_types - {"WebSite", "Organization", "BreadcrumbList", "WebPage", "SiteNavigationElement",
                              "ImageObject", "Person", "SearchAction", "ListItem", "EntryPoint", "PropertyValueSpecification"}
    ent = (8 if page_types else 3 if nodes else 0) + min(6, same_as * 2) + (6 if "BreadcrumbList" in all_types else 0)
    if not nodes:
        flag("NO_STRUCTURED_DATA", "high", "Add JSON-LD for the page type (Article, Product, LocalBusiness, HowTo and so on) and the organization.")
    elif not page_types:
        flag("NO_PAGE_TYPE", "medium", "The JSON-LD only describes the site. Add a node for this page's own type (Article, Product, Service, and so on).")
    if nodes and not same_as:
        flag("NO_SAMEAS", "low", "Add sameAs links (LinkedIn, Wikipedia, Crunchbase, social profiles) to the Organization or author.")

    # Query fit: does the page use the words of the questions it should be cited for?
    query_fit = []
    for q in queries:
        tq = terms(q)
        if not tq:
            continue

        def cov(text):
            return round(len(tq & terms(text)) / len(tq), 2)

        fit = {"query": q, "title": cov(p.title), "h1": cov(h1), "first_paragraph": cov(first_para),
               "best_heading": max([cov(h) for h in heads] or [0]), "body": cov(body_text)}
        query_fit.append(fit)
        if fit["body"] < 0.6:
            recs.append(("high", "The page barely covers \"%s\": add a section that answers it directly." % q))
        elif fit["first_paragraph"] < 0.5 and fit["best_heading"] < 0.5:
            recs.append(("medium", "Answer \"%s\" under a heading that asks it, using the words people search with." % q))

    scores = {"extractability": min(20, ext), "quotability": min(20, quot), "authority": min(20, auth),
              "freshness": min(20, fresh), "entity_clarity": min(20, ent)}
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    seen, ordered = set(), []
    for pri, text in sorted(recs, key=lambda r: rank[r[0]]):
        if text not in seen:
            seen.add(text)
            ordered.append({"priority": pri, "action": text})
    return {
        "url": url, "status": "ok", "final_url": final, "title": p.title.strip(), "h1": h1,
        "score": sum(scores.values()), "scores": scores,
        "eligibility": directives,
        "measures": {
            "words": total_words, "first_paragraph_words": fp_words, "headings": len(heads),
            "question_headings": len(q_heads), "long_paragraphs": len(long_paras), "lists_and_tables": structured,
            "statistic_sentences": len(stats), "definition_sentences": len(defs), "external_citations": len(cites),
            "schema_types": sorted(all_types), "newest_date": newest.isoformat() if newest else None,
            "days_since_update": age, "author_in_schema": author_node, "author_visible": bool(author_meta or p.byline),
        },
        "findings": findings, "query_fit": query_fit, "recommendations": ordered, "rewrite_targets": rewrite[:10],
        "examples": {"statistic_sentences": stats[:3], "definition_sentences": defs[:2]},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", required=True, help="Page to optimize (repeat or comma-separate, up to 10)")
    ap.add_argument("--query", action="append", default=[], help="A question the page should be cited for (repeatable)")
    args = ap.parse_args()
    urls = list(dict.fromkeys(u.strip() for arg in args.url for u in arg.split(",") if u.strip()))[:10]
    if not urls or any(not re.match(r"^https?://[^/\s]+", u) for u in urls):
        fail("INPUT_INVALID", "Each --url must be an absolute http(s) URL.")
    queries = [q.strip() for q in args.query if q.strip()][:10]
    today = datetime.date.today()
    pages = [analyze(u, queries, today) for u in urls]
    if all(pg["status"] != "ok" for pg in pages):
        fail("PAGES_UNREACHABLE", "None of the pages could be fetched as HTML.", pages=pages)
    domains = {}
    for u in urls:
        sp = urllib.parse.urlsplit(u)
        origin = "%s://%s" % (sp.scheme, sp.netloc)
        if origin not in domains:
            paths = []
            for x in urls:
                xs = urllib.parse.urlsplit(x)
                if "%s://%s" % (xs.scheme, xs.netloc) == origin:
                    paths.append((xs.path or "/") + ("?" + xs.query if xs.query else ""))
            domains[origin] = domain_checks(origin, paths)
    json.dump({"status": "ok", "checked": today.isoformat(), "pages": pages, "domains": domains}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
