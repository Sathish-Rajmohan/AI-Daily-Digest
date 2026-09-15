#!/usr/bin/env python3
"""Daily News Digest

Reads the feeds listed in topics.json, has a language model write a
briefing for each topic, and emails the result through Gmail. Setup is in
README.md.
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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests
from dateutil import parser as dateparser

CONFIG_PATH = os.environ.get("DIGEST_CONFIG_PATH", "topics.json")

# Actions pipes stdout, which Python buffers, while stderr isn't buffered.
# Line buffering keeps progress lines and warnings in the order they happened.
sys.stdout.reconfigure(line_buffering=True)

# Most articles a topic can send to the model. This is well above a quiet
# topic's count, and _interleave_by_feed() decides what fits on busy days.
MAX_ARTICLES_PER_TOPIC = 100

# RSS summaries usually lead with the substance, so a short snippet leaves
# room for more articles in the prompt.
SNIPPET_CHARS = 300

FEED_FETCH_TIMEOUT = 20

# Feeds fetched at once per topic. Enough to keep a slow topic to a couple
# of timeout rounds without hitting any one host too hard.
FEED_FETCH_WORKERS = 8

# feedparser's fallback fetch in fetch_feed() takes no timeout argument and
# can hang on a bad host. It uses the socket default, so set one here.
socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)

USER_AGENT = (
    "daily-digest/1.0 (+https://github.com/Sathish-Rajmohan/AI-Daily-News-Digest; "
    "RSS reader for personal digest)"
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict) or not isinstance(config.get("topics"), list):
        raise ValueError(f"{CONFIG_PATH} must contain a 'topics' array")
    for index, topic in enumerate(config["topics"], start=1):
        _check_topic(topic, index)
    _check_settings(config.get("settings"))
    return config


# Catch mistakes in topics.json early. A feeds value written as a string, for
# example, would be read one character at a time and the topic would report
# no articles.

def _check_topic(topic, index):
    if not isinstance(topic, dict):
        raise ValueError(
            f"{CONFIG_PATH}: topic {index} must be an object with a name and a feeds list"
        )

    where = f"topic {index}"
    name = topic.get("name")
    if name is not None:
        if not isinstance(name, str):
            raise ValueError(f"{CONFIG_PATH}: {where} 'name' must be text")
        where = f"topic {index} ({name!r})"

    feeds = topic.get("feeds", [])
    if not isinstance(feeds, list) or not all(isinstance(url, str) for url in feeds):
        raise ValueError(f"{CONFIG_PATH}: {where} 'feeds' must be a list of feed URLs")

    max_stories = topic.get("max_stories", 5)
    if isinstance(max_stories, bool) or not isinstance(max_stories, int) or max_stories < 1:
        raise ValueError(f"{CONFIG_PATH}: {where} 'max_stories' must be a whole number of 1 or more")


def _check_settings(settings):
    if settings is None:
        return
    if not isinstance(settings, dict):
        raise ValueError(f"{CONFIG_PATH}: 'settings' must be an object")

    lookback = settings.get("lookback_hours", 24)
    if isinstance(lookback, bool) or not isinstance(lookback, (int, float)) or lookback <= 0:
        raise ValueError(f"{CONFIG_PATH}: 'lookback_hours' must be a number above 0")

    for key in ("fact_of_the_day", "collapsible_stories"):
        if not isinstance(settings.get(key, True), bool):
            raise ValueError(f"{CONFIG_PATH}: '{key}' must be true or false")

    for key in ("email_subject_prefix", "timezone"):
        if key in settings and not isinstance(settings[key], str):
            raise ValueError(f"{CONFIG_PATH}: '{key}' must be text")


def parse_entry_time(entry):
    """Best-effort publish time for a feedparser entry, in UTC.

    feedparser's parsed times come first because it understands zone
    abbreviations like EDT. dateutil treats those as UTC, which puts US feeds
    hours out. The raw strings are the fallback.
    """
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return datetime(*struct[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    for key in ("published", "updated", "created"):
        if key in entry:
            try:
                dt = dateparser.parse(entry[key])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (ValueError, TypeError, OverflowError):
                pass
    return None


def _clean_text(text):
    """Plain text from a feed title or summary. Feeds send these as HTML, so
    strip the tags and decode entities like &amp;.
    """
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _web_link(url):
    """The URL if it's an absolute http or https link, otherwise "". Feed links
    become hrefs in the email, so javascript:, data: and relative links are
    dropped.
    """
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() in ("http", "https") and parts.netloc:
        return url
    return ""


def fetch_feed(feed_url):
    """Fetch a feed with a timeout and User-Agent, then parse it. Calling
    feedparser.parse(url) directly can hang on a bad host.
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
        # Some hosts turn away non-browser clients but accept feedparser's own
        # request, so try that.
        print(f"  [warn] HTTP fetch failed for {feed_url}: {e}; trying feedparser", file=sys.stderr)
        return feedparser.parse(feed_url)


