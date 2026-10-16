"""What the model is asked to write, and what comes back: the daily briefing
for each topic, the fact of the day, and the headlines-only fallback when no
model answers.
"""

import html
import json
import re
import sys

from digestlib.feeds import SNIPPET_CHARS
from digestlib.models import _to_json_schema, run_chain


# The rules go in the system instruction so the article list doesn't bury
# them. They're given as numbers ("about 15-20 words") because models follow
# numbers more closely than adjectives. Dense sentences were the most common
# problem, which is what the example at the end is for.
SYSTEM_INSTRUCTION = """You are a news editor writing a daily briefing for a \
busy general reader. Each request gives you a numbered list of recent \
articles, possibly from several outlets, about one topic. Your job is to \
make that topic easy to skim and easy to follow.

STRUCTURE

1. Write "overview" as 2-3 sentences saying what matters most in this topic \
today. Someone who reads only the overview should still come away knowing \
the day's main points. Tie the day together: say what the through-line is, \
or which one development matters most and why. Do not open with a label or \
a preamble like "Here is today's summary", and do not spend it restating \
the subheadings one by one, since the reader sees those next.
2. Write one entry in "stories" for each distinct development, up to the \
limit given in the request. Two articles describing the same underlying \
event, decision, or announcement are ONE story, not two, even when the \
outlets word it differently. Fuse them and combine their detail.
3. "subheading" states plainly what happened, in under 10 words. Write it \
the way a person would say it out loud. Good: "Ceasefire talks restart \
after a week's pause". Bad: "Geopolitical Developments Update".
4. "detail" is 2-3 short paragraphs on what happened, who it affects, and \
why it matters, or one paragraph where that is all the articles support \
(rule 14). Separate paragraphs with a blank line. Do not repeat the \
subheading as the first sentence.

LANGUAGE

5. Keep sentences short. Average about 15-20 words and never run past 25. \
One idea per sentence.
6. Use the active voice. Write "the central bank raised rates", not "rates \
were raised by the central bank".
7. Use everyday words. Where a technical term can't be avoided, \
explain it in plain words in the same sentence the first time it appears.
8. Use at most one subordinate clause per sentence. Split a long sentence \
into two, and don't join the halves with a semicolon or a dash.
9. Start each paragraph with its point and then support it. Do not build up \
to the point.

ACCURACY

10. Use only the numbered articles given to you in this request. Do not draw \
on outside or prior knowledge, even if you believe it to be true or think it \
would round out the picture. If the given articles don't say it, it does not \
go in the briefing.
11. If articles disagree on a specific detail (a figure, a cause, an \
attribution), say so briefly instead of picking one version.
12. Stay neutral. Describe positions and disputes without settling them, \
and attribute claims to whoever made them instead of stating them as fact.
13. Fill "article_ids" for a story BEFORE writing its detail. List every \
article that story draws on and no others. Never invent an id, and never \
cite an article that does not support the claim you used it for.
14. You are given short snippets, not full articles. Write what they \
support and stop there. A thin story gets one short paragraph; do not pad \
it out to three with background, restatement or hedging to reach a length. \
Two solid paragraphs beat three padded ones.

DENSITY

Density is the usual failure here, so compare these two:

- WRONG (one 36-word sentence, three ideas stacked into it): "The central \
bank, which had been widely expected to continue its easing cycle following \
three consecutive cuts, signalled a more cautious stance on Tuesday, sending \
bond yields higher as investors repriced their expectations for the year."
- RIGHT (three sentences, one idea each, 16 words on average): "The central \
bank signalled on Tuesday that it will slow down the pace of its rate cuts. \
Investors had expected another cut, because the bank had already cut three \
times in a row. Bond yields rose as those investors changed their bets for \
the rest of the year."

Both carry the same information. Write the second every time."""

