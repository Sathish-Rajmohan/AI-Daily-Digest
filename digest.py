#!/usr/bin/env python3
"""
Daily News Digest

Reads topics.json, pulls recent items from each topic's RSS feeds,
asks a model to write one synthesized brief per topic (using multiple
outlets to fill in the picture), and emails an HTML digest via Gmail SMTP.

Config stored in topics.json and environment variables (see README.md).
"""

import html
import json
import os
import random
import re
import smtplib
import socket
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import feedparser
import requests
from dateutil import parser as dateparser

CONFIG_PATH = os.environ.get("DIGEST_CONFIG_PATH", "topics.json")

# When stdout is piped (as in Actions logs) Python block-buffers it, while
# stderr stays unbuffered. The two can then land out of chronological order
# in a combined log even though they were printed in order, which is
# confusing to read after the fact. Line-buffer stdout so progress lines
# and warning lines interleave the way they actually happened.
sys.stdout.reconfigure(line_buffering=True)

# Cap how many raw articles we ship to the model per topic. Free-tier
# prompts stay smaller and a runaway feed can't blow up the request.
MAX_ARTICLES_PER_TOPIC = 40
FEED_FETCH_TIMEOUT = 20

# feedparser's own fetch path (used as a fallback in fetch_feed() below)
# doesn't take a timeout argument and can hang indefinitely on a bad host.
# It falls back to the socket module's default timeout when none is given
# explicitly, so set that process-wide to keep a worst-case feed bounded.
socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)

USER_AGENT = (
    "daily-digest/1.0 (+https://github.com/Sathish-Rajmohan/AI-Daily-News-Digest; "
    "RSS reader for personal digest)"
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    if "topics" not in config or not isinstance(config["topics"], list):
        raise ValueError(f"{CONFIG_PATH} must contain a 'topics' array")
    return config


def parse_entry_time(entry):
    """Best-effort publish time from a feedparser entry."""
    for key in ("published", "updated", "created"):
        if key in entry:
            try:
                dt = dateparser.parse(entry[key])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (ValueError, TypeError, OverflowError):
                pass
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return datetime(*struct[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    return None


def fetch_feed(feed_url):
    """
    Fetch feed XML with an explicit timeout and User-Agent, then parse.
    feedparser.parse(url) alone can hang indefinitely on a bad host.
    """
    try:
        resp = requests.get(
            feed_url,
            timeout=FEED_FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
        )
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except requests.exceptions.RequestException as e:
        # Some hosts dislike non-browser clients and only respond to
        # feedparser's own defaults. Fall back to that.
        print(f"  [warn] HTTP fetch failed for {feed_url}: {e}; trying feedparser", file=sys.stderr)
        return feedparser.parse(feed_url)


def fetch_topic_articles(topic, lookback_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles = []
    seen_links = set()

    for feed_url in topic.get("feeds", []):
        try:
            parsed = fetch_feed(feed_url)
        except Exception as e:
            print(f"  [warn] failed to parse feed {feed_url}: {e}", file=sys.stderr)
            continue

        if getattr(parsed, "bozo", False) and not parsed.entries:
            print(f"  [warn] feed unreadable, skipping: {feed_url}", file=sys.stderr)
            continue

        source_name = parsed.feed.get("title", feed_url)

        for entry in parsed.entries:
            pub_time = parse_entry_time(entry)
            # Some feeds omit dates on posts that are otherwise fine to
            # use, so undated items are kept instead of dropped.
            if pub_time is not None and pub_time < cutoff:
                continue

            link = (entry.get("link") or "").strip()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)

            summary = entry.get("summary", "") or entry.get("description", "")
            # Strip tags so the model isn't fed raw HTML soup.
            if summary and "<" in summary:
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s+", " ", summary).strip()

            articles.append({
                "title": entry.get("title", "(untitled)"),
                "link": link,
                "summary": summary,
                "source": source_name,
                "published": pub_time.isoformat() if pub_time else None,
            })

    # Prefer fresher items when we have to trim.
    articles.sort(key=lambda a: a["published"] or "", reverse=True)
    return articles[:MAX_ARTICLES_PER_TOPIC]


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# A 503 from Gemini means one model's shared serving pool is out of capacity
# right now. Waiting and asking the same pool again usually returns the same
# 503, so the chain below moves to a different model instead. Order runs from
# the current Flash release down to the lighter Flash-Lite tier, which sits on
# less contended capacity. Quality degrades a little at the bottom of the
# chain, which beats a topic missing from the email.
GEMINI_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_MODELS",
        "gemini-flash-latest,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite",
    ).split(",")
    if m.strip()
]

# GEMINI_MODEL still pins a first choice, with the rest of the chain kept
# underneath it as fallbacks.
_pinned_model = os.environ.get("GEMINI_MODEL")
if _pinned_model:
    GEMINI_MODELS = [_pinned_model] + [m for m in GEMINI_MODELS if m != _pinned_model]

# An empty or all-whitespace override would otherwise leave nothing to call.
if not GEMINI_MODELS:
    GEMINI_MODELS = ["gemini-flash-latest"]

# Groq sits at the bottom of the chain as a non-Google fallback. Every Gemini
# model shares Google's infrastructure, so an incident on their side takes the
# whole chain above with it. Groq is only reached once all of those have
# already failed, which keeps the digest's voice consistent on normal days.
# Leave GROQ_API_KEY unset to skip it entirely.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GROQ_MODELS", "openai/gpt-oss-120b,llama-3.3-70b-versatile"
    ).split(",")
    if m.strip()
]

