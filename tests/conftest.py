"""
Shared fixtures for the digest tests.

Nothing in the suite touches the network, sleeps for real, or needs real
credentials. HTTP, SMTP and the clock are all replaced with fakes, and any
test that reaches for the real network fails loudly rather than quietly
getting an empty result back.
"""

import json
import sys
import types
from email.utils import format_datetime
from pathlib import Path

import feedparser
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import digest  # noqa: E402

REAL_PARSE = feedparser.parse


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------

class FakeClock:
    """Stands in for time.monotonic and time.sleep. Sleeping advances the
    clock instantly, and every requested sleep is recorded."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


# --------------------------------------------------------------------------
# Model APIs
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        if text is None:
            text = json.dumps(payload) if payload is not None else ""
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("response body is not JSON")
        return self._payload


def gemini_reply(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def groq_reply(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})


def status(code, headers=None):
    return FakeResponse(code, {"error": {"code": code}}, headers=headers)


class FakeTransport:
    """
    Replaces requests.post for both providers. Responses are scripted per
    model name and handed out in order; the last one repeats once the script
    runs out. An exception instance in a script is raised instead of
    returned, the way a dropped connection behaves.
    """

    def __init__(self):
        self.scripts = {}
        self.calls = []

    def script(self, model, *responses):
        self.scripts[model] = list(responses)

    def __call__(self, url, headers=None, json=None, timeout=None):
        if "generativelanguage.googleapis.com" in url:
            provider = "gemini"
            model = url.split("/models/")[1].split(":")[0]
        elif url == digest.GROQ_ENDPOINT:
            provider = "groq"
            model = json["model"]
        else:
            raise AssertionError(f"unexpected POST to {url}")

        self.calls.append({"provider": provider, "model": model, "url": url,
                           "headers": headers, "body": json, "timeout": timeout})
        queue = self.scripts.get(model)
        if not queue:
            raise AssertionError(f"no scripted response for {provider}/{model}")
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, BaseException):
            raise response
        return response

    @property
    def tried(self):
        return [f"{c['provider']}/{c['model']}" for c in self.calls]

    def prompt(self, index=0):
        """The user-turn text of a recorded call, whichever provider made it."""
        body = self.calls[index]["body"]
        if self.calls[index]["provider"] == "gemini":
            return body["contents"][0]["parts"][0]["text"]
        return body["messages"][1]["content"]


# --------------------------------------------------------------------------
# Feeds
# --------------------------------------------------------------------------

class FeedResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise digest.requests.exceptions.HTTPError(f"{self.status_code} Client Error")


class FakeFeeds:
    """Serves feed bytes by URL in place of requests.get, and in place of
    feedparser's own fetch for the fallback path."""

    def __init__(self):
        self.routes = {}
        self.fallback_routes = {}
        self.requested = []
        self.fallback_requested = []

    def serve(self, url, body, status_code=200):
        self.routes[url] = (body, status_code)

    def fail(self, url, exc=None):
        self.routes[url] = exc or digest.requests.exceptions.ConnectionError("host down")

    def serve_fallback(self, url, body):
        self.fallback_routes[url] = body

    def get(self, url, timeout=None, headers=None):
        self.requested.append({"url": url, "timeout": timeout, "headers": headers})
        route = self.routes.get(url)
        if route is None:
            raise digest.requests.exceptions.ConnectionError(f"no route for {url}")
        if isinstance(route, BaseException):
            raise route
        body, code = route
        return FeedResponse(body, code)

    def parse(self, source, *args, **kwargs):
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            self.fallback_requested.append(source)
            return REAL_PARSE(self.fallback_routes.get(source, b""))
        return REAL_PARSE(source, *args, **kwargs)


def rfc822(dt):
    return format_datetime(dt)


def rss(*items, title="Test Outlet"):
    """
    A minimal RSS 2.0 document. Item values are inserted as-is, so a caller
    wanting entities or escaped markup in the XML writes them that way.
    """
    out = ['<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>']
    if title is not None:
        out.append(f"<title>{title}</title>")
    for item in items:
        out.append("<item>")
        for tag in ("title", "link", "description", "pubDate", "guid"):
            if tag in item:
                out.append(f"<{tag}>{item[tag]}</{tag}>")
        out.append("</item>")
    out.append("</channel></rss>")
    return "".join(out).encode("utf-8")


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

