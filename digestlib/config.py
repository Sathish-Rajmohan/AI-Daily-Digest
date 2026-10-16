"""Reading topics.json, and the checks that catch mistakes in it before a run
starts.
"""

import json
import os
import re

from digestlib.channels import CHANNELS, EDITIONS


CONFIG_PATH = os.environ.get("DIGEST_CONFIG_PATH", "topics.json")

DELIVER_AT = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict) or not isinstance(config.get("topics"), list):
        raise ValueError(f"{CONFIG_PATH} must contain a 'topics' array")
    for index, topic in enumerate(config["topics"], start=1):
        _check_topic(topic, index)
    _check_settings(config.get("settings"))
    _check_weekly(config.get("weekly"))
    _check_channels(config.get("channels"), config.get("weekly") or {})
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

    deliver_at = settings.get("deliver_at")
    if deliver_at is not None and not (
        isinstance(deliver_at, str) and DELIVER_AT.fullmatch(deliver_at)
    ):
        raise ValueError(f"{CONFIG_PATH}: 'deliver_at' must be a 24-hour time such as \"08:00\"")


def _check_weekly(weekly):
    """The "weekly" block turns on the weekly edition. It's optional, and the
    edition is off without it.
    """
    if weekly is None:
        return
    if not isinstance(weekly, dict):
        raise ValueError(f"{CONFIG_PATH}: 'weekly' must be an object")

    for key in ("enabled", "skip_daily"):
        if not isinstance(weekly.get(key, False), bool):
            raise ValueError(f"{CONFIG_PATH}: 'weekly.{key}' must be true or false")

    day = weekly.get("day", "Sunday")
    if not (isinstance(day, str) and day.strip().title() in WEEKDAYS):
        raise ValueError(f"{CONFIG_PATH}: 'weekly.day' must be a day of the week, such as \"Sunday\"")

    deliver_at = weekly.get("deliver_at")
    if deliver_at is not None and not (
        isinstance(deliver_at, str) and DELIVER_AT.fullmatch(deliver_at)
    ):
        raise ValueError(f"{CONFIG_PATH}: 'weekly.deliver_at' must be a 24-hour time such as \"09:00\"")

    max_threads = weekly.get("max_threads", 3)
    if isinstance(max_threads, bool) or not isinstance(max_threads, int) or max_threads < 1:
        raise ValueError(f"{CONFIG_PATH}: 'weekly.max_threads' must be a whole number of 1 or more")

    if "email_subject_prefix" in weekly and not isinstance(weekly["email_subject_prefix"], str):
        raise ValueError(f"{CONFIG_PATH}: 'weekly.email_subject_prefix' must be text")


def _check_channels(channels, weekly):
    """The "channels" list says where the digest goes. It's optional, and the
    digest goes by email without it.
    """
    if channels is None:
        return
    if not isinstance(channels, list) or not channels:
        raise ValueError(f"{CONFIG_PATH}: 'channels' must be a list with at least one channel, "
                         'such as [{"type": "email"}]')

    seen = set()
    for index, channel in enumerate(channels, start=1):
        if not isinstance(channel, dict) or channel.get("type") not in CHANNELS:
            raise ValueError(f"{CONFIG_PATH}: channel {index} needs a 'type', one of: "
                             f"{', '.join(CHANNELS)}")
        kind = channel["type"]
        if kind in seen:
            raise ValueError(f"{CONFIG_PATH}: '{kind}' is listed twice in 'channels'")
        seen.add(kind)
        editions = channel.get("editions", list(EDITIONS))
        if not (isinstance(editions, list) and editions and all(e in EDITIONS for e in editions)):
            raise ValueError(f"{CONFIG_PATH}: the '{kind}' channel's 'editions' must list "
                             '"daily", "weekly" or both')

    # The daily runs save what the weekly edition is written from, so the
    # daily edition always needs somewhere to go.
    if not any("daily" in c.get("editions", EDITIONS) for c in channels):
        raise ValueError(f"{CONFIG_PATH}: no channel in 'channels' carries the daily edition")
    if weekly.get("enabled") and not any("weekly" in c.get("editions", EDITIONS) for c in channels):
        raise ValueError(f"{CONFIG_PATH}: the weekly edition is on, but no channel in 'channels' "
                         "carries it")
