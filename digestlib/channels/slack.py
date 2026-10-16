"""Sending the digest to a Slack channel, through an incoming webhook.

Each message is one section block, which holds up to 3,000 characters.
Slack only reads <, > and & as markup, so escaping them means feed text
can't add a link or a mention such as <!channel>.
"""

import os
import time

import requests

from digestlib.channels.chat import PlainText, chat_messages

# A section block's text allows 3,000 characters.
SLACK_LIMIT = 2900

# Slack allows one message a second in a channel.
SLACK_GAP = 1.0

SLACK_MAX_RETRY = 30


class SlackMrkdwn(PlainText):
    def escape(self, text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def bold(self, text):
        return f"*{self.escape(text)}*"

    def link(self, text, url):
        # | and > would end the address early.
        url = url.replace("|", "%7C").replace("<", "%3C").replace(">", "%3E")
        return f"<{url}|{self.escape(text)}>"


def send_slack(message, story_text=False):
    url = os.environ["SLACK_WEBHOOK_URL"].strip()
    texts = chat_messages(message, SlackMrkdwn(), SLACK_LIMIT, story_text)
    for index, text in enumerate(texts):
        if index:
            time.sleep(SLACK_GAP)
        _post(url, {
            # Shown in notifications, where blocks aren't.
            "text": text,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            "unfurl_links": False,
            "unfurl_media": False,
        })
    print(f"  sent {len(texts)} Slack messages")


def _post(url, body):
    """One webhook call, with one retry after a 429."""
    for attempt in (1, 2):
        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code == 200:
            return
        wait = resp.headers.get("Retry-After", "")
        if resp.status_code == 429 and attempt == 1 and wait.isdigit() and int(wait) <= SLACK_MAX_RETRY:
            time.sleep(int(wait))
            continue
        raise RuntimeError(f"Slack answered {resp.status_code}: {(resp.text or 'no reason').strip()[:200]}")
