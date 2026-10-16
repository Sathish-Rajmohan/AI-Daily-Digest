#!/usr/bin/env python3
"""Daily News Digest

Reads the feeds listed in topics.json, has a language model write a
briefing for each topic, and emails the result through Gmail. Setup is in
README.md.
"""

import argparse
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
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests
from dateutil import parser as dateparser

# The code lives in digestlib/. digest.py runs it, and keeps the names that
# the tests and run_history.py read as digest.X.
from digestlib.config import CONFIG_PATH, WEEKDAYS, load_config
from digestlib.schedule import (
    MAX_HOLD, _hold_until, _local_time_on, _local_zone, _minutes, _stamp, delivery_time,
    scheduled_start,
)
from digestlib.feeds import (
    FEED_FETCH_TIMEOUT, FEED_FETCH_WORKERS, MAX_ARTICLES_PER_TOPIC, SNIPPET_CHARS,
    USER_AGENT, _interleave_by_feed, _safe_fetch_feed, _web_link, fetch_feed,
    fetch_topic_articles, parse_entry_time,
)
from digestlib.models import (
    ATTEMPTS_PER_MODEL, GEMINI_API_KEY, GEMINI_MODELS, GROQ_API_KEY, GROQ_ENDPOINT,
    GROQ_MODELS, MAX_OUTPUT_TOKENS, PROVIDER_ARTICLE_CAP, REQUEST_TIMEOUT, TOTAL_BUDGET,
    _ModelUnusable, _backoff, _budget_left, _call_model, _disabled_providers,
    _gemini_extract, _gemini_request, _groq_extract, _groq_request, _logged_once,
    _outside_budget, _retry_after, _sleep_within_budget, _to_json_schema,
    _unusable_models, build_model_chain, run_chain, start_budget,
)
from digestlib import models as _models


def __getattr__(name):
    # start_budget() rebinds _deadline in models.py, so a copy taken here
    # would go stale. digest._deadline reads the current one.
    if name == "_deadline":
        return _models._deadline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Where each run keeps a copy of what it sent, for the weekly edition. The
# workflow uploads this folder as an artifact.
SAVE_DIR = os.environ.get("DIGEST_SAVE_DIR", "saved-digests")

# Actions pipes stdout, which Python buffers, while stderr isn't buffered.
# Line buffering keeps progress lines and warnings in the order they happened.
sys.stdout.reconfigure(line_buffering=True)

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


BRIEF_SCHEMA_JSON = _to_json_schema(BRIEF_SCHEMA)


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

# What differs between the daily email and the weekly edition. The colours,
# fonts and folding are shared.
DAILY = {
    "title": "Daily Digest",
    "ready": "Your daily digest is ready.",
    "headlines": ("No summary was available for this topic this run, so the "
                  "latest stories are listed directly."),
    "empty": "No new stories found in the lookback window.",
}
WEEKLY = {
    "title": "Weekly Digest",
    "ready": "Your weekly digest is ready.",
    "headlines": ("No summary was available for this topic this week, so the "
                  "week's stories are listed by day."),
    "empty": "No stories were saved this week.",
}


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


def _build_preheader(topic_results, ready=DAILY["ready"]):
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
        return ready
    text = " • ".join(subheadings)
    if len(text) > 140:
        text = text[:137].rstrip() + "..."
    return text