# Kept shallow because Flash models get unreliable with deeply nested
# schemas. article_ids is a flat list of integers for the same reason.
#
# propertyOrdering has the model write the cited ids before the prose that
# uses them, and the stories before the overview that sums them up.
BRIEF_SCHEMA = {
    "type": "OBJECT",
    "description": "One day's briefing for a single news topic.",
    "properties": {
        "stories": {
            "type": "ARRAY",
            "description": (
                "One entry per distinct development, most significant first. "
                "Articles covering the same underlying event belong in the "
                "same entry, per rule 2."
            ),
            "items": {
                "type": "OBJECT",
                "properties": {
                    "article_ids": {
                        "type": "ARRAY",
                        "description": (
                            "Ids of the numbered articles this story draws on, "
                            "chosen before the detail is written, per rule 13."
                        ),
                        "items": {"type": "INTEGER"},
                    },
                    "subheading": {
                        "type": "STRING",
                        "description": (
                            "What happened, stated plainly in under 10 words, "
                            "per rule 3. Not a category label."
                        ),
                    },
                    "detail": {
                        "type": "STRING",
                        "description": (
                            "Up to 3 short paragraphs separated by a blank "
                            "line, per rules 4 and 14, following the language "
                            "rules 5-9."
                        ),
                    },
                },
                "propertyOrdering": ["article_ids", "subheading", "detail"],
                "required": ["article_ids", "subheading", "detail"],
            },
        },
        "overview": {
            "type": "STRING",
            "description": (
                "2-3 sentences on what matters most across this topic today, "
                "per rule 1. Written after the stories, complete enough for a "
                "reader who reads nothing else, and not a restatement of the "
                "subheadings."
            ),
        },
    },
    "propertyOrdering": ["stories", "overview"],
    "required": ["stories", "overview"],
}


BRIEF_SCHEMA_JSON = _to_json_schema(BRIEF_SCHEMA)


# The subject changes daily and repeats every fourteen days. Left to choose,
# a model keeps picking the same few well-known facts.
FACT_FIELDS = [
    "physics", "biology", "economics", "history", "psychology",
    "mathematics", "engineering", "linguistics", "geology", "medicine",
    "astronomy", "chemistry", "anthropology", "computer science",
]

# The angle is picked separately. Asking about a field the same broad way
# brings back its most famous fact, and an angle narrows the question. With
# 11 angles and 14 fields, a pairing repeats every 154 days.
#
# The angles stay everyday and concrete. Abstract ones such as "a hard limit,
# and what sets it" produced dense, jargon-heavy facts.
FACT_ANGLES = [
    "something ordinary that works differently than most people guess",
    "a number that sounds wrong but is true",
    "an everyday word or name with a surprising origin",
    "an accident or mistake that led to something people use today",
    "a common belief that is wrong, and what is actually true",
    "a record: the biggest, smallest, oldest, fastest or strangest of its kind",
    "how people did something ordinary before modern technology",
    "a surprising link between two things that seem unrelated",
    "something that happens every day that almost nobody notices",
    "a comparison of size or time that makes something easy to picture",
    "a simple reason behind something people see all the time",
]

# Word limits given in FACT_SYSTEM and checked on each reply.
FACT_MAX_WORDS = 25
EXPLANATION_MAX_WORDS = 40

# Based on research into memorable facts. A surprise lands best on something
# the reader half knows, and a number needs a familiar comparison. The WRONG
# example is a real fact the digest sent before these rules.
FACT_SYSTEM = f"""You write one fun fact a day for a curious adult reading \
their morning email. It should take about ten seconds to read, make sense on \
the first pass, and leave them knowing something new they could tell a friend.

Each request names a field and an angle. Pick a fact from that field that \
fits the angle. If nothing solid fits the angle, ignore the angle and pick \
the field's best simple fact instead.

CHOOSING THE FACT

1. Pick something the reader can picture: an object, an animal, a place, a \
person, a number, an everyday moment. Skip abstract mechanisms, theories and \
policy.
2. Make it surprising, and land the surprise on something familiar. A twist \
on something the reader already knows sparks curiosity. A fact about \
something they have never heard of does not.
3. One idea only. If the fact needs a second idea explained first, pick a \
different fact.
4. Only well-established facts. No myths, disputed claims, or things \
"everyone knows" that are really folklore. Skip the overused ones too: \
honey never spoils, octopuses have three hearts, bananas are radioactive, \
Napoleon was short, the Great Wall is visible from space.

WRITING IT

5. "fact" is one sentence of {FACT_MAX_WORDS} words or fewer that states the \
surprising thing plainly. No lead-in like "Did you know", and no \
exclamation marks.
6. "explanation" answers "how come?" in two or three short sentences, \
{EXPLANATION_MAX_WORDS} words or fewer in total, with one idea per sentence.
7. Write so a curious 12-year-old could follow every word. Use everyday \
words. Leave out technical terms and the names of concepts, and describe the \
thing itself instead. The only exception is a word most adults already know, \
like "DNA" or "gravity".
8. When a number matters, round it and compare it to something familiar, \
like "about as heavy as a car" or "longer than a football field". Leave out \
numbers that don't add to the surprise.
9. Stop once "how come?" is answered. Do not end with a lesson or a big \
claim like "this shows that".

THE DIFFERENCE

WRONG (a real fact this digest sent, shortened: jargon, three ideas, 79 words):
fact: "For decades, economists believed central banks could never cut interest \
rates below zero because depositors would withdraw their money as paper cash. \
This barrier, the zero lower bound, is actually determined by the physical \
cost of storing, insuring, and transporting large amounts of currency."
explanation: "When European and Japanese central banks introduced negative \
rates, commercial banks chose not to hoard cash. They realized that renting \
secure vaults and hiring guards cost more than paying the central bank's \
negative interest fee."

RIGHT (the same idea: one picture, plain words, 48 words):
fact: "Some European banks had to pay a fee to keep their money at the \
central bank, and most paid rather than take it out."
explanation: "The other choice was taking it out as cash. But mountains of \
banknotes need vaults, guards and insurance. That cost more than the fee."

Use the example for its style only, and do not reuse its topic."""

