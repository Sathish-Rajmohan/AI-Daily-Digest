"""main(): the whole run, from config to a sent email."""

import email
import re
from datetime import datetime, timedelta, timezone

import pytest

import digest
from conftest import SAMPLE_FACT, gemini_reply, make_articles, rfc822, rss, status


def config(*topics, **settings):
    return {"topics": list(topics), "settings": {"timezone": "UTC", **settings}}


def topic(name, **extra):
    return {"name": name, "feeds": [f"https://{name.lower()}.example/rss"], **extra}


def written_brief(name):
    return {
        "overview": f"{name} overview.",
        "stories": [{"subheading": f"{name} lead story", "detail": "Short. Clear.",
                     "sources": [{"title": f"{name} source", "link": f"https://{name.lower()}.example/1",
                                  "outlet": "Outlet"}]}],
    }


@pytest.fixture
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(digest, "send_email", lambda subject, body: box.append((subject, body)))
    return box


@pytest.fixture
def fake_pipeline(monkeypatch):
    """
    Stubs the three expensive steps with recorders. Set `articles`,
    `summaries` or `fact` on the returned object to shape a run; an
    exception instance as a value is raised instead.
    """
    class Pipeline:
        articles = {}
        summaries = {}
        fact = {"field": "physics", "fact": "A fact.", "why": "A reason."}
        events = []

    p = Pipeline()
    p.events = []

    def fetch(topic_cfg, lookback):
        p.events.append(("fetch", topic_cfg["name"] if "name" in topic_cfg else None, lookback))
        value = p.articles.get(topic_cfg.get("name"), make_articles(3))
        if isinstance(value, BaseException):
            raise value
        return value

    def summarize(name, articles, max_developments):
        p.events.append(("summarize", name, max_developments))
        value = p.summaries.get(name, written_brief(name))
        if isinstance(value, BaseException):
            raise value
        return value

    def fact(day):
        p.events.append(("fact", day))
        if isinstance(p.fact, BaseException):
            raise p.fact
        return p.fact

    monkeypatch.setattr(digest, "fetch_topic_articles", fetch)
    monkeypatch.setattr(digest, "summarize_topic", summarize)
    monkeypatch.setattr(digest, "fetch_fact_of_the_day", fact)
    return p


# --------------------------------------------------------------------------
# Refusing to start
# --------------------------------------------------------------------------

