"""The Discord channel, against a fake webhook."""

import pytest

import digest
from conftest import FakeResponse
from test_main import config, fake_pipeline, sent, topic  # noqa: F401

WEBHOOK = "https://discord.com/api/webhooks/111222333/secret-webhook-token"


class FakeWebhook:
    """Records each post. Queue (status, payload) pairs in .replies; an
    exception is raised instead. Other addresses go to `other`."""

    def __init__(self, other):
        self.calls = []
        self.replies = []
        self.other = other

    def __call__(self, url, json=None, timeout=None, **kwargs):
        if url != WEBHOOK:
            return self.other(url, json=json, timeout=timeout, **kwargs)
        self.calls.append(json)
        reply = self.replies.pop(0) if self.replies else (204, None)
        if isinstance(reply, BaseException):
            raise reply
        return FakeResponse(*reply)

    @property
    def texts(self):
        return [call["embeds"][0]["description"] for call in self.calls]


@pytest.fixture
def discord(monkeypatch):
    fake = FakeWebhook(digest.requests.post)
    monkeypatch.setattr(digest.requests, "post", fake)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    return fake


def to_discord(cfg, *also):
    return {**cfg, "channels": [{"type": "discord"}, *({"type": t} for t in also)]}


def test_each_topic_is_one_embed_with_mentions_off(write_config, sent, fake_pipeline, discord, clock):
    write_config(to_discord(config(topic("Tech"), topic("World"), fact_of_the_day=False)))
    digest.main()
    assert len(discord.calls) == 2
    for call in discord.calls:
        assert call["allowed_mentions"] == {"parse": []}
        assert list(call) == ["embeds", "allowed_mentions"] and len(call["embeds"]) == 1
    assert discord.texts[0].startswith("**Daily Digest \\- ")
    assert "**Tech**\n\nTech overview.\n\n• [Tech lead story](https://tech.example/1)" in discord.texts[0]
    assert clock.sleeps.count(1.0) == 1


def test_a_planted_everyone_pings_no_one(write_config, sent, fake_pipeline, discord):
    fake_pipeline.summaries = {"Tech": {"overview": "Hi @everyone and @here <@123>", "stories": [
        {"subheading": "**Bold** [fake](https://evil.example) `code`", "detail": "x",
         "sources": [{"title": "t", "link": "https://a.example/wiki/A_(b) c", "outlet": "o"}]}]}}
    write_config(to_discord(config(topic("Tech"), fact_of_the_day=False)))
    digest.main()
    [call] = discord.calls
    text = call["embeds"][0]["description"]
    assert call["allowed_mentions"] == {"parse": []}
    assert "Hi \\@everyone and \\@here \\<\\@123\\>" in text
    assert ("[\\*\\*Bold\\*\\* \\[fake\\]\\(https\\://evil.example\\) \\`code\\`]"
            "(https://a.example/wiki/A_%28b%29%20c)") in text


def test_a_broken_webhook_leaves_the_email_delivered(write_config, mail_env, sent, fake_pipeline, discord,
                                                     capsys):
    discord.replies = [(404, {"message": "Unknown Webhook", "code": 10015})]
    write_config(to_discord(config(topic("Tech"), fact_of_the_day=False), "email"))
    digest.main()
    out, err = capsys.readouterr()
    assert len(sent) == 1
    assert "discord failed: Discord answered 404: Unknown Webhook" in err
    assert "secret-webhook-token" not in out + err


def test_an_error_that_quotes_the_path_hides_the_token(write_config, mail_env, sent, fake_pipeline,
                                                       discord, capsys):
    discord.replies = [digest.requests.exceptions.ConnectionError(
        "Max retries exceeded with url: /api/webhooks/111222333/secret-webhook-token")]
    write_config(to_discord(config(topic("Tech"), fact_of_the_day=False), "email"))
    digest.main()
    err = capsys.readouterr().err
    assert "secret-webhook-token" not in err
    assert "url: [DISCORD_WEBHOOK_URL path]" in err


def test_a_429_waits_as_told_and_tries_once_more(write_config, sent, fake_pipeline, discord, clock):
    discord.replies = [(429, {"message": "You are being rate limited.", "retry_after": 1.5,
                              "global": False})]
    write_config(to_discord(config(topic("Tech"), fact_of_the_day=False)))
    digest.main()
    assert len(discord.calls) == 2 and 1.5 in clock.sleeps


def test_a_missing_webhook_stops_the_run_before_any_work(write_config, sent, fake_pipeline, discord,
                                                         monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", " ")
    write_config(to_discord(config(topic("Tech"))))
    with pytest.raises(SystemExit):
        digest.main()
    assert "DISCORD_WEBHOOK_URL (the discord channel needs them)" in capsys.readouterr().err
    assert fake_pipeline.events == [] and discord.calls == []