FACT_SCHEMA = {
    "type": "OBJECT",
    "description": "One fun fact, and how come it's true.",
    "properties": {
        "fact": {
            "type": "STRING",
            "description": (
                f"One plain sentence of {FACT_MAX_WORDS} words or fewer, per rule 5."
            ),
        },
        "explanation": {
            "type": "STRING",
            "description": (
                f"How come, in two or three short sentences of "
                f"{EXPLANATION_MAX_WORDS} words or fewer, per rules 6-9."
            ),
        },
    },
    "propertyOrdering": ["fact", "explanation"],
    "required": ["fact", "explanation"],
}


def fetch_fact_of_the_day(today):
    """The fact of the day, from the same model chain as the briefings.

    It comes from the model's own knowledge, so it has no sources. It runs
    after the topics so they use the time budget first, and returns None if no
    model answers.
    """
    ordinal = today.toordinal()
    field = FACT_FIELDS[ordinal % len(FACT_FIELDS)]
    angle = FACT_ANGLES[ordinal % len(FACT_ANGLES)]
    print(f"Fetching the fact of the day ({field})...")

    def build_prompt(_cap):
        return (
            f"Today is {today.strftime('%d %B %Y')}.\n"
            f"Field: {field}\n"
            f"Angle: {angle}\n\n"
            "Give one fact from that field, approached from that angle, "
            "chosen per your instructions."
        )

    fact = run_chain(build_prompt, FACT_SYSTEM, FACT_SCHEMA, "fact of the day")
    if not isinstance(fact, dict):
        return None

    text = str(fact.get("fact") or "").strip()
    why = str(fact.get("explanation") or "").strip()
    if not text:
        return None

    # Log the lengths so a drift back to long facts shows up.
    fact_words, why_words = len(text.split()), len(why.split())
    print(f"  fact is {fact_words} words, explanation {why_words}")
    if fact_words > FACT_MAX_WORDS or why_words > EXPLANATION_MAX_WORDS:
        print(
            f"  [warn] the fact came back longer than the prompt allows "
            f"({FACT_MAX_WORDS} and {EXPLANATION_MAX_WORDS} words)",
            file=sys.stderr,
        )
    return {"field": field, "fact": text, "why": why}


