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

from concurrent.futures import ThreadPoolExecutor

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

# How many articles a topic may carry into the prompt. Set well above what a
# quiet topic produces, because the cost of trimming is a story the digest
# never mentions. _interleave_by_feed() below decides which ones survive when
# a busy topic runs past this.
MAX_ARTICLES_PER_TOPIC = 100

# Per-article snippet. RSS descriptions lead with the substance, so the tail
# end is mostly boilerplate, and trimming it buys room for more articles at
# the same token cost.
SNIPPET_CHARS = 300

FEED_FETCH_TIMEOUT = 20

# Feeds per topic fetched at once. Held low enough to stay a polite client
# while keeping a topic's worst case to a couple of timeout rounds rather
# than one per feed.
FEED_FETCH_WORKERS = 8

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


def _safe_fetch_feed(feed_url):
    """fetch_feed that reports its own failure instead of raising, so one
    bad host can't take down the whole parallel batch."""
    try:
        return fetch_feed(feed_url)
    except Exception as e:
        print(f"  [warn] failed to parse feed {feed_url}: {e}", file=sys.stderr)
        return None


def fetch_topic_articles(topic, lookback_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    feed_urls = topic.get("feeds", [])
    by_feed = []
    seen_links = set()

    # Fetch in parallel, then process in the original feed order. These are
    # dozens of independent HTTP calls, and done one at a time a topic's
    # worst case is its feed count times the per-feed timeout, which on a
    # long list is the whole job's time budget spent before a single word
    # gets summarized. Only the waiting overlaps: the dedup below still runs
    # in a fixed order, so the same inputs always give the same digest.
    with ThreadPoolExecutor(max_workers=FEED_FETCH_WORKERS) as pool:
        fetched = list(pool.map(_safe_fetch_feed, feed_urls))

    for feed_url, parsed in zip(feed_urls, fetched):
        if parsed is None:
            continue

        if getattr(parsed, "bozo", False) and not parsed.entries:
            print(f"  [warn] feed unreadable, skipping: {feed_url}", file=sys.stderr)
            continue

        source_name = parsed.feed.get("title", feed_url)
        from_this_feed = []

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

            from_this_feed.append({
                "title": entry.get("title", "(untitled)"),
                "link": link,
                "summary": summary,
                "source": source_name,
                "published": pub_time.isoformat() if pub_time else None,
            })

        if from_this_feed:
            from_this_feed.sort(key=lambda a: a["published"] or "", reverse=True)
            by_feed.append(from_this_feed)

    return _interleave_by_feed(by_feed, MAX_ARTICLES_PER_TOPIC)


def _interleave_by_feed(by_feed, cap):
    """
    Take one article from each feed in turn, freshest first within a feed,
    until the cap is reached.

    Sorting everything by time and cutting at the cap loses whole outlets on
    a busy topic: one wire publishing every few minutes can fill every slot
    and push a story the rest of the world led with out of the list
    entirely. Going round the feeds instead means every outlet is
    represented before any outlet gets a second turn, which is what actually
    protects against missing a major story, since a major story is the one
    thing several outlets all cover.
    """
    picked = []
    round_index = 0
    while len(picked) < cap:
        added = False
        for feed_articles in by_feed:
            if round_index < len(feed_articles):
                picked.append(feed_articles[round_index])
                added = True
                if len(picked) >= cap:
                    break
        if not added:
            break  # every feed exhausted
        round_index += 1
    return picked


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

# Headroom for the longest topic. A briefing runs well under this, but some
# of these models spend output tokens on internal reasoning before the JSON,
# so the ceiling needs to clear both.
MAX_OUTPUT_TOKENS = 8192

# How many articles each provider is sent. Gemini has room for the full list.
# Groq's free tier meters tokens per minute rather than per request, and a
# hundred articles plus the reply would spend most of a minute's allowance on
# one topic, so the emergency path gets a shorter list. Fewer articles is a
# smaller picture, which still beats no briefing at all.
PROVIDER_ARTICLE_CAP = {"gemini": MAX_ARTICLES_PER_TOPIC, "groq": 45}

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
# don't compete with the article list for attention. The rules are literal
# numbered constraints ("about 15-20 words", "never past 25") rather than
# vague guidance ("keep it readable"), since concrete limits are followed
# far more reliably than adjectives. The worked example at the end targets
# density directly, which is the failure mode plain instructions are worst
# at preventing on their own.
SYSTEM_INSTRUCTION = """You are a news editor writing a daily briefing for a \
busy general reader. Each request gives you a numbered list of recent \
articles, possibly from several outlets, about one topic. Your job is to \
make that topic easy to skim and easy to follow.

STRUCTURE

1. Write "overview" as 2-3 sentences saying what matters most in this topic \
today. Someone who reads only the overview should still come away knowing \
the day's main points. Do not write a label or a throat-clearing preamble \
like "Here is today's summary".
2. Write one entry in "stories" for each distinct development, up to the \
limit given in the request. Two articles describing the same underlying \
event, decision, or announcement are ONE story, not two, even when the \
outlets word it differently. Fuse them and combine their detail.
3. "subheading" states plainly what happened, in under 10 words. Write it \
the way a person would say it out loud. Good: "Ceasefire talks restart \
after a week's pause". Bad: "Geopolitical Developments Update".
4. "detail" is 2-3 short paragraphs on what happened, who it affects, and \
why it matters. Separate paragraphs with a blank line. Do not repeat the \
subheading as the first sentence.

LANGUAGE

5. Keep sentences short. Average about 15-20 words and never run past 25. \
One idea per sentence.
6. Use the active voice. Write "the central bank raised rates", not "rates \
were raised by the central bank".
7. Use everyday words. Where a technical term is genuinely unavoidable, \
explain it in plain words in the same sentence the first time it appears.
8. Use at most one subordinate clause per sentence. Split a long sentence \
into two rather than joining the halves with a semicolon or a dash.
9. Start each paragraph with its point and then support it. Do not build up \
to the point.

ACCURACY

10. Use only the numbered articles given to you in this request. Do not draw \
on outside or prior knowledge, even if you believe it to be true or think it \
would round out the picture. If the given articles don't say it, it does not \
go in the briefing.
11. If articles disagree on a specific detail (a figure, a cause, an \
attribution), say so briefly rather than silently picking one version.
12. Stay neutral. Describe positions and disputes rather than settling them, \
and attribute claims to whoever made them instead of stating them as fact.
13. Fill "article_ids" for a story BEFORE writing its detail. List every \
article that story draws on and no others. Never invent an id, and never \
cite an article that does not support the claim you used it for.

THE DENSITY TO AVOID

This is the single most important thing to get right. Compare:

- WRONG (one 43-word sentence, three ideas stacked up): "The central bank, \
which had been widely expected to continue its easing cycle following three \
consecutive cuts, signalled a more cautious stance on Tuesday, sending bond \
yields higher as investors repriced their expectations for the year."
- RIGHT (three sentences, one idea each, 14 words on average): "The central \
bank signalled it will slow down its rate cuts. Investors had expected \
another cut after three in a row. Bond yields rose as they changed their \
bets for the rest of the year."

Both say the same thing. The second is the one to write, every time."""

# Kept deliberately shallow. Flash-class models get unreliable on deeply
# nested schemas (repetitive output, brackets left unclosed at the token
# limit) and Google's own docs warn that very large or deeply nested schemas
# may be rejected outright. article_ids is a flat list of integers rather
# than a list of one-field objects, which removes a nesting level from the
# old shape even though the output now carries more structure than it did.
#
# propertyOrdering makes the model fill fields in a useful order: the cited
# ids before the prose that leans on them, and the whole story list before
# the overview that summarizes it. Both are generated in the order listed.
BRIEF_SCHEMA = {
    "type": "OBJECT",
    "description": "One day's briefing for a single news topic.",
    "properties": {
        "stories": {
            "type": "ARRAY",
            "description": (
                "One entry per distinct development, most significant first. "
                "Articles covering the same underlying event belong in the "
                "same entry, per rule 2."
            ),
            "items": {
                "type": "OBJECT",
                "properties": {
                    "article_ids": {
                        "type": "ARRAY",
                        "description": (
                            "Ids of the numbered articles this story draws on, "
                            "chosen before the detail is written, per rule 13."
                        ),
                        "items": {"type": "INTEGER"},
                    },
                    "subheading": {
                        "type": "STRING",
                        "description": (
                            "What happened, stated plainly in under 10 words, "
                            "per rule 3. Not a category label."
                        ),
                    },
                    "detail": {
                        "type": "STRING",
                        "description": (
                            "2-3 short paragraphs separated by a blank line, "
                            "per rule 4, following the language rules 5-9."
                        ),
                    },
                },
                "propertyOrdering": ["article_ids", "subheading", "detail"],
                "required": ["article_ids", "subheading", "detail"],
            },
        },
        "overview": {
            "type": "STRING",
            "description": (
                "2-3 sentences on what matters most across this topic today, "
                "per rule 1. Written after the stories, and standing on its "
                "own for a reader who stops there."
            ),
        },
    },
    "propertyOrdering": ["stories", "overview"],
    "required": ["stories", "overview"],
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


def _gemini_request(model, prompt, system, schema, temperature):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
            # Set explicitly so a long topic can't run into a low default and
            # come back as JSON cut off mid-string, which is how these models
            # fail on structured output rather than with a clean error.
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
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


def _groq_request(model, prompt, system, schema, temperature):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        # strict constrained decoding, so the reply matches BRIEF_SCHEMA the
        # same way Gemini's responseSchema does.
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "reply",
                "strict": True,
                "schema": _to_json_schema(schema),
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


def _call_model(provider, model, prompt, topic_name, system, schema, temperature):
    """
    Run one prompt against one model, retrying only what's worth retrying.
    Returns the model's raw JSON text, or None if it didn't answer in time.
    Raises _ModelUnusable when the model name itself is the problem, so the
    caller drops it instead of trying it again on the next topic.
    """
    spec = _PROVIDERS[provider]
    url, headers, body = spec["build"](model, prompt, system, schema, temperature)
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


# Rotated by date so the subject changes every day and comes back around
# only after a fortnight. Left to its own devices a model gravitates to the
# same handful of physics and biology chestnuts.
FACT_FIELDS = [
    "physics", "biology", "economics", "history", "psychology",
    "mathematics", "engineering", "linguistics", "geology", "medicine",
    "astronomy", "chemistry", "anthropology", "computer science",
]

FACT_SYSTEM = """You write one fact a day for a curious, well-read adult who \
wants to finish it thinking about something they hadn't considered.

1. Pick something specific and concrete. Not a generality, not a definition.
2. Prefer the solidly established over the surprising but shaky. If a claim \
is contested, or is one of those things "everyone knows" that turns out to \
be folklore, leave it alone.
3. The reader has already seen the usual circuit: honey never spoils, \
bananas are radioactive, octopuses have three hearts, Napoleon was average \
height. Skip anything in that family. Go for what an interested amateur \
would not already have run into.
4. Write "fact" as 1-2 plain sentences. No "did you know", no exclamation \
marks, no build-up.
5. Write "why" as 2-3 sentences on what the fact explains, what it connects \
to, or what it should make the reader reconsider. This is the part that \
earns the fact its place, so do not just restate the fact in other words.
6. Same language rules as any good explainer: sentences averaging 15-20 \
words, active voice, everyday vocabulary, and any technical term explained \
in plain words the first time it appears."""

FACT_SCHEMA = {
    "type": "OBJECT",
    "description": "One fact worth knowing, and why it is worth knowing.",
    "properties": {
        "fact": {
            "type": "STRING",
            "description": "The fact itself, in 1-2 plain sentences, per rule 4.",
        },
        "why": {
            "type": "STRING",
            "description": (
                "2-3 sentences on what it explains, connects to, or overturns, "
                "per rule 5. Not a restatement of the fact."
            ),
        },
    },
    "propertyOrdering": ["fact", "why"],
    "required": ["fact", "why"],
}


def fetch_fact_of_the_day(today):
    """
    One thing worth knowing, from the same model chain as the briefings.

    Unlike everything else in the digest this isn't grounded in a fetched
    article, so it carries no sources and is the model's own knowledge.
    Runs after the topics so news always gets first call on the time budget,
    and returns None rather than holding up the email if nothing answers.
    """
    field = FACT_FIELDS[today.toordinal() % len(FACT_FIELDS)]
    print(f"Fetching the fact of the day ({field})...")

    def build_prompt(_cap):
        return (
            f"Today is {today.strftime('%d %B %Y')}. Give one fact from "
            f"{field}, chosen per your instructions."
        )

    # Warmer than the briefings. At news temperature the same few answers
    # come back for a given field no matter what day it is.
    fact = run_chain(build_prompt, FACT_SYSTEM, FACT_SCHEMA, 0.95, "fact of the day")
    if not isinstance(fact, dict):
        return None

    text = str(fact.get("fact") or "").strip()
    why = str(fact.get("why") or "").strip()
    if not text:
        return None
    return {"field": field, "fact": text, "why": why}


def run_chain(build_prompt, system, schema, temperature, label):
    """
    Walk the provider chain until a model answers, then parse its JSON.

    Each model that doesn't answer costs a few seconds rather than the
    minutes a same-model retry loop spends waiting on capacity that isn't
    coming back. `build_prompt` takes the article cap for the provider being
    tried, so a provider on a tighter token budget gets a shorter list.
    Returns the decoded object, or None.
    """
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
            print(f"  [error] out of time budget before '{label}'", file=sys.stderr)
            break
        try:
            prompt = build_prompt(PROVIDER_ARTICLE_CAP.get(provider, MAX_ARTICLES_PER_TOPIC))
            text = _call_model(provider, model, prompt, label, system, schema, temperature)
        except _ModelUnusable as e:
            print(f"  [warn] dropping {provider}/{model} for this run ({e})", file=sys.stderr)
            _unusable_models.add((provider, model))
            continue
        if text:
            answered_by = (provider, model)
            break
        print(f"  [warn] {provider}/{model} didn't answer, trying the next model", file=sys.stderr)

    if not text:
        print(f"  [error] no model answered for '{label}'", file=sys.stderr)
        return None

    if answered_by != chain[0]:
        print(f"  [info] answered by fallback model {answered_by[0]}/{answered_by[1]}")

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [warn] could not parse the model's JSON for {label}: {e}", file=sys.stderr)
        print(f"  raw text: {text[:500]}", file=sys.stderr)
        return None


def summarize_topic(topic_name, articles, max_developments):
    """
    Get ONE briefing per topic from the first model in the chain that answers.

    Multiple outlets covering the same development are fused into one story
    rather than repeated. Sources are cited by numeric id and resolved back
    to the real title/link/outlet from `articles` below, so a mistyped or
    invented URL can never reach the email. Returns None, or
    {overview, stories: [{subheading, detail, sources}, ...]}.
    """
    if not articles:
        return None

    # 1-indexed so article_id in the model's response maps straight back to
    # this dict. The model only ever sees id, title, outlet, and snippet,
    # never the link, so there's nothing for it to mistype or invent. The
    # real link comes back from `by_id` once the model has answered.
    by_id = {i: a for i, a in enumerate(articles, start=1)}

    def build_prompt(cap):
        # Ids stay stable across providers because a shorter list is just the
        # front of the same list, so an id cited by any model resolves against
        # the same by_id map.
        numbered = [
            {
                "id": i,
                "title": a["title"],
                "outlet": a["source"],
                "snippet": (a["summary"] or "")[:SNIPPET_CHARS],
            }
            for i, a in list(by_id.items())[:cap]
        ]

        # The article list comes first, and the instruction comes last with an
        # anchor phrase pointing back at it. Gemini follows an instruction
        # placed right after a large data block more reliably than one stated
        # before it.
        return f"""Numbered articles for the topic "{topic_name}", drawn from \
{len({a["source"] for a in list(by_id.values())[:cap]})} outlets and cycled \
through them so every outlet is represented:

{json.dumps(numbered, ensure_ascii=False)}

Based only on the numbered articles above, write today's "{topic_name}"
briefing per your instructions. Give at most {max_developments} stories,
most significant first, and cite each one by the numeric ids it draws on.
Judge significance by what happened, not by where an article sits in the
list. Several outlets covering the same event is the strongest signal that
it matters, so lead with those. Keep the sentences short and plain, per
language rules 5-9.
"""

    brief = run_chain(build_prompt, SYSTEM_INSTRUCTION, BRIEF_SCHEMA, 0.2, topic_name)
    if brief is None:
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

    stories = []
    for entry in brief.get("stories") or []:
        if not isinstance(entry, dict):
            continue
        subheading = str(entry.get("subheading") or "").strip()
        detail = str(entry.get("detail") or "").strip()
        if not subheading and not detail:
            continue
        stories.append({
            "subheading": subheading,
            "detail": detail,
            "sources": _resolve_sources(entry.get("article_ids"), by_id, topic_name),
        })

    overview = str(brief.get("overview") or "").strip()
    if not stories and not overview:
        print(f"  [warn] empty briefing for {topic_name}", file=sys.stderr)
        return None

    return {
        "overview": overview,
        "stories": stories,
    }


def average_sentence_length(brief):
    """
    Mean words per sentence across a briefing's prose. Readability guidance
    for general-audience news puts the target around 15-20 words, so this
    is a cheap way to see from the log whether the language rules in the
    system instruction are actually landing. Returns None with nothing to
    measure.
    """
    prose = [brief.get("overview") or ""]
    prose += [s.get("detail") or "" for s in brief.get("stories") or []]
    sentences = [
        s for s in re.split(r"(?<=[.!?])\s+", " ".join(prose).strip()) if s.strip()
    ]
    if not sentences:
        return None
    words = sum(len(s.split()) for s in sentences)
    return words / len(sentences)


def _resolve_sources(article_ids, by_id, topic_name):
    """
    Turn the ids a story cited into real articles. The model never sees a
    link, so the title, link, and outlet all come from what we fetched
    rather than from anything it wrote. An id it invented resolves to
    nothing and is dropped.
    """
    sources = []
    seen = set()
    if not isinstance(article_ids, list):
        return sources

    for raw in article_ids:
        try:
            article_id = int(raw)
        except (TypeError, ValueError):
            continue
        if article_id in seen:
            continue
        article = by_id.get(article_id)
        if article is None:
            print(
                f"  [warn] cited unknown article_id {article_id} for {topic_name}; dropping",
                file=sys.stderr,
            )
            continue
        seen.add(article_id)
        sources.append({
            "title": article["title"],
            "link": article["link"],
            "outlet": article["source"],
        })
    return sources


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
        "overview": None,
        "degraded": True,
        "stories": [{
            "subheading": None,
            "detail": None,
            "sources": [
                {"title": a["title"], "link": a["link"], "outlet": a["source"]}
                for a in picked
            ],
        }],
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
_FACT_BG = "#f5f7fc"
_FACT_BORDER = "#dde4f2"


def _paragraphs_to_html(text, size=15, color=None, margin="0 0 12px 0"):
    """Turn blank-line paragraph breaks into HTML paragraphs."""
    color = color or _TEXT_BODY
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(
        f'<p style="font-size:{size}px;color:{color};line-height:1.6;'
        f'margin:{margin};">{html.escape(p)}</p>'
        for p in parts
    )


def _sources_html(sources, label="Sources"):
    """The compact link list that sits under a story."""
    items = ""
    for s in sources or []:
        title = html.escape(s.get("title", "(untitled)"))
        link = html.escape(s.get("link", "#"), quote=True)
        outlet = html.escape(s.get("outlet", ""))
        outlet_bit = f' <span style="color:{_TEXT_MUTED};">({outlet})</span>' if outlet else ""
        items += (
            f'<li style="margin:0 0 6px 0;">'
            f'<span style="color:{_ACCENT};">&#8250;</span> '
            f'<a href="{link}" style="color:{_ACCENT};text-decoration:none;'
            f'font-weight:500;">{title}</a>{outlet_bit}</li>'
        )
    if not items:
        return ""
    return (
        f'<div style="margin-top:12px;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.06em;'
        f'text-transform:uppercase;color:{_TEXT_MUTED};margin-bottom:6px;">{label}</div>'
        f'<ul style="margin:0;padding:0;list-style:none;font-size:13px;'
        f'line-height:1.5;">{items}</ul></div>'
    )


def _build_preheader(topic_results):
    """
    Short summary shown as the inbox preview line, built from the story
    subheadings that actually came back this run. Capped well under what any
    client displays, so it never gets cut off mid-thought.
    """
    subheadings = []
    for _, brief, _ in topic_results:
        if not brief or brief.get("degraded"):
            continue
        for story in brief.get("stories") or []:
            sub = (story.get("subheading") or "").strip()
            if sub:
                subheadings.append(sub)
                break  # one per topic keeps the line varied
    if not subheadings:
        return "Your daily digest is ready."
    text = " • ".join(subheadings)
    if len(text) > 140:
        text = text[:137].rstrip() + "..."
    return text


def _fact_block(fact):
    """
    The one-a-day fact, boxed at the top of the email.

    Sits above the news on purpose. It's the part worth reading slowly, and
    it gets skipped if it's buried under five topics.
    """
    if not fact:
        return ""
    why = (
        f'<p style="font-size:14px;color:{_TEXT_BODY};line-height:1.6;'
        f'margin:8px 0 0 0;">{html.escape(fact["why"])}</p>'
        if fact.get("why") else ""
    )
    return (
        f'<div style="margin-top:20px;padding:16px 18px;background:{_FACT_BG};'
        f'border:1px solid {_FACT_BORDER};border-radius:8px;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.07em;'
        f'text-transform:uppercase;color:{_ACCENT};margin-bottom:8px;">'
        f'One thing worth knowing &middot; {html.escape(fact["field"])}</div>'
        f'<p style="font-size:15px;font-weight:600;color:{_TEXT_HEADING};'
        f'line-height:1.55;margin:0;">{html.escape(fact["fact"])}</p>'
        f'{why}</div>'
    )


def build_html(topic_results, date_str, fact=None):
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

        # The topic's own summary sits directly under the topic name, before
        # any story. A reader who stops here should still have the gist, so
        # it gets a little more weight than the body copy below it.
        if degraded:
            overview_html = (
                f'<p style="font-size:14px;color:{_TEXT_MUTED};line-height:1.6;'
                f'margin:0 0 4px 0;">No summary was available for this topic '
                f'this run, so the latest stories are listed directly.</p>'
            )
        else:
            overview_html = _paragraphs_to_html(
                brief.get("overview") or "", size=16, color=_TEXT_HEADING, margin="0 0 6px 0"
            )

        stories_html = ""
        for story in brief.get("stories") or []:
            subheading = (story.get("subheading") or "").strip()
            detail = (story.get("detail") or "").strip()
            sources = story.get("sources") or []

            sub_html = ""
            if subheading:
                sub_html = (
                    f'<div style="font-size:16px;font-weight:700;'
                    f'color:{_TEXT_HEADING};line-height:1.35;'
                    f'margin:0 0 8px 0;">{html.escape(subheading)}</div>'
                )

            stories_html += (
                f'<div style="margin-top:22px;">'
                f'{sub_html}'
                f'{_paragraphs_to_html(detail)}'
                f'{_sources_html(sources, "Stories" if degraded else "Sources")}'
                f'</div>'
            )

        sections.append(
            f'<div style="padding:26px 0;border-top:1px solid {_BORDER};">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:0.07em;'
            f'text-transform:uppercase;color:{_ACCENT};'
            f'margin-bottom:10px;">{html.escape(topic_name)}</div>'
            f'{overview_html}'
            f'{stories_html}'
            f'</div>'
        )

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

    return _compact_html(f"""
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
                  {_fact_block(fact)}
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
    """)


# Gmail stops rendering at about 102KB of HTML and hides the rest behind a
# "View entire message" link. That link still shows everything, so a long
# digest is never lost, but the reader has to go and get it. Warn a little
# early instead of at the cliff.
GMAIL_CLIP_BYTES = 102 * 1024
GMAIL_WARN_BYTES = 92 * 1024


def _compact_html(markup):
    """
    Squeeze the layout whitespace out of the templates above. HTML collapses
    runs of whitespace when rendering anyway, so this changes nothing a
    reader sees, and it buys back a meaningful share of the Gmail budget on
    a digest with a lot of topics.
    """
    # Collapse each run of whitespace to a single space rather than removing
    # it. Stripping the gap between tags outright would also eat the real
    # space in constructions like "</a> <span>(Outlet)</span>", which the
    # reader does see.
    return re.sub(r"\s+", " ", markup).strip()


def check_email_size(html_body):
    """Log how much of Gmail's clipping budget this digest uses."""
    size = len(html_body.encode("utf-8"))
    pct = size / GMAIL_CLIP_BYTES * 100
    print(f"Email is {size / 1024:.0f}KB ({pct:.0f}% of Gmail's clipping limit)")
    if size >= GMAIL_WARN_BYTES:
        print(
            f"  [warn] approaching Gmail's ~102KB limit. Past it, Gmail shows "
            f"the first part inline and puts the rest behind a "
            f"'View entire message' link. Lower max_stories in topics.json, "
            f"or drop a topic, to keep it inline.",
            file=sys.stderr,
        )
    return size


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
            stories = brief.get("stories") or []
            n_sources = sum(len(s.get("sources") or []) for s in stories)
            print(f"  got {len(stories)} stories citing {n_sources} sources")
            avg = average_sentence_length(brief)
            if avg is not None:
                flag = "" if avg <= 22 else "  [warn] denser than intended"
                print(f"  average sentence length: {avg:.0f} words{flag}")
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

    # After the topics, so a bad day for the API costs the fact rather than
    # a topic. A failure here just leaves the block out of the email.
    fact = None
    if settings.get("fact_of_the_day", True):
        try:
            fact = fetch_fact_of_the_day(now_local.date())
        except Exception as e:
            print(f"  [warn] fact of the day failed: {e}", file=sys.stderr)
        if fact:
            print(f"  got a fact from {fact['field']}")

    date_str = now_local.strftime("%A, %d %B %Y")
    html_body = build_html(topic_results, date_str, fact)
    subject = f"{subject_prefix} - {date_str}"
    check_email_size(html_body)

    print("Sending email...")
    try:
        send_email(subject, html_body)
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
