"""Collapsible stories inside the regular HTML email (the checkbox version)."""

import html
import re
from html.parser import HTMLParser

import pytest

import digest

DATE = "Tuesday, 15 September 2026"
VOID_TAGS = {"meta", "br", "img", "hr", "input", "link"}


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


def build(results, fact=None, collapsible=True):
    return digest.build_html(results, DATE, fact, collapsible=collapsible)


class Visibility(HTMLParser):
    """
    Reads the markup the way an app that ignores the <style> block would:
    only inline display:none hides anything. Splits the text into what such
    an app shows and what it hides, and records the checkbox structure.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.visible = []
        self.hidden = []
        self.inputs = []
        self.labels = []
        self.errors = []
        self.hrefs = []

    def _hidden_now(self):
        return any(node["hidden"] for node in self.stack)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = (attrs.get("style") or "").replace(" ", "").lower()
        hidden = "display:none" in style
        if tag == "input":
            self.inputs.append({"attrs": attrs, "parent": self.stack[-1] if self.stack else None,
                                "siblings_after": []})
        if tag == "a":
            self.hrefs.append(attrs.get("href"))
        node = {"tag": tag, "attrs": attrs, "hidden": hidden, "children": []}
        if self.stack:
            self.stack[-1]["children"].append(node)
        if tag == "label":
            self.labels.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]["tag"] != tag:
            self.errors.append(f"</{tag}> with {[n['tag'] for n in self.stack[-3:]]} open")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if not data.strip():
            return
        if self.stack and self.stack[-1]["tag"] in ("style", "script", "title"):
            return
        (self.hidden if self._hidden_now() else self.visible).append(data)


def visibility(markup):
    parser = Visibility()
    parser.feed(markup)
    parser.close()
    return parser


def css_rules(markup):
    block = re.search(r"<style>(.*?)</style>", markup, re.S)
    if not block:
        return []
    return [r.strip() + "}" for r in block.group(1).split("}") if r.strip()]


# --------------------------------------------------------------------------
# Off by default at the function level, on when asked
# --------------------------------------------------------------------------

def test_plain_version_has_no_checkboxes_or_stylesheet():
    out = build([("Tech", brief(), None)], collapsible=False)
    assert "<style" not in out and "<input" not in out and "<label" not in out


def test_collapsible_version_has_one_checkbox_per_story():
    out = build([("Tech", brief(stories=3), None), ("World", brief("World", 2), None)])
    parsed = visibility(out)
    assert len(parsed.inputs) == 5
    assert parsed.errors == []


# --------------------------------------------------------------------------
# The safety rules that stop anything being lost
# --------------------------------------------------------------------------

def test_every_hiding_rule_depends_on_checked():
    rules = css_rules(build([("Tech", brief(), None)]))
    assert rules
    for rule in rules:
        selector, declarations = rule.split("{", 1)
        if "display:none" in declarations.replace(" ", ""):
            assert "input:checked" in selector, rule


def test_checked_is_only_used_on_a_bare_input_type_selector():
    for rule in css_rules(build([("Tech", brief(), None)])):
        for match in re.finditer(r"(\S*):checked", rule):
            assert match.group(1).endswith("input") and "." not in match.group(1).split()[-1], rule


def test_stylesheet_avoids_what_breaks_in_common_apps():
    out = build([("Tech", brief(), None)])
    block = re.search(r"<style>(.*?)</style>", out, re.S).group(1)
    assert "/*" not in block
    assert ":not(" not in block
    assert len(block.encode("utf-8")) < 16_000
    assert out.index("<style>") < out.index("</head>") < out.index("<body")


def test_checkboxes_start_ticked_hidden_and_are_wrapped_by_their_label():
    out = build([("Tech", brief(stories=2), None)])
    parsed = visibility(out)
    for box in parsed.inputs:
        attrs = box["attrs"]
        assert attrs.get("type") == "checkbox"
        assert "checked" in attrs
        assert "display:none" in attrs["style"].replace(" ", "")
        assert "id" not in attrs
        assert box["parent"]["tag"] == "label"
    assert all("for" not in label["attrs"] for label in parsed.labels)


def test_body_and_heading_are_siblings_after_the_checkbox():
    parsed = visibility(build([("Tech", brief(stories=1), None)]))
    [label] = parsed.labels
    kids = [(c["tag"], c["attrs"].get("class")) for c in label["children"]]
    assert kids == [("input", None), ("span", "dd-head"), ("span", "dd-body")]


def test_an_app_that_ignores_the_stylesheet_shows_every_story_in_full():
    b = brief(stories=3)
    results = [("Tech", b, None)]
    parsed = visibility(build(results, {"field": "physics", "fact": "F.", "why": "W."}))
    # The inbox preview line is hidden on purpose and isn't part of any story.
    preheader = digest._build_preheader(results)
    parsed.hidden = [h for h in parsed.hidden if not h.strip().startswith(preheader)]
    visible = " ".join(parsed.visible)
    for story in b["stories"]:
        assert story["subheading"] in visible
        for para in story["detail"].split("\n\n"):
            assert para in visible
        for s in story["sources"]:
            assert s["title"] in visible
    # The only thing such an app hides is the "+ Read more" cue, which would
    # be wrong to show on a story that's already fully open.
    assert parsed.hidden
    assert all("Read\xa0more" in h for h in parsed.hidden), parsed.hidden


def test_nothing_in_the_plain_version_is_missing_from_the_collapsible_one():
    results = [("Tech", brief(stories=3), None), ("World", brief("World", 2), None),
               ("Broken", None, "feeds failed")]
    fact = {"field": "history", "fact": "A fact.", "why": "A reason."}
    plain = visibility(build(results, fact, collapsible=False))
    folded = visibility(build(results, fact, collapsible=True))
    folded_text = " ".join(folded.visible)
    for piece in plain.visible:
        assert piece.strip() in folded_text, piece
    assert sorted(plain.hrefs) == sorted(folded.hrefs)


# --------------------------------------------------------------------------
# Content and edge cases
# --------------------------------------------------------------------------

def test_overview_and_fact_stay_outside_any_label():
    out = build([("Tech", brief(), None)], {"field": "physics", "fact": "The fact.", "why": ""})
    first_label = out.index("<label")
    assert out.index("Tech overview.") < first_label
    assert out.index("The fact.") < first_label


def test_headline_fallback_topic_is_not_folded():
    degraded = digest.headlines_only_brief(
        [{"title": "H1", "link": "https://x.example/1", "source": "S"}], 3)
    out = build([("World", degraded, None)])
    assert "<input" not in out
    assert "No summary was available for this topic" in out
    assert 'href="https://x.example/1"' in out


def test_story_with_nothing_beneath_it_is_a_plain_line():
    b = {"overview": "O.", "stories": [{"subheading": "Just a line", "detail": "", "sources": []}]}
    out = build([("Tech", b, None)])
    assert "Just a line" in out
    assert "<input" not in out and "Read&nbsp;more" not in out


def test_story_without_subheading_is_labelled_by_its_first_sentence():
    b = {"overview": "", "stories": [{"subheading": "", "detail": "Opening line. Then more.",
                                      "sources": []}]}
    out = build([("Tech", b, None)])
    head = re.search(r'<span class="dd-head"[^>]*>(.*?)<span class="dd-more"', out).group(1)
    assert head == "Opening line."


def test_hostile_content_is_escaped_and_links_stay_web_only():
    evil = '<img src=x onerror="alert(1)">'
    b = {"overview": evil, "stories": [
        {"subheading": evil, "detail": evil,
         "sources": [source(1, title=evil), source(2, link="javascript:alert(1)")]}]}
    out = build([(evil, b, None)])
    parsed = visibility(out)
    assert "<img" not in out
    assert parsed.errors == []
    assert parsed.hrefs == ["https://news.example/1"]


def test_topic_with_no_stories_has_no_empty_wrapper():
    out = build([("Tech", {"overview": "Quiet.", "stories": []}, None)])
    assert "Quiet." in out and "<label" not in out
