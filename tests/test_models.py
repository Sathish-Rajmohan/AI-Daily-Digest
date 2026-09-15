"""The provider chain: configuration, request shapes, retries, fallback, budget."""

import json
import os
import subprocess
import sys

import pytest

import digest
from conftest import ROOT, FakeResponse, gemini_reply, groq_reply, status

OK = {"stories": [], "overview": "fine"}


def call(provider="gemini", model="gem-a"):
    return digest._call_model(provider, model, "prompt", "Topic", "system", digest.BRIEF_SCHEMA)


def chain(build_prompt=lambda cap: "prompt", label="Topic"):
    return digest.run_chain(build_prompt, "system", digest.BRIEF_SCHEMA, label)


# --------------------------------------------------------------------------
# Model lists from the environment (read once, at import)
# --------------------------------------------------------------------------

def models_imported_with(**env_overrides):
    env = {k: v for k, v in os.environ.items()
           if k not in ("GEMINI_MODELS", "GEMINI_MODEL", "GROQ_MODELS")}
    env.update(env_overrides)
    code = "import json, digest; print(json.dumps([digest.GEMINI_MODELS, digest.GROQ_MODELS]))"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_default_model_lists():
    gemini, groq = models_imported_with()
    assert gemini[0] == "gemini-flash-latest"
    assert len(gemini) == len(set(gemini)) >= 2
    assert groq and all(m.strip() == m and m for m in groq)


def test_model_list_override_is_trimmed_and_blanks_dropped():
    gemini, _ = models_imported_with(GEMINI_MODELS=" a , ,b ")
    assert gemini == ["a", "b"]


@pytest.mark.parametrize("pinned, expected", [
    ("b", ["b", "a", "c"]),
    ("z", ["z", "a", "b", "c"]),
])
def test_pinned_model_goes_first_and_the_rest_stay_as_fallbacks(pinned, expected):
    gemini, _ = models_imported_with(GEMINI_MODELS="a,b,c", GEMINI_MODEL=pinned)
    assert gemini == expected


def test_blank_gemini_override_falls_back_to_a_default():
    gemini, _ = models_imported_with(GEMINI_MODELS=" , ")
    assert gemini == ["gemini-flash-latest"]


def test_blank_groq_override_leaves_groq_with_no_models():
    _, groq = models_imported_with(GROQ_MODELS="")
    assert groq == []


# --------------------------------------------------------------------------
# build_model_chain
# --------------------------------------------------------------------------

def test_chain_with_only_gemini():
    assert digest.build_model_chain() == [("gemini", "gem-a"), ("gemini", "gem-b")]


def test_chain_puts_groq_after_every_gemini_model(with_groq):
    assert digest.build_model_chain() == [("gemini", "gem-a"), ("gemini", "gem-b"), ("groq", "groq-a")]


def test_chain_with_only_groq(with_groq, monkeypatch):
    monkeypatch.setattr(digest, "GEMINI_API_KEY", None)
    assert digest.build_model_chain() == [("groq", "groq-a")]


def test_chain_with_no_keys_is_empty(monkeypatch):
    monkeypatch.setattr(digest, "GEMINI_API_KEY", "")
    assert digest.build_model_chain() == []


# --------------------------------------------------------------------------
# _to_json_schema
# --------------------------------------------------------------------------

def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


@pytest.mark.parametrize("schema", [digest.BRIEF_SCHEMA, digest.FACT_SCHEMA])
def test_json_schema_conversion_meets_strict_mode(schema):
    converted = digest._to_json_schema(schema)
    for node in walk(converted):
        assert "propertyOrdering" not in node
        if "type" in node and isinstance(node["type"], str):
            assert node["type"] == node["type"].lower()
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


def test_json_schema_conversion_keeps_descriptions_and_does_not_mutate_input():
    before = json.dumps(digest.BRIEF_SCHEMA, sort_keys=True)
    converted = digest._to_json_schema(digest.BRIEF_SCHEMA)
    assert json.dumps(digest.BRIEF_SCHEMA, sort_keys=True) == before
    assert converted["properties"]["overview"]["description"] == \
        digest.BRIEF_SCHEMA["properties"]["overview"]["description"]


