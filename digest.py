#!/usr/bin/env python3
"""
Daily News Digest

Reads topics.json, pulls recent items from each topic's RSS feeds,
asks Gemini to pick and summarize the top stories per topic, and
emails an HTML digest via Gmail SMTP.

Config lives in topics.json and environment variables (see README.md).
"""

import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import feedparser
import requests
from dateutil import parser as dateparser

CONFIG_PATH = os.environ.get("DIGEST_CONFIG_PATH", "topics.json")

# Cap how many raw articles we ship to the model per topic. Free-tier
# prompts stay smaller and a runaway feed can't blow up the request.
MAX_ARTICLES_PER_TOPIC = 40
FEED_FETCH_TIMEOUT = 20

USER_AGENT = (
    "daily-digest/1.0 (+https://github.com/Sathish-Rajmohan/AI-Daily-News-Digest; "
    "RSS reader for personal digest)"
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    if "topics" not in config or not isinstance(config["topics"], list):
        raise ValueError(f"{CONFIG_PATH} must contain a 'topics' array")
    return config


def parse_entry_time(entry):
    """Best-effort publish time from a feedparser entry."""
    for key in ("published", "updated", "created"):
        if key in entry:
            try:
                dt = dateparser.parse(entry[key])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (ValueError, TypeError, OverflowError):
                pass
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return datetime(*struct[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    return None


def fetch_feed(feed_url):
    """
    Fetch feed XML with an explicit timeout and User-Agent, then parse.
    feedparser.parse(url) alone can hang indefinitely on a bad host.
    """
    try:
        resp = requests.get(
            feed_url,
            timeout=FEED_FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
        )
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except requests.exceptions.RequestException as e:
        # Fall back to feedparser's own fetch — some hosts dislike
        # non-browser clients and only respond to its defaults.
        print(f"  [warn] HTTP fetch failed for {feed_url}: {e}; trying feedparser", file=sys.stderr)
        return feedparser.parse(feed_url)


def fetch_topic_articles(topic, lookback_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles = []
    seen_links = set()

    for feed_url in topic.get("feeds", []):
        try:
            parsed = fetch_feed(feed_url)
        except Exception as e:
            print(f"  [warn] failed to parse feed {feed_url}: {e}", file=sys.stderr)
            continue

        if getattr(parsed, "bozo", False) and not parsed.entries:
            print(f"  [warn] feed unreadable, skipping: {feed_url}", file=sys.stderr)
            continue

        source_name = parsed.feed.get("title", feed_url)

        for entry in parsed.entries:
            pub_time = parse_entry_time(entry)
            # Keep undated items rather than silently dropping them —
            # some feeds omit dates on otherwise useful posts.
            if pub_time is not None and pub_time < cutoff:
                continue

            link = (entry.get("link") or "").strip()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)

            summary = entry.get("summary", "") or entry.get("description", "")
            # Strip tags so the model isn't fed raw HTML soup.
            if summary and "<" in summary:
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s+", " ", summary).strip()

            articles.append({
                "title": entry.get("title", "(untitled)"),
                "link": link,
                "summary": summary,
                "source": source_name,
                "published": pub_time.isoformat() if pub_time else None,
            })

    # Prefer fresher items when we have to trim.
    articles.sort(key=lambda a: a["published"] or "", reverse=True)
    return articles[:MAX_ARTICLES_PER_TOPIC]


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# "gemini-flash-latest" is Google's alias for the current Flash release.
# Override with GEMINI_MODEL if you want to pin a dated ID.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_MAX_RETRIES = 3

STORY_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "summary": {"type": "STRING"},
            "link": {"type": "STRING"},
            "source": {"type": "STRING"},
        },
        "required": ["title", "summary", "link", "source"],
    },
}