# Attempts against a single model before moving down the chain. Three is
# enough to ride out a brief blip; past that the pool is genuinely saturated
# and another model is the faster route to an answer.
ATTEMPTS_PER_MODEL = 3
BACKOFF_BASE = 4
REQUEST_TIMEOUT = 90

# Ceiling on the wall-clock time all summarization may take in one run.
# Without it, a broad outage means every topic serially exhausts its own retry
# budget and the job runs until the workflow timeout kills it mid-flight,
# sending nothing at all.
TOTAL_BUDGET = 480


def build_model_chain():
    """Every (provider, model) pair to try, best first. A provider with no
    key configured is left out rather than called and rejected."""
    chain = []
    if GEMINI_API_KEY:
        chain += [("gemini", m) for m in GEMINI_MODELS]
    if GROQ_API_KEY:
        chain += [("groq", m) for m in GROQ_MODELS]
    return chain

# Persona and standing rules live in systemInstruction, not the user turn.
# Gemini processes system instructions before the request content, so they
# don't compete with the article list for attention. The rules below are
# literal numbered constraints ("at most N developments") instead of vague
# guidance ("keep it short"), since Gemini follows concrete limits more
# reliably.
SYSTEM_INSTRUCTION = """You are a careful, neutral news editor producing one \
synthesized briefing per topic for a personal daily digest. Each request \
gives you a numbered list of recent articles, possibly from several \
outlets, about one topic.

Rules, always in force:

1. Output exactly one briefing per the response schema. Never structure it \
as a list of separate per-story summaries - that is the single failure \
mode to avoid above everything else below.
2. Treat two articles as covering "the same development" when they \
describe the same underlying real-world event, decision, or announcement, \
even if worded differently or from different outlets. Fuse same-development \
articles into one thread and combine their detail into a fuller account \
(who, what, where, why it matters, what happens next) instead of repeating \
the same lead more than once.
3. Write the summary as 3-5 short paragraphs of connected prose covering \
the most significant distinct developments. Do not format it as a bulleted \
or numbered list, and do not label or number individual stories within it.
4. Use only the numbered articles given to you in this request. Do not draw \
on outside or prior knowledge of the topic, even if you believe it to be \
true or think it would round out the picture - if the given articles don't \
say it, it does not go in the briefing.
5. If articles disagree on a specific detail (a figure, a cause, an \
attribution), say so briefly rather than silently picking one version.
6. Every entry in "sources" must be the integer id of an article from the \
numbered list that you actually drew a claim from. Never invent an id, and \
never cite an id for a claim that specific article doesn't support. Choose \
your sources deliberately, typically 3-8 of them, before writing the prose \
in "summary" - do not write the narrative first and then guess citations \
for it afterward.
7. Stay neutral: describe positions and disputes rather than adjudicating \
them, and attribute opinions or claims to whoever made them instead of \
stating them as settled fact.

Example contrasting rule 1's pass and fail case, for a topic with two \
articles covering the same product launch:
- WRONG (separate story cards): "Article 1 reports Company X launched \
Y today... Article 2 reports reviewers had mixed reactions..."
- RIGHT (one synthesized thread): "Company X launched Y today, and early \
reviewer reaction has been mixed, with critics pointing to [the specific \
detail from the second article, folded into the same narrative]."
"""