def test_json_schema_conversion_passes_scalars_through():
    assert digest._to_json_schema("x") == "x"
    assert digest._to_json_schema(3) == 3


# --------------------------------------------------------------------------
# Request builders
# --------------------------------------------------------------------------

SAMPLING_KEYS = {"temperature", "top_p", "top_k", "topP", "topK"}


def test_gemini_request_shape():
    url, headers, body = digest._gemini_request("gem-a", "the prompt", "the system", digest.FACT_SCHEMA)
    assert url.endswith("/models/gem-a:generateContent")
    assert headers["x-goog-api-key"] == "gemini-test-key"
    assert body["systemInstruction"]["parts"][0]["text"] == "the system"
    assert body["contents"][0]["parts"][0]["text"] == "the prompt"
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] is digest.FACT_SCHEMA
    assert config["maxOutputTokens"] == digest.MAX_OUTPUT_TOKENS
    assert not SAMPLING_KEYS & set(config) and not SAMPLING_KEYS & set(body)


def test_groq_request_shape(with_groq):
    url, headers, body = digest._groq_request("groq-a", "the prompt", "the system", digest.FACT_SCHEMA)
    assert url == digest.GROQ_ENDPOINT
    assert headers["Authorization"] == "Bearer groq-test-key"
    assert body["model"] == "groq-a"
    assert body["messages"] == [{"role": "system", "content": "the system"},
                                {"role": "user", "content": "the prompt"}]
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == digest._to_json_schema(digest.FACT_SCHEMA)
    assert body["max_tokens"] == digest.MAX_OUTPUT_TOKENS
    assert not SAMPLING_KEYS & set(body)


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------

def test_gemini_extract_joins_parts_and_tolerates_textless_ones():
    data = {"candidates": [{"content": {"parts": [{"text": '{"a":'}, {"thoughtSignature": "x"},
                                                  {"text": " 1}  "}]}}]}
    assert digest._gemini_extract(data, "T") == '{"a": 1}'


def test_gemini_extract_with_no_candidates_reports_feedback(capsys):
    assert digest._gemini_extract({"promptFeedback": {"blockReason": "SAFETY"}}, "T") is None
    assert "SAFETY" in capsys.readouterr().err


def test_gemini_extract_with_a_contentless_candidate():
    assert digest._gemini_extract({"candidates": [{"finishReason": "SAFETY"}]}, "T") is None


def test_groq_extract():
    assert digest._groq_extract({"choices": [{"message": {"content": " {} "}}]}, "T") == "{}"
    assert digest._groq_extract({"choices": [{"message": {"content": None}}]}, "T") == ""
    assert digest._groq_extract({"choices": []}, "T") is None


# --------------------------------------------------------------------------
# Backoff, Retry-After, budget
# --------------------------------------------------------------------------

@pytest.mark.parametrize("attempt, base", [(1, 4), (2, 8), (3, 16)])
def test_backoff_doubles_with_bounded_jitter(attempt, base):
    waits = [digest._backoff(attempt) for _ in range(300)]
    assert all(base * 0.7 <= w <= base * 1.3 for w in waits)
    assert len({round(w, 6) for w in waits}) > 1


@pytest.mark.parametrize("header, expected", [
    ("7", 7.0), (" 3.5 ", 3.5), ("120", 60.0), ("-5", 0.0), ("", None),
    ("Wed, 21 Oct 2026 07:28:00 GMT", None),
])
def test_retry_after(header, expected):
    response = FakeResponse(429, {}, headers={"Retry-After": header} if header else {})
    assert digest._retry_after(response) == expected


def test_retry_after_nan_stays_in_range():
    got = digest._retry_after(FakeResponse(429, {}, headers={"Retry-After": "nan"}))
    assert got is None or 0 <= got <= 60


def test_budget_is_unlimited_until_started():
    assert digest._budget_left() == float("inf")


def test_budget_counts_down(clock):
    digest.start_budget()
    assert digest._budget_left() == digest.TOTAL_BUDGET
    clock.now += 100
    assert digest._budget_left() == digest.TOTAL_BUDGET - 100