def summarize_topic_with_gemini(topic_name, articles, max_stories):
    """
    Ask Gemini to pick the top N distinct stories and summarize each.
    Returns a list of dicts: {title, summary, link, source}
    """
    if not articles:
        return []

    trimmed = []
    for a in articles:
        trimmed.append({
            "title": a["title"],
            "source": a["source"],
            "link": a["link"],
            "snippet": (a["summary"] or "")[:500],
        })

    prompt = f"""You are a careful news editor. Below is a JSON list of recent articles
related to the topic "{topic_name}", pulled from RSS feeds in the lookback window.

Your job:
1. Identify the top {max_stories} most significant, distinct stories or developments.
   Merge duplicate coverage of the same story from different outlets into ONE entry,
   and pick the best/original source link for it.
2. Write a neutral, factual 2-3 sentence summary for each, based only on the
   provided titles/snippets. Do not invent details not implied by the source text.
3. Order them by significance, most important first.

Return ONLY valid JSON (no markdown fences, no preamble), as a list of objects:
[
  {{"title": "...", "summary": "...", "link": "...", "source": "..."}}
]

Articles:
{json.dumps(trimmed, ensure_ascii=False)}
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": STORY_SCHEMA,
        },
    }

    data = None
    last_error = None
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=90)
            if resp.status_code == 429:
                wait = 15 * attempt
                print(
                    f"  [warn] rate limited (429), waiting {wait}s before retry "
                    f"{attempt}/{GEMINI_MAX_RETRIES}",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if resp.status_code == 403:
                print(
                    "  [error] Gemini API returned 403 PERMISSION_DENIED. "
                    "Check that your API key is active in Google AI Studio "
                    "(https://aistudio.google.com/app/apikey) and that the "
                    "project isn't restricted or awaiting verification.",
                    file=sys.stderr,
                )
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            print(
                f"  [warn] request failed (attempt {attempt}/{GEMINI_MAX_RETRIES}): {e}",
                file=sys.stderr,
            )
            time.sleep(5 * attempt)

    if data is None:
        print(
            f"  [error] giving up on topic '{topic_name}' after "
            f"{GEMINI_MAX_RETRIES} attempts: {last_error}",
            file=sys.stderr,
        )
        return []

    # Blocked / empty candidates (safety filters, etc.)
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        print(
            f"  [warn] no candidates for {topic_name}; promptFeedback={feedback}",
            file=sys.stderr,
        )
        return []

    try:
        parts = candidates[0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as e:
        print(f"  [warn] unexpected Gemini response shape for {topic_name}: {e}", file=sys.stderr)
        return []

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()

    try:
        stories = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [warn] could not parse Gemini JSON for {topic_name}: {e}", file=sys.stderr)
        print(f"  raw text: {text[:500]}", file=sys.stderr)
        return []

    if not isinstance(stories, list):
        print(f"  [warn] Gemini returned non-list JSON for {topic_name}", file=sys.stderr)
        return []

    cleaned = []
    for s in stories:
        if not isinstance(s, dict):
            continue
        cleaned.append({
            "title": str(s.get("title") or "(untitled)"),
            "summary": str(s.get("summary") or ""),
            "link": str(s.get("link") or "#"),
            "source": str(s.get("source") or ""),
        })
    return cleaned[:max_stories]


def build_html(topic_results, date_str):
    sections = []
    for topic_name, stories in topic_results:
        if not stories:
            continue
        items_html = ""
        for s in stories:
            title = html.escape(s.get("title", "(untitled)"))
            summary = html.escape(s.get("summary", ""))
            link = html.escape(s.get("link", "#"), quote=True)
            source = html.escape(s.get("source", ""))
            items_html += f"""
            <div style="margin-bottom:18px;">
              <a href="{link}" style="font-size:16px;font-weight:600;color:#1a1a1a;text-decoration:none;">{title}</a>
              <div style="font-size:13px;color:#888;margin:2px 0 6px 0;">{source}</div>
              <div style="font-size:14px;color:#333;line-height:1.5;">{summary}</div>
              <a href="{link}" style="font-size:13px;color:#2563eb;">Read original &rarr;</a>
            </div>
            """
        sections.append(f"""
        <div style="margin-bottom:30px;">
          <h2 style="font-size:19px;border-bottom:2px solid #1a1a1a;padding-bottom:6px;">{html.escape(topic_name)}</h2>
          {items_html}
        </div>
        """)

    body = "".join(sections) if sections else "<p>No new stories found in the lookback window.</p>"

    return f"""
    <html>
    <body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto;padding:20px;background:#fafafa;">
      <h1 style="font-size:22px;margin-bottom:4px;">Your Daily Digest</h1>
      <div style="color:#888;font-size:13px;margin-bottom:24px;">{html.escape(date_str)}</div>
      {body}
      <div style="margin-top:30px;padding-top:16px;border-top:1px solid #ddd;font-size:12px;color:#999;">
        Generated automatically. Edit topics.json in your repo to customize topics and sources.
      </div>
    </body>
    </html>
    """


def send_email(subject, html_body):
    sender = os.environ["GMAIL_ADDRESS"].strip()
    # App passwords are often copied with spaces; Gmail accepts them either way,
    # but stripping avoids accidental paste issues.
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
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=60) as server:
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())


def main():
    missing = [k for k in ("GEMINI_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing required env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    settings = config.get("settings") or {}
    lookback_hours = settings.get("lookback_hours", 24)
    subject_prefix = settings.get("email_subject_prefix", "Daily Digest")
    local_tz_name = settings.get("timezone", "Australia/Sydney")

    try:
        local_tz = ZoneInfo(local_tz_name)
    except Exception:
        print(f"  [warn] unknown timezone '{local_tz_name}', falling back to UTC", file=sys.stderr)
        local_tz = timezone.utc

    topic_results = []
    for topic in config["topics"]:
        name = topic.get("name") or "Untitled"
        max_stories = topic.get("max_stories", 5)
        print(f"Fetching articles for topic: {name}")
        try:
            articles = fetch_topic_articles(topic, lookback_hours)
        except Exception as e:
            print(f"  [error] fetch failed for '{name}': {e}", file=sys.stderr)
            topic_results.append((name, []))
            continue

        print(f"  found {len(articles)} raw articles (capped at {MAX_ARTICLES_PER_TOPIC})")

        if not articles:
            topic_results.append((name, []))
            continue

        print(f"  summarizing with {GEMINI_MODEL}...")
        try:
            stories = summarize_topic_with_gemini(name, articles, max_stories)
        except Exception as e:
            print(f"  [error] summarize failed for '{name}': {e}", file=sys.stderr)
            stories = []
        print(f"  got {len(stories)} summarized stories")
        topic_results.append((name, stories))

        # Be gentle on free-tier rate limits across topics.
        time.sleep(2)

    if not any(stories for _, stories in topic_results):
        print(
            "WARNING: every topic came back empty. Still sending a stub email "
            "so you notice the run happened.",
            file=sys.stderr,
        )

    now_local = datetime.now(local_tz)
    date_str = now_local.strftime("%A, %d %B %Y")
    html_body = build_html(topic_results, date_str)
    subject = f"{subject_prefix} — {date_str}"

    print("Sending email...")
    try:
        send_email(subject, html_body)
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
