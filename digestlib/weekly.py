"""The weekly edition: which days and stories it covers, and what the model is
asked to write about them.

It is written from the week's saved daily digests, not from the feeds,
because most feeds have dropped Monday's items by Sunday. The weekly
workflow downloads the saved files into WEEKLY_DIR and runs
`digest.py --weekly`.
"""

import html
import json
import os
import sys
from datetime import timedelta

from digestlib.briefing import _as_article_id
from digestlib.config import WEEKDAYS
from digestlib.feeds import MAX_ARTICLES_PER_TOPIC, SNIPPET_CHARS
from digestlib.models import run_chain
from digestlib.render import _story_label


WEEKLY_DIR = os.environ.get("DIGEST_WEEKLY_DIR", "weekly-input")
WEEKLY_DAYS = 7

# Characters of each daily story's text given to the model. A provider with a
# tight token limit gets SNIPPET_CHARS instead.
WEEKLY_TEXT_CHARS = 700

# Kept low so a full week stays under Gmail's clipping limit.
WEEKLY_THREAD_SOURCES = 6
WEEKLY_LINE_SOURCES = 2
WEEKLY_MAX_ALSO = 8
WEEKLY_HEADINGS_PER_DAY = 5


def week_days(end):
    """The WEEKLY_DAYS dates that end on `end`, oldest first."""
    return [end - timedelta(days=n) for n in range(WEEKLY_DAYS - 1, -1, -1)]


def _day_name(day):
    return f"{WEEKDAYS[day.weekday()]} {day.day} {day:%B}"


def _join(words):
    words = list(words)
    if len(words) < 2:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def week_label(first, last):
    """The week's dates, as in "12 to 18 October 2026"."""
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day} to {last.day} {last:%B %Y}"
    if first.year == last.year:
        return f"{first.day} {first:%B} to {last.day} {last:%B %Y}"
    return f"{first.day} {first:%B %Y} to {last.day} {last:%B %Y}"


def coverage_notice(days, found):
    """What the email says when some days have no saved digest, or None."""
    missing = [day for day in days if day not in found]
    if not missing:
        return None
    return (
        f"This edition covers {len(days) - len(missing)} of {len(days)} days. "
        f"No digest was saved for {_join(_day_name(day) for day in missing)}."
    )


def _saved_sources(raw):
    return [
        {"title": s.get("title"), "link": s.get("link"), "outlet": s.get("outlet")}
        for s in raw or []
        if isinstance(s, dict)
    ]


def week_stories(found, days, topic_name):
    """A topic's stories from the saved days, oldest first, each with its
    date. A day that only had headlines gives one entry per headline.
    """
    stories = []
    for day in days:
        record = found.get(day)
        if not record:
            continue
        for saved in record.get("topics") or []:
            if not isinstance(saved, dict) or saved.get("name") != topic_name:
                continue
            for story in saved.get("stories") or []:
                if not isinstance(story, dict):
                    continue
                sources = _saved_sources(story.get("sources"))
                if saved.get("headlines_only"):
                    stories += [
                        {"date": day, "subheading": str(s.get("title") or "").strip(),
                         "detail": "", "sources": [s], "headline": True}
                        for s in sources if s.get("title")
                    ]
                    continue
                subheading = str(story.get("subheading") or "").strip()
                detail = str(story.get("detail") or "").strip()
                if subheading or detail:
                    stories.append({"date": day, "subheading": subheading, "detail": detail,
                                    "sources": sources, "headline": False})
    return stories


def weekly_topic_names(config, found):
    """The topics.json topics in order, so each keeps its colour, then any
    topic that's only in the saved files, such as one renamed this week.
    """
    names = [topic.get("name") or "Untitled" for topic in config["topics"]]
    for day in sorted(found):
        for saved in found[day].get("topics") or []:
            name = saved.get("name") if isinstance(saved, dict) else None
            if isinstance(name, str) and name not in names:
                names.append(name)
    return names


