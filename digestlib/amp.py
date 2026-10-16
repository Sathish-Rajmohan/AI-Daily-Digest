"""The AMP copy of the email, for Gmail.

Gmail can't fold ordinary HTML, but it folds AMP for Email built with
amp-accordion. This copy shows each story as its subheading until tapped.
Other apps, and Gmail after 30 days, show the regular HTML email.
"""

import html
import re
import sys

from digestlib.feeds import _web_link
from digestlib.render import (
    DAILY, _ACCENT, _BORDER, _CARD_BG, _FONT_STACK, _PAGE_BG, _SERIF_STACK, _TEXT_BODY,
    _TEXT_HEADING, _TEXT_MUTED, _TOPIC_INKS, _WARN_BG, _WARN_BORDER, _WARN_TEXT,
    _compact_html, _digest_summary, _footer_text, _story_label, _summary_line,
    _topic_meta,
)


# AMP's size limit. Gmail ignores a larger AMP part, so one isn't sent.
AMP_MAX_BYTES = 200_000

def _amp_ink_css():
    """CSS for each topic colour. A topic's wrapper carries its ink class."""
    rules = ""
    for i, ink in enumerate(_TOPIC_INKS):
        rules += (
            f".ink{i} .topic-name, .ink{i} .toggle, .ink{i} .source-list a, "
            f".ink{i} .arrow, .ink{i} .mark {{ color:{ink}; }}\n"
            f".bg{i} {{ background:{ink}; }}\n"
        )
    return rules


# The AMP copy uses one stylesheet. It shares the HTML version's palette and
# fonts.
_AMP_CSS = f"""
body {{ margin:0; padding:0; background:{_PAGE_BG}; font-family:{_FONT_STACK}; color:{_TEXT_BODY}; }}
.wrap {{ max-width:640px; margin:0 auto; padding:24px 10px; }}
.card {{ background:{_CARD_BG}; border-radius:6px; padding:34px 30px 30px 30px;
  overflow-wrap:anywhere; }}
.dateline {{ font-size:11px; font-weight:700; letter-spacing:0.14em;
  text-transform:uppercase; color:{_TEXT_MUTED}; }}
.title {{ font-family:{_SERIF_STACK}; font-size:38px; line-height:1.1; font-weight:700;
  letter-spacing:-0.01em; color:{_TEXT_HEADING}; margin-top:8px; }}
.summary {{ margin-top:8px; font-size:13px; color:{_TEXT_MUTED}; }}
.strip {{ display:flex; height:6px; margin-top:18px; }}
.seg {{ height:6px; }}
.seg + .seg {{ border-left:2px solid {_CARD_BG}; }}
.index {{ margin-top:10px; font-size:13px; line-height:1.9; }}
.entry {{ white-space:nowrap; margin-right:14px; }}
.entry-name {{ color:{_TEXT_BODY}; }}
.hint {{ margin-top:2px; font-size:12px; color:{_TEXT_MUTED}; }}
.fact {{ margin-top:34px; }}
.label {{ font-size:11px; font-weight:700; letter-spacing:0.12em;
  text-transform:uppercase; color:{_TEXT_MUTED}; }}
.fact-text {{ font-family:{_SERIF_STACK}; font-size:22px; line-height:1.4;
  color:{_TEXT_HEADING}; margin:10px 0 0 0; }}
.fact-why {{ font-size:15px; color:{_TEXT_BODY}; line-height:1.65; margin:10px 0 0 0; }}
.warn {{ margin:28px 0 0 0; padding:12px 14px; background:{_WARN_BG};
  border:1px solid {_WARN_BORDER}; border-radius:4px; font-size:13px; color:{_WARN_TEXT}; }}
.topic {{ margin-top:36px; padding-top:30px; border-top:1px solid {_BORDER}; }}
.topic-name {{ font-family:{_SERIF_STACK}; font-size:25px; line-height:1.2;
  font-weight:700; color:{_ACCENT}; }}
.topic-meta {{ margin-top:5px; font-size:12px; color:{_TEXT_MUTED}; }}
.overview {{ font-size:17px; color:{_TEXT_HEADING}; line-height:1.6; margin:14px 0 0 0; }}
.note {{ font-size:14px; color:{_TEXT_MUTED}; line-height:1.6; margin:14px 0 0 0; }}
.stories {{ margin-top:18px; }}
.story {{ border-top:1px solid {_BORDER}; }}
.story > .story-head {{ display:flex; align-items:center; justify-content:space-between;
  background:{_CARD_BG}; border:0; margin:0; padding:15px 0; cursor:pointer;
  font-size:16px; font-weight:600; color:{_TEXT_HEADING}; line-height:1.4; }}
.toggle {{ flex:none; margin-left:14px; font-size:20px; font-weight:400;
  line-height:1; color:{_ACCENT}; }}
.less {{ display:none; }}
.story[expanded] .more {{ display:none; }}
.story[expanded] .less {{ display:inline; }}
.story-body {{ padding:0 0 16px 0; }}
.para {{ font-size:15px; color:{_TEXT_BODY}; line-height:1.65; margin:0 0 12px 0; }}
.sources {{ margin-top:14px; }}
.sources-label {{ font-size:10px; font-weight:700; letter-spacing:0.12em;
  text-transform:uppercase; color:{_TEXT_MUTED}; margin-bottom:7px; }}
.source-list {{ margin:0; padding:0; list-style:none; font-size:13px; line-height:1.5; }}
.source-list li {{ margin:0 0 7px 0; }}
.source-list a {{ color:{_ACCENT}; text-decoration:none; font-weight:600; }}
.plain {{ color:{_TEXT_BODY}; font-weight:600; }}
.muted {{ color:{_TEXT_MUTED}; }}
.arrow {{ color:{_ACCENT}; }}
.mark {{ color:{_ACCENT}; }}
.day {{ font-size:15px; font-weight:700; color:{_TEXT_HEADING}; margin:20px 0 0 0; }}
.also {{ margin-top:22px; }}
.also-list {{ font-size:14px; color:{_TEXT_BODY}; }}
.empty {{ margin-top:36px; padding-top:30px; border-top:1px solid {_BORDER};
  font-size:15px; color:{_TEXT_BODY}; }}
.footer {{ margin-top:36px; padding-top:20px; border-top:1px solid {_BORDER};
  font-size:12px; line-height:1.6; color:{_TEXT_MUTED}; }}
""" + _amp_ink_css()


