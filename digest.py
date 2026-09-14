#!/usr/bin/env python3
"""
Daily News Digest

Reads topics.json, pulls recent items from each topic's RSS feeds,
asks Gemini to write one synthesized brief per topic (using multiple
outlets to fill in the picture), and emails an HTML digest via Gmail SMTP.

Config lives in topics.json and environment variables (see README.md).
"""

import html
import json
import os
import re
import smtplib
import socket
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

# feedparser's own fetch path (used as a fallback in fetch_feed() below)
# doesn't take a timeout argument and can hang indefinitely on a bad host.
# It falls back to the socket module's default timeout when none is given
# explicitly, so set that process-wide to keep a worst-case feed bounded.
socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)

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
        # Fall back to feedparser's own fetch; some hosts dislike
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
            # Keep undated items rather than silently dropping them;
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

BRIEF_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "sources": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "link": {"type": "STRING"},
                    "outlet": {"type": "STRING"},
                },
                "required": ["title", "link", "outlet"],
            },
        },
    },
    "required": ["headline", "summary", "sources"],
}


def summarize_topic_with_gemini(topic_name, articles, max_developments):
    """
    Ask Gemini for ONE briefing per topic.

    Multiple outlets covering the same development should be fused into that
    single narrative (not listed as separate summaries). Return shape:
    {headline, summary, sources: [{title, link, outlet}, ...]} or None.
    """
    if not articles:
        return None

    trimmed = []
    for a in articles:
        trimmed.append({
            "title": a["title"],
            "source": a["source"],
            "link": a["link"],
            "snippet": (a["summary"] or "")[:500],
        })

    prompt = f"""You are a careful news editor writing a daily briefing section
for the topic "{topic_name}". Below is a JSON list of recent articles from
several outlets in the lookback window.

Hard rules:
1. Produce exactly ONE briefing for this topic. Do not emit a list of
   separate story summaries.
2. Cluster coverage of the same event across outlets. When several feeds
   report the same development, treat them as one thread and use their
   combined detail to paint a fuller picture (who, what, where, stakes,
   what happens next). Prefer corroboration over repeating the same lead.
3. Inside that single briefing, cover at most the {max_developments} most
   significant distinct developments. Weave them into one coherent
   narrative (a few short paragraphs), not a bullet list of mini-stories.
4. Stay neutral and factual. Use only information implied by the provided
   titles/snippets. Do not invent quotes, numbers, or outcomes.
5. In "sources", list the specific articles you relied on (typically 3-8),
   preferring primary or clearer reporting when duplicates exist. Every
   cited link must come from the article list below.

Return ONLY valid JSON matching:
{{
  "headline": "short section headline for the whole topic",
  "summary": "multi-paragraph briefing that synthesizes the day for this topic",
  "sources": [
    {{"title": "...", "link": "...", "outlet": "..."}}
  ]
}}

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
            "responseSchema": BRIEF_SCHEMA,
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
        return None

    # Blocked / empty candidates (safety filters, etc.)
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        print(
            f"  [warn] no candidates for {topic_name}; promptFeedback={feedback}",
            file=sys.stderr,
        )
        return None

    try:
        parts = candidates[0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as e:
        print(f"  [warn] unexpected Gemini response shape for {topic_name}: {e}", file=sys.stderr)
        return None

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()

    try:
        brief = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [warn] could not parse Gemini JSON for {topic_name}: {e}", file=sys.stderr)
        print(f"  raw text: {text[:500]}", file=sys.stderr)
        return None

    # Older prompts returned a list of stories; refuse that shape so we never
    # accidentally email multiple per-topic summaries again.
    if isinstance(brief, list):
        print(
            f"  [warn] Gemini returned a list for {topic_name}; expected one briefing object",
            file=sys.stderr,
        )
        return None
    if not isinstance(brief, dict):
        print(f"  [warn] Gemini returned non-object JSON for {topic_name}", file=sys.stderr)
        return None

    sources_in = brief.get("sources") or []
    sources = []
    if isinstance(sources_in, list):
        for s in sources_in:
            if not isinstance(s, dict):
                continue
            link = str(s.get("link") or "").strip()
            if not link:
                continue
            sources.append({
                "title": str(s.get("title") or "(untitled)"),
                "link": link,
                "outlet": str(s.get("outlet") or s.get("source") or ""),
            })

    summary = str(brief.get("summary") or "").strip()
    headline = str(brief.get("headline") or topic_name).strip()
    if not summary:
        print(f"  [warn] empty summary for {topic_name}", file=sys.stderr)
        return None

    return {
        "headline": headline,
        "summary": summary,
        "sources": sources,
    }


def _summary_to_html(summary):
    """Turn paragraph breaks in the briefing into HTML paragraphs."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", summary) if p.strip()]
    if not parts:
        parts = [summary.strip()]
    return "".join(
        f'<p style="font-size:14px;color:#333;line-height:1.55;margin:0 0 12px 0;">{html.escape(p)}</p>'
        for p in parts
    )


