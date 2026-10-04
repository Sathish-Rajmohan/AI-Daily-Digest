#!/usr/bin/env python3
"""How late the scheduled digest starts, from GitHub's public run history.

    python run_history.py --repo OWNER/REPO

prints one line per scheduled run and a summary, so a schedule change can be
compared with the runs before it. The history is public and needs no token,
but GitHub allows only 60 unauthenticated calls an hour.

A run's lateness is the time GitHub created it minus the time its cron entry
was due. The workflow doesn't record which entry was in force for an old run,
so say which one with --cron and --tz, and cut the history to the runs under
that entry with --from and --to. The dates are the days the runs were due.
To compare two schedules, run this once for each.

Run length and the send time come from the run's last update, which is a few
seconds after the email goes out.
"""

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

import digest

API = "https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs"
WITHIN = 10     # minutes either side of the target that count as on time


def _time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def fetch_runs(repo, workflow):
    """Every scheduled run GitHub lists for the workflow, newest first."""
    runs, page = [], 1
    while True:
        try:
            resp = requests.get(
                API.format(repo=repo, workflow=workflow),
                params={"event": "schedule", "per_page": 100, "page": page},
                headers={"Accept": "application/vnd.github+json", "User-Agent": digest.USER_AGENT},
                timeout=30,
            )
        except requests.exceptions.RequestException as e:
            raise SystemExit(f"Couldn't reach GitHub: {e}")
        if resp.status_code in (403, 429):
            raise SystemExit("GitHub refused the request, probably the 60 calls an hour limit "
                             "for requests without a token. Try again later, or save the response "
                             "and use --file.")
        resp.raise_for_status()
        body = resp.json()
        runs += body["workflow_runs"]
        if len(runs) >= body["total_count"] or not body["workflow_runs"]:
            return runs
        page += 1


def measure(run, cron, tz, deliver_at=None):
    """One run's numbers, or None if its due time can't be worked out. Times
    are UTC, and every difference is in minutes.
    """
    created = _time(run["created_at"])
    due = digest.scheduled_start(cron, tz, created)
    if due is None:
        return None
    target = digest.delivery_time(deliver_at, due, tz) if deliver_at else due
    row = {
        "number": run["run_number"],
        "due": due,
        "created": created,
        "late": (created - due).total_seconds() / 60,
        "conclusion": run["conclusion"] if run["status"] == "completed" else run["status"],
        "finished": None,
        "length": None,
        "after_target": None,
    }
    if run["status"] == "completed":
        row["finished"] = _time(run["updated_at"])
        row["length"] = (row["finished"] - _time(run["run_started_at"])).total_seconds() / 60
        row["after_target"] = (row["finished"] - target).total_seconds() / 60
    return row