BRIEF_SCHEMA = {
    "type": "OBJECT",
    "description": "One synthesized daily briefing for a single topic.",
    "properties": {
        "sources": {
            "type": "ARRAY",
            "description": (
                "The numbered articles this briefing actually draws on, "
                "typically 3-8 of them. Chosen before writing the summary, "
                "per rule 6: list every article_id a claim in the summary "
                "relies on, and no others."
            ),
            "items": {
                "type": "OBJECT",
                "properties": {
                    "article_id": {
                        "type": "INTEGER",
                        "description": "The id field of one article from the numbered list you were given.",
                    },
                },
                "required": ["article_id"],
            },
        },
        "headline": {
            "type": "STRING",
            "description": "A short, neutral section headline for the whole topic's briefing (well under 12 words).",
        },
        "summary": {
            "type": "STRING",
            "description": (
                "The synthesized briefing itself: 3-5 short paragraphs of "
                "connected prose, separated by a blank line, per rules 2-4. "
                "Not a bulleted or numbered list."
            ),
        },
    },
    "propertyOrdering": ["sources", "headline", "summary"],
    "required": ["sources", "headline", "summary"],
}


def _to_json_schema(node):
    """
    Translate the schema above into the JSON Schema dialect that
    OpenAI-compatible endpoints expect, so there's only one schema to keep
    correct. Types are uppercase in Gemini's dialect and lowercase here,
    propertyOrdering has no equivalent, and strict mode wants every property
    listed as required with additionalProperties pinned off.
    """
    if not isinstance(node, dict):
        return node

    out = {}
    for key, value in node.items():
        if key == "propertyOrdering":
            continue
        if key == "type" and isinstance(value, str):
            out["type"] = value.lower()
        elif key == "properties":
            out["properties"] = {k: _to_json_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = _to_json_schema(value)
        else:
            out[key] = value

    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}).keys())
    return out


BRIEF_SCHEMA_JSON = _to_json_schema(BRIEF_SCHEMA)


class _ModelUnusable(Exception):
    """The model name itself is rejected, so no amount of retrying helps."""


# Models rejected by name, providers whose credentials don't work, and the
# cutoff for all summarization. All three are per-process: one bad model name
# or one dead key shouldn't be re-probed once per topic. A provider failing
# auth disables only that provider, so a bad Gemini key still leaves Groq to
# fall back to.
_unusable_models = set()
_disabled_providers = set()
_deadline = None


def start_budget():
    global _deadline
    _deadline = time.monotonic() + TOTAL_BUDGET


def _budget_left():
    if _deadline is None:
        return float("inf")
    return _deadline - time.monotonic()


def _sleep_within_budget(seconds):
    """Sleep, never past the deadline. False means there's no time left."""
    left = _budget_left()
    if left <= 0:
        return False
    time.sleep(max(0.0, min(seconds, left)))
    return _budget_left() > 0


