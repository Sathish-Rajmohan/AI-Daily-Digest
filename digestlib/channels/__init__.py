"""Where the digest is delivered.

Each channel names the secrets it needs and sends one message: a dict with
the edition ("daily" or "weekly"), the subject, the date label, the topic
results, the fact, the notices, and the email's HTML and AMP bodies when
email is on. A channel that fails doesn't stop the others.
"""

import os
import sys

from digestlib.channels.email import send_email

EDITIONS = ("daily", "weekly")

# type -> what the channel needs. "needs" must be set before the run starts.
# "secrets" are hidden in any line the run prints. "html" says the channel
# sends the HTML email, so the run only builds it when one does.
CHANNELS = {
    "email": {
        "needs": ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"),
        "secrets": ("GMAIL_APP_PASSWORD",),
        "html": True,
        "send": lambda message: send_email(message["subject"], message["html"], message["amp"]),
    },
}

# Without a "channels" list, the digest goes by email, as it always has.
DEFAULT_CHANNELS = [{"type": "email"}]


def channels_for(config, edition):
    """The channel settings that carry this edition, in the order listed."""
    listed = config.get("channels") or DEFAULT_CHANNELS
    return [c for c in listed if edition in c.get("editions", EDITIONS)]


def missing_secrets(channels):
    """(type, [unset variable names]) for each channel that isn't set up.
    Actions passes an unset secret as an empty string, so empty counts as unset.
    """
    missing = []
    for channel in channels:
        names = [n for n in CHANNELS[channel["type"]]["needs"] if not os.environ.get(n, "").strip()]
        if names:
            missing.append((channel["type"], names))
    return missing


def wants_html(channels):
    return any(CHANNELS[c["type"]]["html"] for c in channels)


def hide_secrets(text):
    """text with every channel secret that's set replaced, so an error that
    quotes an address or a token can be printed.
    """
    text = str(text)
    for spec in CHANNELS.values():
        for name in spec["secrets"]:
            value = os.environ.get(name, "").strip()
            if value:
                text = text.replace(value, f"[{name}]")
    return text


def send_to_channels(channels, message):
    """Send the message to each channel on its own. Returns the types that worked."""
    worked, failed = [], []
    for channel in channels:
        kind = channel["type"]
        print(f"Sending to {kind}...")
        try:
            CHANNELS[kind]["send"](message)
        except Exception as e:
            print(f"  [error] {kind} failed: {hide_secrets(e)}", file=sys.stderr)
            failed.append(kind)
        else:
            worked.append(kind)
    if len(channels) > 1:
        print(f"Delivered to {', '.join(worked) or 'no channel'}"
              + (f"; failed: {', '.join(failed)}." if failed else "."))
    return worked
