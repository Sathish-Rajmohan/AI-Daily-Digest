"""
The pieces around digest.py that the daily run depends on: the GitHub
Actions workflows, the dependency files, and the script's behaviour when
started the way the workflow starts it.
"""

import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import yaml

import digest
from conftest import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"


def script_source():
    """digest.py and the digestlib modules it runs, as one text."""
    paths = [ROOT / "digest.py", *sorted((ROOT / "digestlib").rglob("*.py"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)

# Every environment variable digest.py reads, split by who sets it.
SECRETS = {"GEMINI_API_KEY", "GROQ_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL",
           "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"}
OPTIONAL_OVERRIDES = {"GEMINI_MODELS", "GEMINI_MODEL", "GROQ_MODELS", "DIGEST_CONFIG_PATH",
                      "DIGEST_SCHEDULE_TZ", "DIGEST_SAVE_DIR", "DIGEST_WEEKLY_DIR"}
# How the script learns which trigger started it. Actions sets the first, and
# the workflow passes the second.
RUN_CONTEXT = {"GITHUB_EVENT_NAME", "DIGEST_SCHEDULE"}


def load_workflow(name):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


@pytest.fixture(scope="module")
def digest_workflow():
    return load_workflow("daily-digest.yml")


@pytest.fixture(scope="module")
def keepalive_workflow():
    return load_workflow("keepalive.yml")


@pytest.fixture(scope="module")
def weekly_workflow():
    return load_workflow("weekly-digest.yml")


def steps(workflow, job):
    return workflow["jobs"][job]["steps"]


def run_step(workflow):
    return next(s for s in steps(workflow, "send-digest") if s.get("run", "").strip() == "python digest.py")


CRON_FIELD = re.compile(r"^(\*|\d+(-\d+)?)(/\d+)?(,(\d+(-\d+)?))*$")
CRON_LIMITS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def assert_valid_cron(expression):
    fields = expression.split()
    assert len(fields) == 5, expression
    for field, (low, high) in zip(fields, CRON_LIMITS):
        assert CRON_FIELD.match(field), (expression, field)
        for number in re.findall(r"\d+", field.split("/")[0]):
            assert low <= int(number) <= high, (expression, field)


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["daily-digest.yml", "keepalive.yml"])
def test_schedules_are_valid_and_can_also_be_run_by_hand(name):
    workflow = load_workflow(name)
    schedule = workflow["on"]["schedule"]
    assert schedule
    for entry in schedule:
        assert_valid_cron(entry["cron"])
        if "timezone" in entry:
            ZoneInfo(entry["timezone"])
    assert "workflow_dispatch" in workflow["on"]


def test_digest_is_scheduled_once_a_day(digest_workflow):
    [entry] = digest_workflow["on"]["schedule"]
    minute, hour, day, month, weekday = entry["cron"].split()
    assert minute.isdigit() and hour.isdigit()
    assert (day, month, weekday) == ("*", "*", "*")


def test_keepalive_runs_well_inside_githubs_60_day_idle_window(keepalive_workflow):
    [entry] = keepalive_workflow["on"]["schedule"]
    _, _, day, month, weekday = entry["cron"].split()
    # A fixed day of every month is at most 31 days apart.
    assert day.isdigit() and int(day) <= 28
    assert month == "*" and weekday == "*"


# --------------------------------------------------------------------------
# The digest job
# --------------------------------------------------------------------------

def test_digest_job_checks_out_installs_and_runs_the_script(digest_workflow):
    job_steps = steps(digest_workflow, "send-digest")
    uses = [s.get("uses", "") for s in job_steps]
    runs = [s.get("run", "").strip() for s in job_steps]
    assert any(u.startswith("actions/checkout@") for u in uses)
    assert any(u.startswith("actions/setup-python@") for u in uses)
    assert "pip install -r requirements.txt" in runs
    assert "python digest.py" in runs
    assert runs.index("pip install -r requirements.txt") < runs.index("python digest.py")


def test_workflow_python_is_new_enough_for_the_script(digest_workflow):
    setup = next(s for s in steps(digest_workflow, "send-digest")
                 if s.get("uses", "").startswith("actions/setup-python@"))
    major, minor = (int(part) for part in str(setup["with"]["python-version"]).split(".")[:2])
    # zoneinfo, and sys.stdlib_module_names used by the tests, need 3.10.
    assert (major, minor) >= (3, 10)


def test_every_environment_variable_the_script_reads_is_accounted_for():
    source = script_source()
    read = set(re.findall(r'os\.environ(?:\.get\(\s*|\[)"([A-Z_]+)"', source))
    assert read == SECRETS | OPTIONAL_OVERRIDES | RUN_CONTEXT


def test_every_secret_reaches_the_script_under_its_own_name(digest_workflow):
    env = run_step(digest_workflow)["env"]
    assert set(env) == SECRETS | {"DIGEST_SCHEDULE"}
    for name in SECRETS:
        assert env[name].replace(" ", "") == "${{secrets.%s}}" % name


# Without this the script can't tell when a scheduled run was due, so it
# can't measure lateness or hold until deliver_at.
def test_the_workflow_tells_the_script_which_schedule_started_it(digest_workflow):
    env = run_step(digest_workflow)["env"]
    assert env["DIGEST_SCHEDULE"].replace(" ", "") == "${{github.event.schedule}}"


def test_job_timeout_leaves_room_for_the_worst_case_run_and_the_longest_hold(digest_workflow):
    config = json.loads((ROOT / "topics.json").read_text(encoding="utf-8"))
    worst = 0
    for topic in config["topics"]:
        rounds = math.ceil(len(topic["feeds"]) / digest.FEED_FETCH_WORKERS)
        # A dead host can use the HTTP timeout and then feedparser's own
        # socket timeout on the fallback, and topics pause between them.
        worst += rounds * 2 * digest.FEED_FETCH_TIMEOUT + 2
    worst += digest.TOTAL_BUDGET          # every model call, the fact included
    worst += digest.REQUEST_TIMEOUT       # one request started just before the budget ran out
    worst += 60                           # SMTP connection timeout
    worst += 180                          # checkout, Python setup, pip install
    worst += digest.MAX_HOLD              # waiting for deliver_at, after the run's own work
    timeout = digest_workflow["jobs"]["send-digest"]["timeout-minutes"] * 60
    assert worst < timeout, f"worst case {worst}s against a {timeout}s job timeout"
    assert timeout < 360 * 60


# deliver_at isn't shipped, so this checks nothing until someone sets it. Then
# it stops a schedule and a delivery time that can't meet: a run that has to
# wait longer than MAX_HOLD sends early instead.
def test_a_shipped_deliver_at_is_within_reach_of_the_shipped_schedule(digest_workflow):
    settings = json.loads((ROOT / "topics.json").read_text(encoding="utf-8")).get("settings", {})
    if "deliver_at" not in settings:
        pytest.skip("topics.json has no deliver_at")
    [entry] = digest_workflow["on"]["schedule"]
    tz = ZoneInfo(entry.get("timezone", "UTC"))
    for day in range(1, 366, 7):          # weekly samples cover both daylight saving changes
        due = digest.scheduled_start(entry["cron"], tz, datetime(2026, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=day))
        hold = digest.delivery_time(settings["deliver_at"], due, ZoneInfo(settings["timezone"])) - due
        assert hold.total_seconds() <= digest.MAX_HOLD, f"{hold} between the schedule and deliver_at"


def test_topics_file_the_workflow_uses_is_where_the_script_looks():
    env = {k: v for k, v in os.environ.items() if k != "DIGEST_CONFIG_PATH"}
    result = subprocess.run([sys.executable, "-c", "import digest; print(digest.CONFIG_PATH)"],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    path = result.stdout.strip().splitlines()[-1]
    assert path == "topics.json"
    assert (ROOT / path).is_file()


def test_the_saved_digest_is_uploaded_after_the_run_and_kept_two_weeks(digest_workflow):
    job_steps = steps(digest_workflow, "send-digest")
    upload = next(s for s in job_steps if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert job_steps.index(upload) > job_steps.index(run_step(digest_workflow))
    # A one-week edition needs a week of files, with room for a late run.
    assert upload["with"]["retention-days"] == 14
    # A run whose save failed has still sent its email, so it stays green.
    assert upload["with"]["if-no-files-found"] == "warn"

    env = {k: v for k, v in os.environ.items() if k != "DIGEST_SAVE_DIR"}
    result = subprocess.run([sys.executable, "-c", "import digest; print(digest.SAVE_DIR)"],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert upload["with"]["path"].rstrip("/") == result.stdout.strip().splitlines()[-1]


# --------------------------------------------------------------------------
# The weekly job
# --------------------------------------------------------------------------

def weekly_step(workflow, name):
    return next(s for s in steps(workflow, "send-weekly") if s.get("name") == name)


def test_weekly_starts_when_a_daily_run_ends_and_by_hand(weekly_workflow, digest_workflow):
    trigger = weekly_workflow["on"]["workflow_run"]
    # workflow_run matches on the daily workflow's name, so a rename would
    # silently stop the weekly edition.
    assert trigger["workflows"] == [digest_workflow["name"]]
    assert trigger["types"] == ["completed"]
    assert "workflow_dispatch" in weekly_workflow["on"]
    assert "schedule" not in weekly_workflow["on"]


def test_weekly_only_follows_the_first_attempt_of_a_scheduled_daily_run(weekly_workflow):
    condition = " ".join(weekly_workflow["jobs"]["send-weekly"]["if"].split())
    assert "github.event.workflow_run.event == 'schedule'" in condition
    assert "github.event.workflow_run.run_attempt == 1" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition


def test_weekly_can_read_artifacts_and_write_nothing(weekly_workflow):
    assert weekly_workflow["permissions"] == {"actions": "read", "contents": "read"}


def test_weekly_uses_the_same_actions_and_python_as_the_daily(weekly_workflow, digest_workflow):
    def setup(workflow, job):
        return {s["uses"]: s.get("with") for s in steps(workflow, job) if "uses" in s
                and s["uses"].startswith(("actions/checkout@", "actions/setup-python@"))}
    assert setup(weekly_workflow, "send-weekly") == setup(digest_workflow, "send-digest")
    runs = [s.get("run", "").strip() for s in steps(weekly_workflow, "send-weekly")]
    assert "pip install -r requirements.txt" in runs


def test_weekly_downloads_what_the_daily_uploads_into_the_folder_it_reads(weekly_workflow, digest_workflow):
    upload = next(s for s in steps(digest_workflow, "send-digest")
                  if s.get("uses", "").startswith("actions/upload-artifact@"))
    download = weekly_step(weekly_workflow, "Download the saved daily digests")
    run = weekly_step(weekly_workflow, "Run weekly edition")
    job_steps = steps(weekly_workflow, "send-weekly")
    assert job_steps.index(download) < job_steps.index(run)

    script = download["run"]
    name = upload["with"]["name"]
    assert f"artifacts?name={name}" in script and f"--name {name}" in script
    assert "select(.expired == false" in script
    # One folder per run, because every artifact has the same name.
    assert '--dir "weekly-input/$run"' in script
    assert run["env"]["DIGEST_WEEKLY_DIR"] == "weekly-input"
    assert download["env"]["GH_TOKEN"].replace(" ", "") == "${{github.token}}"
    # A failed download leaves days out, and the email says so.
    assert download["continue-on-error"] is True


def test_weekly_passes_the_secrets_and_runs_the_weekly_edition(weekly_workflow):
    run = weekly_step(weekly_workflow, "Run weekly edition")
    env = run["env"]
    assert SECRETS <= set(env)
    for name in SECRETS:
        assert env[name].replace(" ", "") == "${{secrets.%s}}" % name
    assert run["run"].strip() == "python digest.py --weekly $ANY_DAY"
    assert env["ANY_DAY"].replace(" ", "") == "${{inputs.any_day&&'--any-day'||''}}"
    assert weekly_workflow["on"]["workflow_dispatch"]["inputs"]["any_day"]["type"] == "boolean"


def test_weekly_timeout_leaves_room_for_the_run_and_the_longest_hold(weekly_workflow):
    topics = len(json.loads((ROOT / "topics.json").read_text(encoding="utf-8"))["topics"])
    worst = 180                           # checkout, Python setup, pip install
    worst += 300                          # downloading two weeks of artifacts
    worst += digest.TOTAL_BUDGET + digest.REQUEST_TIMEOUT + 2 * topics
    worst += 60                           # SMTP connection timeout
    worst += digest.MAX_HOLD
    timeout = weekly_workflow["jobs"]["send-weekly"]["timeout-minutes"] * 60
    assert worst < timeout < 360 * 60


def test_the_shipped_weekly_block_is_on_for_sundays():
    weekly = json.loads((ROOT / "topics.json").read_text(encoding="utf-8"))["weekly"]
    assert weekly["enabled"] is True and weekly["day"] == "Sunday"


# --------------------------------------------------------------------------
# The keepalive job
# --------------------------------------------------------------------------

def test_keepalive_has_permission_to_push_and_syncs_before_committing(keepalive_workflow):
    assert keepalive_workflow["permissions"]["contents"] == "write"
    script = "\n".join(s.get("run", "") for s in steps(keepalive_workflow, "keepalive"))
    assert "user.name" in script and "user.email" in script
    assert script.index("git pull --ff-only") < script.index("git commit --allow-empty") \
        < script.index("git push")
    assert keepalive_workflow["jobs"]["keepalive"]["timeout-minutes"] <= 10


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------

def requirement_lines(name):
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def test_runtime_dependencies_are_pinned_and_cover_every_third_party_import():
    pinned = {}
    for line in requirement_lines("requirements.txt"):
        name, _, version = line.partition("==")
        assert version, f"{line} isn't pinned to an exact version"
        pinned[name.lower()] = version
    source = script_source()
    imported = set(re.findall(r"^(?:import|from) ([A-Za-z_]+)", source, re.M))
    third_party = {m for m in imported if m not in sys.stdlib_module_names and m != "digestlib"}
    distribution = {"dateutil": "python-dateutil"}
    assert third_party, "expected some third-party imports"
    for module in third_party:
        assert distribution.get(module, module) in pinned, f"{module} is imported but not in requirements.txt"


def test_dev_requirements_build_on_the_runtime_ones():
    lines = requirement_lines("requirements-dev.txt")
    assert "-r requirements.txt" in lines
    names = {line.partition("==")[0].lower() for line in lines if "==" in line}
    assert {"pytest", "pyyaml"} <= names


# --------------------------------------------------------------------------
# Starting the script the way the workflow does
# --------------------------------------------------------------------------

def with_topics(tmp_path):
    """A topics.json with no topics. The script reads it before it checks the
    secrets, because the channels it lists decide which secrets are needed.
    """
    (tmp_path / "topics.json").write_text('{"topics": []}', encoding="utf-8")
    return tmp_path


def run_script(tmp_path, **env_values):
    env = {k: v for k, v in os.environ.items() if k not in SECRETS | OPTIONAL_OVERRIDES | RUN_CONTEXT}
    env.update(env_values)
    return subprocess.run([sys.executable, str(ROOT / "digest.py")], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)


def test_the_weekly_edition_starts_the_way_the_workflow_starts_it(tmp_path):
    with_topics(tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in SECRETS | OPTIONAL_OVERRIDES | RUN_CONTEXT}
    env.update(GEMINI_API_KEY="k")
    result = subprocess.run([sys.executable, str(ROOT / "digest.py"), "--weekly", "--any-day"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 1
    assert "missing required env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD" in result.stderr


def test_script_exits_with_a_reason_when_gmail_secrets_are_missing(tmp_path):
    result = run_script(with_topics(tmp_path), GEMINI_API_KEY="k")
    assert result.returncode == 1
    assert "missing required env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD" in result.stderr


# Actions passes an unset secret through as an empty string, not as a missing
# variable, so an empty value has to count as not set.
def test_empty_secrets_count_as_not_set(tmp_path):
    result = run_script(with_topics(tmp_path), **{name: "" for name in SECRETS})
    assert result.returncode == 1
    assert "missing required env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD" in result.stderr


def test_script_exits_with_a_reason_when_no_model_key_is_set(tmp_path):
    result = run_script(with_topics(tmp_path), GMAIL_ADDRESS="a@example.com", GMAIL_APP_PASSWORD="x",
                        GEMINI_API_KEY="", GROQ_API_KEY="")
    assert result.returncode == 1
    assert "set GEMINI_API_KEY, GROQ_API_KEY, or both" in result.stderr


def test_script_fails_loudly_without_its_topics_file(tmp_path):
    result = run_script(tmp_path, GMAIL_ADDRESS="a@example.com", GMAIL_APP_PASSWORD="x", GEMINI_API_KEY="k")
    assert result.returncode != 0
    assert "topics.json" in result.stderr


def test_an_empty_groq_secret_leaves_groq_out_of_the_chain():
    env = {k: v for k, v in os.environ.items() if k not in SECRETS | OPTIONAL_OVERRIDES}
    env.update(GEMINI_API_KEY="k", GROQ_API_KEY="")
    code = "import digest; print(sorted({p for p, _ in digest.build_model_chain()}))"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip().splitlines()[-1] == "['gemini']"