def _safe_fetch_feed(feed_url):
    """fetch_feed(), but logs any error and returns None so one bad feed doesn't
    stop the others.
    """
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

    # Fetched in parallel, since a long list of dead feeds could otherwise use the
    # whole run's time. Results are handled in the listed order, so duplicate
    # links resolve the same way on every run.
    with ThreadPoolExecutor(max_workers=FEED_FETCH_WORKERS) as pool:
        fetched = list(pool.map(_safe_fetch_feed, feed_urls))

    for feed_url, parsed in zip(feed_urls, fetched):
        if parsed is None:
            continue

        if getattr(parsed, "bozo", False) and not parsed.entries:
            print(f"  [warn] feed unreadable, skipping: {feed_url}", file=sys.stderr)
            continue

        # Use the host name when the feed's title is empty.
        source_name = (
            _clean_text(parsed.feed.get("title")) or urlsplit(feed_url).netloc or feed_url
        )
        from_this_feed = []

        for entry in parsed.entries:
            pub_time = parse_entry_time(entry)
            # Keep undated items. Some feeds that are otherwise fine don't date posts.
            if pub_time is not None and pub_time < cutoff:
                continue

            link = _web_link(entry.get("link"))
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)

            summary = _clean_text(entry.get("summary", "") or entry.get("description", ""))

            from_this_feed.append({
                "title": _clean_text(entry.get("title")) or "(untitled)",
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
    """Take one article from each feed in turn, newest first within each feed,
    until the cap is reached.

    Cutting a time-sorted list lets one feed that posts every few minutes fill
    every slot. Taking turns keeps every outlet in, and a big story is one that
    many outlets cover.
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

# Tried in order. A 503 means that model is out of capacity, and moving to
# another model is faster than waiting for it. Flash-Lite, at the end, often
# has capacity when the others don't.
GEMINI_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_MODELS",
        "gemini-flash-latest,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite",
    ).split(",")
    if m.strip()
]

# GEMINI_MODEL moves one model to the front and keeps the rest as fallbacks.
_pinned_model = os.environ.get("GEMINI_MODEL")
if _pinned_model:
    GEMINI_MODELS = [_pinned_model] + [m for m in GEMINI_MODELS if m != _pinned_model]

# A blank override would leave nothing to call.
if not GEMINI_MODELS:
    GEMINI_MODELS = ["gemini-flash-latest"]

# Groq runs outside Google, so it still answers during a Google-wide outage.
# It's only used after every Gemini model fails. Leave GROQ_API_KEY unset to
# skip it.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GROQ_MODELS", "openai/gpt-oss-120b,llama-3.3-70b-versatile"
    ).split(",")
    if m.strip()
]

# Attempts per model before moving on. Three is enough for a brief blip.
ATTEMPTS_PER_MODEL = 3
BACKOFF_BASE = 4
REQUEST_TIMEOUT = 90

# Some models spend output tokens on reasoning before the JSON, so leave
# plenty of room above a normal briefing's length.
MAX_OUTPUT_TOKENS = 8192

# Articles sent to each provider. Groq's free tier limits tokens per minute,
# and 100 articles would use most of a minute's allowance on one topic.
PROVIDER_ARTICLE_CAP = {"gemini": MAX_ARTICLES_PER_TOPIC, "groq": 45}

# Time limit for all model calls in a run, not counting feed fetching. In a
# broad outage it ends the run with headlines before the workflow timeout
# would kill it. Healthy runs have needed five to nine minutes.
TOTAL_BUDGET = 900


def build_model_chain():
    """Every (provider, model) pair to try, in order. Providers without a key
    are left out.
    """
    chain = []
    if GEMINI_API_KEY:
        chain += [("gemini", m) for m in GEMINI_MODELS]
    if GROQ_API_KEY:
        chain += [("groq", m) for m in GROQ_MODELS]
    return chain

# The rules go in the system instruction so the article list doesn't bury
# them. They're given as numbers ("about 15-20 words") because models follow
# numbers more closely than adjectives. Dense sentences were the most common
# problem, which is what the example at the end is for.
SYSTEM_INSTRUCTION = """You are a news editor writing a daily briefing for a \
busy general reader. Each request gives you a numbered list of recent \
articles, possibly from several outlets, about one topic. Your job is to \
make that topic easy to skim and easy to follow.

STRUCTURE

1. Write "overview" as 2-3 sentences saying what matters most in this topic \
today. Someone who reads only the overview should still come away knowing \
the day's main points. Tie the day together: say what the through-line is, \
or which one development matters most and why. Do not open with a label or \
a preamble like "Here is today's summary", and do not spend it restating \
the subheadings one by one, since the reader sees those next.
2. Write one entry in "stories" for each distinct development, up to the \
limit given in the request. Two articles describing the same underlying \
event, decision, or announcement are ONE story, not two, even when the \
outlets word it differently. Fuse them and combine their detail.
3. "subheading" states plainly what happened, in under 10 words. Write it \
the way a person would say it out loud. Good: "Ceasefire talks restart \
after a week's pause". Bad: "Geopolitical Developments Update".
4. "detail" is 2-3 short paragraphs on what happened, who it affects, and \
why it matters, or one paragraph where that is all the articles support \
(rule 14). Separate paragraphs with a blank line. Do not repeat the \
subheading as the first sentence.

LANGUAGE

5. Keep sentences short. Average about 15-20 words and never run past 25. \
One idea per sentence.
6. Use the active voice. Write "the central bank raised rates", not "rates \
were raised by the central bank".
7. Use everyday words. Where a technical term can't be avoided, \
explain it in plain words in the same sentence the first time it appears.
8. Use at most one subordinate clause per sentence. Split a long sentence \
into two, and don't join the halves with a semicolon or a dash.
9. Start each paragraph with its point and then support it. Do not build up \
to the point.

ACCURACY

10. Use only the numbered articles given to you in this request. Do not draw \
on outside or prior knowledge, even if you believe it to be true or think it \
would round out the picture. If the given articles don't say it, it does not \
go in the briefing.
11. If articles disagree on a specific detail (a figure, a cause, an \
attribution), say so briefly instead of picking one version.
12. Stay neutral. Describe positions and disputes without settling them, \
and attribute claims to whoever made them instead of stating them as fact.
13. Fill "article_ids" for a story BEFORE writing its detail. List every \
article that story draws on and no others. Never invent an id, and never \
cite an article that does not support the claim you used it for.
14. You are given short snippets, not full articles. Write what they \
support and stop there. A thin story gets one short paragraph; do not pad \
it out to three with background, restatement or hedging to reach a length. \
Two solid paragraphs beat three padded ones.

DENSITY

Density is the usual failure here, so compare these two:

- WRONG (one 36-word sentence, three ideas stacked into it): "The central \
bank, which had been widely expected to continue its easing cycle following \
three consecutive cuts, signalled a more cautious stance on Tuesday, sending \
bond yields higher as investors repriced their expectations for the year."
- RIGHT (three sentences, one idea each, 16 words on average): "The central \
bank signalled on Tuesday that it will slow down the pace of its rate cuts. \
Investors had expected another cut, because the bank had already cut three \
times in a row. Bond yields rose as those investors changed their bets for \
the rest of the year."

Both carry the same information. Write the second every time."""

# Kept shallow because Flash models get unreliable with deeply nested
# schemas. article_ids is a flat list of integers for the same reason.
#
# propertyOrdering has the model write the cited ids before the prose that
# uses them, and the stories before the overview that sums them up.
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
                            "Up to 3 short paragraphs separated by a blank "
                            "line, per rules 4 and 14, following the language "
                            "rules 5-9."
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
                "per rule 1. Written after the stories, complete enough for a "
                "reader who reads nothing else, and not a restatement of the "
                "subheadings."
            ),
        },
    },
    "propertyOrdering": ["stories", "overview"],
    "required": ["stories", "overview"],
}


def _to_json_schema(node):
    """Convert a schema from Gemini's format to the JSON Schema that
    OpenAI-compatible APIs take. Types become lowercase, propertyOrdering is
    dropped, and strict mode needs every property required with
    additionalProperties set to false.
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
    """The model name was rejected, so retrying won't help."""


# These last for the whole run, so a bad model name or key isn't retried on
# every topic. A rejected key only disables its own provider.
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


@contextmanager
def _outside_budget():
    """Time spent in this block is added back to the model budget."""
    global _deadline
    started = time.monotonic()
    try:
        yield
    finally:
        if _deadline is not None:
            _deadline += time.monotonic() - started


def _sleep_within_budget(seconds):
    """Sleep, but not past the deadline. Returns False once time has run out."""
    left = _budget_left()
    if left <= 0:
        return False
    time.sleep(max(0.0, min(seconds, left)))
    return _budget_left() > 0


def _backoff(attempt):
    """Exponential backoff with jitter, so retries from a busy moment don't all
    arrive together.
    """
    base = BACKOFF_BASE * (2 ** (attempt - 1))
    return base * (0.7 + random.random() * 0.6)


def _retry_after(resp):
    """Seconds from a Retry-After header, capped at 60. None if it's missing or
    unreadable.
    """
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), 60.0))
    except ValueError:
        return None


def _gemini_request(model, prompt, system, schema):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    # No temperature, top_p or top_k. Google advises leaving them at their
    # defaults for Gemini 3 models, and a low temperature can make them repeat
    # until the token limit cuts off the JSON.
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            # High enough that a long briefing isn't cut off mid-JSON.
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
        },
    }
    return url, headers, body


def _gemini_extract(data, topic_name):
    """The JSON text from a Gemini response, or None if the response was blocked
    or has an unexpected shape.
    """
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


def _groq_request(model, prompt, system, schema):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }
    # No temperature here either. gpt-oss expects its default, and the schema
    # fixes the structure regardless.
    body = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        # Strict mode holds the reply to the schema, like responseSchema does for
        # Gemini.
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