WEEKLY_SYSTEM = """You are a news editor writing a weekly review for a busy \
general reader. Each request gives you one topic's stories from the past \
week's daily briefings, as a numbered list. Each entry has its day, its \
subheading and the text the daily briefing printed. Some entries are bare \
headlines. Your job is to show what mattered over the week and how the main \
stories developed.

STRUCTURE

1. "threads" are events that the list covers on two or more different days. \
Give at most the number the request allows, most significant first. Entries \
about the same event, decision or dispute belong to one thread, even when \
different days word it differently.
2. For each thread, fill "story_ids" first, with every entry the thread \
draws on and no others. "heading" says plainly what the thread is about, in \
under 10 words. "narrative" is 2-3 short paragraphs separated by a blank \
line: how it started, what changed during the week, and where it stands at \
the end. Name the days, as in "On Tuesday".
3. "also" holds the other stories worth knowing, one line each: events the \
list covers on one day only. Fill "story_ids" first. "line" is one sentence \
of at most 20 words. Leave out minor stories.
4. "overview" is 3-4 sentences on what mattered most in this topic over the \
week. Someone who reads only the overview should know the week's main \
points. Do not restate the headings one by one.

LANGUAGE

5. Keep sentences short. Average about 15-20 words and never run past 25. \
One idea per sentence.
6. Use the active voice and everyday words.

ACCURACY

7. Use only the numbered entries. They are summaries written earlier in the \
week. Add nothing from outside knowledge, even to fill a gap or explain a \
cause.
8. When a later day corrects or updates an earlier one, use the later \
version and say what changed, as in "Monday's count of 40 was revised to 52 \
on Wednesday."
9. Where entries disagree and no later one settles it, say so briefly.
10. Stay neutral, and attribute claims to whoever made them.
11. Never invent an id, and cite only entries that support what you wrote."""

_STORY_IDS = {
    "type": "ARRAY",
    "description": "Ids of the numbered entries this draws on, chosen before the text, per rule 11.",
    "items": {"type": "INTEGER"},
}

WEEKLY_SCHEMA = {
    "type": "OBJECT",
    "description": "One week's review of a single news topic.",
    "properties": {
        "threads": {
            "type": "ARRAY",
            "description": "Events covered on two or more days, most significant first, per rule 1.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "story_ids": _STORY_IDS,
                    "heading": {"type": "STRING", "description": "Under 10 words, per rule 2."},
                    "narrative": {
                        "type": "STRING",
                        "description": "2-3 short paragraphs: start, change, where it stands. Rule 2.",
                    },
                },
                "propertyOrdering": ["story_ids", "heading", "narrative"],
                "required": ["story_ids", "heading", "narrative"],
            },
        },
        "also": {
            "type": "ARRAY",
            "description": "One-line stories from a single day, per rule 3.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "story_ids": _STORY_IDS,
                    "line": {"type": "STRING", "description": "One sentence, at most 20 words."},
                },
                "propertyOrdering": ["story_ids", "line"],
                "required": ["story_ids", "line"],
            },
        },
        "overview": {
            "type": "STRING",
            "description": "3-4 sentences on the week, written last, per rule 4.",
        },
    },
    "propertyOrdering": ["threads", "also", "overview"],
    "required": ["threads", "also", "overview"],
}


def _cited_stories(story_ids, by_id, label):
    """The saved stories an entry cited, in date order. Unknown ids are dropped."""
    cited = {}
    if not isinstance(story_ids, list):
        return []
    for raw in story_ids:
        story_id = _as_article_id(raw)
        if story_id is None or story_id in cited:
            continue
        if story_id not in by_id:
            print(f"  [warn] cited unknown story_id {story_id} for {label}; dropping", file=sys.stderr)
            continue
        cited[story_id] = by_id[story_id]
    return [cited[i] for i in sorted(cited)]


def _week_sources(cited, cap):
    """Links for a thread or line, copied from the saved stories it cites. One
    from each story comes first, so every day keeps a link.
    """
    firsts = [s["sources"][0] for s in cited if s["sources"]]
    rest = [src for s in cited for src in s["sources"][1:]]
    picked, seen = [], set()
    for src in firsts + rest:
        key = src.get("link") or src.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        picked.append(dict(src))
        if len(picked) == cap:
            break
    return picked