def _amp_paragraphs(text, css_class):
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(f'<p class="{css_class}">{html.escape(p)}</p>' for p in parts)


def _amp_sources(sources, label="Sources"):
    items = ""
    for s in sources or []:
        title = html.escape(s.get("title") or "(untitled)")
        link = _web_link(s.get("link"))
        outlet = html.escape(s.get("outlet") or "")
        outlet_bit = f' <span class="muted">({outlet})</span>' if outlet else ""
        if link:
            title_html = f'<a href="{html.escape(link, quote=True)}" target="_blank">{title}</a>'
        else:
            title_html = f'<span class="plain">{title}</span>'
        items += f'<li><span class="arrow">&#8250;</span> {title_html}{outlet_bit}</li>'
    if not items:
        return ""
    heading = f'<div class="sources-label">{label}</div>' if label else ""
    return f'<div class="sources">{heading}<ul class="source-list">{items}</ul></div>'


def _amp_also(items):
    """The AMP copy of _also_html()."""
    rows = ""
    for item in items or []:
        line = (item.get("line") or "").strip()
        if not line:
            continue
        links = []
        for s in item.get("sources") or []:
            link = _web_link(s.get("link"))
            if link:
                name = html.escape(s.get("outlet") or s.get("title") or "Source")
                links.append(f'<a href="{html.escape(link, quote=True)}" target="_blank">{name}</a>')
        tail = f' <span class="arrow">&#8250;</span> {" &middot; ".join(links)}' if links else ""
        rows += f"<li>{html.escape(line)}{tail}</li>"
    if not rows:
        return ""
    return (
        '<div class="also"><div class="sources-label">Also this week</div>'
        f'<ul class="source-list also-list">{rows}</ul></div>'
    )


def _amp_strip(summary):
    """The topic bar for the AMP copy. The widths change every day, so they're
    added to this email's stylesheet.
    """
    topics = [t for t in summary["topics"] if t["count"]]
    total = sum(t["count"] for t in topics)
    if not total:
        return "", ""
    segments = ""
    css = ""
    for position, t in enumerate(topics):
        ink_class = f"bg{t['index'] % len(_TOPIC_INKS)}"
        segments += f'<div class="seg s{position} {ink_class}"></div>'
        css += f".s{position} {{ width:{t['count'] / total * 100:.1f}%; }}\n"
    return f'<div class="strip">{segments}</div>', css


def _amp_index(summary):
    # The space between entries is where the line can wrap. Each entry is
    # nowrap, so without it the whole index is one line wider than a phone.
    entries = " ".join(
        f'<span class="entry ink{t["index"] % len(_TOPIC_INKS)}">'
        f'<span class="mark">&#9632;</span> '
        f'<span class="entry-name">{html.escape(t["name"])}</span> '
        f'<span class="muted">{t["count"]}</span></span>'
        for t in summary["topics"]
    )
    return f'<div class="index">{entries}</div>' if entries else ""