def _call_model(provider, model, prompt, topic_name, system, schema):
    """Send one prompt to one model, retrying errors that may clear up.

    Returns the reply's JSON text, or None if the model didn't answer in time.
    Raises _ModelUnusable when the model name is rejected, so the caller can
    drop it.
    """
    spec = _PROVIDERS[provider]
    url, headers, body = spec["build"](model, prompt, system, schema)
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
            # A proxy or captive portal can answer 200 with an HTML page. Count that as no
            # answer so the next model gets a turn.
            try:
                data = resp.json()
            except ValueError:
                print(f"  [warn] {label}: 200 but the body isn't JSON: {resp.text[:200]}", file=sys.stderr)
                return None
            if not isinstance(data, dict):
                print(f"  [warn] {label}: 200 with an unrecognized body: {resp.text[:200]}", file=sys.stderr)
                return None
            return spec["extract"](data, topic_name)

        if code in (400, 404):
            # Unknown or retired model name. Later calls would fail the same way.
            raise _ModelUnusable(f"{code}: {resp.text[:200]}")

        if code in (401, 403):
            # Bad credentials. Skip this provider for the rest of the run.
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


# The subject changes daily and repeats every fourteen days. Left to choose,
# a model keeps picking the same few well-known facts.
FACT_FIELDS = [
    "physics", "biology", "economics", "history", "psychology",
    "mathematics", "engineering", "linguistics", "geology", "medicine",
    "astronomy", "chemistry", "anthropology", "computer science",
]

# The angle is picked separately. Asking about a field the same broad way
# brings back its most famous fact, and an angle narrows the question. With
# 11 angles and 14 fields, a pairing repeats every 154 days.
#
# The angles stay everyday and concrete. Abstract ones such as "a hard limit,
# and what sets it" produced dense, jargon-heavy facts.
FACT_ANGLES = [
    "something ordinary that works differently than most people guess",
    "a number that sounds wrong but is true",
    "an everyday word or name with a surprising origin",
    "an accident or mistake that led to something people use today",
    "a common belief that is wrong, and what is actually true",
    "a record: the biggest, smallest, oldest, fastest or strangest of its kind",
    "how people did something ordinary before modern technology",
    "a surprising link between two things that seem unrelated",
    "something that happens every day that almost nobody notices",
    "a comparison of size or time that makes something easy to picture",
    "a simple reason behind something people see all the time",
]

# Word limits given in FACT_SYSTEM and checked on each reply.
FACT_MAX_WORDS = 25
EXPLANATION_MAX_WORDS = 40

# Based on research into memorable facts. A surprise lands best on something
# the reader half knows, and a number needs a familiar comparison. The WRONG
# example is a real fact the digest sent before these rules.
FACT_SYSTEM = f"""You write one fun fact a day for a curious adult reading \
their morning email. It should take about ten seconds to read, make sense on \
the first pass, and leave them knowing something new they could tell a friend.

Each request names a field and an angle. Pick a fact from that field that \
fits the angle. If nothing solid fits the angle, ignore the angle and pick \
the field's best simple fact instead.

CHOOSING THE FACT

1. Pick something the reader can picture: an object, an animal, a place, a \
person, a number, an everyday moment. Skip abstract mechanisms, theories and \
policy.
2. Make it surprising, and land the surprise on something familiar. A twist \
on something the reader already knows sparks curiosity. A fact about \
something they have never heard of does not.
3. One idea only. If the fact needs a second idea explained first, pick a \
different fact.
4. Only well-established facts. No myths, disputed claims, or things \
"everyone knows" that are really folklore. Skip the overused ones too: \
honey never spoils, octopuses have three hearts, bananas are radioactive, \
Napoleon was short, the Great Wall is visible from space.

WRITING IT

5. "fact" is one sentence of {FACT_MAX_WORDS} words or fewer that states the \
surprising thing plainly. No lead-in like "Did you know", and no \
exclamation marks.
6. "explanation" answers "how come?" in two or three short sentences, \
{EXPLANATION_MAX_WORDS} words or fewer in total, with one idea per sentence.
7. Write so a curious 12-year-old could follow every word. Use everyday \
words. Leave out technical terms and the names of concepts, and describe the \
thing itself instead. The only exception is a word most adults already know, \
like "DNA" or "gravity".
8. When a number matters, round it and compare it to something familiar, \
like "about as heavy as a car" or "longer than a football field". Leave out \
numbers that don't add to the surprise.
9. Stop once "how come?" is answered. Do not end with a lesson or a big \
claim like "this shows that".

THE DIFFERENCE

WRONG (a real fact this digest sent, shortened: jargon, three ideas, 79 words):
fact: "For decades, economists believed central banks could never cut interest \
rates below zero because depositors would withdraw their money as paper cash. \
This barrier, the zero lower bound, is actually determined by the physical \
cost of storing, insuring, and transporting large amounts of currency."
explanation: "When European and Japanese central banks introduced negative \
rates, commercial banks chose not to hoard cash. They realized that renting \
secure vaults and hiring guards cost more than paying the central bank's \
negative interest fee."

RIGHT (the same idea: one picture, plain words, 48 words):
fact: "Some European banks had to pay a fee to keep their money at the \
central bank, and most paid rather than take it out."
explanation: "The other choice was taking it out as cash. But mountains of \
banknotes need vaults, guards and insurance. That cost more than the fee."

Use the example for its style only, and do not reuse its topic."""

FACT_SCHEMA = {
    "type": "OBJECT",
    "description": "One fun fact, and how come it's true.",
    "properties": {
        "fact": {
            "type": "STRING",
            "description": (
                f"One plain sentence of {FACT_MAX_WORDS} words or fewer, per rule 5."
            ),
        },
        "explanation": {
            "type": "STRING",
            "description": (
                f"How come, in two or three short sentences of "
                f"{EXPLANATION_MAX_WORDS} words or fewer, per rules 6-9."
            ),
        },
    },
    "propertyOrdering": ["fact", "explanation"],
    "required": ["fact", "explanation"],
}


def fetch_fact_of_the_day(today):
    """The fact of the day, from the same model chain as the briefings.

    It comes from the model's own knowledge, so it has no sources. It runs
    after the topics so they use the time budget first, and returns None if no
    model answers.
    """
    ordinal = today.toordinal()
    field = FACT_FIELDS[ordinal % len(FACT_FIELDS)]
    angle = FACT_ANGLES[ordinal % len(FACT_ANGLES)]
    print(f"Fetching the fact of the day ({field})...")

    def build_prompt(_cap):
        return (
            f"Today is {today.strftime('%d %B %Y')}.\n"
            f"Field: {field}\n"
            f"Angle: {angle}\n\n"
            "Give one fact from that field, approached from that angle, "
            "chosen per your instructions."
        )

    fact = run_chain(build_prompt, FACT_SYSTEM, FACT_SCHEMA, "fact of the day")
    if not isinstance(fact, dict):
        return None

    text = str(fact.get("fact") or "").strip()
    why = str(fact.get("explanation") or "").strip()
    if not text:
        return None

    # Log the lengths so a drift back to long facts shows up.
    fact_words, why_words = len(text.split()), len(why.split())
    print(f"  fact is {fact_words} words, explanation {why_words}")
    if fact_words > FACT_MAX_WORDS or why_words > EXPLANATION_MAX_WORDS:
        print(
            f"  [warn] the fact came back longer than the prompt allows "
            f"({FACT_MAX_WORDS} and {EXPLANATION_MAX_WORDS} words)",
            file=sys.stderr,
        )
    return {"field": field, "fact": text, "why": why}


