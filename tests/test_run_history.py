"""run_history.py: lateness and run length from GitHub's run history."""

import json

import pytest

import run_history

# The scheduled runs GitHub listed on 5 October 2026: run number, when GitHub
# created the run, and its last update. The first 18 are the ones the figures
# in the docs come from.
REAL_RUNS = [
    (5, "2026-09-15T00:16:19Z", "2026-09-15T00:22:02Z"),
    (9, "2026-09-15T23:57:41Z", "2026-09-16T00:04:55Z"),
    (10, "2026-09-17T00:04:05Z", "2026-09-17T00:07:54Z"),
    (11, "2026-09-17T23:54:03Z", "2026-09-17T23:59:13Z"),
    (12, "2026-09-18T23:52:02Z", "2026-09-18T23:58:28Z"),
    (13, "2026-09-19T23:53:17Z", "2026-09-19T23:57:34Z"),
    (14, "2026-09-20T23:50:21Z", "2026-09-20T23:56:16Z"),
    (15, "2026-09-22T00:35:11Z", "2026-09-22T00:39:03Z"),
    (16, "2026-09-22T23:58:51Z", "2026-09-23T00:09:25Z"),
    (17, "2026-09-24T00:13:31Z", "2026-09-24T00:19:01Z"),
    (18, "2026-09-25T00:16:05Z", "2026-09-25T00:22:51Z"),
    (19, "2026-09-26T00:21:31Z", "2026-09-26T00:27:23Z"),
    (20, "2026-09-27T00:13:16Z", "2026-09-27T00:16:21Z"),
    (21, "2026-09-28T00:17:19Z", "2026-09-28T00:23:57Z"),
    (22, "2026-09-29T01:27:56Z", "2026-09-29T01:37:04Z"),
    (23, "2026-09-30T01:03:11Z", "2026-09-30T01:09:38Z"),
    (24, "2026-10-01T01:03:16Z", "2026-10-01T01:12:47Z"),
    (25, "2026-10-02T01:16:44Z", "2026-10-02T01:25:06Z"),
    (26, "2026-10-03T00:55:07Z", "2026-10-03T01:09:38Z"),
    (27, "2026-10-03T23:30:21Z", "2026-10-03T23:36:01Z"),
]


def api_run(number, created, updated, conclusion="success", status="completed"):
    return {"run_number": number, "event": "schedule", "status": status, "conclusion": conclusion,
            "created_at": created, "run_started_at": created, "updated_at": updated}


def real_runs():
    # GitHub lists the newest first.
    return [api_run(*row) for row in reversed(REAL_RUNS)]


def run_script(capsys, runs, *args):
    code = run_history.main(list(args), fetch=lambda repo, workflow: runs)
    return code, capsys.readouterr().out


def summary_row(out, label):
    [line] = [l for l in out.splitlines() if l.startswith(label)]
    return line[len(label):].split()


# The 18 runs from 15 September to 2 October, as the roadmap describes them.
def test_the_first_18_runs_give_the_documented_figures(capsys):
    code, out = run_script(capsys, real_runs(), "--to", "2026-10-02")
    assert code == 0
    assert summary_row(out, "Started, min after due")[:3] == ["110", "135", "208"]
    assert summary_row(out, "Run length, min")[:3] == ["3.1", "6.2", "10.6"]
    assert summary_row(out, "Sent, time of day") == ["09:56", "10:20", "11:37"]
    assert "Spread of the start (latest minus earliest): 98 min" in out
    assert out.startswith(" Run  Due")
    assert "18 runs, due 15 Sep 2026 to 02 Oct 2026" in out
    assert "Sent within 10 min of the target: 0 of 18" in out


def test_the_run_after_daylight_saving_began_is_measured_from_its_own_due_time(capsys):
    code, out = run_script(capsys, real_runs(), "--from", "2026-10-04")
    assert code == 0
    assert "Sun 04 Oct 08:00 AEDT" in out
    assert summary_row(out, "Started, min after due")[:3] == ["150", "150", "150"]
    assert "Clock zones in the period: AEDT" in out


