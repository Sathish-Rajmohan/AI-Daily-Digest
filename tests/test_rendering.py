"""The email: HTML building, escaping, link safety, size, and sending."""

import email
from email.header import decode_header, make_header
from html.parser import HTMLParser

import pytest

import digest

DATE = "Tuesday, 15 September 2026"
VOID_TAGS = {"meta", "br", "img", "hr", "input", "link"}


class Inspector(HTMLParser):
    """Collects every href and flags any tag closed out of order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.hrefs = []
        self.tags = set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag == "a":
            self.hrefs.append(dict(attrs).get("href"))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> with {self.stack[-3:]} open")
        else:
            self.stack.pop()


def inspect(markup):
    parser = Inspector()
    parser.feed(markup)
    parser.close()
    return parser


def source(n, **overrides):
    s = {"title": f"Source {n}", "link": f"https://news.example/{n}", "outlet": f"Outlet {n}"}
    s.update(overrides)
    return s


def brief(name="Tech", stories=2):
    return {
        "overview": f"{name} overview.",
        "stories": [
            {"subheading": f"{name} story {i}", "detail": f"First para {i}.\n\nSecond para {i}.",
             "sources": [source(i)]}
            for i in range(1, stories + 1)
        ],
    }


# --------------------------------------------------------------------------
# _paragraphs_to_html
# --------------------------------------------------------------------------

def test_blank_lines_split_paragraphs_and_single_newlines_do_not():
    out = digest._paragraphs_to_html("One\nstill one.\n\nTwo.\n   \nThree.")
    assert out.count("<p ") == 3
    assert ">One\nstill one.</p>" in out


@pytest.mark.parametrize("text", ["", None, "\n\n  \n"])
def test_nothing_gives_no_paragraphs(text):
    assert digest._paragraphs_to_html(text) == ""


def test_paragraph_text_is_escaped():
    out = digest._paragraphs_to_html("<script>alert('x')</script> & more")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out and "&amp; more" in out


# --------------------------------------------------------------------------
# _sources_html
# --------------------------------------------------------------------------

def test_no_sources_gives_nothing():
    assert digest._sources_html([]) == ""
    assert digest._sources_html(None) == ""


def test_sources_render_title_link_outlet_and_label():
    out = digest._sources_html([source(1)], label="Stories")
    assert 'href="https://news.example/1"' in out
    assert ">Source 1</a> <span" in out and "(Outlet 1)" in out
    assert ">Stories</div>" in out


def test_source_without_outlet_has_no_empty_parens():
    out = digest._sources_html([source(1, outlet="")])
    assert "()" not in out


def test_source_fields_are_escaped():
    out = digest._sources_html([source(1, title="<b>Bold</b>", outlet="A&B",
                                       link='https://x.example/?q="quoted"&a=1')])
    assert "<b>" not in out
    assert 'href="https://x.example/?q=&quot;quoted&quot;&amp;a=1"' in out
    assert "(A&amp;B)" in out


@pytest.mark.parametrize("link", [
    "javascript:alert(1)",
    "  JavaScript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "",
])
def test_non_web_links_are_not_rendered_as_links(link):
    out = digest._sources_html([source(1, link=link)])
    assert "Source 1" in out
    assert inspect(out).hrefs == []


def test_source_missing_link_key_is_not_a_dead_hash_link():
    out = digest._sources_html([{"title": "No link", "outlet": "X"}])
    assert "No link" in out
    assert inspect(out).hrefs == []


def test_source_missing_title_shows_untitled():
    assert "(untitled)" in digest._sources_html([{"link": "https://x.example/"}])


# --------------------------------------------------------------------------
# _build_preheader
# --------------------------------------------------------------------------

def test_preheader_default():
    assert digest._build_preheader([]) == "Your daily digest is ready."
    assert digest._build_preheader([("T", None, "failed")]) == "Your daily digest is ready."


def test_preheader_takes_the_first_real_subheading_per_topic():
    degraded = digest.headlines_only_brief([{"title": "h", "link": "https://x/", "source": "s"}], 3)
    results = [
        ("A", {"stories": [{"subheading": "  "}, {"subheading": "A first"}, {"subheading": "A second"}]}, None),
        ("B", degraded, None),
        ("C", None, "failed"),
        ("D", {"stories": [{"subheading": "D first"}]}, None),
    ]
    assert digest._build_preheader(results) == "A first • D first"


def test_preheader_is_capped():
    results = [(str(i), {"stories": [{"subheading": "x" * 50}]}, None) for i in range(4)]
    text = digest._build_preheader(results)
    assert len(text) <= 140 and text.endswith("...")


def test_preheader_at_exactly_the_cap_is_untouched():
    text = "y" * 140
    assert digest._build_preheader([("T", {"stories": [{"subheading": text}]}, None)]) == text


# --------------------------------------------------------------------------
# _fact_block
# --------------------------------------------------------------------------

def test_no_fact_no_block():
    assert digest._fact_block(None) == ""


def test_fact_block_escapes_and_shows_field():
    out = digest._fact_block({"field": "computer science", "fact": "<i>x</i> & y", "why": "Why <b>"})
    assert "computer science" in out
    assert "&lt;i&gt;x&lt;/i&gt; &amp; y" in out
    assert "Why &lt;b&gt;" in out


def test_fact_block_without_why_has_one_paragraph():
    out = digest._fact_block({"field": "physics", "fact": "F.", "why": ""})
    assert out.count("<p ") == 1


# --------------------------------------------------------------------------
# build_html
# --------------------------------------------------------------------------

def test_topics_render_in_order_with_their_content():
    out = digest.build_html([("Tech", brief("Tech"), None), ("World", brief("World"), None)], DATE)
    # The hidden preheader repeats subheadings near the top, so look past it.
    body = out[out.index(DATE):]
    assert body.index("Tech overview.") < body.index("Tech story 1") < body.index("World overview.")
    assert "Second para 2." in out
    assert "In today's digest: Tech, World" in out
    assert DATE in out


def test_markup_is_well_formed_and_every_link_is_a_web_link():
    results = [
        ("Tech", brief("Tech"), None),
        ("World", digest.headlines_only_brief(
            [{"title": "Evil", "link": "javascript:alert(1)", "source": "X"},
             {"title": "Fine", "link": "https://ok.example/", "source": "Y"}], 3), None),
        ("Broken", None, "feeds failed"),
    ]
    fact = {"field": "history", "fact": "F.", "why": "W."}
    parser = inspect(digest.build_html(results, DATE, fact))
    assert parser.errors == []
    assert parser.stack == []
    assert parser.hrefs and all(h.startswith(("https://", "http://")) for h in parser.hrefs)


def test_hostile_content_is_escaped_everywhere():
    evil = '<img src=x onerror="alert(1)">'
    hostile = {
        "overview": evil,
        "stories": [{"subheading": evil, "detail": evil, "sources": [source(1, title=evil, outlet=evil)]}],
    }
    out = digest.build_html([(evil, hostile, None), ("Bad " + evil, None, "note")], evil,
                            {"field": evil, "fact": evil, "why": evil})
    assert "<img" not in out
    assert "onerror=\"alert" not in out
    assert "<script" not in inspect(out).tags


def test_degraded_topic_is_labelled_as_headlines():
    degraded = digest.headlines_only_brief([{"title": "H1", "link": "https://x.example/1", "source": "S"}], 3)
    out = digest.build_html([("World", degraded, None)], DATE)
    assert "No summary was available for this topic" in out
    assert ">Stories</div>" in out
    assert ">Sources</div>" not in out


def test_only_topics_with_a_note_appear_in_the_skipped_notice():
    results = [("Tech", brief(), None), ("Quiet", None, None), ("Broken & Co", None, "fetch failed")]
    out = digest.build_html(results, DATE)
    assert "Skipped this run: Broken &amp; Co." in out
    assert "Quiet" not in out
    assert "In today's digest: Tech</div>" in out


def test_no_topics_at_all_gives_the_empty_notice():
    out = digest.build_html([("Quiet", None, None)], DATE)
    assert "No new stories found in the lookback window." in out
    assert "In today's digest" not in out
    assert "Skipped this run" not in out


def test_fact_sits_above_the_news():
    out = digest.build_html([("Tech", brief(), None)], DATE, {"field": "physics", "fact": "F.", "why": ""})
    assert out.index(DATE) < out.index("One thing worth knowing") < out.index("Tech overview.")


def test_no_fact_block_without_a_fact():
    assert "One thing worth knowing" not in digest.build_html([("Tech", brief(), None)], DATE)


def test_styles_are_inline_and_the_output_is_compact():
    out = digest.build_html([("Tech", brief(), None)], DATE)
    assert "<style" not in out
    assert "\n" not in out and "  " not in out
    assert "</a> <span" in out


def test_preheader_is_hidden_and_escaped():
    b = brief()
    b["stories"][0]["subheading"] = "Q&A <live>"
    out = digest.build_html([("Tech", b, None)], DATE)
    assert "display:none" in out
    assert "Q&amp;A &lt;live&gt;" in out


def test_story_without_subheading_or_sources_still_renders_detail():
    b = {"overview": "", "stories": [{"subheading": "", "detail": "Just detail.", "sources": []}]}
    out = digest.build_html([("Tech", b, None)], DATE)
    assert "Just detail." in out
    assert ">Sources</div>" not in out


# --------------------------------------------------------------------------
# _compact_html / check_email_size
# --------------------------------------------------------------------------

def test_compact_collapses_whitespace_runs_to_one_space():
    assert digest._compact_html("  <a>x</a> \n\t <span>y</span>\n") == "<a>x</a> <span>y</span>"


def test_size_is_measured_in_utf8_bytes():
    assert digest.check_email_size("é" * 10) == 20


def test_size_warning_threshold(capsys):
    digest.check_email_size("x" * (digest.GMAIL_WARN_BYTES - 1))
    assert "[warn]" not in capsys.readouterr().err
    digest.check_email_size("x" * digest.GMAIL_WARN_BYTES)
    assert "approaching Gmail's ~102KB limit" in capsys.readouterr().err


# --------------------------------------------------------------------------
# send_email
# --------------------------------------------------------------------------

def sent_message(smtp):
    [server] = smtp.instances
    [(from_addr, to_addrs, raw)] = server.sent
    return server, from_addr, to_addrs, email.message_from_string(raw)


def test_send_email(smtp, mail_env):
    digest.send_email("Daily Digest - Tuesday", "<p>Body</p>")
    server, from_addr, to_addrs, msg = sent_message(smtp)
    assert (server.host, server.port) == ("smtp.gmail.com", 465)
    assert server.timeout == 60
    assert server.logins == [("sender@example.com", "abcdefghijklmnop")]
    assert from_addr == "sender@example.com" and to_addrs == ["reader@example.com"]
    assert msg["From"] == "sender@example.com" and msg["To"] == "reader@example.com"
    assert msg["Subject"] == "Daily Digest - Tuesday"
    [part] = msg.get_payload()
    assert part.get_content_type() == "text/html"
    assert part.get_payload(decode=True).decode("utf-8") == "<p>Body</p>"


def test_non_ascii_subject_and_body_survive_the_wire(smtp, mail_env):
    digest.send_email("📰 Résumé du jour", "<p>Café – 東京</p>")
    _, _, _, msg = sent_message(smtp)
    assert str(make_header(decode_header(msg["Subject"]))) == "📰 Résumé du jour"
    assert msg.get_payload()[0].get_payload(decode=True).decode("utf-8") == "<p>Café – 東京</p>"


@pytest.mark.parametrize("recipient", [None, "", "   "])
def test_recipient_defaults_to_the_sender(smtp, mail_env, monkeypatch, recipient):
    if recipient is None:
        monkeypatch.delenv("RECIPIENT_EMAIL")
    else:
        monkeypatch.setenv("RECIPIENT_EMAIL", recipient)
    digest.send_email("S", "<p>B</p>")
    _, _, to_addrs, _ = sent_message(smtp)
    assert to_addrs == ["sender@example.com"]


def test_addresses_are_trimmed(smtp, mail_env, monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "  sender@example.com \n")
    monkeypatch.setenv("RECIPIENT_EMAIL", " reader@example.com ")
    digest.send_email("S", "<p>B</p>")
    _, from_addr, to_addrs, _ = sent_message(smtp)
    assert (from_addr, to_addrs) == ("sender@example.com", ["reader@example.com"])


@pytest.mark.parametrize("var, value", [("GMAIL_ADDRESS", "   "), ("GMAIL_APP_PASSWORD", "    ")])
def test_blank_credentials_are_refused_before_connecting(smtp, mail_env, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(RuntimeError, match=var):
        digest.send_email("S", "<p>B</p>")
    assert smtp.instances == []