def run_chain(build_prompt, system, schema, label):
    """Try each model in the chain until one replies with valid JSON.

    `build_prompt` receives the article cap for the provider being tried, so a
    provider with a tighter limit gets a shorter list. Returns the decoded
    JSON, or None.
    """
    chain = build_model_chain()
    if not chain:
        print("  [error] no provider configured", file=sys.stderr)
        return None

    for provider, model in chain:
        if provider in _disabled_providers or (provider, model) in _unusable_models:
            continue
        if _budget_left() <= 0:
            print(f"  [error] out of time budget before '{label}'", file=sys.stderr)
            break
        try:
            prompt = build_prompt(PROVIDER_ARTICLE_CAP.get(provider, MAX_ARTICLES_PER_TOPIC))
            asked_at = time.monotonic()
            text = _call_model(provider, model, prompt, label, system, schema)
        except _ModelUnusable as e:
            print(f"  [warn] dropping {provider}/{model} for this run ({e})", file=sys.stderr)
            _unusable_models.add((provider, model))
            continue
        took = time.monotonic() - asked_at
        if not text:
            print(
                f"  [warn] {provider}/{model} didn't answer after {took:.0f}s, trying the next model",
                file=sys.stderr,
            )
            continue

        # Parse inside the loop so a reply cut off mid-JSON falls through to the next
        # model. The model stays in the chain, since a cut-off is usually a one-off.
        ok, value = _decode_reply(text)
        if not ok:
            print(
                f"  [warn] could not parse {provider}/{model}'s JSON for {label} ({value}), "
                "trying the next model",
                file=sys.stderr,
            )
            print(f"  raw text: {text[:500]}", file=sys.stderr)
            continue

        print(f"  {provider}/{model} answered in {took:.0f}s")
        if (provider, model) != chain[0]:
            print(f"  [info] answered by fallback model {provider}/{model}")
        return value

    print(f"  [error] no model answered for '{label}'", file=sys.stderr)
    return None


def _decode_reply(text):
    """(True, value) if the reply parses as JSON, else (False, error). Handles a
    markdown code fence around the JSON.
    """
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        return True, json.loads(text)
    except json.JSONDecodeError as e:
        return False, e


def summarize_topic(topic_name, articles, max_developments):
    """One briefing for a topic, from the first model that answers.

    The model cites articles by number, and titles, links and outlets are
    filled in from `articles`, so it can't invent a URL. Returns None or
    {overview, stories: [{subheading, detail, sources}, ...]}.
    """
    if not articles:
        return None

    # Numbered from 1 to match the ids the model cites. The model gets the id,
    # title, outlet and snippet, never the link.
    by_id = {i: a for i, a in enumerate(articles, start=1)}

    def build_prompt(cap):
        # A shorter list for one provider is the front of the same list, so an id
        # points at the same article whichever model answers.
        numbered = [
            {
                "id": i,
                "title": a["title"],
                "outlet": a["source"],
                "snippet": (a["summary"] or "")[:SNIPPET_CHARS],
            }
            for i, a in list(by_id.items())[:cap]
        ]

        # Data first and instructions last, with the data inside a tag. Google
        # suggests this layout for long inputs.
        #
        # Feed text could contain the closing tag and end the list early. Escaping
        # angle brackets inside the JSON leaves it identical once decoded. The topic
        # name is escaped too, because it goes in an attribute.
        outlets = len({a["source"] for a in list(by_id.values())[:cap]})
        data = (
            json.dumps(numbered, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )
        return f"""<articles topic="{html.escape(topic_name, quote=True)}" outlets="{outlets}">
{data}
</articles>

The articles above are cycled through the outlets, so every outlet is
represented and position in the list carries no ranking.

Based only on the articles above, write today's "{topic_name}" briefing per
your instructions. Give at most {max_developments} stories, most significant
first, and cite each one by the numeric ids it draws on. Judge significance
by what happened, not by where an article sits in the list. Several outlets
covering the same event is the strongest signal that it matters, so lead
with those. Keep the sentences short and plain, per language rules 5-9.
"""

    brief = run_chain(build_prompt, SYSTEM_INSTRUCTION, BRIEF_SCHEMA, topic_name)
    if brief is None:
        return None

    # A bare list isn't a briefing, so refuse it.
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
    """Mean words per sentence across a briefing's overview and story text.
    Logged on each run to check the prompt's 15-20 word target. None if there's
    no text.
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
    """Look up the articles a story cited. Titles, links and outlets come from the
    fetched articles, so nothing the model wrote becomes a link. Unknown ids
    are dropped.
    """
    sources = []
    seen = set()
    if not isinstance(article_ids, list):
        return sources

    for raw in article_ids:
        article_id = _as_article_id(raw)
        if article_id is None:
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


def _as_article_id(raw):
    """An integer id from what the model cited, or None. Accepts digit strings and
    whole-number floats. Rejects fractions and booleans, which int() would turn
    into a different id.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def headlines_only_brief(articles, max_developments):
    """A plain list of headlines for a topic no model could summarise. Marked
    degraded so the email labels it as headlines.
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


# Off-black and off-white, because Gmail's apps invert colours in dark mode
# and pure black and white invert badly. Every text colour passes WCAG AA
# against the card, and a test checks it.
#
# Each topic gets a dark, muted colour for its name, links and "Read more"
# label. The strip at the top of the email uses the same colours.
#
# Font names use single quotes because these stacks go inside double-quoted
# style attributes.
_FONT_STACK = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
# Georgia is available in every major mail app. Android doesn't have it and
# uses its own serif.
_SERIF_STACK = "Georgia,'Times New Roman',Times,serif"
_PAGE_BG = "#eef2f1"
_CARD_BG = "#fcfdfc"
_TEXT_HEADING = "#141a19"
_TEXT_BODY = "#2f3836"
_TEXT_MUTED = "#5f6a67"
_ACCENT = "#0e6a66"
_BORDER = "#e1e7e5"
_WARN_BG = "#fff8e6"
_WARN_BORDER = "#f0dca0"
_WARN_TEXT = "#8a6100"
_TOPIC_INKS = ["#0e6a66", "#9a2c3c", "#34489a", "#52661d", "#8b5a06", "#7a3e7c"]

# Words per minute for the reading-time estimate.
_READING_WPM = 230


def _topic_ink(index):
    """A topic's colour, picked by its position in topics.json so it stays the
    same from day to day.
    """
    return _TOPIC_INKS[index % len(_TOPIC_INKS)]


def _paragraphs_to_html(text, size=15, color=None, margin="0 0 12px 0"):
    """Turn blank-line paragraph breaks into HTML paragraphs."""
    color = color or _TEXT_BODY
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(
        f'<p style="font-size:{size}px;color:{color};line-height:1.6;'
        f'margin:{margin};">{html.escape(p)}</p>'
        for p in parts
    )


def _sources_html(sources, label="Sources", ink=_ACCENT):
    """The source links under a story, in the topic's colour."""
    items = ""
    for s in sources or []:
        title = html.escape(s.get("title") or "(untitled)")
        link = _web_link(s.get("link"))
        outlet = html.escape(s.get("outlet") or "")
        outlet_bit = f" ({outlet})" if outlet else ""
        # Links are checked again here, where they become hrefs. A title without a
        # usable link is still listed.
        #
        # The arrow goes inside the link and the outlet is plain text. That saves a
        # span per source, which adds up to a few KB across a full digest.
        if link:
            title_html = (
                f'<a href="{html.escape(link, quote=True)}" style="color:{ink};'
                f'text-decoration:none;font-weight:600;">&#8250;&nbsp;{title}</a>'
            )
        else:
            title_html = (
                f'<span style="color:{_TEXT_BODY};font-weight:600;">&#8250;&nbsp;{title}</span>'
            )
        items += f'<li style="margin:0 0 7px;color:{_TEXT_MUTED};">{title_html}{outlet_bit}</li>'
    if not items:
        return ""
    heading = (
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_TEXT_MUTED};margin-bottom:7px;">{label}</div>'
        if label else ""
    )
    return (
        f'<div style="margin-top:14px;">{heading}'
        f'<ul style="margin:0;padding:0;list-style:none;font-size:13px;'
        f'line-height:1.5;">{items}</ul></div>'
    )