def summarize_week(topic_name, stories, max_threads):
    """One topic's weekly review, from the first model that answers.

    The model cites the saved stories by number, and the links are copied
    from those stories, so it can't invent a URL. The days a thread ran are
    named by the script. Returns None or
    {overview, stories: [threads], also: [{line, sources}]}.
    """
    if not stories:
        return None
    by_id = {i: s for i, s in enumerate(stories, start=1)}
    label = f"{topic_name} (weekly)"

    def build_prompt(cap):
        # Every story goes in for every provider, so an id means the same story
        # whichever model answers. A tighter provider gets shorter text.
        chars = WEEKLY_TEXT_CHARS if cap >= MAX_ARTICLES_PER_TOPIC else SNIPPET_CHARS
        numbered = []
        for i, s in by_id.items():
            entry = {"id": i, "day": _day_name(s["date"]), "subheading": s["subheading"]}
            if s["headline"]:
                entry["headline_only"] = True
            else:
                entry["text"] = s["detail"][:chars]
            numbered.append(entry)
        data = (
            json.dumps(numbered, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )
        days = len({s["date"] for s in stories})
        return f"""<stories topic="{html.escape(topic_name, quote=True)}" days="{days}">
{data}
</stories>

The entries above are in date order, oldest first.

Based only on the entries above, write this week's "{topic_name}" review per
your instructions. Give at most {max_threads} threads, each drawn from
entries on two or more different days, then the one-line stories. Keep the
sentences short and plain, per rule 5.
"""

    reply = run_chain(build_prompt, WEEKLY_SYSTEM, WEEKLY_SCHEMA, label)
    if not isinstance(reply, dict):
        if reply is not None:
            print(f"  [warn] model returned non-object JSON for {label}", file=sys.stderr)
        return None

    threads, also = [], []
    for entry in reply.get("threads") or []:
        if not isinstance(entry, dict):
            continue
        cited = _cited_stories(entry.get("story_ids"), by_id, label)
        heading = str(entry.get("heading") or "").strip()
        narrative = str(entry.get("narrative") or "").strip()
        if not cited or not (heading or narrative):
            continue
        days = sorted({s["date"] for s in cited})
        if len(days) < 2 or len(threads) >= max_threads:
            # A thread needs two days and fits under the limit. Anything else
            # becomes a one-line story.
            also.append({"line": heading or _story_label({"detail": narrative}),
                         "sources": _week_sources(cited, WEEKLY_LINE_SOURCES)})
            continue
        threads.append({
            "subheading": f"{heading} · {_join(WEEKDAYS[d.weekday()][:3] for d in days)}"
                          if heading else _join(WEEKDAYS[d.weekday()] for d in days),
            "detail": narrative,
            "sources": _week_sources(cited, WEEKLY_THREAD_SOURCES),
        })

    for entry in reply.get("also") or []:
        if not isinstance(entry, dict):
            continue
        cited = _cited_stories(entry.get("story_ids"), by_id, label)
        line = str(entry.get("line") or "").strip()
        if cited and line:
            also.append({"line": line, "sources": _week_sources(cited, WEEKLY_LINE_SOURCES)})

    overview = str(reply.get("overview") or "").strip()
    if not threads and not also and not overview:
        print(f"  [warn] empty weekly review for {topic_name}", file=sys.stderr)
        return None
    return {"overview": overview, "stories": threads, "also": also[:WEEKLY_MAX_ALSO]}


def headings_by_day(stories):
    """The week's story headings for a topic no model could review, grouped by
    day, each linked to the first source its story cited. Marked degraded so
    the email labels it.
    """
    by_day = {}
    for s in stories:
        first = s["sources"][0] if s["sources"] else {}
        heading = s["subheading"] or first.get("title")
        if heading and len(by_day.setdefault(s["date"], [])) < WEEKLY_HEADINGS_PER_DAY:
            by_day[s["date"]].append(
                {"title": heading, "link": first.get("link"), "outlet": first.get("outlet")}
            )
    if not by_day:
        return None
    return {
        "overview": None,
        "degraded": True,
        "stories": [
            {"subheading": _day_name(day), "detail": None, "sources": items}
            for day, items in sorted(by_day.items())
        ],
    }