class FakeSMTP:
    instances = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host = host
        self.port = port
        self.context = context
        self.timeout = timeout
        self.logins = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.logins.append((user, password))

    def sendmail(self, from_addr, to_addrs, msg):
        # The real smtplib encodes a str message as ASCII, so do the same
        # here and catch anything that would fail on the wire.
        msg.encode("ascii")
        self.sent.append((from_addr, to_addrs, msg))


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def make_articles(n, outlets=None, summary=None):
    return [
        {
            "title": f"Headline {i}",
            "link": f"https://news.example/story-{i}",
            "summary": summary if summary is not None else f"Snippet for story {i}.",
            "source": outlets[(i - 1) % len(outlets)] if outlets else f"Outlet {i}",
            "published": "2026-09-15T00:00:00+00:00",
        }
        for i in range(1, n + 1)
    ]


SAMPLE_BRIEF = {
    "stories": [
        {"article_ids": [1, 2], "subheading": "Central bank slows its rate cuts",
         "detail": "The bank signalled a slower pace of cuts.\n\nBond yields rose."},
        {"article_ids": [3], "subheading": "Storm heads for the coast",
         "detail": "A storm is expected this weekend."},
    ],
    "overview": "Rate policy set the tone for the day.",
}

SAMPLE_FACT = {
    "fact": "Honeybees communicate distance with a waggle dance.",
    "why": "It was one of the first animal languages decoded.",
}


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """
    Every test starts from the same module state: a fake clock, a known
    model chain with only Gemini configured, no leftover dropped models or
    disabled providers, no running budget, and no network.
    """
    clock = FakeClock()
    monkeypatch.setattr(digest, "time", types.SimpleNamespace(
        monotonic=clock.monotonic, sleep=clock.sleep))
    monkeypatch.setattr(digest, "GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setattr(digest, "GROQ_API_KEY", None)
    monkeypatch.setattr(digest, "GEMINI_MODELS", ["gem-a", "gem-b"])
    monkeypatch.setattr(digest, "GROQ_MODELS", ["groq-a"])
    monkeypatch.setattr(digest, "_deadline", None)
    digest._unusable_models.clear()
    digest._disabled_providers.clear()

    violations = []

    def no_network(url, *args, **kwargs):
        violations.append(url)
        raise AssertionError(f"test tried to reach the network: {url}")

    def parse_offline(source, *args, **kwargs):
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            violations.append(source)
            raise AssertionError(f"test tried to reach the network: {source}")
        return REAL_PARSE(source, *args, **kwargs)

    monkeypatch.setattr(digest.requests, "get", no_network)
    monkeypatch.setattr(digest.requests, "post", no_network)
    monkeypatch.setattr(digest.feedparser, "parse", parse_offline)

    yield clock

    digest._unusable_models.clear()
    digest._disabled_providers.clear()
    assert not violations, f"unmocked network access: {violations}"


@pytest.fixture
def clock(isolated):
    return isolated


@pytest.fixture
def no_jitter(monkeypatch):
    """Pins the backoff jitter to its midpoint, so waits are exact."""
    monkeypatch.setattr(digest, "random", types.SimpleNamespace(random=lambda: 0.5))


@pytest.fixture
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(digest.requests, "post", fake)
    return fake


@pytest.fixture
def with_groq(monkeypatch):
    monkeypatch.setattr(digest, "GROQ_API_KEY", "groq-test-key")


@pytest.fixture
def feeds(monkeypatch):
    fake = FakeFeeds()
    monkeypatch.setattr(digest.requests, "get", fake.get)
    monkeypatch.setattr(digest.feedparser, "parse", fake.parse)
    return fake


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(digest.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def mail_env(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("RECIPIENT_EMAIL", "reader@example.com")


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Writes a topics.json and points the module at it."""
    def _write(obj):
        path = tmp_path / "topics.json"
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(digest, "CONFIG_PATH", str(path))
        return path
    return _write
