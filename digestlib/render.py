"""The HTML email: its look, the daily and weekly editions, and the size check
that keeps it under Gmail's clipping limit.
"""

import html
import re
import sys

from digestlib.feeds import _web_link


# Off-black and off-white, because Gmail's apps invert colours in dark mode
# and pure black and white invert badly. Every text colour passes WCAG AA
# against the card, and a test checks it.
#
# Each topic gets a dark, muted colour for its name, links and "Read more"
# label. The strip at the top of the email uses the same colours.
#
# Font names use single quotes because these stacks go inside double-quoted
# style attributes.
_FONT_STACK = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
# Georgia is available in every major mail app. Android doesn't have it and
# uses its own serif.
_SERIF_STACK = "Georgia,'Times New Roman',Times,serif"
_PAGE_BG = "#eef2f1"
_CARD_BG = "#fcfdfc"
_TEXT_HEADING = "#141a19"
_TEXT_BODY = "#2f3836"
_TEXT_MUTED = "#5f6a67"
_ACCENT = "#0e6a66"
_BORDER = "#e1e7e5"
_WARN_BG = "#fff8e6"
_WARN_BORDER = "#f0dca0"
_WARN_TEXT = "#8a6100"
_TOPIC_INKS = ["#0e6a66", "#9a2c3c", "#34489a", "#52661d", "#8b5a06", "#7a3e7c"]

# Words per minute for the reading-time estimate.
_READING_WPM = 230

# What differs between the daily email and the weekly edition. The colours,
# fonts and folding are shared.
DAILY = {
    "title": "Daily Digest",
    "ready": "Your daily digest is ready.",
    "headlines": ("No summary was available for this topic this run, so the "
                  "latest stories are listed directly."),
    "empty": "No new stories found in the lookback window.",
}
WEEKLY = {
    "title": "Weekly Digest",
    "ready": "Your weekly digest is ready.",
    "headlines": ("No summary was available for this topic this week, so the "
                  "week's stories are listed by day."),
    "empty": "No stories were saved this week.",
}


def _topic_ink(index):
    """A topic's colour, picked by its position in topics.json so it stays the
    same from day to day.
    """
    return _TOPIC_INKS[index % len(_TOPIC_INKS)]


def _paragraphs_to_html(text, size=15, color=None, margin="0 0 12px 0"):
    """Turn blank-line paragraph breaks into HTML paragraphs."""
    color = color or _TEXT_BODY
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(
        f'<p style="font-size:{size}px;color:{color};line-height:1.6;'
        f'margin:{margin};">{html.escape(p)}</p>'
        for p in parts
    )


def _sources_html(sources, label="Sources", ink=_ACCENT):
    """The source links under a story, in the topic's colour."""
    items = ""
    for s in sources or []:
        title = html.escape(s.get("title") or "(untitled)")
        link = _web_link(s.get("link"))
        outlet = html.escape(s.get("outlet") or "")
        outlet_bit = f" ({outlet})" if outlet else ""
        # Links are checked again here, where they become hrefs. A title without a
        # usable link is still listed.
        #
        # The arrow goes inside the link and the outlet is plain text. That saves a
        # span per source, which adds up to a few KB across a full digest.
        if link:
            title_html = (
                f'<a href="{html.escape(link, quote=True)}" style="color:{ink};'
                f'text-decoration:none;font-weight:600;">&#8250;&nbsp;{title}</a>'
            )
        else:
            title_html = (
                f'<span style="color:{_TEXT_BODY};font-weight:600;">&#8250;&nbsp;{title}</span>'
            )
        items += f'<li style="margin:0 0 7px;color:{_TEXT_MUTED};">{title_html}{outlet_bit}</li>'
    if not items:
        return ""
    heading = (
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_TEXT_MUTED};margin-bottom:7px;">{label}</div>'
        if label else ""
    )
    return (
        f'<div style="margin-top:14px;">{heading}'
        f'<ul style="margin:0;padding:0;list-style:none;font-size:13px;'
        f'line-height:1.5;">{items}</ul></div>'
    )


