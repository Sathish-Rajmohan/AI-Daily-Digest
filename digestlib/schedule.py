"""When a run was meant to start, when it should send, and the wait in between.
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


# Longest a finished run waits for deliver_at. Waiting uses Actions minutes, so
# it's capped, and the workflow's timeout has to cover it on top of a
# worst-case run.
MAX_HOLD = 180 * 60


# When a run was meant to start, and when it should send. Scheduled runs are
# set off by a cron entry that GitHub starts late, so these work out the time
# the entry was due from the entry itself. A manual run has no such time.

def _local_time_on(day, hour, minute, tz):
    """That clock time on that date in tz, as UTC. The conversion has to come
    first, because subtracting two datetimes that share one ZoneInfo ignores
    daylight saving.
    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)


def scheduled_start(cron, tz, now):
    """When the cron entry that started this run was due, in UTC. This is the
    daily occurrence nearest to `now`, so a start a few seconds early still
    counts as today's. None unless the entry is a plain daily time like
    "0 8 * * *".
    """
    fields = (cron or "").split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return None
    if not (fields[0].isdigit() and fields[1].isdigit()):
        return None
    minute, hour = int(fields[0]), int(fields[1])
    if minute > 59 or hour > 23:
        return None
    now = now.astimezone(timezone.utc)
    today = now.astimezone(tz).date()
    due = [_local_time_on(today + timedelta(days=d), hour, minute, tz) for d in (-1, 0, 1)]
    return min(due, key=lambda t: abs(t - now))


def delivery_time(deliver_at, scheduled, tz):
    """The first deliver_at ("HH:MM" in tz) at or after the scheduled start, in UTC."""
    hour, minute = (int(part) for part in deliver_at.split(":"))
    day = scheduled.astimezone(tz).date()
    due = _local_time_on(day, hour, minute, tz)
    if due < scheduled:
        due = _local_time_on(day + timedelta(days=1), hour, minute, tz)
    return due


def _minutes(delta):
    return round(delta.total_seconds() / 60)


def _stamp(moment, tz):
    return moment.astimezone(tz).strftime("%a %d %b %H:%M %Z")


def _local_zone(settings):
    """The time zone from settings, and whether it was known. UTC if it wasn't."""
    name = settings.get("timezone", "Australia/Sydney")
    try:
        return ZoneInfo(name), True
    except Exception:
        print(f"  [warn] unknown timezone '{name}', falling back to UTC", file=sys.stderr)
        return timezone.utc, False


def _hold_until(deliver, local_tz):
    """Wait until `deliver`, for at most MAX_HOLD. Returns the seconds waited."""
    left = (deliver - datetime.now(timezone.utc)).total_seconds()
    if left <= 0:
        print(f"Ready {_minutes(timedelta(seconds=-left))} min after deliver_at. Sending now.")
        return 0
    wait = min(left, MAX_HOLD)
    if wait < left:
        print(f"  [warn] deliver_at is {left / 60:.0f} min away, past the {MAX_HOLD // 60} min "
              "hold limit, so this run only waits that long.", file=sys.stderr)
    print(f"Ready early. Holding {wait / 60:.0f} min until {_stamp(deliver, local_tz)}.")
    time.sleep(wait)
    return wait
