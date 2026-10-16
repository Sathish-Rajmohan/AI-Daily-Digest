"""The chat version of the digest: one message per topic, split between
stories, with feed text escaped by the channel's markup.
"""

import html

import pytest

import digest
from digestlib.briefing import headlines_only_brief
from digestlib.channels.chat import PlainText, chat_messages
from digestlib.render import DAILY, WEEKLY
from test_main import config, topic


class Tags(PlainText):
    """HTML-style markup, so tests can see what was escaped."""

    def escape(self, text):
        return html.escape(text, quote=False)

    def bold(self, text):
        return f"<b>{self.escape(text)}</b>"

    def link(self, text, url):
        return f'<a href="{html.escape(url)}">{self.escape(text)}</a>'


def story(heading, detail="One sentence. Another one.", link=None):
    return {"subheading": heading, "detail": detail,
            "sources": [{"title": f"{heading} source", "link": link or f"https://x.example/{len(heading)}",
                         "outlet": "Outlet"}]}


def brief(*stories, overview="The overview."):
    return {"overview": overview, "stories": list(stories)}


def daily(*topic_results, fact=None, notices=()):
    return {"edition": "daily", "subject": "Daily Digest - Saturday, 17 October 2026",
            "date": "Saturday, 17 October 2026", "topic_results": list(topic_results),
            "fact": fact, "notices": list(notices), "html": None, "amp": None}


# --------------------------------------------------------------------------
# What goes in a message
# --------------------------------------------------------------------------

def test_one_message_per_topic_with_the_overview_and_linked_headings():
    texts = chat_messages(daily(("Tech", brief(story("Chips"), story("Phones")), None),
                                ("World", brief(story("Talks")), None)), PlainText(), 4096)
    assert texts == [
        "Daily Digest - Saturday, 17 October 2026\n\n"
        "Tech\n\nThe overview.\n\n"
        "• Chips (https://x.example/5)\n• Phones (https://x.example/6)",
        "World\n\nThe overview.\n\n• Talks (https://x.example/5)",
    ]


def test_story_text_adds_each_story_under_its_heading():
    message = daily(("Tech", brief(story("Chips", "Chips got faster.")), None))
    assert "Chips got faster." not in chat_messages(message, PlainText(), 4096)[0]
    assert chat_messages(message, PlainText(), 4096, story_text=True)[0].endswith(
        "• Chips (https://x.example/5)\nChips got faster.")


def test_a_story_without_a_subheading_uses_its_first_sentence():
    texts = chat_messages(daily(("Tech", brief(story(None, "First part. Second part.", link="https://x.example/1")), None)),
                          PlainText(), 4096)
    assert "• First part. (" in texts[0]


def test_headlines_only_topics_say_so_and_list_each_headline():
    fallback = headlines_only_brief(
        [{"title": f"Headline {n}", "link": f"https://h.example/{n}", "source": "Outlet"} for n in (1, 2, 3)],
        5)
    [text] = chat_messages(daily(("Tech", fallback, None)), PlainText(), 4096)
    assert DAILY["headlines"] in text
    assert text.endswith("• Headline 1 (https://h.example/1)\n• Headline 2 (https://h.example/2)\n"
                         "• Headline 3 (https://h.example/3)")


def test_skipped_topics_notices_and_an_empty_digest_are_said():
    [text] = chat_messages(daily(("Tech", None, "its feeds couldn't be fetched this run"),
                                 ("World", None, None), notices=["Covers 2 of 7 days."]),
                           PlainText(), 4096)
    assert "Covers 2 of 7 days." in text
    assert "Skipped this run: Tech." in text
    assert text.endswith(DAILY["empty"])


def test_the_fact_of_the_day_comes_last_on_its_own():
    fact = {"field": "physics", "fact": "Light bends.", "why": "Gravity curves space."}
    texts = chat_messages(daily(("Tech", brief(story("Chips")), None), fact=fact), PlainText(), 4096)
    assert texts[-1] == "Fact of the day · physics\n\nLight bends.\n\nGravity curves space."


def test_the_weekly_edition_lists_threads_then_one_line_stories():
    week = {"overview": "A busy week.",
            "stories": [story("Talks restart · Mon, Wed")],
            "also": [{"line": "A one-day story.", "sources": [{"title": "t", "link": "https://a.example/1"}]}]}
    message = {**daily(("Tech", week, None)), "edition": "weekly", "subject": "Weekly Digest - 11-17 Oct"}
    [text] = chat_messages(message, PlainText(), 4096)
    assert text.endswith("A busy week.\n\n• Talks restart · Mon, Wed (https://x.example/24)\n"
                         "• A one-day story. (https://a.example/1)")


def test_the_weekly_fallback_lists_headings_by_day():
    by_day = {"overview": None, "degraded": True, "stories": [
        {"subheading": "Monday", "detail": None,
         "sources": [{"title": "Mon story", "link": "https://m.example/1"}]}]}
    message = {**daily(("Tech", by_day, None)), "edition": "weekly"}
    [text] = chat_messages(message, PlainText(), 4096)
    assert WEEKLY["headlines"] in text
    assert text.endswith("Monday\n• Mon story (https://m.example/1)")


# --------------------------------------------------------------------------
# Escaping
# --------------------------------------------------------------------------

def test_feed_text_goes_through_the_channel_markup():
    sneaky = story("<b>Win</b> & @everyone", "<script>x</script>",
                   link='https://x.example/a"><img src=x>')
    [text] = chat_messages(daily(("Tech <i>", brief(sneaky, overview="Over <u>view</u>"), None)),
                           Tags(), 4096, story_text=True)
    assert "<b>Tech &lt;i&gt;</b>" in text
    assert "Over &lt;u&gt;view&lt;/u&gt;" in text
    assert ('<a href="https://x.example/a&quot;&gt;&lt;img src=x&gt;">'
            "&lt;b&gt;Win&lt;/b&gt; &amp; @everyone</a>") in text
    assert "&lt;script&gt;x&lt;/script&gt;" in text
    assert "<script>" not in text and "<img" not in text


# --------------------------------------------------------------------------
# Splitting
# --------------------------------------------------------------------------

def test_a_long_topic_is_split_between_stories():
    stories = [story(f"Story number {n:02d}") for n in range(1, 21)]
    texts = chat_messages(daily(("Tech", brief(*stories), None)), PlainText(), 200)
    assert len(texts) > 1
    assert all(len(t) <= 200 for t in texts)
    assert texts[0].startswith("Daily Digest") and not any(t.startswith("Daily") for t in texts[1:])
    lines = [line for t in texts for line in t.split("\n")]
    # Every story is whole, once, and in order.
    assert [line for line in lines if line.startswith("• ")] == [
        f"• Story number {n:02d} (https://x.example/15)" for n in range(1, 21)]


def test_a_story_text_too_long_for_a_message_is_shortened():
    long_text = "word " * 400
    [first, second] = chat_messages(daily(("Tech", brief(story("Short"), story("Long", long_text)), None)),
                                    PlainText(), 300, story_text=True)
    assert len(second) <= 300
    assert second.startswith("• Long (https://x.example/4)\nword word")
    assert second.endswith("…")


# --------------------------------------------------------------------------
# The setting
# --------------------------------------------------------------------------

def test_story_text_must_be_true_or_false(write_config):
    write_config({**config(topic("Tech")), "channels": [{"type": "email", "story_text": "yes"}]})
    with pytest.raises(ValueError, match="'story_text' must be true or false"):
        digest.load_config()
