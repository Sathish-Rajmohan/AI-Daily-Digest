"""The copy of each day's digest that a run keeps, and reading those copies
back for the weekly edition.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timezone

from digestlib.config import WEEKDAYS


# Where each run keeps a copy of what it sent, for the weekly edition. The
# workflow uploads this folder as an artifact.
SAVE_DIR = os.environ.get("DIGEST_SAVE_DIR", "saved-digests")


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
SAVED_NAME = re.compile(r"digest-\d{4}-\d{2}-\d{2}\.json")


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
