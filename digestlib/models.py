"""Calling the language models: the chain of providers and models to try, the
time budget for a run, retries, and what a 429 says about the limit it hit.
"""

import json
import os
import random
import sys
import time
from contextlib import contextmanager

import requests

from digestlib.feeds import MAX_ARTICLES_PER_TOPIC


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Tried in order. A 503 means that model is out of capacity, and moving to
# another model is faster than waiting for it. Flash-Lite, at the end, often
# has capacity when the others don't.
GEMINI_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_MODELS",
        "gemini-flash-latest,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite",
    ).split(",")
    if m.strip()
]

# GEMINI_MODEL moves one model to the front and keeps the rest as fallbacks.
_pinned_model = os.environ.get("GEMINI_MODEL")
if _pinned_model:
    GEMINI_MODELS = [_pinned_model] + [m for m in GEMINI_MODELS if m != _pinned_model]

# A blank override would leave nothing to call.
if not GEMINI_MODELS:
    GEMINI_MODELS = ["gemini-flash-latest"]

# Groq runs outside Google, so it still answers during a Google-wide outage.
# It's only used after every Gemini model fails. Leave GROQ_API_KEY unset to
# skip it.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GROQ_MODELS", "openai/gpt-oss-120b,llama-3.3-70b-versatile"
    ).split(",")
    if m.strip()
]

# Attempts per model before moving on. Three is enough for a brief blip.
ATTEMPTS_PER_MODEL = 3
BACKOFF_BASE = 4
REQUEST_TIMEOUT = 90

# Some models spend output tokens on reasoning before the JSON, so leave
# plenty of room above a normal briefing's length.
MAX_OUTPUT_TOKENS = 8192

# Articles sent to each provider. Groq's free tier limits tokens per minute,
# and 100 articles would use most of a minute's allowance on one topic.
PROVIDER_ARTICLE_CAP = {"gemini": MAX_ARTICLES_PER_TOPIC, "groq": 45}

# Time limit for all model calls in a run, not counting feed fetching. In a
# broad outage it ends the run with headlines before the workflow timeout
# would kill it. Healthy runs have needed five to nine minutes.
TOTAL_BUDGET = 900


def build_model_chain():
    """Every (provider, model) pair to try, in order. Providers without a key
    are left out.
    """
    chain = []
    if GEMINI_API_KEY:
        chain += [("gemini", m) for m in GEMINI_MODELS]
    if GROQ_API_KEY:
        chain += [("groq", m) for m in GROQ_MODELS]
    return chain


def _to_json_schema(node):
    """Convert a schema from Gemini's format to the JSON Schema that
    OpenAI-compatible APIs take. Types become lowercase, propertyOrdering is
    dropped, and strict mode needs every property required with
    additionalProperties set to false.
    """
    if not isinstance(node, dict):
        return node

    out = {}
    for key, value in node.items():
        if key == "propertyOrdering":
            continue
        if key == "type" and isinstance(value, str):
            out["type"] = value.lower()
        elif key == "properties":
            out["properties"] = {k: _to_json_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = _to_json_schema(value)
        else:
            out[key] = value

    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}).keys())
    return out


class _ModelUnusable(Exception):
    """The model name was rejected, so retrying won't help."""


# These last for the whole run, so a bad model name or key isn't retried on
# every topic. A rejected key only disables its own provider.
_unusable_models = set()
_disabled_providers = set()
_deadline = None


def start_budget():
    global _deadline
    _deadline = time.monotonic() + TOTAL_BUDGET


def _budget_left():
    if _deadline is None:
        return float("inf")
    return _deadline - time.monotonic()


@contextmanager
def _outside_budget():
    """Time spent in this block is added back to the model budget."""
    global _deadline
    started = time.monotonic()
    try:
        yield
    finally:
        if _deadline is not None:
            _deadline += time.monotonic() - started


def _sleep_within_budget(seconds):
    """Sleep, but not past the deadline. Returns False once time has run out."""
    left = _budget_left()
    if left <= 0:
        return False
    time.sleep(max(0.0, min(seconds, left)))
    return _budget_left() > 0


def _backoff(attempt):
    """Exponential backoff with jitter, so retries from a busy moment don't all
    arrive together.
    """
    base = BACKOFF_BASE * (2 ** (attempt - 1))
    return base * (0.7 + random.random() * 0.6)


def _retry_after(resp):
    """Seconds from a Retry-After header, capped at 60. None if it's missing or
    unreadable.
    """
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), 60.0))
    except ValueError:
        return None


