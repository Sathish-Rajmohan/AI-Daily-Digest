"""Feed fetching, date parsing, cleaning, dedup and round-robin selection."""

import threading
import time as real_time
from datetime import datetime, timedelta, timezone

import pytest

import digest
from conftest import REAL_PARSE, FeedResponse, rfc822, rss

URL_A = "https://news.example/a.xml"
URL_B = "https://news.example/b.xml"


def ago(hours):
    return rfc822(datetime.now(timezone.utc) - timedelta(hours=hours))


def one_topic(*urls):
    return {"name": "T", "feeds": list(urls)}


def first_entry(xml):
    return REAL_PARSE(xml).entries[0]


# --------------------------------------------------------------------------
# parse_entry_time
# --------------------------------------------------------------------------

def test_offset_timestamp_is_converted_to_utc():
    got = digest.parse_entry_time({"published": "2026-09-15T10:00:00+10:00"})
    assert got == datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)


def test_naive_timestamp_is_taken_as_utc():
    got = digest.parse_entry_time({"published": "2026-09-15 06:30:00"})
    assert got == datetime(2026, 9, 15, 6, 30, tzinfo=timezone.utc)


def test_updated_is_used_when_published_is_absent():
    got = digest.parse_entry_time({"updated": "2026-09-15T01:00:00Z"})
    assert got == datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)


def test_struct_is_used_when_strings_are_absent():
    struct = real_time.struct_time((2026, 9, 15, 1, 2, 3, 0, 258, 0))
    got = digest.parse_entry_time({"updated_parsed": struct})
    assert got == datetime(2026, 9, 15, 1, 2, 3, tzinfo=timezone.utc)


def test_garbage_string_falls_back_to_struct():
    struct = real_time.struct_time((2026, 9, 15, 4, 0, 0, 0, 258, 0))
    got = digest.parse_entry_time({"published": "not a date", "published_parsed": struct})
    assert got == datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc)


def test_nothing_usable_returns_none():
    assert digest.parse_entry_time({}) is None
    assert digest.parse_entry_time({"published": "soon"}) is None


def test_impossible_struct_returns_none():
    struct = real_time.struct_time((2026, 13, 40, 0, 0, 0, 0, 0, 0))
    assert digest.parse_entry_time({"published_parsed": struct}) is None


def test_absurd_year_does_not_raise():
    digest.parse_entry_time({"published": "Tue, 15 Sep 99999999999 08:00:00 +0000"})