def test_sleep_within_budget(clock):
    digest.start_budget()
    assert digest._sleep_within_budget(5) is True
    clock.now += digest.TOTAL_BUDGET - 5 - 3
    assert digest._sleep_within_budget(10) is False
    assert clock.sleeps == [5, 3]
    assert digest._sleep_within_budget(1) is False
    assert clock.sleeps == [5, 3]


# --------------------------------------------------------------------------
# _call_model
# --------------------------------------------------------------------------

def test_success_returns_text(transport):
    transport.script("gem-a", gemini_reply(OK))
    assert json.loads(call()) == OK
    assert transport.calls[0]["timeout"] == digest.REQUEST_TIMEOUT


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_retryable_status_then_success(transport, clock, no_jitter, code):
    transport.script("gem-a", status(code), status(code), gemini_reply(OK))
    assert json.loads(call()) == OK
    assert len(transport.calls) == 3
    assert clock.sleeps == [4.0, 8.0]


def test_no_sleep_after_the_final_attempt(transport, clock, no_jitter, capsys):
    transport.script("gem-a", status(503))
    assert call() is None
    assert len(transport.calls) == digest.ATTEMPTS_PER_MODEL
    assert clock.sleeps == [4.0, 8.0]
    assert "last attempt" in capsys.readouterr().err


def test_retry_after_header_is_honoured(transport, clock, no_jitter):
    transport.script("gem-a", status(429, {"Retry-After": "9"}), gemini_reply(OK))
    assert call() is not None
    assert clock.sleeps == [9.0]


@pytest.mark.parametrize("code", [400, 404])
def test_bad_model_name_raises_after_one_request(transport, code):
    transport.script("gem-a", status(code))
    with pytest.raises(digest._ModelUnusable, match=str(code)):
        call()
    assert len(transport.calls) == 1


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_credentials_disable_the_provider(transport, capsys, code):
    transport.script("gem-a", status(code))
    assert call() is None
    assert len(transport.calls) == 1
    assert "gemini" in digest._disabled_providers
    assert "aistudio.google.com" in capsys.readouterr().err


def test_unexpected_status_is_not_retried(transport):
    transport.script("gem-a", status(418))
    assert call() is None
    assert len(transport.calls) == 1


def test_connection_error_is_retried(transport, clock, no_jitter):
    transport.script("gem-a", digest.requests.exceptions.ConnectionError("reset"), gemini_reply(OK))
    assert call() is not None
    assert clock.sleeps == [4.0]


def test_repeated_timeouts_give_up(transport, clock, no_jitter):
    transport.script("gem-a", digest.requests.exceptions.Timeout("slow"))
    assert call() is None
    assert len(transport.calls) == 3
    assert clock.sleeps == [4.0, 8.0]


def test_no_request_once_the_budget_is_spent(transport, clock):
    transport.script("gem-a", gemini_reply(OK))
    digest.start_budget()
    clock.now += digest.TOTAL_BUDGET
    assert call() is None
    assert transport.calls == []


def test_budget_running_out_mid_backoff_stops_retrying(transport, clock, no_jitter):
    transport.script("gem-a", status(503))
    digest.start_budget()
    clock.now += digest.TOTAL_BUDGET - 5
    assert call() is None
    assert len(transport.calls) == 2
    assert clock.sleeps == [4.0, 1.0]


# A 200 whose body isn't the provider's JSON, like a proxy's error page,
# counts as no answer.
@pytest.mark.parametrize("response", [
    FakeResponse(200, None, text="<html>Service temporarily unavailable</html>"),
    FakeResponse(200, []),
    FakeResponse(200, "a string"),
])
def test_malformed_success_body_is_no_answer_rather_than_a_crash(transport, response, capsys):
    transport.script("gem-a", response)
    assert call() is None
    assert capsys.readouterr().err


def test_groq_calls_route_through_the_same_retry_logic(transport, with_groq, clock, no_jitter):
    transport.script("groq-a", status(503), groq_reply(OK))
    assert json.loads(call("groq", "groq-a")) == OK
    assert clock.sleeps == [4.0]


# --------------------------------------------------------------------------
# run_chain
# --------------------------------------------------------------------------