def _build_preheader(topic_results):
    """The inbox preview text: the first subheading from each topic, capped at
    140 characters.
    """
    subheadings = []
    for _, brief, _ in topic_results:
        if not brief or brief.get("degraded"):
            continue
        for story in brief.get("stories") or []:
            sub = (story.get("subheading") or "").strip()
            if sub:
                subheadings.append(sub)
                break  # first subheading per topic
    if not subheadings:
        return "Your daily digest is ready."
    text = " • ".join(subheadings)
    if len(text) > 140:
        text = text[:137].rstrip() + "..."
    return text


def _plural(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def _story_count(brief):
    """Stories in a topic, or headlines if it's the fallback list."""
    stories = brief.get("stories") or []
    if brief.get("degraded"):
        return sum(len(s.get("sources") or []) for s in stories)
    return len(stories)


def _topic_outlets(brief):
    return {
        s["outlet"]
        for story in brief.get("stories") or []
        for s in story.get("sources") or []
        if s.get("outlet")
    }


def _topic_meta(brief):
    """The line under a topic's name, such as "8 stories · 5 outlets"."""
    count = _story_count(brief)
    if brief.get("degraded"):
        meta = _plural(count, "headline", "headlines")
    else:
        meta = _plural(count, "story", "stories")
    outlets = _topic_outlets(brief)
    if outlets:
        meta += " · " + _plural(len(outlets), "outlet", "outlets")
    return meta


def _words(text):
    return len((text or "").split())


def _digest_summary(topic_results, fact, collapsible):
    """Totals for the top of the email: stories per topic, outlets cited and
    reading time, all counted from this email. With stories folded, the reading
    time only covers what shows before anything is opened.
    """
    topics = []
    outlets = set()
    skim = _words((fact or {}).get("fact")) + _words((fact or {}).get("why"))
    full = skim
    for index, (name, brief, _note) in enumerate(topic_results):
        if not brief:
            continue
        topics.append({"index": index, "name": name, "count": _story_count(brief)})
        outlets |= _topic_outlets(brief)
        skim += _words(brief.get("overview"))
        full += _words(brief.get("overview"))
        for story in brief.get("stories") or []:
            skim += _words(story.get("subheading"))
            full += _words(story.get("subheading")) + _words(story.get("detail"))
            if brief.get("degraded"):
                titles = sum(_words(s.get("title")) for s in story.get("sources") or [])
                skim += titles
                full += titles
    words = skim if collapsible else full
    return {
        "topics": topics,
        "stories": sum(t["count"] for t in topics),
        "outlets": len(outlets),
        "minutes": max(1, round(words / _READING_WPM)),
    }


def _summary_line(summary, collapsible):
    if not summary["stories"]:
        return ""
    line = _plural(summary["stories"], "story", "stories")
    if summary["outlets"]:
        line += " from " + _plural(summary["outlets"], "outlet", "outlets")
    line += f" · about {summary['minutes']} min {'to skim' if collapsible else 'read'}"
    return line


def _footer_text(summary):
    text = "Change topics and sources in topics.json."
    if summary["outlets"]:
        text = f"Drawn from {_plural(summary['outlets'], 'outlet', 'outlets')}. {text}"
    return text


def _topic_strip_html(summary):
    """A thin bar split into topic colours, each part sized by that topic's share
    of stories. Table cells with bgcolor, so Outlook draws it.
    """
    topics = [t for t in summary["topics"] if t["count"]]
    total = sum(t["count"] for t in topics)
    if not total:
        return ""
    cells = ""
    for position, t in enumerate(topics):
        width = f"{t['count'] / total * 100:.1f}%"
        ink = _topic_ink(t["index"])
        gap = f"border-left:2px solid {_CARD_BG};" if position else ""
        cells += (
            f'<td width="{width}" height="6" bgcolor="{ink}" style="width:{width};height:6px;'
            f'background:{ink};{gap}font-size:0;line-height:0;">&nbsp;</td>'
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;margin-top:18px;border-collapse:collapse;"><tr>{cells}</tr></table>'
    )


def _topic_index_html(summary):
    """The key for the bar: each topic's colour, name and story count."""
    entries = [
        f'<span style="white-space:nowrap;margin-right:14px;">'
        f'<span style="color:{_topic_ink(t["index"])};">&#9632;</span>&nbsp;'
        f'<span style="color:{_TEXT_BODY};">{html.escape(t["name"])}</span>&nbsp;'
        f'<span style="color:{_TEXT_MUTED};">{t["count"]}</span></span>'
        for t in summary["topics"]
    ]
    if not entries:
        return ""
    return f'<div style="margin-top:10px;font-size:13px;line-height:1.9;">{" ".join(entries)}</div>'


def _fact_block(fact):
    """The fact of the day, shown after the header in large serif type."""
    if not fact:
        return ""
    why = (
        f'<p style="font-size:15px;color:{_TEXT_BODY};line-height:1.65;'
        f'margin:10px 0 0 0;">{html.escape(fact["why"])}</p>'
        if fact.get("why") else ""
    )
    return (
        f'<div style="margin-top:34px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_TEXT_MUTED};">'
        f'Fact of the day &middot;{html.escape(fact["field"])}</div>'
        f'<p style="font-family:{_SERIF_STACK};font-size:22px;line-height:1.4;'
        f'color:{_TEXT_HEADING};margin:10px 0 0 0;">{html.escape(fact["fact"])}</p>'
        f'{why}</div>'
    )


# Folding stories in the regular HTML email. Email has no JavaScript, so a
# hidden checkbox inside a <label> switches a CSS rule that hides the story.
#
# The box starts ticked and the hiding rule needs input:checked, so apps
# without :checked support (Gmail's HTML view, Outlook for Windows) show every
# story open. The label wraps the checkbox because some apps rename ids, and a
# for= link would break.
#
# Outlook.com only supports :checked on a plain element selector. There are
# no CSS comments because Yahoo skips the rule after one. The "Read more"
# label is styled here because it only appears where this CSS works.
_COLLAPSE_CSS = (
    "<style>"
    ".dd-story input:checked ~ .dd-body { display:none !important; }"
    ".dd-story input:checked ~ .dd-head .dd-more { display:inline !important;"
    " font-size:13px; font-weight:600; white-space:nowrap; }"
    "</style>"
)


def _collapsible_story_html(story, sources_label, ink=_ACCENT):
    """One story, folded to its subheading where the app supports it. A story
    with nothing under its subheading is a plain line.
    """
    label = html.escape(_story_label(story))
    body = _paragraphs_to_html((story.get("detail") or "").strip()) + _sources_html(
        story.get("sources") or [], sources_label, ink
    )
    head_style = (
        f"display:block;padding:15px 0;font-size:16px;font-weight:600;"
        f"color:{_TEXT_HEADING};line-height:1.4;"
    )
    if not body:
        return (
            f'<div style="border-top:1px solid {_BORDER};">'
            f'<div style="{head_style}">{label}</div></div>'
        )
    return (
        f'<div class="dd-story" style="border-top:1px solid {_BORDER};">'
        f'<label style="display:block;cursor:pointer;">'
        f'<input type="checkbox" checked style="display:none;mso-hide:all;">'
        f'<span class="dd-head" style="{head_style}">{label}'
        f'<span class="dd-more" style="display:none;color:{ink};"> &nbsp;+&nbsp;Read&nbsp;more</span></span>'
        f'<span class="dd-body" style="display:block;padding:0 0 16px;">{body}</span>'
        f"</label></div>"
    )


def build_html(topic_results, date_str, fact=None, collapsible=False):
    summary = _digest_summary(topic_results, fact, collapsible)
    sections = []
    failed_topics = []
    for index, (topic_name, brief, note) in enumerate(topic_results):
        if note:
            failed_topics.append(topic_name)
        if not brief:
            continue

        ink = _topic_ink(index)
        degraded = bool(brief.get("degraded"))

        # The overview comes first and is set a little larger than the stories.
        if degraded:
            overview_html = (
                f'<p style="font-size:14px;color:{_TEXT_MUTED};line-height:1.6;'
                f'margin:14px 0 0 0;">No summary was available for this topic '
                f'this run, so the latest stories are listed directly.</p>'
            )
        else:
            overview_html = _paragraphs_to_html(
                brief.get("overview") or "", size=17, color=_TEXT_HEADING, margin="14px 0 0 0"
            )

        stories_html = ""
        if collapsible and not degraded:
            folded = brief.get("stories") or []
            if folded:
                stories_html = (
                    '<div style="margin-top:18px;">'
                    + "".join(_collapsible_story_html(story, None, ink) for story in folded)
                    + "</div>"
                )
            plain_stories = []
        else:
            plain_stories = brief.get("stories") or []
        for story in plain_stories:
            subheading = (story.get("subheading") or "").strip()
            detail = (story.get("detail") or "").strip()
            sources = story.get("sources") or []

            sub_html = ""
            if subheading:
                sub_html = (
                    f'<div style="font-size:17px;font-weight:700;'
                    f'color:{_TEXT_HEADING};line-height:1.4;'
                    f'margin:0 0 8px 0;">{html.escape(subheading)}</div>'
                )

            stories_html += (
                f'<div style="margin-top:24px;">'
                f'{sub_html}'
                f'{_paragraphs_to_html(detail)}'
                f'{_sources_html(sources, "Stories" if degraded else None, ink)}'
                f'</div>'
            )

        sections.append(
            f'<div style="margin-top:36px;padding-top:30px;border-top:1px solid {_BORDER};">'
            f'<div style="font-family:{_SERIF_STACK};font-size:25px;line-height:1.2;'
            f'font-weight:700;color:{ink};">{html.escape(topic_name)}</div>'
            f'<div style="margin-top:5px;font-size:12px;color:{_TEXT_MUTED};">'
            f'{html.escape(_topic_meta(brief))}</div>'
            f'{overview_html}'
            f'{stories_html}'
            f'</div>'
        )

    body = "".join(sections) if sections else (
        f'<div style="margin-top:36px;padding-top:30px;border-top:1px solid {_BORDER};'
        f'font-size:15px;color:{_TEXT_BODY};">No new stories found in the '
        f'lookback window.</div>'
    )

    failure_notice = ""
    if failed_topics:
        names = ", ".join(html.escape(n) for n in failed_topics)
        failure_notice = f"""
        <div style="margin:28px 0 0 0;padding:12px 14px;background:{_WARN_BG};
          border:1px solid {_WARN_BORDER};border-radius:4px;font-size:13px;
          color:{_WARN_TEXT};">
          Skipped this run: {names}. Check the Actions log for details.
        </div>
        """

    summary_line = _summary_line(summary, collapsible)
    summary_html = (
        f'<div style="margin-top:8px;font-size:13px;color:{_TEXT_MUTED};">'
        f'{html.escape(summary_line)}</div>'
        if summary_line else ""
    )

    preheader = html.escape(_build_preheader(topic_results))
    # Stops Gmail and Outlook filling the rest of the preview with body text.
    preheader_pad = "&#8203;&nbsp;" * 120

    return _compact_html(f"""
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta name="color-scheme" content="light">
      <meta name="supported-color-schemes" content="light">
      <title>Daily Digest</title>
      {_COLLAPSE_CSS if collapsible else ""}
    </head>
    <body style="margin:0;padding:0;background:{_PAGE_BG};">
      <div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">
        {preheader}{preheader_pad}
      </div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
        style="background:{_PAGE_BG};">
        <tr>
          <td align="center" style="padding:24px 10px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
              style="width:100%;max-width:640px;">
              <tr>
                <td style="background:{_CARD_BG};border-radius:6px;padding:34px 30px 30px 30px;
                  text-align:left;font-family:{_FONT_STACK};color:{_TEXT_BODY};">
                  <div style="font-size:11px;font-weight:700;letter-spacing:0.14em;
                    text-transform:uppercase;color:{_TEXT_MUTED};">{html.escape(date_str)}</div>
                  <div style="font-family:{_SERIF_STACK};font-size:38px;line-height:1.1;
                    font-weight:700;letter-spacing:-0.01em;color:{_TEXT_HEADING};
                    margin-top:8px;">Daily Digest</div>
                  {summary_html}
                  {_topic_strip_html(summary)}
                  {_topic_index_html(summary)}
                  {_fact_block(fact)}
                  {failure_notice}
                  {body}
                  <div style="margin-top:36px;padding-top:20px;border-top:1px solid {_BORDER};
                    font-size:12px;line-height:1.6;color:{_TEXT_MUTED};">
                    {html.escape(_footer_text(summary))}
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


# Gmail hides HTML past about 102KB behind "View entire message". Warn a
# little before that.
GMAIL_CLIP_BYTES = 102 * 1024
GMAIL_WARN_BYTES = 92 * 1024


def _compact_html(markup):
    """Collapse the templates' whitespace. Rendering ignores it, and removing it
    saves room under Gmail's size limit.
    """
    # Replace each run with one space. Some spaces between tags are visible, as
    # in "</a> (Outlet)".
    return re.sub(r"\s+", " ", markup).strip()


def check_email_size(html_body):
    """Log how much of Gmail's size limit this email uses."""
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


# ---------------------------------------------------------------------------
# AMP version for Gmail
# ---------------------------------------------------------------------------
#
# Gmail can't fold ordinary HTML, but it folds AMP for Email built with
# amp-accordion. This copy shows each story as its subheading until tapped.
# Other apps, and Gmail after 30 days, show the regular HTML email.

# AMP's size limit. Gmail ignores a larger AMP part, so one isn't sent.
AMP_MAX_BYTES = 200_000

def _amp_ink_css():
    """CSS for each topic colour. A topic's wrapper carries its ink class."""
    rules = ""
    for i, ink in enumerate(_TOPIC_INKS):
        rules += (
            f".ink{i} .topic-name, .ink{i} .toggle, .ink{i} .source-list a, "
            f".ink{i} .arrow, .ink{i} .mark {{ color:{ink}; }}\n"
            f".bg{i} {{ background:{ink}; }}\n"
        )
    return rules


# The AMP copy uses one stylesheet. It shares the HTML version's palette and
# fonts.
_AMP_CSS = f"""
body {{ margin:0; padding:0; background:{_PAGE_BG}; font-family:{_FONT_STACK}; color:{_TEXT_BODY}; }}
.wrap {{ max-width:640px; margin:0 auto; padding:24px 10px; }}
.card {{ background:{_CARD_BG}; border-radius:6px; padding:34px 30px 30px 30px; }}
.dateline {{ font-size:11px; font-weight:700; letter-spacing:0.14em;
  text-transform:uppercase; color:{_TEXT_MUTED}; }}
.title {{ font-family:{_SERIF_STACK}; font-size:38px; line-height:1.1; font-weight:700;
  letter-spacing:-0.01em; color:{_TEXT_HEADING}; margin-top:8px; }}
.summary {{ margin-top:8px; font-size:13px; color:{_TEXT_MUTED}; }}
.strip {{ display:flex; height:6px; margin-top:18px; }}
.seg {{ height:6px; }}
.seg + .seg {{ border-left:2px solid {_CARD_BG}; }}
.index {{ margin-top:10px; font-size:13px; line-height:1.9; }}
.entry {{ white-space:nowrap; margin-right:14px; }}
.entry-name {{ color:{_TEXT_BODY}; }}
.hint {{ margin-top:2px; font-size:12px; color:{_TEXT_MUTED}; }}
.fact {{ margin-top:34px; }}
.label {{ font-size:11px; font-weight:700; letter-spacing:0.12em;
  text-transform:uppercase; color:{_TEXT_MUTED}; }}
.fact-text {{ font-family:{_SERIF_STACK}; font-size:22px; line-height:1.4;
  color:{_TEXT_HEADING}; margin:10px 0 0 0; }}
.fact-why {{ font-size:15px; color:{_TEXT_BODY}; line-height:1.65; margin:10px 0 0 0; }}
.warn {{ margin:28px 0 0 0; padding:12px 14px; background:{_WARN_BG};
  border:1px solid {_WARN_BORDER}; border-radius:4px; font-size:13px; color:{_WARN_TEXT}; }}
.topic {{ margin-top:36px; padding-top:30px; border-top:1px solid {_BORDER}; }}
.topic-name {{ font-family:{_SERIF_STACK}; font-size:25px; line-height:1.2;
  font-weight:700; color:{_ACCENT}; }}
.topic-meta {{ margin-top:5px; font-size:12px; color:{_TEXT_MUTED}; }}
.overview {{ font-size:17px; color:{_TEXT_HEADING}; line-height:1.6; margin:14px 0 0 0; }}
.note {{ font-size:14px; color:{_TEXT_MUTED}; line-height:1.6; margin:14px 0 0 0; }}
.stories {{ margin-top:18px; }}
.story {{ border-top:1px solid {_BORDER}; }}
.story > .story-head {{ display:flex; align-items:center; justify-content:space-between;
  background:{_CARD_BG}; border:0; margin:0; padding:15px 0; cursor:pointer;
  font-size:16px; font-weight:600; color:{_TEXT_HEADING}; line-height:1.4; }}
.toggle {{ flex:none; margin-left:14px; font-size:20px; font-weight:400;
  line-height:1; color:{_ACCENT}; }}
.less {{ display:none; }}
.story[expanded] .more {{ display:none; }}
.story[expanded] .less {{ display:inline; }}
.story-body {{ padding:0 0 16px 0; }}
.para {{ font-size:15px; color:{_TEXT_BODY}; line-height:1.65; margin:0 0 12px 0; }}
.sources {{ margin-top:14px; }}
.sources-label {{ font-size:10px; font-weight:700; letter-spacing:0.12em;
  text-transform:uppercase; color:{_TEXT_MUTED}; margin-bottom:7px; }}
.source-list {{ margin:0; padding:0; list-style:none; font-size:13px; line-height:1.5; }}
.source-list li {{ margin:0 0 7px 0; }}
.source-list a {{ color:{_ACCENT}; text-decoration:none; font-weight:600; }}
.plain {{ color:{_TEXT_BODY}; font-weight:600; }}
.muted {{ color:{_TEXT_MUTED}; }}
.arrow {{ color:{_ACCENT}; }}
.mark {{ color:{_ACCENT}; }}
.empty {{ margin-top:36px; padding-top:30px; border-top:1px solid {_BORDER};
  font-size:15px; color:{_TEXT_BODY}; }}
.footer {{ margin-top:36px; padding-top:20px; border-top:1px solid {_BORDER};
  font-size:12px; line-height:1.6; color:{_TEXT_MUTED}; }}
""" + _amp_ink_css()


def _amp_paragraphs(text, css_class):
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(f'<p class="{css_class}">{html.escape(p)}</p>' for p in parts)


def _amp_sources(sources, label="Sources"):
    items = ""
    for s in sources or []:
        title = html.escape(s.get("title") or "(untitled)")
        link = _web_link(s.get("link"))
        outlet = html.escape(s.get("outlet") or "")
        outlet_bit = f' <span class="muted">({outlet})</span>' if outlet else ""
        if link:
            title_html = f'<a href="{html.escape(link, quote=True)}" target="_blank">{title}</a>'
        else:
            title_html = f'<span class="plain">{title}</span>'
        items += f'<li><span class="arrow">&#8250;</span> {title_html}{outlet_bit}</li>'
    if not items:
        return ""
    heading = f'<div class="sources-label">{label}</div>' if label else ""
    return f'<div class="sources">{heading}<ul class="source-list">{items}</ul></div>'


def _amp_strip(summary):
    """The topic bar for the AMP copy. The widths change every day, so they're
    added to this email's stylesheet.
    """
    topics = [t for t in summary["topics"] if t["count"]]
    total = sum(t["count"] for t in topics)
    if not total:
        return "", ""
    segments = ""
    css = ""
    for position, t in enumerate(topics):
        ink_class = f"bg{t['index'] % len(_TOPIC_INKS)}"
        segments += f'<div class="seg s{position} {ink_class}"></div>'
        css += f".s{position} {{ width:{t['count'] / total * 100:.1f}%; }}\n"
    return f'<div class="strip">{segments}</div>', css


def _amp_index(summary):
    entries = "".join(
        f'<span class="entry ink{t["index"] % len(_TOPIC_INKS)}">'
        f'<span class="mark">&#9632;</span> '
        f'<span class="entry-name">{html.escape(t["name"])}</span> '
        f'<span class="muted">{t["count"]}</span></span>'
        for t in summary["topics"]
    )
    return f'<div class="index">{entries}</div>' if entries else ""


def _story_label(story):
    """The line a folded story shows: its subheading, or its first sentence if it
    has none.
    """
    subheading = (story.get("subheading") or "").strip()
    if subheading:
        return subheading
    detail = (story.get("detail") or "").strip()
    first = re.split(r"(?<=[.!?])\s+", detail, maxsplit=1)[0] if detail else ""
    if len(first) > 120:
        first = first[:117].rstrip() + "..."
    return first or "More on this topic"


def build_amp(topic_results, date_str, fact=None):
    """The AMP version of the email, with the same content as build_html().
    Each story's text and sources fold under its subheading. Overviews and the
    fact stay open.
    """
    summary = _digest_summary(topic_results, fact, collapsible=True)
    sections = []
    failed_topics = []
    has_accordion = False

    for index, (topic_name, brief, note) in enumerate(topic_results):
        if note:
            failed_topics.append(topic_name)
        if not brief:
            continue

        if brief.get("degraded"):
            # A headline list has nothing to fold.
            inner = (
                '<p class="note">No summary was available for this topic this run, '
                "so the latest stories are listed directly.</p>"
            )
            for story in brief.get("stories") or []:
                inner += _amp_sources(story.get("sources"), "Stories")
        else:
            inner = _amp_paragraphs(brief.get("overview"), "overview")
            rows = ""
            for story in brief.get("stories") or []:
                body = _amp_paragraphs(story.get("detail"), "para") + _amp_sources(story.get("sources"), None)
                if not body:
                    body = '<p class="para">No further detail.</p>'
                rows += (
                    '<section class="story">'
                    f'<h4 class="story-head"><span>{html.escape(_story_label(story))}</span>'
                    '<span class="toggle" aria-hidden="true">'
                    '<span class="more">+</span><span class="less">&#8722;</span></span></h4>'
                    f'<div class="story-body">{body}</div>'
                    "</section>"
                )
            if rows:
                has_accordion = True
                inner += f'<amp-accordion class="stories">{rows}</amp-accordion>'

        sections.append(
            f'<div class="topic ink{index % len(_TOPIC_INKS)}">'
            f'<div class="topic-name">{html.escape(topic_name)}</div>'
            f'<div class="topic-meta">{html.escape(_topic_meta(brief))}</div>'
            f"{inner}</div>"
        )

    body = "".join(sections) if sections else (
        '<div class="empty">No new stories found in the lookback window.</div>'
    )

    failure_notice = ""
    if failed_topics:
        names = ", ".join(html.escape(n) for n in failed_topics)
        failure_notice = (
            f'<div class="warn">Skipped this run: {names}. Check the Actions log for details.</div>'
        )

    summary_line = _summary_line(summary, collapsible=True)
    summary_html = f'<div class="summary">{html.escape(summary_line)}</div>' if summary_line else ""
    strip_html, strip_css = _amp_strip(summary)
    index_html = _amp_index(summary)
    if has_accordion:
        index_html += '<div class="hint">Tap a story to read more.</div>'

    fact_html = ""
    if fact:
        why = f'<p class="fact-why">{html.escape(fact["why"])}</p>' if fact.get("why") else ""
        fact_html = (
            f'<div class="fact"><div class="label">Fact of the day &middot;'
            f'{html.escape(fact["field"])}</div>'
            f'<p class="fact-text">{html.escape(fact["fact"])}</p>{why}</div>'
        )

    return _compact_html(f"""
    <!doctype html>
    <html amp4email data-css-strict>
    <head>
      <meta charset="utf-8">
      <script async src="https://cdn.ampproject.org/v0.js"></script>
      <script async custom-element="amp-accordion"
        src="https://cdn.ampproject.org/v0/amp-accordion-0.1.js"></script>
      <style amp4email-boilerplate>body{{visibility:hidden}}</style>
      <style amp-custom>{_AMP_CSS}{strip_css}</style>
    </head>
    <body>
      <div class="wrap"><div class="card">
        <div class="dateline">{html.escape(date_str)}</div>
        <div class="title">Daily Digest</div>
        {summary_html}
        {strip_html}
        {index_html}
        {fact_html}
        {failure_notice}
        {body}
        <div class="footer">{html.escape(_footer_text(summary))}</div>
      </div></div>
    </body>
    </html>
    """)


def send_email(subject, html_body, amp_body=None):
    sender = os.environ["GMAIL_ADDRESS"].strip()
    # App Passwords are often pasted with spaces in them.
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

    if amp_body and sender.lower() == recipient.lower():
        # Gmail ignores the AMP part when From and To are the same address.
        print(
            "  [warn] collapsible stories need RECIPIENT_EMAIL to be a different "
            "address from GMAIL_ADDRESS, or Gmail won't show them. Sending the "
            "full version only.",
            file=sys.stderr,
        )
        amp_body = None

    # Clients show the last part they support, so HTML goes last. Gmail expects
    # the AMP part before it.
    if amp_body:
        msg.attach(MIMEText(amp_body, "x-amp-html", "utf-8"))
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

    # Either provider can write the briefings.
    if not (GEMINI_API_KEY or GROQ_API_KEY):
        print(
            "ERROR: set GEMINI_API_KEY, GROQ_API_KEY, or both. Neither is set, "
            "so there's nothing to write the briefs with.",
            file=sys.stderr,
        )
        sys.exit(1)

    # A key with an empty model list would crash on the first topic, after every
    # feed had already been fetched.
    if not build_model_chain():
        print(
            "ERROR: a provider key is set but it has no models to call. Check "
            "that GEMINI_MODELS or GROQ_MODELS isn't set to an empty value.",
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
        # Most stories in this topic's briefing.
        max_developments = topic.get("max_stories", 5)
        print(f"Fetching articles for topic: {name}")
        try:
            with _outside_budget():
                articles = fetch_topic_articles(topic, lookback_hours)
        except Exception as e:
            print(f"  [error] fetch failed for '{name}': {e}", file=sys.stderr)
            topic_results.append((name, None, "its feeds couldn't be fetched this run"))
            continue

        print(f"  found {len(articles)} raw articles (capped at {MAX_ARTICLES_PER_TOPIC})")

        if not articles:
            # No new articles. That isn't a failure, so the email gets no notice.
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
            # No briefing, so list the headlines that were fetched.
            fallback = headlines_only_brief(articles, max_developments)
            if fallback:
                n_headlines = len(fallback["stories"][0]["sources"])
                print(f"  no briefing; listing {n_headlines} headlines instead")
                topic_results.append((name, fallback, None))
            else:
                print("  got no briefing")
                topic_results.append((name, None, "no summary came back this run"))

        # Pause between topics for the free tier's rate limits.
        with _outside_budget():
            time.sleep(2)

    if not any(brief for _, brief, _ in topic_results):
        print(
            "WARNING: every topic came back empty. Still sending a stub email "
            "so you notice the run happened.",
            file=sys.stderr,
        )

    now_local = datetime.now(local_tz)

    # The fact comes last so the topics use the time budget first. If it fails,
    # the email goes out without it.
    fact = None
    if settings.get("fact_of_the_day", True):
        try:
            fact = fetch_fact_of_the_day(now_local.date())
        except Exception as e:
            print(f"  [warn] fact of the day failed: {e}", file=sys.stderr)
        if fact:
            print(f"  got a fact from {fact['field']}")

    print(f"Model time used: {TOTAL_BUDGET - _budget_left():.0f}s of {TOTAL_BUDGET}s")

    date_str = now_local.strftime("%A, %d %B %Y")
    # One setting controls the AMP copy and the checkbox folding.
    collapsible = settings.get("collapsible_stories", True)
    html_body = build_html(topic_results, date_str, fact, collapsible=collapsible)
    subject = f"{subject_prefix} - {date_str}"
    check_email_size(html_body)

    amp_body = None
    if collapsible:
        amp_body = build_amp(topic_results, date_str, fact)
        amp_size = len(amp_body.encode("utf-8"))
        print(f"Collapsible version is {amp_size / 1024:.0f}KB (AMP allows {AMP_MAX_BYTES // 1000}KB)")
        if amp_size > AMP_MAX_BYTES:
            print(
                "  [warn] the collapsible version is over AMP's size limit, so Gmail "
                "would ignore it. Sending the full version only. Lower max_stories "
                "in topics.json to bring it back.",
                file=sys.stderr,
            )
            amp_body = None

    print("Sending email...")
    try:
        send_email(subject, html_body, amp_body)
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
