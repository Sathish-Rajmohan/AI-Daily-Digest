"""The Telegram channel, against a fake of the Bot API."""

import pytest

import digest
from conftest import FakeResponse, gemini_reply
from digestlib.channels.telegram import TELEGRAM_API
from test_main import config, fake_pipeline, sent, topic  # noqa: F401
from test_weekly import REVIEW, folder, wall, weekly_config, write_week  # noqa: F401

TOKEN = "123456:secret-bot-token"


class FakeTelegram:
    """Records each sendMessage call. Queue (status, payload) pairs in
    .replies to shape the answers; an exception is raised instead. Other
    addresses go to `other`, the fake that was in place before."""

    def __init__(self, other):
        self.calls = []
        self.replies = []
        self.other = other

    def __call__(self, url, json=None, timeout=None, **kwargs):
        if not url.startswith(TELEGRAM_API):
            return self.other(url, json=json, timeout=timeout, **kwargs)
        assert url == f"{TELEGRAM_API}/bot{TOKEN}/sendMessage"
        self.calls.append(json)
        reply = self.replies.pop(0) if self.replies else (200, {"ok": True, "result": {}})
        if isinstance(reply, BaseException):
            raise reply
        return FakeResponse(*reply)

    @property
    def texts(self):
        return [call["text"] for call in self.calls]


@pytest.fixture
def telegram(monkeypatch):
    fake = FakeTelegram(digest.requests.post)
    monkeypatch.setattr(digest.requests, "post", fake)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234")
    return fake


def to_telegram(cfg, **channel):
    return {**cfg, "channels": [{"type": "telegram", **channel}]}


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

def test_each_topic_is_one_message_to_the_chat(write_config, sent, fake_pipeline, telegram, clock):
    write_config(to_telegram(config(topic("Tech"), topic("World"))))
    digest.main()
    assert sent == []
    # Two topics and the fact of the day.
    assert len(telegram.calls) == 3
    first = telegram.calls[0]
    assert first["chat_id"] == "-1001234"
    assert first["parse_mode"] == "HTML"
    assert first["link_preview_options"] == {"is_disabled": True}
    assert first["text"].startswith("<b>Daily Digest - ")
    assert '<b>Tech</b>\n\nTech overview.\n\n• <a href="https://tech.example/1">Tech lead story</a>' \
        in first["text"]
    assert telegram.texts[2].startswith("<b>Fact of the day · physics</b>")
    # One second between messages, as Telegram asks.
    assert clock.sleeps.count(1.0) == 2


def test_story_text_is_a_setting_of_the_channel(write_config, sent, fake_pipeline, telegram):
    write_config(to_telegram(config(topic("Tech"), fact_of_the_day=False), story_text=True))
    digest.main()
    assert telegram.texts[0].endswith("Tech lead story</a>\nShort. Clear.")


def test_a_headline_cannot_add_markup_or_mention_anyone(write_config, sent, fake_pipeline, telegram):
    fake_pipeline.summaries = {"Tech": {"overview": "<i>Big</i> news & more", "stories": [
        {"subheading": "@everyone <b>look</b>", "detail": "x",
         "sources": [{"title": "t", "link": 'https://a.example/"><b>', "outlet": "o"}]}]}}
    write_config(to_telegram(config(topic("Tech"), fact_of_the_day=False)))
    digest.main()
    [text] = telegram.texts
    assert "&lt;i&gt;Big&lt;/i&gt; news &amp; more" in text
    assert ('<a href="https://a.example/&quot;&gt;&lt;b&gt;">'
            "@⁠everyone &lt;b&gt;look&lt;/b&gt;</a>") in text


def test_the_weekly_edition_goes_to_telegram(write_config, sent, wall, transport, folder, telegram):
    write_config({**weekly_config(), "channels": [{"type": "telegram"}]})
    write_week(folder)
    transport.script("gem-a", gemini_reply(REVIEW))
    digest.weekly_main(str(folder))
    [text] = telegram.texts
    assert text.startswith("<b>Weekly Digest - ")
    assert "Talks restart · Mon and Wed" in text


# --------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------

def test_a_broken_token_leaves_the_email_delivered(write_config, mail_env, sent, fake_pipeline, telegram,
                                                   capsys):
    telegram.replies = [(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
    write_config({**config(topic("Tech"), fact_of_the_day=False),
                  "channels": [{"type": "email"}, {"type": "telegram"}]})
    digest.main()
    out, err = capsys.readouterr()
    assert len(sent) == 1
    assert "telegram failed: Telegram answered 401: Unauthorized" in err
    assert "Delivered to email; failed: telegram." in out
    assert TOKEN not in out + err


def test_an_error_that_quotes_the_address_hides_the_token(write_config, mail_env, sent, fake_pipeline,
                                                          telegram, capsys):
    telegram.replies = [digest.requests.exceptions.ConnectionError(
        f"Max retries exceeded with url: /bot{TOKEN}/sendMessage")]
    write_config({**config(topic("Tech"), fact_of_the_day=False),
                  "channels": [{"type": "email"}, {"type": "telegram"}]})
    digest.main()
    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "/bot[TELEGRAM_BOT_TOKEN]/sendMessage" in err


def test_a_429_waits_as_told_and_tries_once_more(write_config, sent, fake_pipeline, telegram, clock):
    telegram.replies = [(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                               "parameters": {"retry_after": 3}})]
    write_config(to_telegram(config(topic("Tech"), fact_of_the_day=False)))
    digest.main()
    assert len(telegram.calls) == 2
    assert 3 in clock.sleeps


def test_a_second_429_fails_the_channel(write_config, sent, fake_pipeline, telegram, capsys):
    busy = (429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 3}})
    telegram.replies = [busy, busy]
    write_config(to_telegram(config(topic("Tech"), fact_of_the_day=False)))
    with pytest.raises(SystemExit):
        digest.main()
    assert "Telegram answered 429: Too Many Requests" in capsys.readouterr().err


def test_a_missing_chat_id_stops_the_run_before_any_work(write_config, sent, fake_pipeline, telegram,
                                                         monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    write_config(to_telegram(config(topic("Tech"))))
    with pytest.raises(SystemExit):
        digest.main()
    assert "TELEGRAM_CHAT_ID (the telegram channel needs them)" in capsys.readouterr().err
    assert fake_pipeline.events == [] and telegram.calls == []