def test_head_model_answers(transport, capsys):
    transport.script("gem-a", gemini_reply(OK))
    assert chain() == OK
    assert transport.tried == ["gemini/gem-a"]
    assert "fallback" not in capsys.readouterr().out


def test_falls_back_to_the_next_model(transport, capsys):
    transport.script("gem-a", status(503))
    transport.script("gem-b", gemini_reply(OK))
    assert chain() == OK
    assert transport.tried == ["gemini/gem-a"] * 3 + ["gemini/gem-b"]
    assert "answered by fallback model gemini/gem-b" in capsys.readouterr().out


def test_a_bad_model_name_is_skipped_for_the_rest_of_the_run(transport):
    transport.script("gem-a", status(404))
    transport.script("gem-b", gemini_reply(OK))
    assert chain() == OK
    assert chain() == OK
    assert transport.tried == ["gemini/gem-a", "gemini/gem-b", "gemini/gem-b"]


def test_dead_gemini_key_degrades_to_groq_for_the_rest_of_the_run(transport, with_groq):
    transport.script("gem-a", status(401))
    transport.script("groq-a", groq_reply(OK))
    assert chain() == OK
    assert chain() == OK
    assert transport.tried == ["gemini/gem-a", "groq/groq-a", "groq/groq-a"]


def test_every_model_failing_returns_none(transport, capsys):
    transport.script("gem-a", status(503))
    transport.script("gem-b", status(503))
    assert chain(label="World") is None
    assert "no model answered for 'World'" in capsys.readouterr().err


def test_no_provider_configured(transport, monkeypatch, capsys):
    monkeypatch.setattr(digest, "GEMINI_API_KEY", None)
    assert chain() is None
    assert transport.calls == []
    assert "no provider configured" in capsys.readouterr().err


def test_out_of_budget_before_starting(transport, clock, capsys):
    transport.script("gem-a", gemini_reply(OK))
    digest.start_budget()
    clock.now += digest.TOTAL_BUDGET + 1
    assert chain(label="Science") is None
    assert transport.calls == []
    assert "out of time budget before 'Science'" in capsys.readouterr().err


@pytest.mark.parametrize("text", [
    '```json\n{"a": 1}\n```',
    '```JSON\n{"a": 1}```',
    '```\n{"a": 1}\n```',
    '   {"a": 1}   ',
])
def test_code_fences_around_the_json_are_tolerated(transport, text):
    transport.script("gem-a", gemini_reply(text))
    assert chain() == {"a": 1}


def test_empty_reply_moves_on_to_the_next_model(transport):
    transport.script("gem-a", gemini_reply(""))
    transport.script("gem-b", gemini_reply(OK))
    assert chain() == OK
    assert transport.tried == ["gemini/gem-a", "gemini/gem-b"]


# A reply cut off mid-JSON should fall through to the next model.
def test_unparseable_json_moves_on_to_the_next_model(transport, capsys):
    transport.script("gem-a", gemini_reply('{"stories": [{"subheading": "Cut off mid'))
    transport.script("gem-b", gemini_reply(OK))
    assert chain() == OK
    assert transport.tried == ["gemini/gem-a", "gemini/gem-b"]
    assert "could not parse" in capsys.readouterr().err


def test_unparseable_json_does_not_retire_the_model(transport):
    transport.script("gem-a", gemini_reply("{oops"), gemini_reply(OK))
    transport.script("gem-b", gemini_reply(OK))
    chain()
    chain()
    assert transport.tried == ["gemini/gem-a", "gemini/gem-b", "gemini/gem-a"]


def test_unparseable_json_everywhere_returns_none(transport):
    transport.script("gem-a", gemini_reply("{oops"))
    transport.script("gem-b", gemini_reply("[1, 2"))
    assert chain() is None


def test_each_provider_is_given_its_own_article_cap(transport, with_groq):
    transport.script("gem-a", status(503))
    transport.script("gem-b", status(503))
    transport.script("groq-a", groq_reply(OK))
    caps = []
    chain(build_prompt=lambda cap: caps.append(cap) or "prompt")
    assert caps == [digest.PROVIDER_ARTICLE_CAP["gemini"]] * 2 + [digest.PROVIDER_ARTICLE_CAP["groq"]]
