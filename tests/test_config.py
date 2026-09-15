"""load_config: the shipped topics.json, and what a broken one does."""

import json

import pytest

import digest
from conftest import ROOT


def topic(**overrides):
    base = {"name": "Tech", "max_stories": 5, "feeds": ["https://feeds.example/rss"]}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# The file that actually ships
# --------------------------------------------------------------------------

@pytest.fixture
def shipped(monkeypatch):
    monkeypatch.setattr(digest, "CONFIG_PATH", str(ROOT / "topics.json"))
    return digest.load_config()


def test_shipped_config_loads(shipped):
    assert shipped["topics"]


def test_every_shipped_topic_is_well_formed(shipped):
    for t in shipped["topics"]:
        assert isinstance(t["name"], str) and t["name"].strip()
        assert isinstance(t["max_stories"], int) and t["max_stories"] >= 1
        assert t["feeds"], f"{t['name']} has no feeds"
        for url in t["feeds"]:
            assert url.startswith("https://"), url


def test_no_feed_is_listed_twice_within_a_topic(shipped):
    for t in shipped["topics"]:
        assert len(t["feeds"]) == len(set(t["feeds"])), t["name"]


def test_shipped_settings_are_valid(shipped):
    s = shipped["settings"]
    assert s["lookback_hours"] > 0
    assert isinstance(s["email_subject_prefix"], str)
    assert isinstance(s["fact_of_the_day"], bool)
    from zoneinfo import ZoneInfo
    ZoneInfo(s["timezone"])


# --------------------------------------------------------------------------
# Valid shapes
# --------------------------------------------------------------------------

def test_minimal_config_without_settings_is_accepted(write_config):
    write_config({"topics": [{"feeds": []}]})
    assert digest.load_config()["topics"] == [{"feeds": []}]


def test_unicode_topic_names_survive(write_config):
    write_config({"topics": [topic(name="Économie 🌍")]})
    assert digest.load_config()["topics"][0]["name"] == "Économie 🌍"


def test_extra_keys_like_comments_are_ignored(write_config):
    write_config({"_comment": "notes", "topics": [topic(_note="x")]})
    assert digest.load_config()["_comment"] == "notes"


# --------------------------------------------------------------------------
# Broken files
# --------------------------------------------------------------------------

def test_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(digest, "CONFIG_PATH", str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        digest.load_config()


def test_invalid_json_raises(write_config):
    write_config('{"topics": [')
    with pytest.raises(json.JSONDecodeError):
        digest.load_config()


@pytest.mark.parametrize("config", [
    {},
    {"topics": "Tech"},
    {"topics": {"name": "Tech"}},
])
def test_topics_must_be_an_array(write_config, config):
    write_config(config)
    with pytest.raises(ValueError, match="topics"):
        digest.load_config()


# Each of these used to load without complaint and then fail somewhere far
# from the typo, or not fail at all. A feeds value written as a string, for
# instance, was iterated character by character, every "URL" failed, and the
# topic silently reported no new articles.
@pytest.mark.parametrize("bad_topic, field", [
    ("just a string", "topic"),
    (topic(feeds="https://feeds.example/rss"), "feeds"),
    (topic(feeds=["https://ok.example/rss", 123]), "feeds"),
    (topic(max_stories="8"), "max_stories"),
    (topic(max_stories=0), "max_stories"),
    (topic(max_stories=True), "max_stories"),
    (topic(name=42), "name"),
])
def test_malformed_topic_is_rejected_with_a_clear_message(write_config, bad_topic, field):
    write_config({"topics": [topic(name="Fine"), bad_topic]})
    with pytest.raises(ValueError, match=field):
        digest.load_config()


@pytest.mark.parametrize("settings, field", [
    ("not an object", "settings"),
    ({"lookback_hours": "24"}, "lookback_hours"),
    ({"lookback_hours": 0}, "lookback_hours"),
    ({"lookback_hours": True}, "lookback_hours"),
    ({"fact_of_the_day": "false"}, "fact_of_the_day"),
    ({"email_subject_prefix": 7}, "email_subject_prefix"),
    ({"timezone": 10}, "timezone"),
])
def test_malformed_settings_are_rejected_with_a_clear_message(write_config, settings, field):
    write_config({"topics": [topic()], "settings": settings})
    with pytest.raises(ValueError, match=field):
        digest.load_config()


def test_fractional_lookback_is_allowed(write_config):
    write_config({"topics": [topic()], "settings": {"lookback_hours": 12.5}})
    assert digest.load_config()["settings"]["lookback_hours"] == 12.5
