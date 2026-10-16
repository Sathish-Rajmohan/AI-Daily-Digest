"""The digest as chat messages.

Chat apps can't fold stories, so each topic gets one message: its name, the
overview, then each story's heading linked to its first source. A channel's
"story_text" setting adds each story's text under its heading. A message
over the channel's limit is split between stories. The fact of the day, if
there is one, comes last on its own.

Every piece of feed or model text goes through the channel's markup, which
escapes it, so a headline can't add formatting, links or mentions.
"""

from digestlib.render import DAILY, WEEKLY, _story_label


class PlainText:
    """Markup for a channel without any. Each chat channel has its own class
    with the same four methods.
    """

    def escape(self, text):
        return text

    def bold(self, text):
        return self.escape(text)

    def link(self, text, url):
        return f"{self.escape(text)} ({url})"

    def item(self, text):
        """One line in a list. `text` is already marked up."""
        return f"• {text}"


# Blocks are (text, gap): `gap` goes before the text when it joins the block
# before it in the same message.
PARAGRAPH = "\n\n"
LINE = "\n"


def chat_messages(message, markup, limit, story_text=False):
    """The digest as a list of message texts, each at most `limit` characters."""
    edition = WEEKLY if message["edition"] == "weekly" else DAILY
    header = [(markup.bold(message["subject"]), PARAGRAPH)]
    header += [(markup.escape(text), PARAGRAPH) for text in message.get("notices") or []]
    skipped = [name for name, _, note in message["topic_results"] if note]
    if skipped:
        header.append((markup.escape(f"Skipped this run: {', '.join(skipped)}. "
                                     "Check the Actions log for details."), PARAGRAPH))

    topics = [(name, brief) for name, brief, _ in message["topic_results"] if brief]
    if not topics:
        return _pack(header + [(markup.escape(edition["empty"]), PARAGRAPH)], limit)

    texts = []
    for index, (name, brief) in enumerate(topics):
        blocks = (header if index == 0 else []) + _topic_blocks(name, brief, markup, edition,
                                                               story_text, limit)
        texts += _pack(blocks, limit)
    fact = message.get("fact")
    if fact:
        texts += _pack(_fact_blocks(fact, markup), limit)
    return texts


def _topic_blocks(name, brief, markup, edition, story_text, limit):
    blocks = [(markup.bold(name), PARAGRAPH)]
    if brief.get("degraded"):
        blocks.append((markup.escape(edition["headlines"]), PARAGRAPH))
        # Headlines, or a weekly topic's headings by day: each group is a
        # heading, if it has one, over a list of linked titles.
        for story in brief.get("stories") or []:
            if story.get("subheading"):
                blocks.append((markup.bold(story["subheading"]), PARAGRAPH))
            first = True
            for source in story.get("sources") or []:
                blocks.append((markup.item(_linked(markup, source.get("title") or "", source)),
                               PARAGRAPH if first and not story.get("subheading") else LINE))
                first = False
        return blocks

    if brief.get("overview"):
        blocks.append((markup.escape(brief["overview"]), PARAGRAPH))
    gap = PARAGRAPH
    for story in brief.get("stories") or []:
        blocks.append((_story_text(story, markup, story_text, limit), gap))
        gap = PARAGRAPH if story_text else LINE
    for line in brief.get("also") or []:
        first = (line.get("sources") or [{}])[0]
        blocks.append((markup.item(_linked(markup, line.get("line") or "", first)), gap))
        gap = LINE
    return blocks


def _linked(markup, text, source):
    link = (source or {}).get("link")
    return markup.link(text, link) if link else markup.escape(text)


def _story_text(story, markup, story_text, limit):
    """A story's line, with its text under it if story_text is on. Text too
    long for one message is shortened, so the story still fits in one.
    """
    first = (story.get("sources") or [{}])[0]
    line = markup.item(_linked(markup, _story_label(story), first))
    detail = (story.get("detail") or "").strip() if story_text else ""
    keep = len(detail)
    while keep > 0:
        shown = detail if keep == len(detail) else detail[:keep].rstrip() + "…"
        text = f"{line}{LINE}{markup.escape(shown)}"
        if len(text) <= limit:
            return text
        keep -= len(text) - limit + 1
    return line


def _fact_blocks(fact, markup):
    blocks = [(markup.bold(f"Fact of the day · {fact['field']}"), PARAGRAPH),
              (markup.escape(fact["fact"]), PARAGRAPH)]
    if fact.get("why"):
        blocks.append((markup.escape(fact["why"]), PARAGRAPH))
    return blocks


def _pack(blocks, limit):
    """Join blocks into as few messages as fit under limit, never splitting a
    block. A block longer than limit on its own goes in a message of its own,
    cut at the limit; that only happens to text a channel can't split well.
    """
    texts, current = [], ""
    for text, gap in blocks:
        joined = f"{current}{gap}{text}" if current else text
        if len(joined) <= limit:
            current = joined
            continue
        if current:
            texts.append(current)
        current = text if len(text) <= limit else text[:limit]
    if current:
        texts.append(current)
    return texts