def _plural(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def _story_count(brief):
    """Stories in a topic, or headlines if it's the fallback list. The weekly
    edition's one-line stories count too.
    """
    stories = brief.get("stories") or []
    if brief.get("degraded"):
        return sum(len(s.get("sources") or []) for s in stories)
    return len(stories) + len(brief.get("also") or [])


def _topic_outlets(brief):
    return {
        s["outlet"]
        for story in (brief.get("stories") or []) + (brief.get("also") or [])
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
        for item in brief.get("also") or []:
            skim += _words(item.get("line"))
            full += _words(item.get("line"))
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


def _also_html(items, ink=_ACCENT):
    """The weekly edition's one-line stories. Each line ends with links named
    after the outlets that reported it.
    """
    rows = ""
    for item in items or []:
        line = (item.get("line") or "").strip()
        if not line:
            continue
        links = []
        for s in item.get("sources") or []:
            link = _web_link(s.get("link"))
            if link:
                name = html.escape(s.get("outlet") or s.get("title") or "Source")
                links.append(
                    f'<a href="{html.escape(link, quote=True)}" style="color:{ink};'
                    f'text-decoration:none;font-weight:600;">{name}</a>'
                )
        tail = f' &#8250;&nbsp;{" &middot; ".join(links)}' if links else ""
        rows += f'<li style="margin:0 0 9px;">{html.escape(line)}{tail}</li>'
    if not rows:
        return ""
    return (
        f'<div style="margin-top:22px;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_TEXT_MUTED};margin-bottom:9px;">Also this week</div>'
        f'<ul style="margin:0;padding:0;list-style:none;font-size:14px;line-height:1.55;'
        f'color:{_TEXT_BODY};">{rows}</ul></div>'
    )


def build_html(topic_results, date_str, fact=None, collapsible=False, edition=DAILY, notices=()):
    """The email. `edition` is DAILY or WEEKLY, and each of `notices` is shown
    in a box near the top.
    """
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
                f'margin:14px 0 0 0;">{html.escape(edition["headlines"])}</p>'
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
                f'{_sources_html(sources, "Stories" if degraded and not subheading else None, ink)}'
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
            f'{_also_html(brief.get("also"), ink)}'
            f'</div>'
        )

    body = "".join(sections) if sections else (
        f'<div style="margin-top:36px;padding-top:30px;border-top:1px solid {_BORDER};'
        f'font-size:15px;color:{_TEXT_BODY};">{html.escape(edition["empty"])}</div>'
    )

    notice_html = "".join(
        f'<div style="margin:28px 0 0 0;padding:12px 14px;background:{_WARN_BG};'
        f'border:1px solid {_WARN_BORDER};border-radius:4px;font-size:13px;'
        f'color:{_WARN_TEXT};">{html.escape(text)}</div>'
        for text in notices
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

    preheader = html.escape(_build_preheader(topic_results, edition["ready"]))
    # Stops Gmail and Outlook filling the rest of the preview with body text.
    preheader_pad = "&#8203;&nbsp;" * 120
    title = html.escape(edition["title"])

    return _compact_html(f"""
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta name="color-scheme" content="light">
      <meta name="supported-color-schemes" content="light">
      <title>{title}</title>
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
                    margin-top:8px;">{title}</div>
                  {summary_html}
                  {_topic_strip_html(summary)}
                  {_topic_index_html(summary)}
                  {_fact_block(fact)}
                  {notice_html}
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
.card {{ background:{_CARD_BG}; border-radius:6px; padding:34px 30px 30px 30px;
  overflow-wrap:anywhere; }}
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
.day {{ font-size:15px; font-weight:700; color:{_TEXT_HEADING}; margin:20px 0 0 0; }}
.also {{ margin-top:22px; }}
.also-list {{ font-size:14px; color:{_TEXT_BODY}; }}
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


def _amp_also(items):
    """The AMP copy of _also_html()."""
    rows = ""
    for item in items or []:
        line = (item.get("line") or "").strip()
        if not line:
            continue
        links = []
        for s in item.get("sources") or []:
            link = _web_link(s.get("link"))
            if link:
                name = html.escape(s.get("outlet") or s.get("title") or "Source")
                links.append(f'<a href="{html.escape(link, quote=True)}" target="_blank">{name}</a>')
        tail = f' <span class="arrow">&#8250;</span> {" &middot; ".join(links)}' if links else ""
        rows += f"<li>{html.escape(line)}{tail}</li>"
    if not rows:
        return ""
    return (
        '<div class="also"><div class="sources-label">Also this week</div>'
        f'<ul class="source-list also-list">{rows}</ul></div>'
    )


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
    # The space between entries is where the line can wrap. Each entry is
    # nowrap, so without it the whole index is one line wider than a phone.
    entries = " ".join(
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


def build_amp(topic_results, date_str, fact=None, edition=DAILY, notices=()):
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
            inner = f'<p class="note">{html.escape(edition["headlines"])}</p>'
            for story in brief.get("stories") or []:
                # The weekly edition's list has a heading for each day.
                day = (story.get("subheading") or "").strip()
                if day:
                    inner += f'<div class="day">{html.escape(day)}</div>'
                inner += _amp_sources(story.get("sources"), None if day else "Stories")
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
            inner += _amp_also(brief.get("also"))

        sections.append(
            f'<div class="topic ink{index % len(_TOPIC_INKS)}">'
            f'<div class="topic-name">{html.escape(topic_name)}</div>'
            f'<div class="topic-meta">{html.escape(_topic_meta(brief))}</div>'
            f"{inner}</div>"
        )

    body = "".join(sections) if sections else (
        f'<div class="empty">{html.escape(edition["empty"])}</div>'
    )
    notice_html = "".join(f'<div class="warn">{html.escape(text)}</div>' for text in notices)

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
        <div class="title">{html.escape(edition["title"])}</div>
        {summary_html}
        {strip_html}
        {index_html}
        {fact_html}
        {notice_html}
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


# Raised when a later change to the saved file would break a reader of the
# older ones.
SAVED_DIGEST_VERSION = 1


def digest_record(topic_results, day, fact, sent_at, scheduled):
    """What the email said, as plain data: each topic's overview and stories
    with the sources they cite, and the fact. Nothing else from the run goes
    in, so the file holds no more than the email did. `sent_at` is None for a
    digest kept back by skip_daily, which holds what the email would have said.
    """
    topics = []
    for name, brief, notice in topic_results:
        entry = {"name": name, "overview": None, "headlines_only": False, "stories": []}
        if brief:
            entry["overview"] = brief.get("overview")
            entry["headlines_only"] = bool(brief.get("degraded"))
            entry["stories"] = [
                {
                    "subheading": story.get("subheading"),
                    "detail": story.get("detail"),
                    "sources": [
                        {"title": s.get("title"), "link": s.get("link"), "outlet": s.get("outlet")}
                        for s in story.get("sources") or []
                    ],
                }
                for story in brief.get("stories") or []
            ]
        if notice:
            entry["notice"] = notice
        topics.append(entry)
    return {
        "version": SAVED_DIGEST_VERSION,
        "date": day.isoformat(),
        "sent_at": sent_at.astimezone(timezone.utc).isoformat(timespec="seconds") if sent_at else None,
        "trigger": "schedule" if scheduled else "manual",
        "topics": topics,
        "fact": fact,
    }


def daily_skipped(config, day):
    """True on the weekly edition's day when its "skip_daily" is set."""
    weekly = config.get("weekly") or {}
    return bool(
        weekly.get("enabled", False)
        and weekly.get("skip_daily", False)
        and WEEKDAYS[day.weekday()] == weekly.get("day", "Sunday").strip().title()
    )


def save_digest(record):
    """Write the record to SAVE_DIR as digest-<date>.json, replacing an earlier
    run's file for the same day. The email has already gone, so a failure here
    is only logged.
    """
    path = os.path.join(SAVE_DIR, f"digest-{record['date']}.json")
    try:
        # Encoded before the file is opened, so a failure can't leave half a file.
        text = json.dumps(record, ensure_ascii=False, indent=1)
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except (OSError, TypeError, ValueError) as e:
        print(f"  [warn] couldn't save this digest for the weekly edition: {e}", file=sys.stderr)
        return None
    print(f"Saved this digest to {path}")
    return path


def _check_environment():
    """Stop before any work if Gmail or every model provider isn't set up."""
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


def _amp_copy(topic_results, date_str, fact, edition=DAILY, notices=()):
    """The collapsible AMP copy, or None if it's over AMP's size limit."""
    amp_body = build_amp(topic_results, date_str, fact, edition, notices)
    amp_size = len(amp_body.encode("utf-8"))
    print(f"Collapsible version is {amp_size / 1024:.0f}KB (AMP allows {AMP_MAX_BYTES // 1000}KB)")
    if amp_size > AMP_MAX_BYTES:
        print(
            "  [warn] the collapsible version is over AMP's size limit, so Gmail "
            "would ignore it. Sending the full version only. Lower max_stories "
            "in topics.json to bring it back.",
            file=sys.stderr,
        )
        return None
    return amp_body


def main():
    _check_environment()

    config = load_config()
    settings = config.get("settings") or {}
    lookback_hours = settings.get("lookback_hours", 24)
    subject_prefix = settings.get("email_subject_prefix", "Daily Digest")
    local_tz, tz_known = _local_zone(settings)

    started = datetime.now(timezone.utc)
    due = None          # when the schedule meant this run to start
    deliver = None      # when to send, if deliver_at applies to this run
    deliver_at = settings.get("deliver_at")
    scheduled_run = os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    if scheduled_run:
        cron = os.environ.get("DIGEST_SCHEDULE", "").strip()
        # The cron entry runs in the workflow's time zone, which is the
        # settings one unless the workflow says otherwise.
        cron_tz = local_tz
        cron_tz_name = os.environ.get("DIGEST_SCHEDULE_TZ", "").strip()
        if cron_tz_name:
            try:
                cron_tz = ZoneInfo(cron_tz_name)
            except Exception:
                print(f"  [warn] unknown DIGEST_SCHEDULE_TZ '{cron_tz_name}', using the settings time zone",
                      file=sys.stderr)
        due = scheduled_start(cron, cron_tz, started)
        if due:
            print(f"Scheduled for {_stamp(due, local_tz)}, started {_stamp(started, local_tz)}: "
                  f"{_minutes(started - due)} min late.")
        else:
            print(f"  [warn] scheduled run, but the schedule ({cron!r}) isn't a plain daily time, "
                  "so the start can't be measured.", file=sys.stderr)
    else:
        print("Manual run, so there's no scheduled time to measure the start against.")

    if deliver_at:
        if due and tz_known:
            deliver = delivery_time(deliver_at, due, local_tz)
            # Count the window from the scheduled start and not the real one,
            # so a late start can't leave a gap after the previous digest.
            lookback_hours += max(0.0, (started - due).total_seconds()) / 3600
        elif not scheduled_run:
            print("deliver_at only applies to scheduled runs, so this one sends as soon as it's ready.")
        else:
            print("  [warn] deliver_at is set but can't be applied, so this run sends as soon "
                  "as it's ready. It needs a readable schedule and a known timezone.",
                  file=sys.stderr)

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

    # A held digest is dated for the day it's delivered, which can differ from
    # the day it was written.
    now_local = deliver.astimezone(local_tz) if deliver else datetime.now(local_tz)

    # On the weekly edition's day, skip_daily keeps this email back. The day's
    # stories are still saved, so the weekly edition covers the day.
    if daily_skipped(config, now_local.date()):
        print(f"Model time used: {TOTAL_BUDGET - _budget_left():.0f}s of {TOTAL_BUDGET}s")
        print("Today is the weekly edition's day and skip_daily is set, so no daily email is "
              "sent. The day's stories are saved for the weekly edition.")
        save_digest(digest_record(topic_results, now_local.date(), None, None, scheduled_run))
        print("Done.")
        return

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

    amp_body = _amp_copy(topic_results, date_str, fact) if collapsible else None

    waited = _hold_until(deliver, local_tz) if deliver else 0

    print("Sending email...")
    try:
        send_email(subject, html_body, amp_body)
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        sys.exit(1)
    sent = datetime.now(timezone.utc)

    save_digest(digest_record(topic_results, now_local.date(), fact, sent, scheduled_run))

    # One line per run that says how late it started, how long it waited and
    # when it sent, so the logs can be compared run to run.
    timing = f"started {_stamp(started, local_tz)}"
    if due:
        timing = f"due {_stamp(due, local_tz)}, {timing} ({_minutes(started - due)} min late)"
    timing += f", waited {_minutes(timedelta(seconds=waited))} min, sent {_stamp(sent, local_tz)}"
    if deliver:
        timing += f" ({_minutes(sent - deliver)} min after deliver_at)"
    print(f"Timing: {timing}.")
    print("Done.")


# ---------------------------------------------------------------------------
# The weekly edition
# ---------------------------------------------------------------------------
#
# Written from the week's saved daily digests, not from the feeds, because
# most feeds have dropped Monday's items by Sunday. The weekly workflow
# downloads the saved files into WEEKLY_DIR and runs `digest.py --weekly`.

WEEKLY_DIR = os.environ.get("DIGEST_WEEKLY_DIR", "weekly-input")
SAVED_NAME = re.compile(r"digest-\d{4}-\d{2}-\d{2}\.json")
WEEKLY_DAYS = 7

# Characters of each daily story's text given to the model. A provider with a
# tight token limit gets SNIPPET_CHARS instead.
WEEKLY_TEXT_CHARS = 700

# Kept low so a full week stays under Gmail's clipping limit.
WEEKLY_THREAD_SOURCES = 6
WEEKLY_LINE_SOURCES = 2
WEEKLY_MAX_ALSO = 8
WEEKLY_HEADINGS_PER_DAY = 5


def _read_saved_digest(path):
    """(date, sent_at, record) from one saved file, or None with a warning if
    it can't be used.
    """
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError) as e:
        print(f"  [warn] couldn't read {path}: {e}", file=sys.stderr)
        return None
    if not isinstance(record, dict) or not isinstance(record.get("topics"), list):
        print(f"  [warn] {path} isn't a saved digest, skipping it", file=sys.stderr)
        return None
    version = record.get("version")
    if type(version) is not int or version != SAVED_DIGEST_VERSION:
        print(f"  [warn] {path} is version {version!r}, and this script reads version "
              f"{SAVED_DIGEST_VERSION}, so it's skipped", file=sys.stderr)
        return None
    try:
        day = date.fromisoformat(str(record.get("date")))
    except ValueError:
        print(f"  [warn] {path} has no readable date, skipping it", file=sys.stderr)
        return None
    try:
        sent_at = datetime.fromisoformat(str(record.get("sent_at")))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
    except ValueError:
        sent_at = datetime.min.replace(tzinfo=timezone.utc)
    return day, sent_at, record


def load_saved_digests(folder):
    """Every saved daily digest under `folder`, as {date: record}.

    The workflow unpacks each artifact into its own subfolder, so subfolders
    are searched too. When a day was run twice, the file sent last is used.
    """
    if not os.path.isdir(folder):
        print(f"  [warn] there's no {folder} folder, so no saved digests were found", file=sys.stderr)
        return {}
    newest = {}
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            if not SAVED_NAME.fullmatch(name):
                continue
            loaded = _read_saved_digest(os.path.join(root, name))
            if loaded is None:
                continue
            day, sent_at, record = loaded
            if day not in newest or sent_at > newest[day][0]:
                newest[day] = (sent_at, record)
    return {day: record for day, (_, record) in newest.items()}


def week_days(end):
    """The WEEKLY_DAYS dates that end on `end`, oldest first."""
    return [end - timedelta(days=n) for n in range(WEEKLY_DAYS - 1, -1, -1)]


def _day_name(day):
    return f"{WEEKDAYS[day.weekday()]} {day.day} {day:%B}"


def _join(words):
    words = list(words)
    if len(words) < 2:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def week_label(first, last):
    """The week's dates, as in "12 to 18 October 2026"."""
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day} to {last.day} {last:%B %Y}"
    if first.year == last.year:
        return f"{first.day} {first:%B} to {last.day} {last:%B %Y}"
    return f"{first.day} {first:%B %Y} to {last.day} {last:%B %Y}"


def coverage_notice(days, found):
    """What the email says when some days have no saved digest, or None."""
    missing = [day for day in days if day not in found]
    if not missing:
        return None
    return (
        f"This edition covers {len(days) - len(missing)} of {len(days)} days. "
        f"No digest was saved for {_join(_day_name(day) for day in missing)}."
    )


def _saved_sources(raw):
    return [
        {"title": s.get("title"), "link": s.get("link"), "outlet": s.get("outlet")}
        for s in raw or []
        if isinstance(s, dict)
    ]


def week_stories(found, days, topic_name):
    """A topic's stories from the saved days, oldest first, each with its
    date. A day that only had headlines gives one entry per headline.
    """
    stories = []
    for day in days:
        record = found.get(day)
        if not record:
            continue
        for saved in record.get("topics") or []:
            if not isinstance(saved, dict) or saved.get("name") != topic_name:
                continue
            for story in saved.get("stories") or []:
                if not isinstance(story, dict):
                    continue
                sources = _saved_sources(story.get("sources"))
                if saved.get("headlines_only"):
                    stories += [
                        {"date": day, "subheading": str(s.get("title") or "").strip(),
                         "detail": "", "sources": [s], "headline": True}
                        for s in sources if s.get("title")
                    ]
                    continue
                subheading = str(story.get("subheading") or "").strip()
                detail = str(story.get("detail") or "").strip()
                if subheading or detail:
                    stories.append({"date": day, "subheading": subheading, "detail": detail,
                                    "sources": sources, "headline": False})
    return stories


def weekly_topic_names(config, found):
    """The topics.json topics in order, so each keeps its colour, then any
    topic that's only in the saved files, such as one renamed this week.
    """
    names = [topic.get("name") or "Untitled" for topic in config["topics"]]
    for day in sorted(found):
        for saved in found[day].get("topics") or []:
            name = saved.get("name") if isinstance(saved, dict) else None
            if isinstance(name, str) and name not in names:
                names.append(name)
    return names


WEEKLY_SYSTEM = """You are a news editor writing a weekly review for a busy \
general reader. Each request gives you one topic's stories from the past \
week's daily briefings, as a numbered list. Each entry has its day, its \
subheading and the text the daily briefing printed. Some entries are bare \
headlines. Your job is to show what mattered over the week and how the main \
stories developed.

STRUCTURE

1. "threads" are events that the list covers on two or more different days. \
Give at most the number the request allows, most significant first. Entries \
about the same event, decision or dispute belong to one thread, even when \
different days word it differently.
2. For each thread, fill "story_ids" first, with every entry the thread \
draws on and no others. "heading" says plainly what the thread is about, in \
under 10 words. "narrative" is 2-3 short paragraphs separated by a blank \
line: how it started, what changed during the week, and where it stands at \
the end. Name the days, as in "On Tuesday".
3. "also" holds the other stories worth knowing, one line each: events the \
list covers on one day only. Fill "story_ids" first. "line" is one sentence \
of at most 20 words. Leave out minor stories.
4. "overview" is 3-4 sentences on what mattered most in this topic over the \
week. Someone who reads only the overview should know the week's main \
points. Do not restate the headings one by one.

LANGUAGE

5. Keep sentences short. Average about 15-20 words and never run past 25. \
One idea per sentence.
6. Use the active voice and everyday words.

ACCURACY

7. Use only the numbered entries. They are summaries written earlier in the \
week. Add nothing from outside knowledge, even to fill a gap or explain a \
cause.
8. When a later day corrects or updates an earlier one, use the later \
version and say what changed, as in "Monday's count of 40 was revised to 52 \
on Wednesday."
9. Where entries disagree and no later one settles it, say so briefly.
10. Stay neutral, and attribute claims to whoever made them.
11. Never invent an id, and cite only entries that support what you wrote."""

_STORY_IDS = {
    "type": "ARRAY",
    "description": "Ids of the numbered entries this draws on, chosen before the text, per rule 11.",
    "items": {"type": "INTEGER"},
}

WEEKLY_SCHEMA = {
    "type": "OBJECT",
    "description": "One week's review of a single news topic.",
    "properties": {
        "threads": {
            "type": "ARRAY",
            "description": "Events covered on two or more days, most significant first, per rule 1.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "story_ids": _STORY_IDS,
                    "heading": {"type": "STRING", "description": "Under 10 words, per rule 2."},
                    "narrative": {
                        "type": "STRING",
                        "description": "2-3 short paragraphs: start, change, where it stands. Rule 2.",
                    },
                },
                "propertyOrdering": ["story_ids", "heading", "narrative"],
                "required": ["story_ids", "heading", "narrative"],
            },
        },
        "also": {
            "type": "ARRAY",
            "description": "One-line stories from a single day, per rule 3.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "story_ids": _STORY_IDS,
                    "line": {"type": "STRING", "description": "One sentence, at most 20 words."},
                },
                "propertyOrdering": ["story_ids", "line"],
                "required": ["story_ids", "line"],
            },
        },
        "overview": {
            "type": "STRING",
            "description": "3-4 sentences on the week, written last, per rule 4.",
        },
    },
    "propertyOrdering": ["threads", "also", "overview"],
    "required": ["threads", "also", "overview"],
}


