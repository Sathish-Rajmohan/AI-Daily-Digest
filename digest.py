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
from digestlib.briefing import (
    BRIEF_SCHEMA, EXPLANATION_MAX_WORDS, FACT_ANGLES, FACT_FIELDS, FACT_MAX_WORDS,
    FACT_SCHEMA, FACT_SYSTEM, SYSTEM_INSTRUCTION, _as_article_id, _resolve_sources,
    average_sentence_length, fetch_fact_of_the_day, headlines_only_brief,
    summarize_topic,
)
from digestlib.render import (
    DAILY, GMAIL_WARN_BYTES, WEEKLY, _ACCENT, _BORDER, _CARD_BG, _FONT_STACK, _PAGE_BG,
    _SERIF_STACK, _TEXT_BODY, _TEXT_HEADING, _TEXT_MUTED, _TOPIC_INKS, _WARN_BG,
    _WARN_BORDER, _WARN_TEXT, _build_preheader, _compact_html, _digest_summary,
    _fact_block, _footer_text, _paragraphs_to_html, _plural, _sources_html,
    _story_label, _summary_line, _topic_meta, build_html, check_email_size,
)
from digestlib.amp import AMP_MAX_BYTES, _amp_copy, build_amp


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