def _quotas_from_429(resp):
    """The limits a 429 body says were passed, as [{id, value, model}], and the
    retry delay it suggests in seconds (or None).

    Google lists them in error.details, as a QuotaFailure with one violation per
    limit and a RetryInfo with the delay. The list is empty when the body isn't
    JSON or names no limit, which is what a capacity 429 looks like. The
    message text is never read, so nothing from the request can end up in the
    log.
    """
    try:
        details = resp.json()["error"]["details"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return [], None
    if not isinstance(details, list):
        return [], None

    quotas, delay = [], None
    for item in details:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for violation in item.get("violations") or []:
                if isinstance(violation, dict) and violation.get("quotaId"):
                    dimensions = violation.get("quotaDimensions")
                    quotas.append({
                        "id": str(violation["quotaId"])[:120],
                        "value": str(violation.get("quotaValue", "?"))[:20],
                        "model": str(dimensions.get("model", ""))[:60]
                        if isinstance(dimensions, dict) else "",
                    })
        elif kind.endswith("RetryInfo"):
            try:
                delay = float(str(item.get("retryDelay", "")).rstrip("s"))
            except ValueError:
                pass
    return quotas, delay


# Which 429 limits and which served models have been logged this run. Each is
# logged once, so five topics hitting the same limit leave one line.
_logged_once = set()


def _log_429(label, resp):
    """Say which limit a 429 was about. The Actions log used to keep only the
    status code, so there was no telling a per-minute limit from a per-day one
    or from a model that was simply busy.
    """
    quotas, delay = _quotas_from_429(resp)
    key = ("429", label, tuple((q["id"], q["value"]) for q in quotas))
    if key in _logged_once:
        return
    _logged_once.add(key)
    if not quotas:
        print(f"  [info] {label}: 429 with no limit named in the reply", file=sys.stderr)
        return
    for q in quotas:
        served = f", model {q['model']}" if q["model"] else ""
        retry = f", Google suggests retrying in {delay:.0f}s" if delay is not None else ""
        print(f"  [info] {label}: 429 over {q['id']} (limit {q['value']}{served}){retry}",
              file=sys.stderr)


def _log_served_model(label, data):
    """Gemini's reply names the model that really answered. For an alias such as
    gemini-flash-latest that shows where it points today, and so which
    allowance the call used.
    """
    served = data.get("modelVersion")
    if not isinstance(served, str) or not served or served == label.split("/", 1)[1]:
        return
    key = ("served", label, served)
    if key not in _logged_once:
        _logged_once.add(key)
        print(f"  [info] {label} was answered by {served[:60]}")


def _gemini_request(model, prompt, system, schema):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    # No temperature, top_p or top_k. Google advises leaving them at their
    # defaults for Gemini 3 models, and a low temperature can make them repeat
    # until the token limit cuts off the JSON.
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            # High enough that a long briefing isn't cut off mid-JSON.
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
        },
    }
    return url, headers, body


def _gemini_extract(data, topic_name):
    """The JSON text from a Gemini response, or None if the response was blocked
    or has an unexpected shape.
    """
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
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as e:
        print(f"  [warn] unexpected Gemini response shape for {topic_name}: {e}", file=sys.stderr)
        return None


def _groq_request(model, prompt, system, schema):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }
    # No temperature here either. gpt-oss expects its default, and the schema
    # fixes the structure regardless.
    body = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        # Strict mode holds the reply to the schema, like responseSchema does for
        # Gemini.
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "reply",
                "strict": True,
                "schema": _to_json_schema(schema),
            },
        },
    }
    return GROQ_ENDPOINT, headers, body


def _groq_extract(data, topic_name):
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        print(f"  [warn] unexpected Groq response shape for {topic_name}: {e}", file=sys.stderr)
        return None


_PROVIDERS = {
    "gemini": {
        "build": _gemini_request,
        "extract": _gemini_extract,
        "auth_hint": (
            "Check that your API key is active in Google AI Studio "
            "(https://aistudio.google.com/app/apikey) and that the project "
            "isn't restricted or awaiting verification."
        ),
    },
    "groq": {
        "build": _groq_request,
        "extract": _groq_extract,
        "auth_hint": "Check GROQ_API_KEY at https://console.groq.com/keys.",
    },
}


