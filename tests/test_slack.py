"""The Slack channel, against a fake webhook, and every channel in one run."""

import pytest

import digest
from conftest import FakeResponse
from test_discord import WEBHOOK as DISCORD_WEBHOOK, discord  # noqa: F401
from test_main import config, fake_pipeline, sent, topic  # noqa: F401
from test_telegram import TOKEN as TELEGRAM_TOKEN, telegram  # noqa: F401

WEBHOOK = "https://hooks.slack.com/services/T000/B000/secret-slack-token"


class FakeSlack:
    """Records each post. Queue (status, text, headers) triples in .replies;
    an exception is raised instead. Other addresses go to `other`."""

    def __init__(self, other):
        self.calls = []
        self.replies = []
        self.other = other

    def __call__(self, url, json=None, timeout=None, **kwargs):
        if url != WEBHOOK:
            return self.other(url, json=json, timeout=timeout, **kwargs)
        self.calls.append(json)
        reply = self.replies.pop(0) if self.replies else (200, "ok", None)
        if isinstance(reply, BaseException):
            raise reply
        status, text, headers = reply
        return FakeResponse(status, None, headers, text)

    @property
    def texts(self):
        return [call["blocks"][0]["text"]["text"] for call in self.calls]


@pytest.fixture
def slack(monkeypatch):
    fake = FakeSlack(digest.requests.post)
    monkeypatch.setattr(digest.requests, "post", fake)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", WEBHOOK)
    return fake


def to_slack(cfg, *also):
    return {**cfg, "channels": [{"type": "slack"}, *({"type": t} for t in also)]}


def test_each_topic_is_one_section_with_previews_off(write_config, sent, fake_pipeline, slack, clock):
    write_config(to_slack(config(topic("Tech"), topic("World"), fact_of_the_day=False)))
    digest.main()
    assert len(slack.calls) == 2
    first = slack.calls[0]
    assert first["unfurl_links"] is False and first["unfurl_media"] is False
    assert first["text"] == first["blocks"][0]["text"]["text"]
    assert first["blocks"][0]["text"]["type"] == "mrkdwn"
    assert slack.texts[0].startswith("*Daily Digest - ")
    assert "*Tech*\n\nTech overview.\n\n• <https://tech.example/1|Tech lead story>" in slack.texts[0]
    assert all(len(text) <= 3000 for text in slack.texts)
    assert clock.sleeps.count(1.0) == 1


def test_a_headline_cannot_ping_the_channel_or_add_a_link(write_config, sent, fake_pipeline, slack):
    fake_pipeline.summaries = {"Tech": {"overview": "<!channel> & <@U123> news", "stories": [
        {"subheading": "<https://evil.example|click>", "detail": "x",
         "sources": [{"title": "t", "link": "https://a.example/?q=a|b>c", "outlet": "o"}]}]}}
    write_config(to_slack(config(topic("Tech"), fact_of_the_day=False)))
    digest.main()
    [text] = slack.texts
    assert "&lt;!channel&gt; &amp; &lt;@U123&gt; news" in text
    assert "<https://a.example/?q=a%7Cb%3Ec|&lt;https://evil.example|click&gt;>" in text
    assert "<!channel>" not in text


def test_a_broken_webhook_leaves_the_email_delivered(write_config, mail_env, sent, fake_pipeline, slack,
                                                     capsys):
    slack.replies = [(404, "no_service", None)]
    write_config(to_slack(config(topic("Tech"), fact_of_the_day=False), "email"))
    digest.main()
    out, err = capsys.readouterr()
    assert len(sent) == 1
    assert "slack failed: Slack answered 404: no_service" in err
    assert "secret-slack-token" not in out + err


def test_a_429_waits_for_retry_after_and_tries_once_more(write_config, sent, fake_pipeline, slack, clock):
    slack.replies = [(429, "rate_limited", {"Retry-After": "2"})]
    write_config(to_slack(config(topic("Tech"), fact_of_the_day=False)))
    digest.main()
    assert len(slack.calls) == 2 and 2 in clock.sleeps


# --------------------------------------------------------------------------
# Every channel in one run
# --------------------------------------------------------------------------

def test_a_broken_telegram_token_still_leaves_the_other_three_delivered(
        write_config, mail_env, sent, fake_pipeline, telegram, discord, slack, capsys):
    telegram.replies = [(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
    fake_pipeline.summaries = {"Tech": {"overview": "@everyone <!channel> look", "stories": [
        {"subheading": "@here story", "detail": "x",
         "sources": [{"title": "t", "link": "https://a.example/1", "outlet": "o"}]}]}}
    write_config({**config(topic("Tech"), fact_of_the_day=False), "channels": [
        {"type": "email"}, {"type": "telegram"}, {"type": "discord"}, {"type": "slack"}]})
    digest.main()
    out, err = capsys.readouterr()
    assert len(sent) == 1 and len(discord.calls) == 1 and len(slack.calls) == 1
    assert "Delivered to email, discord, slack; failed: telegram." in out
    assert discord.calls[0]["allowed_mentions"] == {"parse": []}
    assert "<!channel>" not in slack.texts[0]
    for secret in (TELEGRAM_TOKEN, "secret-webhook-token", "secret-slack-token", DISCORD_WEBHOOK, WEBHOOK):
        assert secret not in out + err