def build_html(topic_results, date_str):
    sections = []
    for topic_name, brief in topic_results:
        if not brief:
            continue

        headline = html.escape(brief.get("headline") or topic_name)
        summary_html = _summary_to_html(brief.get("summary") or "")

        sources_html = ""
        for s in brief.get("sources") or []:
            title = html.escape(s.get("title", "(untitled)"))
            link = html.escape(s.get("link", "#"), quote=True)
            outlet = html.escape(s.get("outlet", ""))
            outlet_bit = f" <span style=\"color:#888;\">({outlet})</span>" if outlet else ""
            sources_html += (
                f'<li style="margin:0 0 6px 0;">'
                f'<a href="{link}" style="color:#2563eb;text-decoration:none;">{title}</a>'
                f"{outlet_bit}</li>"
            )

        sources_block = ""
        if sources_html:
            sources_block = f"""
            <div style="margin-top:14px;">
              <div style="font-size:12px;letter-spacing:0.04em;text-transform:uppercase;color:#888;margin-bottom:6px;">Sources</div>
              <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.4;">{sources_html}</ul>
            </div>
            """

        sections.append(f"""
        <div style="margin-bottom:34px;">
          <h2 style="font-size:19px;border-bottom:2px solid #1a1a1a;padding-bottom:6px;margin-bottom:8px;">{html.escape(topic_name)}</h2>
          <div style="font-size:15px;font-weight:600;color:#1a1a1a;margin:0 0 10px 0;">{headline}</div>
          {summary_html}
          {sources_block}
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
        # How many distinct developments to fold into the single topic brief.
        max_developments = topic.get("max_stories", 5)
        print(f"Fetching articles for topic: {name}")
        try:
            articles = fetch_topic_articles(topic, lookback_hours)
        except Exception as e:
            print(f"  [error] fetch failed for '{name}': {e}", file=sys.stderr)
            topic_results.append((name, None))
            continue

        print(f"  found {len(articles)} raw articles (capped at {MAX_ARTICLES_PER_TOPIC})")

        if not articles:
            topic_results.append((name, None))
            continue

        print(f"  writing one synthesized brief with {GEMINI_MODEL}...")
        try:
            brief = summarize_topic_with_gemini(name, articles, max_developments)
        except Exception as e:
            print(f"  [error] summarize failed for '{name}': {e}", file=sys.stderr)
            brief = None
        if brief:
            n_sources = len(brief.get("sources") or [])
            print(f"  got briefing with {n_sources} cited sources")
        else:
            print("  got no briefing")
        topic_results.append((name, brief))

        # Be gentle on free-tier rate limits across topics.
        time.sleep(2)

    if not any(brief for _, brief in topic_results):
        print(
            "WARNING: every topic came back empty. Still sending a stub email "
            "so you notice the run happened.",
            file=sys.stderr,
        )

    now_local = datetime.now(local_tz)
    date_str = now_local.strftime("%A, %d %B %Y")
    html_body = build_html(topic_results, date_str)
    subject = f"{subject_prefix} - {date_str}"

    print("Sending email...")
    try:
        send_email(subject, html_body)
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