def summarize_topic(topic_name, articles, max_developments):
    """One briefing for a topic, from the first model that answers.

    The model cites articles by number, and titles, links and outlets are
    filled in from `articles`, so it can't invent a URL. Returns None or
    {overview, stories: [{subheading, detail, sources}, ...]}.
    """
    if not articles:
        return None

    # Numbered from 1 to match the ids the model cites. The model gets the id,
    # title, outlet and snippet, never the link.
    by_id = {i: a for i, a in enumerate(articles, start=1)}

    def build_prompt(cap):
        # A shorter list for one provider is the front of the same list, so an id
        # points at the same article whichever model answers.
        numbered = [
            {
                "id": i,
                "title": a["title"],
                "outlet": a["source"],
                "snippet": (a["summary"] or "")[:SNIPPET_CHARS],
            }
            for i, a in list(by_id.items())[:cap]
        ]

        # Data first and instructions last, with the data inside a tag. Google
        # suggests this layout for long inputs.
        #
        # Feed text could contain the closing tag and end the list early. Escaping
        # angle brackets inside the JSON leaves it identical once decoded. The topic
        # name is escaped too, because it goes in an attribute.
        outlets = len({a["source"] for a in list(by_id.values())[:cap]})
        data = (
            json.dumps(numbered, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )
        return f"""<articles topic="{html.escape(topic_name, quote=True)}" outlets="{outlets}">
{data}
</articles>

The articles above are cycled through the outlets, so every outlet is
represented and position in the list carries no ranking.

Based only on the articles above, write today's "{topic_name}" briefing per
your instructions. Give at most {max_developments} stories, most significant
first, and cite each one by the numeric ids it draws on. Judge significance
by what happened, not by where an article sits in the list. Several outlets
covering the same event is the strongest signal that it matters, so lead
with those. Keep the sentences short and plain, per language rules 5-9.
"""

    brief = run_chain(build_prompt, SYSTEM_INSTRUCTION, BRIEF_SCHEMA, topic_name)
    if brief is None:
        return None

    # A bare list isn't a briefing, so refuse it.
    if isinstance(brief, list):
        print(
            f"  [warn] model returned a list for {topic_name}; expected one briefing object",
            file=sys.stderr,
        )
        return None
    if not isinstance(brief, dict):
        print(f"  [warn] model returned non-object JSON for {topic_name}", file=sys.stderr)
        return None

    stories = []
    for entry in brief.get("stories") or []:
        if not isinstance(entry, dict):
            continue
        subheading = str(entry.get("subheading") or "").strip()
        detail = str(entry.get("detail") or "").strip()
        if not subheading and not detail:
            continue
        stories.append({
            "subheading": subheading,
            "detail": detail,
            "sources": _resolve_sources(entry.get("article_ids"), by_id, topic_name),
        })

    overview = str(brief.get("overview") or "").strip()
    if not stories and not overview:
        print(f"  [warn] empty briefing for {topic_name}", file=sys.stderr)
        return None

    return {
        "overview": overview,
        "stories": stories,
    }


def average_sentence_length(brief):
    """Mean words per sentence across a briefing's overview and story text.
    Logged on each run to check the prompt's 15-20 word target. None if there's
    no text.
    """
    prose = [brief.get("overview") or ""]
    prose += [s.get("detail") or "" for s in brief.get("stories") or []]
    sentences = [
        s for s in re.split(r"(?<=[.!?])\s+", " ".join(prose).strip()) if s.strip()
    ]
    if not sentences:
        return None
    words = sum(len(s.split()) for s in sentences)
    return words / len(sentences)


def _resolve_sources(article_ids, by_id, topic_name):
    """Look up the articles a story cited. Titles, links and outlets come from the
    fetched articles, so nothing the model wrote becomes a link. Unknown ids
    are dropped.
    """
    sources = []
    seen = set()
    if not isinstance(article_ids, list):
        return sources

    for raw in article_ids:
        article_id = _as_article_id(raw)
        if article_id is None:
            continue
        if article_id in seen:
            continue
        article = by_id.get(article_id)
        if article is None:
            print(
                f"  [warn] cited unknown article_id {article_id} for {topic_name}; dropping",
                file=sys.stderr,
            )
            continue
        seen.add(article_id)
        sources.append({
            "title": article["title"],
            "link": article["link"],
            "outlet": article["source"],
        })
    return sources


def _as_article_id(raw):
    """An integer id from what the model cited, or None. Accepts digit strings and
    whole-number floats. Rejects fractions and booleans, which int() would turn
    into a different id.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def headlines_only_brief(articles, max_developments):
    """A plain list of headlines for a topic no model could summarise. Marked
    degraded so the email labels it as headlines.
    """
    picked = articles[: max(3, min(max_developments, 6))]
    if not picked:
        return None
    return {
        "overview": None,
        "degraded": True,
        "stories": [{
            "subheading": None,
            "detail": None,
            "sources": [
                {"title": a["title"], "link": a["link"], "outlet": a["source"]}
                for a in picked
            ],
        }],
    }