def build_amp(topic_results, date_str, fact=None, edition=DAILY, notices=()):
    """The AMP version of the email, with the same content as build_html().
    Each story's text and sources fold under its subheading. Overviews and the
    fact stay open.
    """
    summary = _digest_summary(topic_results, fact, collapsible=True)
    sections = []
    failed_topics = []
    has_accordion = False

    for index, (topic_name, brief, note) in enumerate(topic_results):
        if note:
            failed_topics.append(topic_name)
        if not brief:
            continue

        if brief.get("degraded"):
            # A headline list has nothing to fold.
            inner = f'<p class="note">{html.escape(edition["headlines"])}</p>'
            for story in brief.get("stories") or []:
                # The weekly edition's list has a heading for each day.
                day = (story.get("subheading") or "").strip()
                if day:
                    inner += f'<div class="day">{html.escape(day)}</div>'
                inner += _amp_sources(story.get("sources"), None if day else "Stories")
        else:
            inner = _amp_paragraphs(brief.get("overview"), "overview")
            rows = ""
            for story in brief.get("stories") or []:
                body = _amp_paragraphs(story.get("detail"), "para") + _amp_sources(story.get("sources"), None)
                if not body:
                    body = '<p class="para">No further detail.</p>'
                rows += (
                    '<section class="story">'
                    f'<h4 class="story-head"><span>{html.escape(_story_label(story))}</span>'
                    '<span class="toggle" aria-hidden="true">'
                    '<span class="more">+</span><span class="less">&#8722;</span></span></h4>'
                    f'<div class="story-body">{body}</div>'
                    "</section>"
                )
            if rows:
                has_accordion = True
                inner += f'<amp-accordion class="stories">{rows}</amp-accordion>'
            inner += _amp_also(brief.get("also"))

        sections.append(
            f'<div class="topic ink{index % len(_TOPIC_INKS)}">'
            f'<div class="topic-name">{html.escape(topic_name)}</div>'
            f'<div class="topic-meta">{html.escape(_topic_meta(brief))}</div>'
            f"{inner}</div>"
        )

    body = "".join(sections) if sections else (
        f'<div class="empty">{html.escape(edition["empty"])}</div>'
    )
    notice_html = "".join(f'<div class="warn">{html.escape(text)}</div>' for text in notices)

    failure_notice = ""
    if failed_topics:
        names = ", ".join(html.escape(n) for n in failed_topics)
        failure_notice = (
            f'<div class="warn">Skipped this run: {names}. Check the Actions log for details.</div>'
        )

    summary_line = _summary_line(summary, collapsible=True)
    summary_html = f'<div class="summary">{html.escape(summary_line)}</div>' if summary_line else ""
    strip_html, strip_css = _amp_strip(summary)
    index_html = _amp_index(summary)
    if has_accordion:
        index_html += '<div class="hint">Tap a story to read more.</div>'

    fact_html = ""
    if fact:
        why = f'<p class="fact-why">{html.escape(fact["why"])}</p>' if fact.get("why") else ""
        fact_html = (
            f'<div class="fact"><div class="label">Fact of the day &middot;'
            f'{html.escape(fact["field"])}</div>'
            f'<p class="fact-text">{html.escape(fact["fact"])}</p>{why}</div>'
        )

    return _compact_html(f"""
    <!doctype html>
    <html amp4email data-css-strict>
    <head>
      <meta charset="utf-8">
      <script async src="https://cdn.ampproject.org/v0.js"></script>
      <script async custom-element="amp-accordion"
        src="https://cdn.ampproject.org/v0/amp-accordion-0.1.js"></script>
      <style amp4email-boilerplate>body{{visibility:hidden}}</style>
      <style amp-custom>{_AMP_CSS}{strip_css}</style>
    </head>
    <body>
      <div class="wrap"><div class="card">
        <div class="dateline">{html.escape(date_str)}</div>
        <div class="title">{html.escape(edition["title"])}</div>
        {summary_html}
        {strip_html}
        {index_html}
        {fact_html}
        {notice_html}
        {failure_notice}
        {body}
        <div class="footer">{html.escape(_footer_text(summary))}</div>
      </div></div>
    </body>
    </html>
    """)


def _amp_copy(topic_results, date_str, fact, edition=DAILY, notices=()):
    """The collapsible AMP copy, or None if it's over AMP's size limit."""
    amp_body = build_amp(topic_results, date_str, fact, edition, notices)
    amp_size = len(amp_body.encode("utf-8"))
    print(f"Collapsible version is {amp_size / 1024:.0f}KB (AMP allows {AMP_MAX_BYTES // 1000}KB)")
    if amp_size > AMP_MAX_BYTES:
        print(
            "  [warn] the collapsible version is over AMP's size limit, so Gmail "
            "would ignore it. Sending the full version only. Lower max_stories "
            "in topics.json to bring it back.",
            file=sys.stderr,
        )
        return None
    return amp_body
