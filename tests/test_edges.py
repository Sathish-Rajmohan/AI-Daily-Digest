"""Edge cases that cut across modules, and paths the per-module files don't reach."""

import re
from datetime import datetime, timedelta, timezone

import digest
from conftest import gemini_reply, rfc822, rss

FIXED_NOW = datetime(2026, 9, 15, 22, 30, tzinfo=timezone.utc)


class FixedDateTime(datetime):
    """datetime with now() pinned, so boundary tests don't race the clock."""

    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW.astimezone(tz) if tz else FIXED_NOW.replace(tzinfo=None)


def recent(minutes):
    return rfc822(datetime.now(timezone.utc) - timedelta(minutes=minutes))


# --------------------------------------------------------------------------
# Links and feeds
# --------------------------------------------------------------------------

def test_a_malformed_or_hostless_url_is_not_a_link():
    assert digest._web_link("http://[::1") == ""
    assert digest._web_link("https://") == ""
    assert digest._web_link(None) == ""


def test_lookback_boundary_keeps_the_edge_and_future_dated_items(feeds, monkeypatch):
    monkeypatch.setattr(digest, "datetime", FixedDateTime)
    url = "https://news.example/a.xml"
    feeds.serve(url, rss(
        {"title": "exactly 24h old", "link": "https://news.example/1",
         "pubDate": rfc822(FIXED_NOW - timedelta(hours=24))},
        {"title": "a second too old", "link": "https://news.example/2",
         "pubDate": rfc822(FIXED_NOW - timedelta(hours=24, seconds=1))},
        {"title": "stamped two hours ahead", "link": "https://news.example/3",
         "pubDate": rfc822(FIXED_NOW + timedelta(hours=2))},
    ))
    titles = [a["title"] for a in digest.fetch_topic_articles({"feeds": [url]}, 24)]
    assert titles == ["stamped two hours ahead", "exactly 24h old"]


def test_a_broken_feed_is_skipped_with_a_warning_and_the_rest_still_read(feeds, capsys):
    broken = "https://broken.example/rss"
    fine = "https://fine.example/rss"
    feeds.serve(broken, b"<?xml version='1.0'?><rss><channel><title>Cut off mid-tag</title")
    feeds.serve(fine, rss({"title": "still here", "link": "https://fine.example/1", "pubDate": recent(1)}))
    titles = [a["title"] for a in digest.fetch_topic_articles({"feeds": [broken, fine]}, 24)]
    assert titles == ["still here"]
    assert f"feed unreadable, skipping: {broken}" in capsys.readouterr().err


def test_a_link_repeated_inside_one_feed_is_kept_once(feeds):
    url = "https://news.example/a.xml"
    feeds.serve(url, rss(
        {"title": "first copy", "link": "https://news.example/same", "pubDate": recent(1)},
        {"title": "second copy", "link": "https://news.example/same", "pubDate": recent(2)},
    ))
    assert [a["title"] for a in digest.fetch_topic_articles({"feeds": [url]}, 24)] == ["first copy"]


def test_a_busy_topic_is_capped_evenly_across_its_feeds(feeds):
    urls = [f"https://feed{i}.example/rss" for i in range(3)]
    for i, url in enumerate(urls):
        feeds.serve(url, rss(*[{"title": f"F{i}-{n}", "link": f"{url}/{n}", "pubDate": recent(n + 1)}
                               for n in range(60)], title=f"Feed {i}"))
    articles = digest.fetch_topic_articles({"feeds": urls}, 24)
    assert len(articles) == digest.MAX_ARTICLES_PER_TOPIC
    per_feed = sorted(sum(a["source"] == f"Feed {i}" for a in articles) for i in range(3))
    assert per_feed == [33, 33, 34]
    assert len({a["link"] for a in articles}) == len(articles)


# --------------------------------------------------------------------------
# Model calls
# --------------------------------------------------------------------------

def test_connection_error_with_no_time_left_to_back_off(transport, clock, no_jitter):
    transport.script("gem-a", digest.requests.exceptions.ConnectionError("reset"))
    digest.start_budget()
    clock.now += digest.TOTAL_BUDGET - 1
    assert digest._call_model("gemini", "gem-a", "p", "T", "s", digest.BRIEF_SCHEMA) is None
    assert len(transport.calls) == 1
    assert clock.sleeps == [1]


def test_zero_attempts_per_model_makes_no_request(transport, monkeypatch):
    monkeypatch.setattr(digest, "ATTEMPTS_PER_MODEL", 0)
    transport.script("gem-a", gemini_reply({}))
    assert digest._call_model("gemini", "gem-a", "p", "T", "s", digest.BRIEF_SCHEMA) is None
    assert transport.calls == []


# --------------------------------------------------------------------------
# Rendering at the edges of the palette and the counts
# --------------------------------------------------------------------------

ONE_STORY = {"overview": "O.", "stories": [{"subheading": "S", "detail": "D.", "sources": []}]}


def test_topics_beyond_the_palette_reuse_inks_in_order():
    names = [f"Topic {i}" for i in range(len(digest._TOPIC_INKS) + 2)]
    results = [(name, ONE_STORY, None) for name in names]
    out = digest.build_html(results, "D")
    amp = digest.build_amp(results, "D")
    first_extra = names[len(digest._TOPIC_INKS)]
    assert f'color:{digest._TOPIC_INKS[0]};">{first_extra}</div>' in out
    assert f'<div class="topic ink0"><div class="topic-name">{first_extra}</div>' in amp
    widths = [float(w) for w in re.findall(r'<td width="([\d.]+)%"', out)]
    assert len(widths) == len(names)
    assert abs(sum(widths) - 100) < 0.5


def test_a_topic_with_no_stories_is_in_the_key_but_not_the_strip():
    results = [("Busy", ONE_STORY, None), ("Quiet", {"overview": "Nothing big.", "stories": []}, None)]
    out = digest.build_html(results, "D")
    assert len(re.findall(r'<td width="', out)) == 1
    assert ">Quiet</span>" in out
    assert "0 stories" in out
    assert "1 story from" not in out  # no outlets cited, so no "from" clause
    assert "1 story · about 1 min read" in out


def test_summary_counts_are_zero_safe():
    summary = digest._digest_summary([("Quiet", {"overview": "", "stories": []}, None)], None, True)
    assert summary == {"topics": [{"index": 0, "name": "Quiet", "count": 0}],
                       "stories": 0, "outlets": 0, "minutes": 1}
    assert digest._summary_line(summary, True) == ""
    assert digest._footer_text(summary) == "Change topics and sources in topics.json."