def _backoff(attempt):
    """
    Exponential with jitter. The jitter matters more than the growth here:
    a fixed schedule means every retry lands on the same congested moment,
    while an offset one has a chance of arriving after capacity frees up.
    """
    base = BACKOFF_BASE * (2 ** (attempt - 1))
    return base * (0.7 + random.random() * 0.6)


def _retry_after(resp):
    """Seconds from a Retry-After header, capped so a large value can't
    swallow the whole run's budget. None when absent or unparseable."""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), 60.0))
    except ValueError:
        return None


def _gemini_request(model, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": BRIEF_SCHEMA,
        },
    }
    return url, headers, body


def _gemini_extract(data, topic_name):
    """Pull the JSON text out of a Gemini response, or None if it was
    blocked or came back in a shape we don't recognize."""
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        print(
            f"  [warn] no candidates for {topic_name}; promptFeedback={feedback}",
            file=sys.stderr,
        )
        return None
    try:
        parts = candidates[0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as e:
        print(f"  [warn] unexpected Gemini response shape for {topic_name}: {e}", file=sys.stderr)
        return None


def _groq_request(model, prompt):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        # strict constrained decoding, so the reply matches BRIEF_SCHEMA the
        # same way Gemini's responseSchema does.
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "daily_brief",
                "strict": True,
                "schema": BRIEF_SCHEMA_JSON,
            },
        },
    }
    return GROQ_ENDPOINT, headers, body


def _groq_extract(data, topic_name):
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        print(f"  [warn] unexpected Groq response shape for {topic_name}: {e}", file=sys.stderr)
        return None


_PROVIDERS = {
    "gemini": {
        "build": _gemini_request,
        "extract": _gemini_extract,
        "auth_hint": (
            "Check that your API key is active in Google AI Studio "
            "(https://aistudio.google.com/app/apikey) and that the project "
            "isn't restricted or awaiting verification."
        ),
    },
    "groq": {
        "build": _groq_request,
        "extract": _groq_extract,
        "auth_hint": "Check GROQ_API_KEY at https://console.groq.com/keys.",
    },
}


def _call_model(provider, model, prompt, topic_name):
    """
    Run one prompt against one model, retrying only what's worth retrying.
    Returns the model's raw JSON text, or None if it didn't answer in time.
    Raises _ModelUnusable when the model name itself is the problem, so the
    caller drops it instead of trying it again on the next topic.
    """
    spec = _PROVIDERS[provider]
    url, headers, body = spec["build"](model, prompt)
    label = f"{provider}/{model}"

    for attempt in range(1, ATTEMPTS_PER_MODEL + 1):
        if _budget_left() <= 0:
            return None

        try:
            resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            print(f"  [warn] {label}: request failed ({e})", file=sys.stderr)
            if attempt == ATTEMPTS_PER_MODEL:
                return None
            if not _sleep_within_budget(_backoff(attempt)):
                return None
            continue

        code = resp.status_code

        if code == 200:
            return spec["extract"](resp.json(), topic_name)

        if code in (400, 404):
            # Wrong or retired model ID for this key. Every later call to it
            # would fail the same way.
            raise _ModelUnusable(f"{code}: {resp.text[:200]}")

        if code in (401, 403):
            # Credentials, not capacity. Disable this provider for the run
            # and let the chain fall through to the next one.
            print(
                f"  [error] {label}: {code}, credentials rejected. "
                f"{spec['auth_hint']} Skipping {provider} for the rest of "
                "this run.",
                file=sys.stderr,
            )
            _disabled_providers.add(provider)
            return None

        if code in (429, 500, 502, 503, 504):
            if attempt == ATTEMPTS_PER_MODEL:
                print(f"  [warn] {label}: {code} on the last attempt, moving on", file=sys.stderr)
                return None
            wait = _retry_after(resp) or _backoff(attempt)
            print(
                f"  [warn] {label}: {code}, retrying in {wait:.0f}s "
                f"({attempt}/{ATTEMPTS_PER_MODEL})",
                file=sys.stderr,
            )
            if not _sleep_within_budget(wait):
                return None
            continue

        print(f"  [warn] {label}: unexpected {code}: {resp.text[:200]}", file=sys.stderr)
        return None

    return None