def percentile(values, share):
    """The value that `share` of the runs are at or under (nearest rank)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def _clock(minutes_of_day):
    return f"{int(minutes_of_day) // 60:02d}:{int(minutes_of_day) % 60:02d}"


def table(rows, tz):
    lines = [f"{'Run':>4}  {'Due':<21} {'Started':<17} {'UTC':<6} {'Late':>5}  {'Length':>6}  {'Sent':<6} {'vs target':>9}"]
    for r in rows:
        due, created = r["due"].astimezone(tz), r["created"].astimezone(tz)
        sent = r["finished"].astimezone(tz).strftime("%H:%M") if r["finished"] else "-"
        length = f"{r['length']:.1f}" if r["length"] is not None else "-"
        target = f"{r['after_target']:+.0f}" if r["after_target"] is not None else "-"
        note = "" if r["conclusion"] == "success" else f"  ({r['conclusion']})"
        lines.append(
            f"{r['number']:>4}  {due.strftime('%a %d %b %H:%M %Z'):<21} "
            f"{created.strftime('%a %d %b %H:%M'):<17} {r['created'].strftime('%H:%M'):<6} "
            f"{r['late']:>5.0f}  {length:>6}  {sent:<6} {target:>9}{note}"
        )
    return "\n".join(lines)


def summary(rows, tz):
    lines = []
    first, last = rows[0]["due"].astimezone(tz), rows[-1]["due"].astimezone(tz)
    count = f"{len(rows)} run" + ("" if len(rows) == 1 else "s")
    lines.append(f"{count}, due {first:%d %b %Y} to {last:%d %b %Y}")
    offsets = sorted({r["due"].astimezone(tz).strftime("%Z") for r in rows})
    lines.append(f"Clock zones in the period: {', '.join(offsets)}")

    def stats_row(label, values, fmt):
        values = [v for v in values if v is not None]
        if not values:
            return f"{label:<28} no completed runs"
        cells = [min(values), statistics.median(values), max(values), percentile(values, 0.95)]
        return f"{label:<28}" + "".join(f"{fmt(v):>10}" for v in cells)

    lines.append(f"{'':<28}{'Earliest':>10}{'Median':>10}{'Latest':>10}{'95 in 100':>10}")
    lines.append(stats_row("Started, min after due", [r["late"] for r in rows], lambda v: f"{v:.0f}"))
    lines.append(stats_row("Run length, min", [r["length"] for r in rows], lambda v: f"{v:.1f}"))
    lines.append(stats_row("Sent, min after target", [r["after_target"] for r in rows], lambda v: f"{v:.0f}"))

    done = [r for r in rows if r["finished"]]
    if done:
        minutes = [(r["finished"].astimezone(tz).hour * 60 + r["finished"].astimezone(tz).minute) for r in done]
        lines.append(f"{'Sent, time of day':<28}" + "".join(
            f"{_clock(v):>10}" for v in (min(minutes), statistics.median(minutes), max(minutes))))
    lates = [r["late"] for r in rows]
    lines.append(f"Spread of the start (latest minus earliest): {max(lates) - min(lates):.0f} min")
    if done:
        on_time = sum(1 for r in done if abs(r["after_target"]) <= WITHIN)
        lines.append(f"Sent within {WITHIN} min of the target: {on_time} of {len(done)}")
    return "\n".join(lines)


def main(argv=None, fetch=fetch_runs):
    parser = argparse.ArgumentParser(description="How late the scheduled digest starts.")
    parser.add_argument("--repo", default="Sathish-Rajmohan/AI-Daily-Digest", help="OWNER/REPO")
    parser.add_argument("--workflow", default="daily-digest.yml")
    parser.add_argument("--file", help="read a saved copy of the API response instead of calling GitHub")
    parser.add_argument("--cron", default="0 8 * * *", help="the entry the runs were scheduled by")
    parser.add_argument("--tz", default="Australia/Sydney", help="the time zone that entry runs in")
    parser.add_argument("--deliver-at", help="measure the send against this HH:MM instead of the cron time")
    parser.add_argument("--from", dest="since", help="first due date to include, YYYY-MM-DD")
    parser.add_argument("--to", dest="until", help="last due date to include, YYYY-MM-DD")
    args = parser.parse_args(argv)

    tz = ZoneInfo(args.tz)
    if digest.scheduled_start(args.cron, tz, datetime.now(timezone.utc)) is None:
        parser.error(f"--cron {args.cron!r} isn't a plain daily time such as '0 8 * * *'")

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            body = json.load(f)
        runs = body["workflow_runs"] if isinstance(body, dict) else body
    else:
        runs = fetch(args.repo, args.workflow)

    rows = [m for m in (measure(r, args.cron, tz, args.deliver_at)
                        for r in runs if r.get("event", "schedule") == "schedule") if m]
    if args.since:
        rows = [r for r in rows if r["due"].astimezone(tz).date().isoformat() >= args.since]
    if args.until:
        rows = [r for r in rows if r["due"].astimezone(tz).date().isoformat() <= args.until]
    if not rows:
        print("No scheduled runs in that range.")
        return 1

    rows.sort(key=lambda r: r["created"])
    print(table(rows, tz))
    print()
    print(summary(rows, tz))
    return 0


if __name__ == "__main__":
    sys.exit(main())