def _call_model(provider, model, prompt, topic_name, system, schema):
    """Send one prompt to one model, retrying errors that may clear up.

    Returns the reply's JSON text, or None if the model didn't answer in time.
    Raises _ModelUnusable when the model name is rejected, so the caller can
    drop it.
    """
    spec = _PROVIDERS[provider]
    url, headers, body = spec["build"](model, prompt, system, schema)
    label = f"{provider}/{model}"

    for attempt in range(1, ATTEMPTS_PER_MODEL + 1):
        if _budget_left() <= 0:
            return None

        try:
            resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            print(f"  [warn] {label}: request failed ({e})", file=sys.stderr)
            if attempt == ATTEMPTS_PER_MODEL:
                return None
            if not _sleep_within_budget(_backoff(attempt)):
                return None
            continue

        code = resp.status_code

        if code == 200:
            # A proxy or captive portal can answer 200 with an HTML page. Count that as no
            # answer so the next model gets a turn.
            try:
                data = resp.json()
            except ValueError:
                print(f"  [warn] {label}: 200 but the body isn't JSON: {resp.text[:200]}", file=sys.stderr)
                return None
            if not isinstance(data, dict):
                print(f"  [warn] {label}: 200 with an unrecognized body: {resp.text[:200]}", file=sys.stderr)
                return None
            if provider == "gemini":
                _log_served_model(label, data)
            return spec["extract"](data, topic_name)

        if code in (400, 404):
            # Unknown or retired model name. Later calls would fail the same way.
            raise _ModelUnusable(f"{code}: {resp.text[:200]}")

        if code in (401, 403):
            # Bad credentials. Skip this provider for the rest of the run.
            print(
                f"  [error] {label}: {code}, credentials rejected. "
                f"{spec['auth_hint']} Skipping {provider} for the rest of "
                "this run.",
                file=sys.stderr,
            )
            _disabled_providers.add(provider)
            return None

        if code in (429, 500, 502, 503, 504):
            if code == 429:
                _log_429(label, resp)
            if attempt == ATTEMPTS_PER_MODEL:
                print(f"  [warn] {label}: {code} on the last attempt, moving on", file=sys.stderr)
                return None
            wait = _retry_after(resp) or _backoff(attempt)
            print(
                f"  [warn] {label}: {code}, retrying in {wait:.0f}s "
                f"({attempt}/{ATTEMPTS_PER_MODEL})",
                file=sys.stderr,
            )
            if not _sleep_within_budget(wait):
                return None
            continue

        print(f"  [warn] {label}: unexpected {code}: {resp.text[:200]}", file=sys.stderr)
        return None

    return None


def run_chain(build_prompt, system, schema, label):
    """Try each model in the chain until one replies with valid JSON.

    `build_prompt` receives the article cap for the provider being tried, so a
    provider with a tighter limit gets a shorter list. Returns the decoded
    JSON, or None.
    """
    chain = build_model_chain()
    if not chain:
        print("  [error] no provider configured", file=sys.stderr)
        return None

    for provider, model in chain:
        if provider in _disabled_providers or (provider, model) in _unusable_models:
            continue
        if _budget_left() <= 0:
            print(f"  [error] out of time budget before '{label}'", file=sys.stderr)
            break
        try:
            prompt = build_prompt(PROVIDER_ARTICLE_CAP.get(provider, MAX_ARTICLES_PER_TOPIC))
            asked_at = time.monotonic()
            text = _call_model(provider, model, prompt, label, system, schema)
        except _ModelUnusable as e:
            print(f"  [warn] dropping {provider}/{model} for this run ({e})", file=sys.stderr)
            _unusable_models.add((provider, model))
            continue
        took = time.monotonic() - asked_at
        if not text:
            print(
                f"  [warn] {provider}/{model} didn't answer after {took:.0f}s, trying the next model",
                file=sys.stderr,
            )
            continue

        # Parse inside the loop so a reply cut off mid-JSON falls through to the next
        # model. The model stays in the chain, since a cut-off is usually a one-off.
        ok, value = _decode_reply(text)
        if not ok:
            print(
                f"  [warn] could not parse {provider}/{model}'s JSON for {label} ({value}), "
                "trying the next model",
                file=sys.stderr,
            )
            print(f"  raw text: {text[:500]}", file=sys.stderr)
            continue

        print(f"  {provider}/{model} answered in {took:.0f}s")
        if (provider, model) != chain[0]:
            print(f"  [info] answered by fallback model {provider}/{model}")
        return value

    print(f"  [error] no model answered for '{label}'", file=sys.stderr)
    return None


def _decode_reply(text):
    """(True, value) if the reply parses as JSON, else (False, error). Handles a
    markdown code fence around the JSON.
    """
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        return True, json.loads(text)
    except json.JSONDecodeError as e:
        return False, e