def test_a_period_across_the_clock_change_shows_both_zones_and_the_start_in_utc(capsys):
    code, out = run_script(capsys, real_runs(), "--from", "2026-10-03")
    assert "Clock zones in the period: AEDT, AEST" in out
    # The UTC column is there so a change in the clocks can't pass for a
    # change in GitHub's delay.
    assert "23:30" in out


def test_a_different_cron_entry_changes_what_counts_as_late(capsys):
    # The same starts measured against 8:17, as after moving off the hour.
    _, out = run_script(capsys, real_runs(), "--to", "2026-09-16", "--cron", "17 8 * * *")
    assert summary_row(out, "Started, min after due")[:3] == ["101", "110", "119"]


def test_the_send_can_be_measured_against_deliver_at_instead(capsys):
    _, out = run_script(capsys, real_runs(), "--to", "2026-09-16", "--cron", "0 5 * * *",
                        "--deliver-at", "08:00")
    # Due at 05:00 but started at 10:16, so the send is 142 min after 08:00.
    assert summary_row(out, "Sent, min after target")[:3] == ["125", "133", "142"]


def test_runs_that_did_not_finish_show_their_start_and_say_why(capsys):
    runs = [api_run(1, "2026-09-14T22:30:00Z", "2026-09-14T22:31:00Z", conclusion="failure"),
            api_run(2, "2026-09-15T22:20:00Z", "2026-09-15T22:20:00Z", conclusion=None, status="in_progress")]
    code, out = run_script(capsys, runs)
    assert code == 0
    assert "(failure)" in out and "(in_progress)" in out
    assert "Sent within 10 min of the target: 0 of 1" in out


def test_only_scheduled_runs_are_counted(capsys):
    manual = api_run(3, "2026-09-15T05:00:00Z", "2026-09-15T05:05:00Z")
    manual["event"] = "workflow_dispatch"
    code, out = run_script(capsys, [manual] + real_runs()[:1])
    assert "1 run, due 04 Oct 2026 to 04 Oct 2026" in out


def test_an_empty_range_says_so(capsys):
    code, out = run_script(capsys, real_runs(), "--from", "2027-01-01")
    assert code == 1
    assert "No scheduled runs in that range." in out


def test_a_cron_entry_that_isnt_a_daily_time_is_refused(capsys):
    with pytest.raises(SystemExit):
        run_history.main(["--cron", "*/15 * * * *"], fetch=lambda repo, workflow: [])
    assert "isn't a plain daily time" in capsys.readouterr().err


def test_a_saved_api_response_can_be_read_from_a_file(tmp_path, capsys):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps({"total_count": 20, "workflow_runs": real_runs()}), encoding="utf-8")
    code = run_history.main(["--file", str(path), "--to", "2026-09-15"])
    assert code == 0
    assert "1 run, due 15 Sep 2026 to 15 Sep 2026" in capsys.readouterr().out


class Page:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise run_history.requests.exceptions.HTTPError(str(self.status_code))


def test_every_page_of_history_is_read(monkeypatch):
    runs = real_runs()
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(params["page"])
        chunk = runs[(params["page"] - 1) * 12: params["page"] * 12]
        return Page(200, {"total_count": len(runs), "workflow_runs": chunk})

    monkeypatch.setattr(run_history.requests, "get", get)
    assert run_history.fetch_runs("owner/repo", "daily-digest.yml") == runs
    assert calls == [1, 2]


def test_the_rate_limit_gets_a_plain_message(monkeypatch):
    monkeypatch.setattr(run_history.requests, "get", lambda *a, **k: Page(403))
    with pytest.raises(SystemExit, match="60 calls an hour"):
        run_history.fetch_runs("owner/repo", "daily-digest.yml")


def test_a_network_failure_gets_a_plain_message(monkeypatch):
    def down(*args, **kwargs):
        raise run_history.requests.exceptions.ConnectionError("no route")

    monkeypatch.setattr(run_history.requests, "get", down)
    with pytest.raises(SystemExit, match="Couldn't reach GitHub"):
        run_history.fetch_runs("owner/repo", "daily-digest.yml")


@pytest.mark.parametrize("values, expected", [([5], 5), ([1, 2, 3, 4], 4), (list(range(1, 21)), 19)])
def test_the_95th_percentile_is_the_nearest_rank(values, expected):
    assert run_history.percentile(values, 0.95) == expected
