#!/usr/bin/env python3
"""Daily News Digest

Reads the feeds listed in topics.json, has a language model write a
briefing for each topic, and emails the result through Gmail. Setup is in
README.md.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Not used here. The tests fake the network, mail and backoff jitter
# through these names on digest.
import random  # noqa: F401
import smtplib  # noqa: F401

import feedparser  # noqa: F401
import requests  # noqa: F401

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
    FACT_SCHEMA, FACT_SYSTEM, SYSTEM_INSTRUCTION, _resolve_sources,
    average_sentence_length, fetch_fact_of_the_day, headlines_only_brief,
    summarize_topic,
)
from digestlib.render import (
    GMAIL_WARN_BYTES, WEEKLY, _ACCENT, _CARD_BG, _TEXT_BODY, _TEXT_HEADING, _TEXT_MUTED,
    _TOPIC_INKS, _build_preheader, _compact_html, _digest_summary, _fact_block,
    _footer_text, _paragraphs_to_html, _plural, _sources_html, _story_label,
    _summary_line, build_html, check_email_size,
)
from digestlib.amp import AMP_MAX_BYTES, _amp_copy, build_amp
from digestlib.channels.email import send_email
from digestlib.channels import channels_for, missing_secrets, send_to_channels, wants_html
from digestlib.saved import (
    SAVE_DIR, daily_skipped, digest_record, load_saved_digests, save_digest,
)
from digestlib.weekly import (
    WEEKLY_DIR, WEEKLY_HEADINGS_PER_DAY, WEEKLY_LINE_SOURCES, WEEKLY_MAX_ALSO,
    WEEKLY_THREAD_SOURCES, _day_name, _join, _week_sources, coverage_notice,
    headings_by_day, summarize_week, week_days, week_label, week_stories,
    weekly_topic_names,
)


def __getattr__(name):
    # start_budget() rebinds _deadline in models.py, so a copy taken here
    # would go stale. digest._deadline reads the current one.
    if name == "_deadline":
        return _models._deadline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Actions pipes stdout, which Python buffers, while stderr isn't buffered.
# Line buffering keeps progress lines and warnings in the order they happened.
sys.stdout.reconfigure(line_buffering=True)


def _check_environment(channels):
    """Stop before any work if a channel or every model provider isn't set up."""
    missing = missing_secrets(channels)
    for kind, names in missing:
        print(f"ERROR: missing required env vars: {', '.join(names)} (the {kind} channel needs them)",
              file=sys.stderr)
    if missing:
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
    config = load_config()
    channels = channels_for(config, "daily")
    _check_environment(channels)

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
    message = {"edition": "daily", "subject": f"{subject_prefix} - {date_str}", "date": date_str,
               "topic_results": topic_results, "fact": fact, "notices": [],
               "html": None, "amp": None}
    if wants_html(channels):
        # One setting controls the AMP copy and the checkbox folding.
        collapsible = settings.get("collapsible_stories", True)
        message["html"] = build_html(topic_results, date_str, fact, collapsible=collapsible)
        check_email_size(message["html"])
        message["amp"] = _amp_copy(topic_results, date_str, fact) if collapsible else None

    waited = _hold_until(deliver, local_tz) if deliver else 0

    if not send_to_channels(channels, message):
        print("ERROR: the digest couldn't be sent to any channel.", file=sys.stderr)
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


def weekly_main(folder=WEEKLY_DIR, any_day=False):
    """Send the weekly edition, if it's on and today is its day."""
    config = load_config()
    channels = channels_for(config, "weekly")
    _check_environment(channels)

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
    message = {"edition": "weekly", "date": label, "topic_results": topic_results, "fact": None,
               "subject": f"{weekly.get('email_subject_prefix', 'Weekly Digest')} - {label}",
               "notices": notices, "html": None, "amp": None}
    if wants_html(channels):
        collapsible = settings.get("collapsible_stories", True)
        message["html"] = build_html(topic_results, label, None, collapsible, WEEKLY, notices)
        check_email_size(message["html"])
        message["amp"] = (_amp_copy(topic_results, label, None, WEEKLY, notices)
                          if collapsible else None)

    # The weekly run starts when the daily run ends. deliver_at only holds a run
    # started that way, so a run by hand sends at once.
    deliver_at = weekly.get("deliver_at")
    if deliver_at and tz_known and os.environ.get("GITHUB_EVENT_NAME") == "workflow_run":
        hour, minute = (int(part) for part in deliver_at.split(":"))
        _hold_until(_local_time_on(today, hour, minute, local_tz), local_tz)

    if not send_to_channels(channels, message):
        print("ERROR: the weekly edition couldn't be sent to any channel.", file=sys.stderr)
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