# dateutil treats zone abbreviations like EDT as UTC, putting US feeds hours
# out. feedparser reads them correctly.
@pytest.mark.parametrize("stamp, expected_hour", [
    ("Tue, 15 Sep 2026 08:00:00 EDT", 12),
    ("Tue, 15 Sep 2026 08:00:00 PST", 16),
    ("Tue, 15 Sep 2026 08:00:00 GMT", 8),
    ("Tue, 15 Sep 2026 08:00:00 +0530", 2),
])
def test_zone_abbreviations_from_real_feeds_resolve_correctly(stamp, expected_hour):
    entry = first_entry(rss({"title": "x", "pubDate": stamp}))
    got = digest.parse_entry_time(entry)
    assert got == datetime(2026, 9, 15, expected_hour, 30 if "+0530" in stamp else 0,
                           tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# fetch_feed / _safe_fetch_feed
# --------------------------------------------------------------------------

def test_fetch_sends_timeout_and_user_agent(feeds):
    feeds.serve(URL_A, rss({"title": "Hello", "link": "https://news.example/1"}))
    parsed = digest.fetch_feed(URL_A)
    assert parsed.entries[0].title == "Hello"
    request = feeds.requested[0]
    assert request["timeout"] == digest.FEED_FETCH_TIMEOUT
    assert request["headers"]["User-Agent"] == digest.USER_AGENT


@pytest.mark.parametrize("failure", ["http_error", "connection_error"])
def test_failed_http_fetch_falls_back_to_feedparser(feeds, failure, capsys):
    if failure == "http_error":
        feeds.serve(URL_A, b"Forbidden", status_code=403)
    else:
        feeds.fail(URL_A)
    feeds.serve_fallback(URL_A, rss({"title": "Via fallback"}))

    parsed = digest.fetch_feed(URL_A)

    assert parsed.entries[0].title == "Via fallback"
    assert feeds.fallback_requested == [URL_A]
    assert "trying feedparser" in capsys.readouterr().err


def test_safe_fetch_returns_none_instead_of_raising(monkeypatch, capsys):
    def explode(url):
        raise RuntimeError("parser blew up")
    monkeypatch.setattr(digest, "fetch_feed", explode)
    assert digest._safe_fetch_feed(URL_A) is None
    assert URL_A in capsys.readouterr().err


# --------------------------------------------------------------------------
# _interleave_by_feed
# --------------------------------------------------------------------------

def test_round_robin_order():
    by_feed = [["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]
    assert digest._interleave_by_feed(by_feed, 10) == ["a1", "b1", "c1", "a2", "c2", "a3"]


def test_cap_can_cut_a_round_short():
    assert digest._interleave_by_feed([["a1", "a2"], ["b1", "b2"], ["c1"]], 2) == ["a1", "b1"]


@pytest.mark.parametrize("by_feed, cap", [([], 5), ([["a"]], 0), ([[], []], 3)])
def test_degenerate_inputs_give_empty_list(by_feed, cap):
    assert digest._interleave_by_feed(by_feed, cap) == []


def test_one_prolific_feed_cannot_crowd_out_the_others():
    busy = [f"wire{i}" for i in range(100)]
    picked = digest._interleave_by_feed([busy, ["bbc"], ["guardian"]], 5)
    assert "bbc" in picked and "guardian" in picked
    assert len(picked) == 5


def test_cap_above_total_returns_everything_once():
    by_feed = [["a1", "a2"], ["b1"]]
    assert sorted(digest._interleave_by_feed(by_feed, 99)) == ["a1", "a2", "b1"]


# --------------------------------------------------------------------------
# fetch_topic_articles
# --------------------------------------------------------------------------

def test_article_shape(feeds):
    feeds.serve(URL_A, rss({"title": "T", "link": "https://news.example/1",
                            "description": "S", "pubDate": ago(1)}, title="Outlet A"))
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert set(article) == {"title", "link", "summary", "source", "published"}
    assert article["source"] == "Outlet A"
    assert datetime.fromisoformat(article["published"]).tzinfo is not None


def test_articles_outside_the_lookback_window_are_dropped(feeds):
    feeds.serve(URL_A, rss(
        {"title": "fresh", "link": "https://news.example/1", "pubDate": ago(1)},
        {"title": "stale", "link": "https://news.example/2", "pubDate": ago(30)},
    ))
    titles = [a["title"] for a in digest.fetch_topic_articles(one_topic(URL_A), 24)]
    assert titles == ["fresh"]


def test_undated_articles_are_kept_after_dated_ones(feeds):
    feeds.serve(URL_A, rss(
        {"title": "undated", "link": "https://news.example/1"},
        {"title": "dated", "link": "https://news.example/2", "pubDate": ago(2)},
    ))
    articles = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert [a["title"] for a in articles] == ["dated", "undated"]
    assert articles[1]["published"] is None


def test_freshest_first_within_a_feed(feeds):
    feeds.serve(URL_A, rss(
        {"title": "5h", "link": "https://news.example/5", "pubDate": ago(5)},
        {"title": "1h", "link": "https://news.example/1", "pubDate": ago(1)},
        {"title": "3h", "link": "https://news.example/3", "pubDate": ago(3)},
    ))
    titles = [a["title"] for a in digest.fetch_topic_articles(one_topic(URL_A), 24)]
    assert titles == ["1h", "3h", "5h"]


def test_same_link_in_two_feeds_is_kept_once_from_the_first_listed(feeds):
    shared = "https://news.example/shared"
    feeds.serve(URL_A, rss({"title": "From A", "link": shared, "pubDate": ago(1)}, title="A"))
    feeds.serve(URL_B, rss({"title": "From B", "link": shared, "pubDate": ago(1)}, title="B"))
    articles = digest.fetch_topic_articles(one_topic(URL_A, URL_B), 24)
    assert [(a["title"], a["source"]) for a in articles] == [("From A", "A")]


def test_linkless_articles_are_not_deduped_against_each_other(feeds):
    feeds.serve(URL_A, rss({"title": "one", "pubDate": ago(1)}, {"title": "two", "pubDate": ago(2)}))
    assert len(digest.fetch_topic_articles(one_topic(URL_A), 24)) == 2


def test_markup_is_stripped_from_summaries(feeds):
    feeds.serve(URL_A, rss({"title": "t", "link": "https://news.example/1", "pubDate": ago(1),
                            "description": "&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;"}))
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert article["summary"] == "Hello world"


# Summaries arrive as HTML, so entities remain after the tags are removed.
@pytest.mark.parametrize("description, expected", [
    ("&lt;p&gt;AT&amp;amp;T &amp;amp; Verizon&amp;nbsp;merge&amp;#8217;s&lt;/p&gt;",
     "AT&T & Verizon merge’s"),
    ("Fish &amp;amp; chips", "Fish & chips"),
])
def test_entities_are_decoded_in_summaries(feeds, description, expected):
    feeds.serve(URL_A, rss({"title": "t", "link": "https://news.example/1", "pubDate": ago(1),
                            "description": description}))
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert article["summary"] == expected


# Titles are cleaned the same way. The headline fallback shows them
# directly.
@pytest.mark.parametrize("raw_title, expected", [
    ("AT&amp;T &lt;b&gt;deal&lt;/b&gt;", "AT&T deal"),
    ("Q&amp;A with the minister", "Q&A with the minister"),
    ("Plain title", "Plain title"),
])
def test_titles_are_cleaned_of_markup_and_entities(feeds, raw_title, expected):
    feeds.serve(URL_A, rss({"title": raw_title, "link": "https://news.example/1", "pubDate": ago(1)}))
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert article["title"] == expected


@pytest.mark.parametrize("item", [
    {"link": "https://news.example/1"},
    {"title": "&lt;b&gt;&lt;/b&gt;", "link": "https://news.example/1"},
    {"title": "   ", "link": "https://news.example/1"},
])
def test_missing_or_empty_titles_become_untitled(feeds, item):
    feeds.serve(URL_A, rss(dict(item, pubDate=ago(1))))
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert article["title"] == "(untitled)"


# An empty feed title would otherwise leave a blank outlet name.
@pytest.mark.parametrize("channel_title", ["", "   ", None])
def test_feed_without_a_usable_title_is_named_by_its_host(feeds, channel_title):
    feeds.serve(URL_A, rss({"title": "t", "link": "https://news.example/1", "pubDate": ago(1)},
                           title=channel_title))
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert article["source"] == "news.example"


# Feed links become hrefs in the email, so only web links are kept.
@pytest.mark.parametrize("link, expected", [
    ("javascript:alert(1)", ""),
    ("JavaScript:alert(1)", ""),
    ("data:text/html;base64,PHNjcmlwdD4=", ""),
    ("/relative/path", ""),
    ("https://news.example/ok", "https://news.example/ok"),
    ("HTTP://news.example/ok", "HTTP://news.example/ok"),
])
def test_only_web_links_are_kept(feeds, link, expected):
    feeds.serve(URL_A, rss({"title": "t", "link": link, "pubDate": ago(1)}))
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert article["link"] == expected


def test_a_dead_feed_does_not_stop_the_others(feeds):
    feeds.fail(URL_A)
    feeds.serve(URL_B, rss({"title": "alive", "link": "https://news.example/1", "pubDate": ago(1)}))
    titles = [a["title"] for a in digest.fetch_topic_articles(one_topic(URL_A, URL_B), 24)]
    assert titles == ["alive"]
    assert feeds.fallback_requested == [URL_A]


def test_an_html_page_instead_of_a_feed_yields_nothing(feeds):
    feeds.serve(URL_A, b"<html><body><h1>Subscribe!</h1></body></html>")
    assert digest.fetch_topic_articles(one_topic(URL_A), 24) == []


def test_a_feed_that_raises_does_not_stop_the_others(feeds, monkeypatch):
    feeds.serve(URL_B, rss({"title": "alive", "link": "https://news.example/1", "pubDate": ago(1)}))
    real = digest.fetch_feed

    def flaky(url):
        if url == URL_A:
            raise RuntimeError("boom")
        return real(url)

    monkeypatch.setattr(digest, "fetch_feed", flaky)
    titles = [a["title"] for a in digest.fetch_topic_articles(one_topic(URL_A, URL_B), 24)]
    assert titles == ["alive"]


def test_topic_cap_is_applied_round_robin(feeds, monkeypatch):
    monkeypatch.setattr(digest, "MAX_ARTICLES_PER_TOPIC", 4)
    feeds.serve(URL_A, rss(*[{"title": f"A{h}", "link": f"https://a.example/{h}", "pubDate": ago(h)}
                            for h in (1, 2, 3)]))
    feeds.serve(URL_B, rss(*[{"title": f"B{h}", "link": f"https://b.example/{h}", "pubDate": ago(h)}
                            for h in (1, 2, 3)]))
    titles = [a["title"] for a in digest.fetch_topic_articles(one_topic(URL_A, URL_B), 24)]
    assert titles == ["A1", "B1", "A2", "B2"]


def test_topic_without_feeds_returns_nothing(feeds):
    assert digest.fetch_topic_articles({"name": "Empty"}, 24) == []
    assert feeds.requested == []


def test_atom_feeds_work(feeds):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()
    feeds.serve(URL_A, f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Outlet</title>
<entry><title>Atom story</title><link href="https://atom.example/1"/>
<updated>{stamp}</updated><summary>Atom summary</summary></entry></feed>""".encode())
    [article] = digest.fetch_topic_articles(one_topic(URL_A), 24)
    assert (article["title"], article["link"], article["source"]) == \
        ("Atom story", "https://atom.example/1", "Atom Outlet")


def test_feeds_are_fetched_concurrently(monkeypatch):
    urls = [f"https://feed{i}.example/rss" for i in range(3)]
    # Each request blocks until all three are in flight at once. Fetched one
    # at a time, the barrier times out and every feed comes back empty.
    barrier = threading.Barrier(3, timeout=5)

    def get(url, timeout=None, headers=None):
        barrier.wait()
        return FeedResponse(rss({"title": url, "link": url + "/1", "pubDate": ago(1)}))

    monkeypatch.setattr(digest.requests, "get", get)
    assert len(digest.fetch_topic_articles(one_topic(*urls), 24)) == 3


def test_dedup_is_deterministic_when_the_first_feed_answers_last(monkeypatch):
    shared = "https://news.example/shared"
    second_done = threading.Event()

    def get(url, timeout=None, headers=None):
        if url == URL_A:
            second_done.wait(timeout=5)
            return FeedResponse(rss({"title": "From A", "link": shared, "pubDate": ago(1)}, title="A"))
        response = FeedResponse(rss({"title": "From B", "link": shared, "pubDate": ago(1)}, title="B"))
        second_done.set()
        return response

    monkeypatch.setattr(digest.requests, "get", get)
    [article] = digest.fetch_topic_articles(one_topic(URL_A, URL_B), 24)
    assert article["source"] == "A"
