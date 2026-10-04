"""When a run was due, how late it started, and holding the email until
deliver_at. Everything runs on a fake clock, so a three-hour wait is instant."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import digest
from test_main import config, topic

SYDNEY = ZoneInfo("Australia/Sydney")
UTC = timezone.utc


def utc(*parts):
    return datetime(*parts, tzinfo=UTC)


# --------------------------------------------------------------------------
# Working out when a scheduled run was due
# --------------------------------------------------------------------------

# Real starts from the run history, as GitHub reported them, against the
# shipped "0 8 * * *" in Sydney. The last one is the first run after the
# clocks went forward on 4 October.
@pytest.mark.parametrize("started, due, late", [
    (utc(2026, 9, 15, 0, 16, 19), utc(2026, 9, 14, 22, 0), 136),
    (utc(2026, 9, 20, 23, 50, 21), utc(2026, 9, 20, 22, 0), 110),
    (utc(2026, 9, 29, 1, 27, 56), utc(2026, 9, 28, 22, 0), 208),
    (utc(2026, 10, 3, 23, 30, 21), utc(2026, 10, 3, 21, 0), 150),
])
def test_the_due_time_matches_real_runs(started, due, late):
    found = digest.scheduled_start("0 8 * * *", SYDNEY, started)
    assert found == due
    assert round((started - found).total_seconds() / 60) == late


def test_daylight_saving_moves_the_due_time_in_utc_but_not_on_the_clock():
    before = digest.scheduled_start("0 8 * * *", SYDNEY, utc(2026, 10, 3, 1, 0))
    after = digest.scheduled_start("0 8 * * *", SYDNEY, utc(2026, 10, 4, 1, 0))
    assert before.astimezone(SYDNEY).hour == after.astimezone(SYDNEY).hour == 8
    # The night the clocks went forward is 23 hours long.
    assert after - before == timedelta(hours=23)


def test_daylight_saving_ending_makes_a_25_hour_gap():
    before = digest.scheduled_start("0 8 * * *", SYDNEY, utc(2027, 4, 3, 1, 0))
    after = digest.scheduled_start("0 8 * * *", SYDNEY, utc(2027, 4, 4, 1, 0))
    assert after - before == timedelta(hours=25)


def test_a_start_a_few_seconds_early_still_belongs_to_that_days_entry():
    found = digest.scheduled_start("0 8 * * *", SYDNEY, utc(2026, 9, 14, 21, 59, 50))
    assert found == utc(2026, 9, 14, 22, 0)


def test_a_cron_entry_can_be_in_a_different_zone_from_the_settings():
    found = digest.scheduled_start("0 22 * * *", UTC, utc(2026, 10, 3, 23, 30))
    assert found == utc(2026, 10, 3, 22, 0)


@pytest.mark.parametrize("cron", [
    None, "", "*/5 * * * *", "0 8 * * 1", "0 8 1 * *", "0 8 * 6 *", "0 */2 * * *",
    "61 8 * * *", "0 25 * * *", "0 8 * *", "0 8 * * * *",
])
def test_a_cron_entry_that_isnt_a_plain_daily_time_has_no_due_time(cron):
    assert digest.scheduled_start(cron, SYDNEY, utc(2026, 9, 15, 0, 16)) is None


def test_delivery_is_the_same_day_when_it_comes_after_the_schedule():
    scheduled = utc(2026, 9, 14, 19, 0)         # 05:00 in Sydney
    assert digest.delivery_time("08:00", scheduled, SYDNEY) == utc(2026, 9, 14, 22, 0)


def test_delivery_equal_to_the_schedule_means_no_wait():
    scheduled = utc(2026, 9, 14, 22, 0)
    assert digest.delivery_time("08:00", scheduled, SYDNEY) == scheduled
    assert digest.delivery_time("8:00", scheduled, SYDNEY) == scheduled


def test_delivery_rolls_to_the_next_day_when_it_is_earlier_than_the_schedule():
    scheduled = utc(2026, 9, 14, 13, 0)         # 23:00 in Sydney
    assert digest.delivery_time("01:00", scheduled, SYDNEY) == utc(2026, 9, 14, 15, 0)


def test_a_wait_across_the_clocks_going_forward_is_an_hour_shorter_on_the_clock():
    scheduled = datetime(2026, 10, 4, 1, 0, tzinfo=SYDNEY).astimezone(UTC)
    wait = digest.delivery_time("08:00", scheduled, SYDNEY) - scheduled
    assert wait == timedelta(hours=6)           # seven hours on the clock face


# --------------------------------------------------------------------------
# Whole runs
# --------------------------------------------------------------------------

class Run:
    """One call to main() on a clock that sleeping moves. `start` is the
    moment the run begins, in UTC."""

    def __init__(self, monkeypatch, clock, start):
        self.sleeps_before = len(clock.sleeps)
        self.clock = clock
        self.origin = clock.now
        self.start = start
        self.lookbacks = []
        self.sent_at = []
        self.subjects = []

        run = self

        class WallClock(datetime):
            @classmethod
            def now(cls, tz=None):
                moment = run.start + timedelta(seconds=run.clock.now - run.origin)
                return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

        def fetch(topic_cfg, lookback):
            run.lookbacks.append(lookback)
            return [{"title": "A", "link": "https://a.example/1", "summary": "", "source": "O",
                     "published": None}]

        def summarize(name, articles, max_developments):
            return {"overview": "Overview.", "stories": []}

        def send(subject, body, amp_body=None):
            run.subjects.append(subject)
            run.sent_at.append(WallClock.now(UTC))

        monkeypatch.setattr(digest, "datetime", WallClock)
        monkeypatch.setattr(digest, "fetch_topic_articles", fetch)
        monkeypatch.setattr(digest, "summarize_topic", summarize)
        monkeypatch.setattr(digest, "fetch_fact_of_the_day", lambda day: None)
        monkeypatch.setattr(digest, "send_email", send)

    @property
    def holds(self):
        """Sleeps longer than the pause between topics."""
        return [s for s in self.clock.sleeps[self.sleeps_before:] if s > 2]


@pytest.fixture
def start_run(monkeypatch, clock, mail_env, write_config):
    def start(start, scheduled=True, cron="0 8 * * *", **settings):
        if scheduled:
            monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
            monkeypatch.setenv("DIGEST_SCHEDULE", cron)
        else:
            monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        write_config(config(topic("Tech"), **{"timezone": "Australia/Sydney", **settings}))
        return Run(monkeypatch, clock, start)
    return start


DUE = utc(2026, 9, 14, 19, 0)                   # 05:00 on the 15th in Sydney
EIGHT = utc(2026, 9, 14, 22, 0)                 # 08:00 on the 15th in Sydney


def test_an_early_run_waits_and_sends_at_the_delivery_time(start_run, capsys):
    run = start_run(DUE + timedelta(minutes=3), cron="0 5 * * *", deliver_at="08:00")
    digest.main()
    assert run.sent_at == [EIGHT]
    [hold] = run.holds
    assert hold == pytest.approx(3 * 3600 - 3 * 60 - 2)    # the 2s pause after the topic counts
    out = capsys.readouterr().out
    assert "Holding 177 min until Tue 15 Sep 08:00 AEST" in out
    assert "waited 177 min" in out and "sent Tue 15 Sep 08:00 AEST (0 min after deliver_at)" in out


def test_a_run_that_finishes_after_the_delivery_time_sends_at_once(start_run, capsys):
    run = start_run(EIGHT + timedelta(minutes=40), cron="0 5 * * *", deliver_at="08:00")
    digest.main()
    assert run.holds == []
    assert run.sent_at == [EIGHT + timedelta(minutes=40, seconds=2)]
    out = capsys.readouterr().out
    assert "Ready 40 min after deliver_at. Sending now." in out
    assert "waited 0 min" in out and "(40 min after deliver_at)" in out


def test_the_hold_never_goes_past_the_cap(start_run, capsys):
    # Due at 04:00 with delivery at 08:00 is a 4 hour wait, and the cap is 3.
    run = start_run(EIGHT - timedelta(hours=4), cron="0 4 * * *", deliver_at="08:00")
    digest.main()
    [hold] = run.holds
    assert hold == digest.MAX_HOLD
    assert run.sent_at == [EIGHT - timedelta(hours=4) + timedelta(seconds=2 + digest.MAX_HOLD)]
    assert "past the 180 min hold limit" in capsys.readouterr().err


def test_a_manual_run_never_waits(start_run, capsys):
    # A manual run at 15:00 would otherwise sit until 08:00 tomorrow.
    afternoon = utc(2026, 9, 14, 5, 0)
    run = start_run(afternoon, scheduled=False, deliver_at="08:00")
    digest.main()
    assert run.holds == []
    assert run.sent_at == [afternoon + timedelta(seconds=2)]
    assert run.lookbacks == [24]
    out = capsys.readouterr().out
    assert "Manual run" in out and "deliver_at only applies to scheduled runs" in out


def test_without_deliver_at_a_late_run_behaves_as_it_always_has(start_run, capsys):
    run = start_run(EIGHT + timedelta(minutes=134))
    digest.main()
    assert run.holds == []
    assert run.lookbacks == [24]
    assert run.sent_at == [EIGHT + timedelta(minutes=134, seconds=2)]
    out = capsys.readouterr().out
    assert "Tue 15 Sep 08:00 AEST, started Tue 15 Sep 10:14 AEST: 134 min late." in out
    assert "due Tue 15 Sep 08:00 AEST" in out and "(134 min late)" in out and "waited 0 min" in out
    assert "deliver_at" not in out


def test_a_manual_run_logs_its_timing_without_a_lateness(start_run, capsys):
    start_run(utc(2026, 9, 14, 5, 0), scheduled=False)
    digest.main()
    out = capsys.readouterr().out
    assert "Manual run, so there's no scheduled time" in out
    [timing] = [line for line in out.splitlines() if line.startswith("Timing:")]
    assert "late" not in timing and "waited 0 min" in timing


# lookback_hours is counted back from the start, which can be hours before
# delivery or hours after the schedule. Counted from the scheduled start
# instead, each digest's window begins exactly where the last one's began, so
# starts that move around from day to day leave no gap between digests.
@pytest.mark.parametrize("late_minutes", [0, 5, 90, 208])
def test_the_window_starts_a_full_lookback_before_the_scheduled_start(start_run, late_minutes):
    start = DUE + timedelta(minutes=late_minutes)
    run = start_run(start, cron="0 5 * * *", deliver_at="08:00", lookback_hours=24)
    digest.main()
    [lookback] = run.lookbacks
    assert start - timedelta(hours=lookback) == DUE - timedelta(hours=24)


def test_a_slightly_early_start_does_not_shrink_the_window(start_run):
    run = start_run(DUE - timedelta(seconds=20), cron="0 5 * * *", deliver_at="08:00")
    digest.main()
    assert run.lookbacks == [24]


def test_a_held_digest_is_dated_for_the_day_it_is_delivered(start_run):
    # Written at 23:05 on 14 September, delivered at 01:00 on the 15th.
    start = datetime(2026, 9, 14, 23, 5, tzinfo=SYDNEY).astimezone(UTC)
    run = start_run(start, cron="0 23 * * *", deliver_at="01:00", email_subject_prefix="Digest")
    digest.main()
    assert run.subjects == ["Digest - Tuesday, 15 September 2026"]


def test_a_schedule_with_its_own_time_zone_is_measured_in_that_zone(start_run, monkeypatch, capsys):
    monkeypatch.setenv("DIGEST_SCHEDULE_TZ", "UTC")
    start_run(utc(2026, 10, 3, 23, 30), cron="0 22 * * *")
    digest.main()
    assert "90 min late" in capsys.readouterr().out


def test_a_bad_schedule_time_zone_falls_back_to_the_settings_one(start_run, monkeypatch, capsys):
    monkeypatch.setenv("DIGEST_SCHEDULE_TZ", "Mars/Olympus_Mons")
    start_run(EIGHT + timedelta(minutes=30))
    digest.main()
    captured = capsys.readouterr()
    assert "unknown DIGEST_SCHEDULE_TZ 'Mars/Olympus_Mons'" in captured.err
    assert "30 min late" in captured.out


# If GitHub doesn't pass the entry through as expected, the email still goes
# out, as soon as it's ready.
@pytest.mark.parametrize("cron", ["", "*/30 * * * *", "0 8 * * 1"])
def test_an_unreadable_schedule_still_sends_without_a_hold(start_run, capsys, cron):
    run = start_run(DUE, cron=cron, deliver_at="08:00")
    digest.main()
    assert run.holds == []
    assert len(run.sent_at) == 1
    assert run.lookbacks == [24]
    err = capsys.readouterr().err
    assert "isn't a plain daily time" in err and "deliver_at is set but can't be applied" in err


def test_an_unknown_time_zone_means_no_hold(start_run, capsys):
    run = start_run(DUE, cron="0 5 * * *", deliver_at="08:00", timezone="Mars/Olympus_Mons")
    digest.main()
    assert run.holds == []
    assert len(run.sent_at) == 1
    assert "deliver_at is set but can't be applied" in capsys.readouterr().err


def test_a_failed_send_after_a_hold_still_exits_non_zero(start_run, monkeypatch, capsys):
    start_run(DUE, cron="0 5 * * *", deliver_at="08:00")

    def refuse(subject, body, amp_body=None):
        raise OSError("535 Username and Password not accepted")

    monkeypatch.setattr(digest, "send_email", refuse)
    with pytest.raises(SystemExit) as exit_info:
        digest.main()
    assert exit_info.value.code == 1
    assert "Timing:" not in capsys.readouterr().out