def _cited_stories(story_ids, by_id, label):
    """The saved stories an entry cited, in date order. Unknown ids are dropped."""
    cited = {}
    if not isinstance(story_ids, list):
        return []
    for raw in story_ids:
        story_id = _as_article_id(raw)
        if story_id is None or story_id in cited:
            continue
        if story_id not in by_id:
            print(f"  [warn] cited unknown story_id {story_id} for {label}; dropping", file=sys.stderr)
            continue
        cited[story_id] = by_id[story_id]
    return [cited[i] for i in sorted(cited)]


def _week_sources(cited, cap):
    """Links for a thread or line, copied from the saved stories it cites. One
    from each story comes first, so every day keeps a link.
    """
    firsts = [s["sources"][0] for s in cited if s["sources"]]
    rest = [src for s in cited for src in s["sources"][1:]]
    picked, seen = [], set()
    for src in firsts + rest:
        key = src.get("link") or src.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        picked.append(dict(src))
        if len(picked) == cap:
            break
    return picked


def summarize_week(topic_name, stories, max_threads):
    """One topic's weekly review, from the first model that answers.

    The model cites the saved stories by number, and the links are copied
    from those stories, so it can't invent a URL. The days a thread ran are
    named by the script. Returns None or
    {overview, stories: [threads], also: [{line, sources}]}.
    """
    if not stories:
        return None
    by_id = {i: s for i, s in enumerate(stories, start=1)}
    label = f"{topic_name} (weekly)"

    def build_prompt(cap):
        # Every story goes in for every provider, so an id means the same story
        # whichever model answers. A tighter provider gets shorter text.
        chars = WEEKLY_TEXT_CHARS if cap >= MAX_ARTICLES_PER_TOPIC else SNIPPET_CHARS
        numbered = []
        for i, s in by_id.items():
            entry = {"id": i, "day": _day_name(s["date"]), "subheading": s["subheading"]}
            if s["headline"]:
                entry["headline_only"] = True
            else:
                entry["text"] = s["detail"][:chars]
            numbered.append(entry)
        data = (
            json.dumps(numbered, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )
        days = len({s["date"] for s in stories})
        return f"""<stories topic="{html.escape(topic_name, quote=True)}" days="{days}">
{data}
</stories>

The entries above are in date order, oldest first.

Based only on the entries above, write this week's "{topic_name}" review per
your instructions. Give at most {max_threads} threads, each drawn from
entries on two or more different days, then the one-line stories. Keep the
sentences short and plain, per rule 5.
"""

    reply = run_chain(build_prompt, WEEKLY_SYSTEM, WEEKLY_SCHEMA, label)
    if not isinstance(reply, dict):
        if reply is not None:
            print(f"  [warn] model returned non-object JSON for {label}", file=sys.stderr)
        return None

    threads, also = [], []
    for entry in reply.get("threads") or []:
        if not isinstance(entry, dict):
            continue
        cited = _cited_stories(entry.get("story_ids"), by_id, label)
        heading = str(entry.get("heading") or "").strip()
        narrative = str(entry.get("narrative") or "").strip()
        if not cited or not (heading or narrative):
            continue
        days = sorted({s["date"] for s in cited})
        if len(days) < 2 or len(threads) >= max_threads:
            # A thread needs two days and fits under the limit. Anything else
            # becomes a one-line story.
            also.append({"line": heading or _story_label({"detail": narrative}),
                         "sources": _week_sources(cited, WEEKLY_LINE_SOURCES)})
            continue
        threads.append({
            "subheading": f"{heading} · {_join(WEEKDAYS[d.weekday()][:3] for d in days)}"
                          if heading else _join(WEEKDAYS[d.weekday()] for d in days),
            "detail": narrative,
            "sources": _week_sources(cited, WEEKLY_THREAD_SOURCES),
        })

    for entry in reply.get("also") or []:
        if not isinstance(entry, dict):
            continue
        cited = _cited_stories(entry.get("story_ids"), by_id, label)
        line = str(entry.get("line") or "").strip()
        if cited and line:
            also.append({"line": line, "sources": _week_sources(cited, WEEKLY_LINE_SOURCES)})

    overview = str(reply.get("overview") or "").strip()
    if not threads and not also and not overview:
        print(f"  [warn] empty weekly review for {topic_name}", file=sys.stderr)
        return None
    return {"overview": overview, "stories": threads, "also": also[:WEEKLY_MAX_ALSO]}


def headings_by_day(stories):
    """The week's story headings for a topic no model could review, grouped by
    day, each linked to the first source its story cited. Marked degraded so
    the email labels it.
    """
    by_day = {}
    for s in stories:
        first = s["sources"][0] if s["sources"] else {}
        heading = s["subheading"] or first.get("title")
        if heading and len(by_day.setdefault(s["date"], [])) < WEEKLY_HEADINGS_PER_DAY:
            by_day[s["date"]].append(
                {"title": heading, "link": first.get("link"), "outlet": first.get("outlet")}
            )
    if not by_day:
        return None
    return {
        "overview": None,
        "degraded": True,
        "stories": [
            {"subheading": _day_name(day), "detail": None, "sources": items}
            for day, items in sorted(by_day.items())
        ],
    }


def weekly_main(folder=WEEKLY_DIR, any_day=False):
    """Send the weekly edition, if it's on and today is its day."""
    _check_environment()

    config = load_config()
    settings = config.get("settings") or {}
    weekly = config.get("weekly") or {}
    if not weekly.get("enabled", False):
        print('The weekly edition is off. Set "enabled": true in the "weekly" block of '
              "topics.json to turn it on.")
        return

    local_tz, tz_known = _local_zone(settings)
    today = datetime.now(local_tz).date()
    send_day = weekly.get("day", "Sunday").strip().title()
    if WEEKDAYS[today.weekday()] != send_day and not any_day:
        print(f"Today is {WEEKDAYS[today.weekday()]}, and the weekly edition goes out on "
              f"{send_day}. Nothing to send.")
        return

    days = week_days(today)
    label = week_label(days[0], days[-1])
    records = load_saved_digests(folder)
    found = {day: records[day] for day in days if day in records}
    missing = [_day_name(day) for day in days if day not in found]
    print(f"Found saved digests for {len(found)} of {len(days)} days ({label})."
          + (f" Missing: {_join(missing)}." if missing else ""))
    if not found:
        print(f"ERROR: no saved daily digest for {label}, so no weekly email is sent. "
              "Check that the daily runs uploaded their 'daily-digest' artifacts and that "
              "the download step found them.", file=sys.stderr)
        sys.exit(1)

    max_threads = weekly.get("max_threads", 3)
    topic_results = []
    start_budget()
    for name in weekly_topic_names(config, found):
        stories = week_stories(found, days, name)
        n_days = len({s["date"] for s in stories})
        print(f"Weekly review for topic: {name} ({len(stories)} stories over "
              f"{_plural(n_days, 'day', 'days')})")
        if not stories:
            topic_results.append((name, None, None))
            continue
        try:
            brief = summarize_week(name, stories, max_threads)
        except Exception as e:
            print(f"  [error] weekly review failed for '{name}': {e}", file=sys.stderr)
            brief = None
        if brief:
            print(f"  got {len(brief['stories'])} threads and {len(brief['also'])} one-line stories")
        else:
            brief = headings_by_day(stories)
            print("  no review; listing the week's headings by day instead")
        topic_results.append((name, brief, None))

        # Pause between topics for the free tier's rate limits.
        with _outside_budget():
            time.sleep(2)

    print(f"Model time used: {TOTAL_BUDGET - _budget_left():.0f}s of {TOTAL_BUDGET}s")

    if not any(brief for _, brief, _ in topic_results):
        print(f"ERROR: the saved digests for {label} hold no stories, so no weekly email is sent.",
              file=sys.stderr)
        sys.exit(1)

    notices = [text for text in [coverage_notice(days, found)] if text]
    collapsible = settings.get("collapsible_stories", True)
    html_body = build_html(topic_results, label, None, collapsible, WEEKLY, notices)
    subject = f"{weekly.get('email_subject_prefix', 'Weekly Digest')} - {label}"
    check_email_size(html_body)
    amp_body = _amp_copy(topic_results, label, None, WEEKLY, notices) if collapsible else None

    # The weekly run starts when the daily run ends. deliver_at only holds a run
    # started that way, so a run by hand sends at once.
    deliver_at = weekly.get("deliver_at")
    if deliver_at and tz_known and os.environ.get("GITHUB_EVENT_NAME") == "workflow_run":
        hour, minute = (int(part) for part in deliver_at.split(":"))
        _hold_until(_local_time_on(today, hour, minute, local_tz), local_tz)

    print("Sending the weekly email...")
    try:
        send_email(subject, html_body, amp_body)
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Sent the weekly edition at {_stamp(datetime.now(timezone.utc), local_tz)}.")
    print("Done.")


def cli(argv=None):
    parser = argparse.ArgumentParser(
        description="Send the daily digest, or with --weekly the weekly edition.")
    parser.add_argument("--weekly", action="store_true",
                        help="send the weekly edition, written from the saved daily digests")
    parser.add_argument("--from", dest="folder", default=WEEKLY_DIR,
                        help="folder that holds the saved digests (default: %(default)s)")
    parser.add_argument("--any-day", action="store_true",
                        help="send the weekly edition even if today isn't its day")
    args = parser.parse_args(argv)
    if args.weekly:
        weekly_main(args.folder, args.any_day)
    else:
        main()


if __name__ == "__main__":
    cli()