def _build_preheader(topic_results, ready=DAILY["ready"]):
    """The inbox preview text: the first subheading from each topic, capped at
    140 characters.
    """
    subheadings = []
    for _, brief, _ in topic_results:
        if not brief or brief.get("degraded"):
            continue
        for story in brief.get("stories") or []:
            sub = (story.get("subheading") or "").strip()
            if sub:
                subheadings.append(sub)
                break  # first subheading per topic
    if not subheadings:
        return ready
    text = " • ".join(subheadings)
    if len(text) > 140:
        text = text[:137].rstrip() + "..."
    return text


def _plural(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def _story_count(brief):
    """Stories in a topic, or headlines if it's the fallback list. The weekly
    edition's one-line stories count too.
    """
    stories = brief.get("stories") or []
    if brief.get("degraded"):
        return sum(len(s.get("sources") or []) for s in stories)
    return len(stories) + len(brief.get("also") or [])


def _topic_outlets(brief):
    return {
        s["outlet"]
        for story in (brief.get("stories") or []) + (brief.get("also") or [])
        for s in story.get("sources") or []
        if s.get("outlet")
    }


def _topic_meta(brief):
    """The line under a topic's name, such as "8 stories · 5 outlets"."""
    count = _story_count(brief)
    if brief.get("degraded"):
        meta = _plural(count, "headline", "headlines")
    else:
        meta = _plural(count, "story", "stories")
    outlets = _topic_outlets(brief)
    if outlets:
        meta += " · " + _plural(len(outlets), "outlet", "outlets")
    return meta


def _words(text):
    return len((text or "").split())


def _digest_summary(topic_results, fact, collapsible):
    """Totals for the top of the email: stories per topic, outlets cited and
    reading time, all counted from this email. With stories folded, the reading
    time only covers what shows before anything is opened.
    """
    topics = []
    outlets = set()
    skim = _words((fact or {}).get("fact")) + _words((fact or {}).get("why"))
    full = skim
    for index, (name, brief, _note) in enumerate(topic_results):
        if not brief:
            continue
        topics.append({"index": index, "name": name, "count": _story_count(brief)})
        outlets |= _topic_outlets(brief)
        skim += _words(brief.get("overview"))
        full += _words(brief.get("overview"))
        for story in brief.get("stories") or []:
            skim += _words(story.get("subheading"))
            full += _words(story.get("subheading")) + _words(story.get("detail"))
            if brief.get("degraded"):
                titles = sum(_words(s.get("title")) for s in story.get("sources") or [])
                skim += titles
                full += titles
        for item in brief.get("also") or []:
            skim += _words(item.get("line"))
            full += _words(item.get("line"))
    words = skim if collapsible else full
    return {
        "topics": topics,
        "stories": sum(t["count"] for t in topics),
        "outlets": len(outlets),
        "minutes": max(1, round(words / _READING_WPM)),
    }


def _summary_line(summary, collapsible):
    if not summary["stories"]:
        return ""
    line = _plural(summary["stories"], "story", "stories")
    if summary["outlets"]:
        line += " from " + _plural(summary["outlets"], "outlet", "outlets")
    line += f" · about {summary['minutes']} min {'to skim' if collapsible else 'read'}"
    return line


def _footer_text(summary):
    text = "Change topics and sources in topics.json."
    if summary["outlets"]:
        text = f"Drawn from {_plural(summary['outlets'], 'outlet', 'outlets')}. {text}"
    return text


def _topic_strip_html(summary):
    """A thin bar split into topic colours, each part sized by that topic's share
    of stories. Table cells with bgcolor, so Outlook draws it.
    """
    topics = [t for t in summary["topics"] if t["count"]]
    total = sum(t["count"] for t in topics)
    if not total:
        return ""
    cells = ""
    for position, t in enumerate(topics):
        width = f"{t['count'] / total * 100:.1f}%"
        ink = _topic_ink(t["index"])
        gap = f"border-left:2px solid {_CARD_BG};" if position else ""
        cells += (
            f'<td width="{width}" height="6" bgcolor="{ink}" style="width:{width};height:6px;'
            f'background:{ink};{gap}font-size:0;line-height:0;">&nbsp;</td>'
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;margin-top:18px;border-collapse:collapse;"><tr>{cells}</tr></table>'
    )


def _topic_index_html(summary):
    """The key for the bar: each topic's colour, name and story count."""
    entries = [
        f'<span style="white-space:nowrap;margin-right:14px;">'
        f'<span style="color:{_topic_ink(t["index"])};">&#9632;</span>&nbsp;'
        f'<span style="color:{_TEXT_BODY};">{html.escape(t["name"])}</span>&nbsp;'
        f'<span style="color:{_TEXT_MUTED};">{t["count"]}</span></span>'
        for t in summary["topics"]
    ]
    if not entries:
        return ""
    return f'<div style="margin-top:10px;font-size:13px;line-height:1.9;">{" ".join(entries)}</div>'


def _fact_block(fact):
    """The fact of the day, shown after the header in large serif type."""
    if not fact:
        return ""
    why = (
        f'<p style="font-size:15px;color:{_TEXT_BODY};line-height:1.65;'
        f'margin:10px 0 0 0;">{html.escape(fact["why"])}</p>'
        if fact.get("why") else ""
    )
    return (
        f'<div style="margin-top:34px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_TEXT_MUTED};">'
        f'Fact of the day &middot;{html.escape(fact["field"])}</div>'
        f'<p style="font-family:{_SERIF_STACK};font-size:22px;line-height:1.4;'
        f'color:{_TEXT_HEADING};margin:10px 0 0 0;">{html.escape(fact["fact"])}</p>'
        f'{why}</div>'
    )


# Folding stories in the regular HTML email. Email has no JavaScript, so a
# hidden checkbox inside a <label> switches a CSS rule that hides the story.
#
# The box starts ticked and the hiding rule needs input:checked, so apps
# without :checked support (Gmail's HTML view, Outlook for Windows) show every
# story open. The label wraps the checkbox because some apps rename ids, and a
# for= link would break.
#
# Outlook.com only supports :checked on a plain element selector. There are
# no CSS comments because Yahoo skips the rule after one. The "Read more"
# label is styled here because it only appears where this CSS works.
_COLLAPSE_CSS = (
    "<style>"
    ".dd-story input:checked ~ .dd-body { display:none !important; }"
    ".dd-story input:checked ~ .dd-head .dd-more { display:inline !important;"
    " font-size:13px; font-weight:600; white-space:nowrap; }"
    "</style>"
)


def _collapsible_story_html(story, sources_label, ink=_ACCENT):
    """One story, folded to its subheading where the app supports it. A story
    with nothing under its subheading is a plain line.
    """
    label = html.escape(_story_label(story))
    body = _paragraphs_to_html((story.get("detail") or "").strip()) + _sources_html(
        story.get("sources") or [], sources_label, ink
    )
    head_style = (
        f"display:block;padding:15px 0;font-size:16px;font-weight:600;"
        f"color:{_TEXT_HEADING};line-height:1.4;"
    )
    if not body:
        return (
            f'<div style="border-top:1px solid {_BORDER};">'
            f'<div style="{head_style}">{label}</div></div>'
        )
    return (
        f'<div class="dd-story" style="border-top:1px solid {_BORDER};">'
        f'<label style="display:block;cursor:pointer;">'
        f'<input type="checkbox" checked style="display:none;mso-hide:all;">'
        f'<span class="dd-head" style="{head_style}">{label}'
        f'<span class="dd-more" style="display:none;color:{ink};"> &nbsp;+&nbsp;Read&nbsp;more</span></span>'
        f'<span class="dd-body" style="display:block;padding:0 0 16px;">{body}</span>'
        f"</label></div>"
    )


def _also_html(items, ink=_ACCENT):
    """The weekly edition's one-line stories. Each line ends with links named
    after the outlets that reported it.
    """
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
                links.append(
                    f'<a href="{html.escape(link, quote=True)}" style="color:{ink};'
                    f'text-decoration:none;font-weight:600;">{name}</a>'
                )
        tail = f' &#8250;&nbsp;{" &middot; ".join(links)}' if links else ""
        rows += f'<li style="margin:0 0 9px;">{html.escape(line)}{tail}</li>'
    if not rows:
        return ""
    return (
        f'<div style="margin-top:22px;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_TEXT_MUTED};margin-bottom:9px;">Also this week</div>'
        f'<ul style="margin:0;padding:0;list-style:none;font-size:14px;line-height:1.55;'
        f'color:{_TEXT_BODY};">{rows}</ul></div>'
    )


def build_html(topic_results, date_str, fact=None, collapsible=False, edition=DAILY, notices=()):
    """The email. `edition` is DAILY or WEEKLY, and each of `notices` is shown
    in a box near the top.
    """
    summary = _digest_summary(topic_results, fact, collapsible)
    sections = []
    failed_topics = []
    for index, (topic_name, brief, note) in enumerate(topic_results):
        if note:
            failed_topics.append(topic_name)
        if not brief:
            continue

        ink = _topic_ink(index)
        degraded = bool(brief.get("degraded"))

        # The overview comes first and is set a little larger than the stories.
        if degraded:
            overview_html = (
                f'<p style="font-size:14px;color:{_TEXT_MUTED};line-height:1.6;'
                f'margin:14px 0 0 0;">{html.escape(edition["headlines"])}</p>'
            )
        else:
            overview_html = _paragraphs_to_html(
                brief.get("overview") or "", size=17, color=_TEXT_HEADING, margin="14px 0 0 0"
            )

        stories_html = ""
        if collapsible and not degraded:
            folded = brief.get("stories") or []
            if folded:
                stories_html = (
                    '<div style="margin-top:18px;">'
                    + "".join(_collapsible_story_html(story, None, ink) for story in folded)
                    + "</div>"
                )
            plain_stories = []
        else:
            plain_stories = brief.get("stories") or []
        for story in plain_stories:
            subheading = (story.get("subheading") or "").strip()
            detail = (story.get("detail") or "").strip()
            sources = story.get("sources") or []

            sub_html = ""
            if subheading:
                sub_html = (
                    f'<div style="font-size:17px;font-weight:700;'
                    f'color:{_TEXT_HEADING};line-height:1.4;'
                    f'margin:0 0 8px 0;">{html.escape(subheading)}</div>'
                )

            stories_html += (
                f'<div style="margin-top:24px;">'
                f'{sub_html}'
                f'{_paragraphs_to_html(detail)}'
                f'{_sources_html(sources, "Stories" if degraded and not subheading else None, ink)}'
                f'</div>'
            )

        sections.append(
            f'<div style="margin-top:36px;padding-top:30px;border-top:1px solid {_BORDER};">'
            f'<div style="font-family:{_SERIF_STACK};font-size:25px;line-height:1.2;'
            f'font-weight:700;color:{ink};">{html.escape(topic_name)}</div>'
            f'<div style="margin-top:5px;font-size:12px;color:{_TEXT_MUTED};">'
            f'{html.escape(_topic_meta(brief))}</div>'
            f'{overview_html}'
            f'{stories_html}'
            f'{_also_html(brief.get("also"), ink)}'
            f'</div>'
        )

    body = "".join(sections) if sections else (
        f'<div style="margin-top:36px;padding-top:30px;border-top:1px solid {_BORDER};'
        f'font-size:15px;color:{_TEXT_BODY};">{html.escape(edition["empty"])}</div>'
    )

    notice_html = "".join(
        f'<div style="margin:28px 0 0 0;padding:12px 14px;background:{_WARN_BG};'
        f'border:1px solid {_WARN_BORDER};border-radius:4px;font-size:13px;'
        f'color:{_WARN_TEXT};">{html.escape(text)}</div>'
        for text in notices
    )

    failure_notice = ""
    if failed_topics:
        names = ", ".join(html.escape(n) for n in failed_topics)
        failure_notice = f"""
        <div style="margin:28px 0 0 0;padding:12px 14px;background:{_WARN_BG};
          border:1px solid {_WARN_BORDER};border-radius:4px;font-size:13px;
          color:{_WARN_TEXT};">
          Skipped this run: {names}. Check the Actions log for details.
        </div>
        """

    summary_line = _summary_line(summary, collapsible)
    summary_html = (
        f'<div style="margin-top:8px;font-size:13px;color:{_TEXT_MUTED};">'
        f'{html.escape(summary_line)}</div>'
        if summary_line else ""
    )

    preheader = html.escape(_build_preheader(topic_results, edition["ready"]))
    # Stops Gmail and Outlook filling the rest of the preview with body text.
    preheader_pad = "&#8203;&nbsp;" * 120
    title = html.escape(edition["title"])

    return _compact_html(f"""
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta name="color-scheme" content="light">
      <meta name="supported-color-schemes" content="light">
      <title>{title}</title>
      {_COLLAPSE_CSS if collapsible else ""}
    </head>
    <body style="margin:0;padding:0;background:{_PAGE_BG};">
      <div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">
        {preheader}{preheader_pad}
      </div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
        style="background:{_PAGE_BG};">
        <tr>
          <td align="center" style="padding:24px 10px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
              style="width:100%;max-width:640px;">
              <tr>
                <td style="background:{_CARD_BG};border-radius:6px;padding:34px 30px 30px 30px;
                  text-align:left;font-family:{_FONT_STACK};color:{_TEXT_BODY};">
                  <div style="font-size:11px;font-weight:700;letter-spacing:0.14em;
                    text-transform:uppercase;color:{_TEXT_MUTED};">{html.escape(date_str)}</div>
                  <div style="font-family:{_SERIF_STACK};font-size:38px;line-height:1.1;
                    font-weight:700;letter-spacing:-0.01em;color:{_TEXT_HEADING};
                    margin-top:8px;">{title}</div>
                  {summary_html}
                  {_topic_strip_html(summary)}
                  {_topic_index_html(summary)}
                  {_fact_block(fact)}
                  {notice_html}
                  {failure_notice}
                  {body}
                  <div style="margin-top:36px;padding-top:20px;border-top:1px solid {_BORDER};
                    font-size:12px;line-height:1.6;color:{_TEXT_MUTED};">
                    {html.escape(_footer_text(summary))}
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """)


# Gmail hides HTML past about 102KB behind "View entire message". Warn a
# little before that.
GMAIL_CLIP_BYTES = 102 * 1024
GMAIL_WARN_BYTES = 92 * 1024


def _compact_html(markup):
    """Collapse the templates' whitespace. Rendering ignores it, and removing it
    saves room under Gmail's size limit.
    """
    # Replace each run with one space. Some spaces between tags are visible, as
    # in "</a> (Outlet)".
    return re.sub(r"\s+", " ", markup).strip()


def check_email_size(html_body):
    """Log how much of Gmail's size limit this email uses."""
    size = len(html_body.encode("utf-8"))
    pct = size / GMAIL_CLIP_BYTES * 100
    print(f"Email is {size / 1024:.0f}KB ({pct:.0f}% of Gmail's clipping limit)")
    if size >= GMAIL_WARN_BYTES:
        print(
            f"  [warn] approaching Gmail's ~102KB limit. Past it, Gmail shows "
            f"the first part inline and puts the rest behind a "
            f"'View entire message' link. Lower max_stories in topics.json, "
            f"or drop a topic, to keep it inline.",
            file=sys.stderr,
        )
    return size


def _story_label(story):
    """The line a folded story shows: its subheading, or its first sentence if it
    has none.
    """
    subheading = (story.get("subheading") or "").strip()
    if subheading:
        return subheading
    detail = (story.get("detail") or "").strip()
    first = re.split(r"(?<=[.!?])\s+", detail, maxsplit=1)[0] if detail else ""
    if len(first) > 120:
        first = first[:117].rstrip() + "..."
    return first or "More on this topic"
