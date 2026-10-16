"""Sending the digest to a Discord channel, through a webhook.

Each message is one embed, whose text can be up to 4,096 characters. Every
mention is switched off, so a headline with @everyone pings nobody.
"""

import os
import re
import time

import requests

from digestlib.channels.chat import PlainText, chat_messages

# An embed's description allows 4,096 characters.
DISCORD_LIMIT = 4000

# A webhook allows about five messages every two seconds.
DISCORD_GAP = 1.0

DISCORD_MAX_RETRY = 30

# The digest's accent colour, for the bar down the side of each embed.
DISCORD_COLOUR = 0x0E6A66

_MARKDOWN = re.compile(r"([\\*_~`|>\[\]()#<@:-])")


class DiscordMarkdown(PlainText):
    def escape(self, text):
        return _MARKDOWN.sub(r"\\\1", text)

    def bold(self, text):
        return f"**{self.escape(text)}**"

    def link(self, text, url):
        # Brackets and spaces in the address would end the link early.
        url = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
        return f"[{self.escape(text)}]({url})"


def send_discord(message, story_text=False):
    url = os.environ["DISCORD_WEBHOOK_URL"].strip()
    texts = chat_messages(message, DiscordMarkdown(), DISCORD_LIMIT, story_text)
    for index, text in enumerate(texts):
        if index:
            time.sleep(DISCORD_GAP)
        _post(url, {
            "embeds": [{"description": text, "color": DISCORD_COLOUR}],
            "allowed_mentions": {"parse": []},
        })
    print(f"  sent {len(texts)} Discord messages")


def _post(url, body):
    """One webhook call, with one retry after a 429."""
    for attempt in (1, 2):
        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code in (200, 204):
            return
        try:
            reply = resp.json()
        except ValueError:
            reply = {}
        wait = reply.get("retry_after")
        if resp.status_code == 429 and attempt == 1 and isinstance(wait, (int, float)) \
                and wait <= DISCORD_MAX_RETRY:
            time.sleep(wait)
            continue
        raise RuntimeError(f"Discord answered {resp.status_code}: {reply.get('message') or 'no message'}")