def summarize_topic(topic_name, articles, max_developments):
    """
    Get ONE briefing per topic from the first model in the chain that answers.

    Multiple outlets covering the same development should be fused into that
    single narrative (not listed as separate summaries). Sources are cited
    by numeric id and resolved back to the real title/link/outlet from
    `articles` below, so a mistyped or invented URL can never reach the
    email. Return shape: {headline, summary, sources: [{title, link,
    outlet}, ...]} or None.
    """
    if not articles:
        return None

    # 1-indexed so article_id in the model's response maps straight back to
    # this dict. The model only ever sees id, title, outlet, and snippet,
    # never the link, so there's nothing for it to mistype or invent. The
    # real link comes back from `by_id` once the model has answered.
    by_id = {i: a for i, a in enumerate(articles, start=1)}
    numbered = [
        {
            "id": i,
            "title": a["title"],
            "outlet": a["source"],
            "snippet": (a["summary"] or "")[:500],
        }
        for i, a in by_id.items()
    ]

    # The article list comes first, and the instruction comes last with an
    # anchor phrase pointing back at it. Gemini follows an instruction
    # placed right after a large data block more reliably than one stated
    # before it.
    prompt = f"""Numbered articles for the topic "{topic_name}", most recent first:

{json.dumps(numbered, ensure_ascii=False)}

Based only on the numbered articles above, write today's "{topic_name}"
briefing per your instructions. Cover at most {max_developments} of the
most significant distinct developments, and cite each source by its
numeric id.
"""

    # Walk the chain until a model answers. Each one that doesn't costs a
    # few seconds rather than the minutes a same-model retry loop spends
    # waiting on capacity that isn't coming back.
    chain = build_model_chain()
    if not chain:
        print("  [error] no provider configured", file=sys.stderr)
        return None

    text = None
    answered_by = None
    for provider, model in chain:
        if provider in _disabled_providers or (provider, model) in _unusable_models:
            continue
        if _budget_left() <= 0:
            print(f"  [error] out of time budget before '{topic_name}'", file=sys.stderr)
            break
        try:
            text = _call_model(provider, model, prompt, topic_name)
        except _ModelUnusable as e:
            print(f"  [warn] dropping {provider}/{model} for this run ({e})", file=sys.stderr)
            _unusable_models.add((provider, model))
            continue
        if text:
            answered_by = (provider, model)
            break
        print(f"  [warn] {provider}/{model} didn't answer, trying the next model", file=sys.stderr)

    if not text:
        print(f"  [error] no model answered for topic '{topic_name}'", file=sys.stderr)
        return None

    if answered_by != chain[0]:
        print(f"  [info] answered by fallback model {answered_by[0]}/{answered_by[1]}")

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()

    try:
        brief = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [warn] could not parse the model's JSON for {topic_name}: {e}", file=sys.stderr)
        print(f"  raw text: {text[:500]}", file=sys.stderr)
        return None

    # An earlier version of the prompt returned a list of stories. Refuse
    # that shape so a list never gets emailed as separate summaries again.
    if isinstance(brief, list):
        print(
            f"  [warn] model returned a list for {topic_name}; expected one briefing object",
            file=sys.stderr,
        )
        return None
    if not isinstance(brief, dict):
        print(f"  [warn] model returned non-object JSON for {topic_name}", file=sys.stderr)
        return None

    # Cited article_ids resolve back to the article we actually fetched.
    # None of the title, link, or outlet in the email comes from Gemini's
    # own output, so a mistyped or invented link can't reach the inbox.
    sources_in = brief.get("sources") or []
    sources = []
    seen_ids = set()
    if isinstance(sources_in, list):
        for s in sources_in:
            if not isinstance(s, dict):
                continue
            try:
                article_id = int(s.get("article_id"))
            except (TypeError, ValueError):
                continue
            if article_id in seen_ids:
                continue
            article = by_id.get(article_id)
            if article is None:
                print(
                    f"  [warn] Gemini cited unknown article_id {article_id} "
                    f"for {topic_name}; dropping",
                    file=sys.stderr,
                )
                continue
            seen_ids.add(article_id)
            sources.append({
                "title": article["title"],
                "link": article["link"],
                "outlet": article["source"],
            })

    summary = str(brief.get("summary") or "").strip()
    headline = str(brief.get("headline") or topic_name).strip()
    if not summary:
        print(f"  [warn] empty summary for {topic_name}", file=sys.stderr)
        return None

    return {
        "headline": headline,
        "summary": summary,
        "sources": sources,
    }