@pytest.mark.parametrize("var", ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"])
def test_missing_gmail_settings_exit_before_doing_anything(write_config, mail_env, sent, fake_pipeline,
                                                          monkeypatch, capsys, var):
    write_config(config(topic("Tech")))
    monkeypatch.delenv(var)
    with pytest.raises(SystemExit) as exit_info:
        digest.main()
    assert exit_info.value.code == 1
    assert var in capsys.readouterr().err
    assert fake_pipeline.events == [] and sent == []


def test_no_provider_key_exits(write_config, mail_env, sent, fake_pipeline, monkeypatch, capsys):
    write_config(config(topic("Tech")))
    monkeypatch.setattr(digest, "GEMINI_API_KEY", None)
    with pytest.raises(SystemExit) as exit_info:
        digest.main()
    assert exit_info.value.code == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().err
    assert fake_pipeline.events == []


# A key with an emptied model list used to get past the key check, fetch
# every feed, and then crash with an IndexError on the first topic that had
# articles, before any email could go out.
def test_a_key_with_no_models_exits_cleanly(write_config, mail_env, sent, fake_pipeline, monkeypatch,
                                            with_groq, capsys):
    write_config(config(topic("Tech")))
    monkeypatch.setattr(digest, "GEMINI_API_KEY", None)
    monkeypatch.setattr(digest, "GROQ_MODELS", [])
    with pytest.raises(SystemExit) as exit_info:
        digest.main()
    assert exit_info.value.code == 1
    assert "model" in capsys.readouterr().err.lower()
    assert fake_pipeline.events == []


def test_invalid_config_stops_the_run(write_config, mail_env, sent, fake_pipeline):
    write_config({"topics": [{"name": "Tech", "feeds": "https://one.example/rss"}]})
    with pytest.raises(ValueError):
        digest.main()
    assert sent == []


# --------------------------------------------------------------------------
# Normal runs
# --------------------------------------------------------------------------

def test_happy_path(write_config, mail_env, sent, fake_pipeline):
    write_config(config(topic("Tech"), topic("World"), email_subject_prefix="My Digest"))
    digest.main()

    [(subject, body)] = sent
    assert re.fullmatch(r"My Digest - \w+day, \d{2} \w+ \d{4}", subject)
    assert "Tech overview." in body and "World lead story" in body
    assert "One thing worth knowing" in body and "A fact." in body
    assert "Skipped this run" not in body


def test_defaults_when_settings_are_absent(write_config, mail_env, sent, fake_pipeline):
    write_config({"topics": [{"name": "Tech", "feeds": ["https://x.example/rss"]}]})
    digest.main()
    [(subject, _)] = sent
    assert subject.startswith("Daily Digest - ")
    assert ("fetch", "Tech", 24) in fake_pipeline.events
    assert ("summarize", "Tech", 5) in fake_pipeline.events
    assert any(e[0] == "fact" for e in fake_pipeline.events)


def test_settings_are_passed_through(write_config, mail_env, sent, fake_pipeline):
    write_config(config(topic("Tech", max_stories=8), lookback_hours=36))
    digest.main()
    assert ("fetch", "Tech", 36) in fake_pipeline.events
    assert ("summarize", "Tech", 8) in fake_pipeline.events


def test_unnamed_topic_is_called_untitled(write_config, mail_env, sent, fake_pipeline):
    write_config(config({"feeds": ["https://x.example/rss"]}))
    digest.main()
    assert ("summarize", "Untitled", 5) in fake_pipeline.events


def test_fact_is_fetched_after_every_topic(write_config, mail_env, sent, fake_pipeline):
    write_config(config(topic("Tech"), topic("World")))
    digest.main()
    kinds = [e[0] for e in fake_pipeline.events]
    assert kinds[-1] == "fact"
    assert kinds.count("fact") == 1


def test_fact_uses_the_configured_timezone_date(write_config, mail_env, sent, fake_pipeline):
    write_config(config(topic("Tech"), timezone="Pacific/Kiritimati"))
    digest.main()
    [(_, day)] = [e for e in fake_pipeline.events if e[0] == "fact"]
    from zoneinfo import ZoneInfo
    assert day == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()


def test_pause_between_summarized_topics(write_config, mail_env, sent, fake_pipeline, clock):
    fake_pipeline.articles = {"Empty": []}
    write_config(config(topic("Tech"), topic("Empty"), topic("World")))
    digest.main()
    assert clock.sleeps == [2, 2]


def test_budget_is_started_for_the_run(write_config, mail_env, sent, fake_pipeline):
    write_config(config(topic("Tech")))
    digest.main()
    assert digest._deadline is not None


# --------------------------------------------------------------------------
# Partial failures still send
# --------------------------------------------------------------------------

def test_fetch_failure_is_reported_in_the_email(write_config, mail_env, sent, fake_pipeline):
    fake_pipeline.articles = {"Broken": RuntimeError("DNS failure")}
    write_config(config(topic("Tech"), topic("Broken")))
    digest.main()
    [(_, body)] = sent
    assert "Skipped this run: Broken." in body
    assert "Tech overview." in body


def test_topic_with_no_new_articles_is_left_out_quietly(write_config, mail_env, sent, fake_pipeline):
    fake_pipeline.articles = {"Quiet": []}
    write_config(config(topic("Tech"), topic("Quiet")))
    digest.main()
    [(_, body)] = sent
    assert "Quiet" not in body
    assert ("summarize", "Quiet", 5) not in fake_pipeline.events


# The headline fallback is the path for a day when no model answers. main()
# read a "sources" key the fallback brief doesn't have, so the exact outage
# it exists for crashed the run and no email went out at all.
@pytest.mark.parametrize("failure", [None, RuntimeError("unexpected")])
def test_no_briefing_falls_back_to_headlines(write_config, mail_env, sent, fake_pipeline, capsys, failure):
    fake_pipeline.summaries = {"World": failure}
    write_config(config(topic("Tech"), topic("World")))
    digest.main()
    [(_, body)] = sent
    assert "No summary was available for this topic" in body
    assert "Headline 1" in body
    assert "Tech overview." in body
    assert "listing 3 headlines instead" in capsys.readouterr().out


def test_fact_disabled_is_never_requested(write_config, mail_env, sent, fake_pipeline):
    write_config(config(topic("Tech"), fact_of_the_day=False))
    digest.main()
    assert not any(e[0] == "fact" for e in fake_pipeline.events)
    assert "One thing worth knowing" not in sent[0][1]


@pytest.mark.parametrize("outcome", [None, RuntimeError("fact exploded")])
def test_fact_failure_leaves_the_block_out(write_config, mail_env, sent, fake_pipeline, outcome):
    fake_pipeline.fact = outcome
    write_config(config(topic("Tech")))
    digest.main()
    [(_, body)] = sent
    assert "One thing worth knowing" not in body
    assert "Tech overview." in body


def test_unknown_timezone_falls_back_to_utc(write_config, mail_env, sent, fake_pipeline, capsys):
    write_config(config(topic("Tech"), timezone="Mars/Olympus_Mons"))
    digest.main()
    assert "unknown timezone 'Mars/Olympus_Mons'" in capsys.readouterr().err
    assert len(sent) == 1


def test_every_topic_empty_still_sends_a_stub(write_config, mail_env, sent, fake_pipeline, capsys):
    fake_pipeline.articles = {"Tech": [], "World": []}
    write_config(config(topic("Tech"), topic("World")))
    digest.main()
    [(_, body)] = sent
    assert "No new stories found in the lookback window." in body
    assert "every topic came back empty" in capsys.readouterr().err


def test_send_failure_exits_non_zero(write_config, mail_env, fake_pipeline, monkeypatch, capsys):
    def refuse(subject, body):
        raise OSError("535 Username and Password not accepted")
    monkeypatch.setattr(digest, "send_email", refuse)
    write_config(config(topic("Tech")))
    with pytest.raises(SystemExit) as exit_info:
        digest.main()
    assert exit_info.value.code == 1
    assert "535" in capsys.readouterr().err


# --------------------------------------------------------------------------
# End to end, with only the network faked
# --------------------------------------------------------------------------

def ago(hours):
    return rfc822(datetime.now(timezone.utc) - timedelta(hours=hours))


def html_of(smtp):
    [server] = smtp.instances
    [(_, _, raw)] = server.sent
    msg = email.message_from_string(raw)
    return msg, msg.get_payload()[0].get_payload(decode=True).decode("utf-8")


def test_full_run(write_config, mail_env, feeds, transport, smtp):
    feeds.serve("https://a.example/rss", rss(
        {"title": "A fresh", "link": "https://a.example/1", "pubDate": ago(1), "description": "Alpha."},
        {"title": "A older", "link": "https://a.example/2", "pubDate": ago(2)},
        {"title": "A stale", "link": "https://a.example/3", "pubDate": ago(48)},
        title="Outlet A"))
    feeds.serve("https://b.example/rss", rss(
        {"title": "B fresh", "link": "https://b.example/1", "pubDate": ago(1)}, title="Outlet B"))
    write_config(config({"name": "World", "max_stories": 4,
                         "feeds": ["https://a.example/rss", "https://b.example/rss"]},
                        email_subject_prefix="Digest"))
    # Round robin gives ids 1 = A fresh, 2 = B fresh, 3 = A older.
    transport.script("gem-a",
                     gemini_reply({"stories": [{"article_ids": [1, 2, 42], "subheading": "Two outlets agree",
                                                "detail": "Both outlets covered it."}],
                                   "overview": "One story led."}),
                     gemini_reply(SAMPLE_FACT))

    digest.main()

    msg, body = html_of(smtp)
    assert msg["Subject"].startswith("Digest - ")
    assert "Two outlets agree" in body and "One story led." in body
    assert 'href="https://a.example/1"' in body and 'href="https://b.example/1"' in body
    assert "https://a.example/2" not in body and "A stale" not in body
    assert SAMPLE_FACT["fact"] in body
    prompt = transport.prompt(0)
    assert "A older" in prompt and "A stale" not in prompt
    assert transport.tried == ["gemini/gem-a", "gemini/gem-a"]


def test_full_run_during_a_total_model_outage(write_config, mail_env, feeds, transport, smtp, capsys):
    feeds.serve("https://a.example/rss", rss(
        {"title": "Still news", "link": "https://a.example/1", "pubDate": ago(1)}, title="Outlet A"))
    write_config(config({"name": "World", "feeds": ["https://a.example/rss"]}))
    transport.script("gem-a", status(503))
    transport.script("gem-b", status(503))

    digest.main()

    _, body = html_of(smtp)
    assert "No summary was available for this topic" in body
    assert 'href="https://a.example/1"' in body
    assert "One thing worth knowing" not in body
