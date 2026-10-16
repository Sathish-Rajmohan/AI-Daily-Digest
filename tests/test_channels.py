"""Channels: the "channels" list in topics.json, the secrets each one needs,
and sending to each on its own.
"""

from pathlib import Path

import pytest

import digest
import digestlib.channels as channels
from conftest import gemini_reply
from test_main import config, fake_pipeline, sent, topic  # noqa: F401
from test_weekly import REVIEW, folder, wall, weekly_config, write_week  # noqa: F401


class Chat(list):
    """The messages a fake "chat" channel was sent. Set .fail to an exception
    to make the channel fail."""
    fail = None


@pytest.fixture
def chat(monkeypatch):
    box = Chat()

    def send(message, channel):
        if box.fail:
            raise box.fail
        box.append(message)

    monkeypatch.setitem(channels.CHANNELS, "chat", {
        "needs": ("CHAT_TOKEN",), "secrets": ("CHAT_TOKEN",), "html": False, "send": send})
    monkeypatch.setenv("CHAT_TOKEN", "chat-secret-123")
    return box


def with_channels(cfg, *listed):
    return {**cfg, "channels": [c if isinstance(c, dict) else {"type": c} for c in listed]}


def saved_files():
    return sorted(Path(digest.SAVE_DIR).glob("*.json"))


def refuse(subject, body, amp_body=None):
    raise OSError("535 Username and Password not accepted")


# --------------------------------------------------------------------------
# The "channels" list
# --------------------------------------------------------------------------

def test_without_a_channels_list_the_digest_goes_by_email(write_config, mail_env, sent, fake_pipeline,
                                                         capsys):
    write_config(config(topic("Tech")))
    digest.main()
    assert len(sent) == 1
    assert "Sending to email..." in capsys.readouterr().out


@pytest.mark.parametrize("listed, message", [
    ([], "at least one channel"),
    ("email", "at least one channel"),
    ([{"type": "pigeon"}], "needs a 'type'"),
    (["email"], "needs a 'type'"),
    ([{"type": "email"}, {"type": "email"}], "listed twice"),
    ([{"type": "email", "editions": ["monthly"]}], "'editions'"),
    ([{"type": "email", "editions": []}], "'editions'"),
    ([{"type": "email", "editions": ["weekly"]}], "carries the daily edition"),
])
def test_a_bad_channels_list_is_refused(write_config, listed, message):
    write_config({**config(topic("Tech")), "channels": listed})
    with pytest.raises(ValueError, match=message):
        digest.load_config()


def test_the_weekly_edition_needs_a_channel_when_it_is_on(write_config):
    daily_only = [{"type": "email", "editions": ["daily"]}]
    write_config({**weekly_config(), "channels": daily_only})
    with pytest.raises(ValueError, match="no channel in 'channels' carries it"):
        digest.load_config()
    write_config({**weekly_config(enabled=False), "channels": daily_only})
    digest.load_config()


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------

def test_gmail_secrets_are_not_needed_without_email(write_config, sent, fake_pipeline, chat):
    write_config(with_channels(config(topic("Tech")), "chat"))
    digest.main()
    assert len(chat) == 1 and sent == []


def test_a_listed_channel_without_its_secret_stops_the_run(write_config, mail_env, sent, fake_pipeline,
                                                          chat, monkeypatch, capsys):
    monkeypatch.setenv("CHAT_TOKEN", "")
    write_config(with_channels(config(topic("Tech")), "email", "chat"))
    with pytest.raises(SystemExit) as exit_info:
        digest.main()
    assert exit_info.value.code == 1
    assert "missing required env vars: CHAT_TOKEN (the chat channel needs them)" in capsys.readouterr().err
    assert fake_pipeline.events == [] and sent == [] and chat == []


def test_a_secret_quoted_in_an_error_is_hidden(write_config, mail_env, sent, fake_pipeline, chat, capsys):
    chat.fail = OSError("POST https://chat.example/bot/chat-secret-123/send refused")
    write_config(with_channels(config(topic("Tech")), "email", "chat"))
    digest.main()
    err = capsys.readouterr().err
    assert "chat-secret-123" not in err
    assert "chat failed: POST https://chat.example/bot/[CHAT_TOKEN]/send refused" in err


# --------------------------------------------------------------------------
# Sending to each channel on its own
# --------------------------------------------------------------------------

def test_one_failing_channel_does_not_stop_the_others(write_config, mail_env, fake_pipeline, chat,
                                                      monkeypatch, capsys):
    monkeypatch.setattr(digest, "send_email", refuse)
    write_config(with_channels(config(topic("Tech")), "email", "chat"))
    digest.main()
    out, err = capsys.readouterr()
    assert len(chat) == 1
    assert "email failed: 535" in err
    assert "Delivered to chat; failed: email." in out
    assert len(saved_files()) == 1


def test_the_run_fails_only_when_every_channel_failed(write_config, mail_env, fake_pipeline, chat,
                                                      monkeypatch, capsys):
    monkeypatch.setattr(digest, "send_email", refuse)
    chat.fail = OSError("chat is down")
    write_config(with_channels(config(topic("Tech")), "email", "chat"))
    with pytest.raises(SystemExit) as exit_info:
        digest.main()
    assert exit_info.value.code == 1
    assert "couldn't be sent to any channel" in capsys.readouterr().err
    assert saved_files() == []


def test_a_channel_gets_the_digest_as_data(write_config, sent, fake_pipeline, chat):
    write_config(with_channels(config(topic("Tech")), "chat"))
    digest.main()
    [message] = chat
    assert message["edition"] == "daily"
    assert message["subject"].startswith("Daily Digest - ")
    assert [name for name, _, _ in message["topic_results"]] == ["Tech"]
    assert message["fact"] == fake_pipeline.fact
    # The HTML email is only built when a channel sends it.
    assert message["html"] is None and message["amp"] is None


def test_channels_are_sent_in_the_order_listed(write_config, mail_env, fake_pipeline, chat, monkeypatch,
                                               capsys):
    order = []
    monkeypatch.setattr(digest, "send_email", lambda *args: order.append("email"))
    monkeypatch.setitem(channels.CHANNELS["chat"], "send", lambda message, channel: order.append("chat"))
    write_config(with_channels(config(topic("Tech")), "chat", "email"))
    digest.main()
    assert order == ["chat", "email"]
    assert "Delivered to chat, email." in capsys.readouterr().out


# --------------------------------------------------------------------------
# The weekly edition
# --------------------------------------------------------------------------

def test_a_daily_only_channel_gets_no_weekly_edition(write_config, mail_env, sent, wall, transport,
                                                     folder, chat):
    write_config({**weekly_config(), "channels": [
        {"type": "email"}, {"type": "chat", "editions": ["daily"]}]})
    write_week(folder)
    transport.script("gem-a", gemini_reply(REVIEW))
    digest.weekly_main(str(folder))
    assert len(sent) == 1 and chat == []


def test_the_weekly_edition_reaches_a_chat_channel(write_config, sent, wall, transport, folder, chat):
    write_config({**weekly_config(), "channels": [{"type": "chat"}]})
    write_week(folder)
    transport.script("gem-a", gemini_reply(REVIEW))
    digest.weekly_main(str(folder))
    [message] = chat
    assert message["edition"] == "weekly" and sent == []
    assert message["subject"].startswith("Weekly Digest - ")
    assert message["notices"] == []