def headlines_only_brief(articles, max_developments):
    """
    Stand-in for a topic when no model would answer. The articles are
    already fetched and their titles and links are real, so the topic can
    still carry usable news instead of dropping out of the email. Flagged
    degraded so build_html can label it rather than pass it off as a
    written brief.
    """
    picked = articles[: max(3, min(max_developments, 6))]
    if not picked:
        return None
    return {
        "headline": None,
        "summary": None,
        "degraded": True,
        "sources": [
            {"title": a["title"], "link": a["link"], "outlet": a["source"]}
            for a in picked
        ],
    }


# Muted grays and a single accent blue, deliberately not pure black or
# white. Gmail's mobile apps auto-invert colors in dark mode regardless of
# any CSS here, and extreme values invert harshest. Everything below stays
# a solid AA contrast ratio against a white card.
_FONT_STACK = (
    '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
)
_PAGE_BG = "#eef1f5"
_CARD_BG = "#ffffff"
_TEXT_HEADING = "#161a23"
_TEXT_BODY = "#39414f"
_TEXT_MUTED = "#67707d"
_ACCENT = "#2454c7"
_BORDER = "#e6e9ee"
_WARN_BG = "#fff8e6"
_WARN_BORDER = "#f0dca0"
_WARN_TEXT = "#8a6100"


