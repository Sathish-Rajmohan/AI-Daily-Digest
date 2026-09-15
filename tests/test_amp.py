"""The collapsible AMP copy of the digest."""

import html
import re
from html.parser import HTMLParser

import pytest

import digest

DATE = "Tuesday, 15 September 2026"
VOID_TAGS = {"meta", "br", "img", "hr", "input", "link"}


class Tree(HTMLParser):
    """Records structure the AMP rules care about: tag balance, what sits
    directly inside each <section>, links, scripts and inline styles."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.sections = []
        self.hrefs = []
        self.scripts = []
        self.inline_styles = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "style" in attrs:
            self.inline_styles += 1
        if tag == "a":
            self.hrefs.append((attrs.get("href"), attrs.get("target")))
        if tag == "script":
            self.scripts.append(attrs.get("src"))
        if self.stack and self.stack[-1]["tag"] == "section":
            self.stack[-1]["children"].append(tag)
        node = {"tag": tag, "children": [], "attrs": attrs,
                "parent": self.stack[-1]["tag"] if self.stack else None}
        if tag == "section":
            self.sections.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]["tag"] != tag:
            self.errors.append(f"</{tag}> with {[n['tag'] for n in self.stack[-3:]]} open")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if not self.stack or self.stack[-1]["tag"] not in ("style", "script"):
            self.text.append(data)


def tree(markup):
    parser = Tree()
    parser.feed(markup)
    parser.close()
    return parser


def plain(markup):
    return re.sub(r"\s+", " ", " ".join(tree(markup).text)).strip()


def source(n, **overrides):
    s = {"title": f"Source {n}", "link": f"https://news.example/{n}", "outlet": f"Outlet {n}"}
    s.update(overrides)
    return s


def brief(name="Tech", stories=2):
    return {
        "overview": f"{name} overview.",
        "stories": [
            {"subheading": f"{name} story {i}",
             "detail": f"First para {i}.\n\nSecond para {i}.",
             "sources": [source(i), source(i + 10)]}
            for i in range(1, stories + 1)
        ],
    }


FACT = {"field": "history", "fact": "A fact.", "why": "A reason."}


# --------------------------------------------------------------------------
# Document shape
# --------------------------------------------------------------------------

def test_required_amp_boilerplate():
    out = digest.build_amp([("Tech", brief(), None)], DATE, FACT)
    assert out.startswith("<!doctype html> <html amp4email data-css-strict>")
    assert '<meta charset="utf-8">' in out
    assert "<style amp4email-boilerplate>body{visibility:hidden}</style>" in out
    assert "<style amp-custom>" in out
    scripts = tree(out).scripts
    assert scripts == ["https://cdn.ampproject.org/v0.js",
                       "https://cdn.ampproject.org/v0/amp-accordion-0.1.js"]
    assert 'custom-element="amp-accordion"' in out


def test_no_inline_styles_and_balanced_markup():
    out = digest.build_amp([("Tech", brief(), None), ("World", brief("World"), "note")], DATE, FACT)
    parsed = tree(out)
    assert parsed.inline_styles == 0
    assert parsed.errors == [] and parsed.stack == []


def test_stylesheet_fits_amps_limit():
    out = digest.build_amp([("Tech", brief(), None)], DATE)
    css = re.search(r"<style amp-custom>(.*?)</style>", out).group(1)
    assert len(css.encode("utf-8")) < 50_000
    assert "!important" not in css
    assert "@import" not in css and "@font-face" not in css


# --------------------------------------------------------------------------
# The accordion
# --------------------------------------------------------------------------

def test_every_story_is_a_collapsible_section_with_heading_then_body():
    out = digest.build_amp([("Tech", brief(stories=3), None), ("World", brief("World", 2), None)], DATE)
    sections = tree(out).sections
    assert len(sections) == 5
    for section in sections:
        assert section["parent"] == "amp-accordion"
        assert section["children"][:2] == ["h4", "div"]
        assert len(section["children"]) == 2
        assert "expanded" not in section["attrs"]


def test_collapsed_line_is_the_subheading_and_the_body_holds_the_rest():
    out = digest.build_amp([("Tech", brief(stories=1), None)], DATE)
    head = re.search(r'<h4 class="story-head">(.*?)</h4>', out).group(1)
    body = re.search(r'<div class="story-body">(.*?)</div></section>', out).group(1)
    assert "Tech story 1" in head
    assert "First para 1." in body and "Second para 1." in body
    assert 'href="https://news.example/1"' in body and 'href="https://news.example/11"' in body
    assert "First para" not in head


def test_overview_and_fact_stay_outside_the_accordion():
    out = digest.build_amp([("Tech", brief(), None)], DATE, FACT)
    accordion_start = out.index("<amp-accordion")
    assert out.index("Tech overview.") < accordion_start
    assert out.index("A fact.") < accordion_start
    assert "Tech overview." not in out[accordion_start:]


def test_toggle_marks_are_decorative_and_switch_on_expansion():
    out = digest.build_amp([("Tech", brief(stories=1), None)], DATE)
    assert '<span class="toggle" aria-hidden="true">' in out
    assert '<span class="more">+</span>' in out and '<span class="less">&#8722;</span>' in out
    css = re.search(r"<style amp-custom>(.*?)</style>", out).group(1)
    assert ".story[expanded] .more { display:none; }" in css
    assert ".story[expanded] .less { display:inline; }" in css


def test_each_topic_gets_its_own_accordion():
    out = digest.build_amp([("Tech", brief(), None), ("World", brief("World"), None)], DATE)
    assert out.count('<amp-accordion class="stories">') == 2


# amp-accordion's docs list these attributes, but the AMP4EMAIL validator
# rejects them.
@pytest.mark.parametrize("attribute", ["disable-session-states", "expand-single-section", "animate"])
def test_attributes_the_email_validator_rejects_are_not_used(attribute):
    out = digest.build_amp([("Tech", brief(), None)], DATE)
    accordion_tags = re.findall(r"<amp-accordion[^>]*>", out)
    assert accordion_tags and not any(attribute in tag for tag in accordion_tags)


@pytest.mark.parametrize("story, expected", [
    ({"subheading": "Plain subheading", "detail": "Detail."}, "Plain subheading"),
    ({"subheading": "", "detail": "Opening sentence here. Second one."}, "Opening sentence here."),
    ({"subheading": None, "detail": "x" * 300}, "x" * 117 + "..."),
    ({"subheading": "  ", "detail": ""}, "More on this topic"),
])
def test_story_label(story, expected):
    assert digest._story_label(story) == expected


def test_story_with_nothing_to_open_says_so_rather_than_opening_empty():
    b = {"overview": "O.", "stories": [{"subheading": "Only a line", "detail": "", "sources": []}]}
    out = digest.build_amp([("Tech", b, None)], DATE)
    assert '<div class="story-body"><p class="para">No further detail.</p></div>' in out


def test_topic_with_overview_but_no_stories_has_no_empty_accordion():
    out = digest.build_amp([("Tech", {"overview": "Quiet day.", "stories": []}, None)], DATE)
    assert "Quiet day." in out
    assert "<amp-accordion" not in out
    assert "Tap a story" not in out


# --------------------------------------------------------------------------
# Nothing is lost
# --------------------------------------------------------------------------

def test_every_piece_of_the_full_email_is_in_the_collapsible_one():
    results = [("Tech", brief(stories=3), None), ("World", brief("World", 2), None),
               ("Broken", None, "feeds failed")]
    full = digest.build_html(results, DATE, FACT)
    amp = digest.build_amp(results, DATE, FACT)
    amp_text = plain(amp)

    for _, b, _ in results:
        if not b:
            continue
        assert b["overview"] in amp_text
        for story in b["stories"]:
            assert story["subheading"] in amp_text
            for para in story["detail"].split("\n\n"):
                assert para in amp_text
            for s in story["sources"]:
                assert s["title"] in amp_text and s["outlet"] in amp_text

    full_links = {h for h in re.findall(r'href="([^"]+)"', full)}
    amp_links = {h for h, _ in tree(amp).hrefs}
    assert full_links == amp_links
    for piece in ("A fact.", "A reason.", "Skipped this run: Broken.", DATE):
        assert piece in amp_text


# --------------------------------------------------------------------------
# Other states
# --------------------------------------------------------------------------

def test_headline_fallback_is_a_plain_list_not_an_accordion():
    degraded = digest.headlines_only_brief(
        [{"title": "H1", "link": "https://x.example/1", "source": "S"},
         {"title": "H2", "link": "https://x.example/2", "source": "S"}], 3)
    out = digest.build_amp([("World", degraded, None)], DATE)
    assert "No summary was available for this topic" in out
    assert "<amp-accordion" not in out
    assert ">Stories</div>" in out
    assert "Tap a story" not in out
    assert [h for h, _ in tree(out).hrefs] == ["https://x.example/1", "https://x.example/2"]


def test_hint_appears_once_when_there_is_something_to_tap():
    out = digest.build_amp([("Tech", brief(), None), ("World", brief("World"), None)], DATE)
    assert out.count("Tap a story to read more.") == 1


def test_skipped_topics_and_empty_digest():
    out = digest.build_amp([("Quiet", None, None), ("Broken & Co", None, "fetch failed")], DATE)
    assert "Skipped this run: Broken &amp; Co." in out
    assert "No new stories found in the lookback window." in out
    assert "In today's digest" not in out


def test_topics_carry_their_ink_class_by_position():
    out = digest.build_amp([("A", brief("A"), None), ("B", None, "failed"), ("C", brief("C"), None)], DATE)
    assert '<div class="topic ink0">' in out and '<div class="topic ink2">' in out
    assert '<div class="topic ink1">' not in out
    css = re.search(r"<style amp-custom>(.*?)</style>", out).group(1)
    for i, ink in enumerate(digest._TOPIC_INKS):
        assert f".ink{i} .topic-name" in css and f".bg{i} {{ background:{ink}; }}" in css


def test_masthead_strip_widths_follow_each_topics_share():
    out = digest.build_amp([("Big", brief("Big", 3), None), ("Small", brief("Small", 1), None)], DATE)
    css = re.search(r"<style amp-custom>(.*?)</style>", out).group(1)
    assert ".s0 { width:75.0%; }" in css and ".s1 { width:25.0%; }" in css
    assert out.count('<div class="seg ') == 2
    assert '<span class="entry ink0">' in out and '<span class="entry ink1">' in out


def test_masthead_line_matches_the_html_version():
    results = [("Tech", brief("Tech", 3), None), ("World", brief("World", 1), None)]
    line = digest._summary_line(digest._digest_summary(results, None, True), True)
    assert line
    assert html.escape(line) in digest.build_amp(results, DATE)
    assert html.escape(line) in digest.build_html(results, DATE, collapsible=True)


def test_no_strip_or_index_when_nothing_to_show():
    out = digest.build_amp([("Quiet", None, None)], DATE)
    assert 'class="strip"' not in out and 'class="index"' not in out


def test_no_fact_no_fact_block():
    out = digest.build_amp([("Tech", brief(), None)], DATE)
    assert "Fact of the day" not in out


def test_fact_without_why():
    out = digest.build_amp([("Tech", brief(), None)], DATE, {"field": "physics", "fact": "F.", "why": ""})
    assert "fact-why" not in out.split("<style amp-custom>")[1].split("</style>")[1]


def test_hostile_content_is_escaped_and_links_are_web_only():
    evil = '<img src=x onerror="alert(1)">'
    hostile = {
        "overview": evil,
        "stories": [
            {"subheading": evil, "detail": evil,
             "sources": [source(1, title=evil, outlet=evil),
                         source(2, link="javascript:alert(1)"),
                         source(3, link="https://ok.example/?a=1&b=\"2\"")]},
        ],
    }
    out = digest.build_amp([(evil, hostile, None), ("Bad " + evil, None, "note")], evil,
                           {"field": evil, "fact": evil, "why": evil})
    parsed = tree(out)
    assert "<img" not in out
    assert parsed.errors == []
    assert parsed.hrefs == [("https://ok.example/?a=1&b=\"2\"", "_blank"),
                            ("https://news.example/1", "_blank")] or \
        sorted(parsed.hrefs) == sorted([("https://news.example/1", "_blank"),
                                        ('https://ok.example/?a=1&b="2"', "_blank")])
    assert "Source 2" in html.unescape(out)


def test_every_link_opens_in_a_new_tab():
    out = digest.build_amp([("Tech", brief(), None)], DATE)
    hrefs = tree(out).hrefs
    assert hrefs and all(target == "_blank" for _, target in hrefs)


def test_a_full_size_digest_stays_under_amps_document_limit():
    long_para = " ".join(["This sentence has about ten words in it, roughly."] * 6)
    heavy = {
        "overview": long_para,
        "stories": [
            {"subheading": "A subheading of a realistic length for a story",
             "detail": "\n\n".join([long_para] * 3),
             "sources": [source(i, title="T" * 90, link="https://news.example/" + "p" * 100)
                         for i in range(4)]}
            for _ in range(9)
        ],
    }
    results = [(f"Topic {i}", heavy, None) for i in range(5)]
    out = digest.build_amp(results, DATE, FACT)
    assert len(out.encode("utf-8")) < digest.AMP_MAX_BYTES
