"""Reading topics.json, and the checks that catch mistakes in it before a run
starts.
"""

import json
import os
import re


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
