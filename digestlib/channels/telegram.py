"""Sending the digest to a Telegram chat, through a bot.

The bot's token is part of every request's address, so an error never
quotes the address; the channel layer also hides the token in anything
printed.
"""

import html
import os
import time

import requests

from digestlib.channels.chat import PlainText, chat_messages

TELEGRAM_API = "https://api.telegram.org"

# Telegram allows 4,096 characters a message, counted after the markup is
# read. The markup counts here too, which leaves room for characters
# Telegram counts twice.
TELEGRAM_LIMIT = 4000

# Telegram asks for no more than one message a second in a chat.
TELEGRAM_GAP = 1.0

# Longest wait for a 429's retry_after before giving up on the message.
TELEGRAM_MAX_RETRY = 30


class TelegramHTML(PlainText):
    """Telegram's HTML parse mode, which only needs <, > and & escaped."""

    def escape(self, text):
        # A word joiner after @ keeps a name in a headline from becoming a
        # mention that notifies someone.
        return html.escape(text, quote=False).replace("@", "@⁠")

    def bold(self, text):
        return f"<b>{self.escape(text)}</b>"

    def link(self, text, url):
        return f'<a href="{html.escape(url, quote=True)}">{self.escape(text)}</a>'


def send_telegram(message, story_text=False):
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    texts = chat_messages(message, TelegramHTML(), TELEGRAM_LIMIT, story_text)
    for index, text in enumerate(texts):
        if index:
            time.sleep(TELEGRAM_GAP)
        _post(token, {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })
    print(f"  sent {len(texts)} Telegram messages")


def _post(token, body):
    """One sendMessage call, with one retry after a 429."""
    for attempt in (1, 2):
        resp = requests.post(f"{TELEGRAM_API}/bot{token}/sendMessage", json=body, timeout=30)
        try:
            reply = resp.json()
        except ValueError:
            reply = {}
        if resp.status_code == 200 and reply.get("ok"):
            return
        wait = (reply.get("parameters") or {}).get("retry_after")
        if resp.status_code == 429 and attempt == 1 and isinstance(wait, int) and wait <= TELEGRAM_MAX_RETRY:
            time.sleep(wait)
            continue
        raise RuntimeError(f"Telegram answered {resp.status_code}: "
                           f"{reply.get('description') or 'no description'}")
