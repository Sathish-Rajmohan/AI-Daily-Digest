"""Sending the digest by email, through Gmail's SMTP server.
"""

import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(subject, html_body, amp_body=None):
    sender = os.environ["GMAIL_ADDRESS"].strip()
    # App Passwords are often pasted with spaces in them.
    app_password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    recipient = os.environ.get("RECIPIENT_EMAIL", sender).strip() or sender

    for name, value in (
        ("GMAIL_ADDRESS", sender),
        ("GMAIL_APP_PASSWORD", app_password),
        ("RECIPIENT_EMAIL", recipient),
    ):
        if not value:
            raise RuntimeError(f"{name} is empty")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    if amp_body and sender.lower() == recipient.lower():
        # Gmail ignores the AMP part when From and To are the same address.
        print(
            "  [warn] collapsible stories need RECIPIENT_EMAIL to be a different "
            "address from GMAIL_ADDRESS, or Gmail won't show them. Sending the "
            "full version only.",
            file=sys.stderr,
        )
        amp_body = None

    # Clients show the last part they support, so HTML goes last. Gmail expects
    # the AMP part before it.
    if amp_body:
        msg.attach(MIMEText(amp_body, "x-amp-html", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=60) as server:
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())