def _summary_to_html(summary):
    """Turn paragraph breaks in the briefing into HTML paragraphs."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", summary) if p.strip()]
    if not parts:
        parts = [summary.strip()]
    return "".join(
        f'<p style="font-size:15px;color:{_TEXT_BODY};line-height:1.6;'
        f'margin:0 0 14px 0;">{html.escape(p)}</p>'
        for p in parts
    )


def _build_preheader(topic_results):
    """
    Short summary shown as the inbox preview line, built from whatever
    headlines actually came back this run. Capped well under what any
    client displays, so it never gets cut off mid-thought.
    """
    headlines = [
        (brief.get("headline") or "").strip()
        for _, brief, _ in topic_results
        if brief and (brief.get("headline") or "").strip()
    ]
    if not headlines:
        return "Your daily digest is ready."
    text = " • ".join(headlines)
    if len(text) > 140:
        text = text[:137].rstrip() + "..."
    return text


def build_html(topic_results, date_str):
    sections = []
    failed_topics = []
    topic_names = []
    for topic_name, brief, note in topic_results:
        if note:
            failed_topics.append(topic_name)
        if not brief:
            continue
        topic_names.append(topic_name)

        degraded = bool(brief.get("degraded"))
        if degraded:
            # No model answered for this topic, so the section carries the
            # raw headlines instead. Say so plainly rather than letting a
            # bare link list read like an editorial choice.
            headline = "Top headlines"
            summary_html = (
                f'<p style="font-size:13px;color:{_TEXT_MUTED};line-height:1.6;'
                f'margin:0 0 14px 0;">No summary was available for this topic '
                f'this run, so the latest stories are listed directly.</p>'
            )
        else:
            headline = html.escape(brief.get("headline") or topic_name)
            summary_html = _summary_to_html(brief.get("summary") or "")

        sources_html = ""
        for s in brief.get("sources") or []:
            title = html.escape(s.get("title", "(untitled)"))
            link = html.escape(s.get("link", "#"), quote=True)
            outlet = html.escape(s.get("outlet", ""))
            outlet_bit = (
                f' <span style="color:{_TEXT_MUTED};">({outlet})</span>' if outlet else ""
            )
            sources_html += (
                f'<li style="margin:0 0 7px 0;">'
                f'<span style="color:{_ACCENT};">&#8250;</span> '
                f'<a href="{link}" style="color:{_ACCENT};text-decoration:none;'
                f'font-weight:500;">{title}</a>{outlet_bit}</li>'
            )

        sources_block = ""
        if sources_html:
            list_label = "Stories" if degraded else "Sources"
            sources_block = f"""
            <div style="margin-top:16px;">
              <div style="font-size:11px;font-weight:700;letter-spacing:0.06em;
                text-transform:uppercase;color:{_TEXT_MUTED};margin-bottom:8px;">{list_label}</div>
              <ul style="margin:0;padding:0;list-style:none;font-size:13px;
                line-height:1.5;">{sources_html}</ul>
            </div>
            """

        sections.append(f"""
        <div style="padding:26px 0;border-top:1px solid {_BORDER};">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.07em;
            text-transform:uppercase;color:{_ACCENT};margin-bottom:8px;">{html.escape(topic_name)}</div>
          <div style="font-size:17px;font-weight:700;color:{_TEXT_HEADING};
            line-height:1.35;margin-bottom:12px;">{headline}</div>
          {summary_html}
          {sources_block}
        </div>
        """)

    body = "".join(sections) if sections else (
        f'<div style="padding:26px 0;border-top:1px solid {_BORDER};'
        f'font-size:15px;color:{_TEXT_BODY};">No new stories found in the '
        f'lookback window.</div>'
    )

    failure_notice = ""
    if failed_topics:
        names = ", ".join(html.escape(n) for n in failed_topics)
        failure_notice = f"""
        <div style="margin:20px 0 0 0;padding:12px 14px;background:{_WARN_BG};
          border:1px solid {_WARN_BORDER};border-radius:6px;font-size:13px;
          color:{_WARN_TEXT};">
          Skipped this run: {names}. Check the Actions log for details.
        </div>
        """

    contents_line = ""
    if topic_names:
        contents_line = (
            f'<div style="margin-top:16px;font-size:13px;color:{_TEXT_MUTED};">'
            f'In today\'s digest: {html.escape(", ".join(topic_names))}</div>'
        )

    preheader = html.escape(_build_preheader(topic_results))
    # Padding so Gmail/Outlook stop pulling trailing body text into the
    # inbox preview once the real preheader text runs out.
    preheader_pad = "&#8203;&nbsp;" * 120

    return f"""
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta name="color-scheme" content="light">
      <meta name="supported-color-schemes" content="light">
      <title>Your Daily Digest</title>
    </head>
    <body style="margin:0;padding:0;background:{_PAGE_BG};">
      <div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">
        {preheader}{preheader_pad}
      </div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
        style="background:{_PAGE_BG};">
        <tr>
          <td align="center" style="padding:28px 12px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
              style="width:100%;max-width:640px;">
              <tr>
                <td style="background:{_CARD_BG};border-radius:10px;padding:32px 28px;
                  font-family:{_FONT_STACK};">
                  <div style="font-size:23px;font-weight:700;color:{_TEXT_HEADING};
                    letter-spacing:-0.01em;">Your Daily Digest</div>
                  <div style="font-size:13px;color:{_TEXT_MUTED};margin-top:4px;">{html.escape(date_str)}</div>
                  {contents_line}
                  {failure_notice}
                  {body}
                  <div style="padding-top:22px;border-top:1px solid {_BORDER};
                    font-size:12px;color:{_TEXT_MUTED};text-align:center;">
                    Generated automatically. Edit topics.json in your repo to customize topics and sources.
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """


def send_email(subject, html_body):
    sender = os.environ["GMAIL_ADDRESS"].strip()
    # App passwords are often copied with spaces. Gmail accepts them either
    # way, but stripping avoids paste mistakes.
    app_password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    recipient = os.environ.get("RECIPIENT_EMAIL", sender).strip() or sender

    for name, value in (
        ("GMAIL_ADDRESS", sender),
        ("GMAIL_APP_PASSWORD", app_password),
        ("RECIPIENT_EMAIL", recipient),
    ):
        if not value:
            raise RuntimeError(f"{name} is empty")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=60) as server:
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())


def main():
    missing = [k for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing required env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Either provider alone is enough to write the briefs, so require one
    # rather than Gemini specifically.
    if not (GEMINI_API_KEY or GROQ_API_KEY):
        print(
            "ERROR: set GEMINI_API_KEY, GROQ_API_KEY, or both. Neither is set, "
            "so there's nothing to write the briefs with.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_config()
    settings = config.get("settings") or {}
    lookback_hours = settings.get("lookback_hours", 24)
    subject_prefix = settings.get("email_subject_prefix", "Daily Digest")
    local_tz_name = settings.get("timezone", "Australia/Sydney")

    try:
        local_tz = ZoneInfo(local_tz_name)
    except Exception:
        print(f"  [warn] unknown timezone '{local_tz_name}', falling back to UTC", file=sys.stderr)
        local_tz = timezone.utc

    topic_results = []
    start_budget()
    for topic in config["topics"]:
        name = topic.get("name") or "Untitled"
        # How many distinct developments to fold into the single topic brief.
        max_developments = topic.get("max_stories", 5)
        print(f"Fetching articles for topic: {name}")
        try:
            articles = fetch_topic_articles(topic, lookback_hours)
        except Exception as e:
            print(f"  [error] fetch failed for '{name}': {e}", file=sys.stderr)
            topic_results.append((name, None, "its feeds couldn't be fetched this run"))
            continue

        print(f"  found {len(articles)} raw articles (capped at {MAX_ARTICLES_PER_TOPIC})")

        if not articles:
            # Genuinely no new articles in the lookback window. Not a
            # failure, so no note, and no mention in the email's failure
            # notice below.
            topic_results.append((name, None, None))
            continue

        print(f"  writing one synthesized brief with {build_model_chain()[0][1]}...")
        try:
            brief = summarize_topic(name, articles, max_developments)
        except Exception as e:
            print(f"  [error] summarize failed for '{name}': {e}", file=sys.stderr)
            brief = None
        if brief:
            n_sources = len(brief.get("sources") or [])
            print(f"  got briefing with {n_sources} cited sources")
            topic_results.append((name, brief, None))
        else:
            # Nothing summarized, but the articles are in hand, so send the
            # headlines rather than an empty slot where the topic should be.
            fallback = headlines_only_brief(articles, max_developments)
            if fallback:
                print(f"  no briefing; listing {len(fallback['sources'])} headlines instead")
                topic_results.append((name, fallback, None))
            else:
                print("  got no briefing")
                topic_results.append((name, None, "no summary came back this run"))

        # Be gentle on free-tier rate limits across topics.
        time.sleep(2)

    if not any(brief for _, brief, _ in topic_results):
        print(
            "WARNING: every topic came back empty. Still sending a stub email "
            "so you notice the run happened.",
            file=sys.stderr,
        )

    now_local = datetime.now(local_tz)
    date_str = now_local.strftime("%A, %d %B %Y")
    html_body = build_html(topic_results, date_str)
    subject = f"{subject_prefix} - {date_str}"

    print("Sending email...")
    try:
        send_email(subject, html_body)
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
