# Contributing

Issues and pull requests are welcome. New feed presets, other model providers
and other ways to deliver the digest would all fit.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests take a few seconds and don't need API keys or a network connection.
Web requests, email sending and the clock are replaced with fakes. A few tests
start `digest.py` as its own process, the way the workflow does, to check what
happens when secrets are missing.

To see which lines the tests reach:

```bash
pip install pytest-cov
python -m pytest --cov=digest --cov-branch --cov-report=term-missing
```

## Before changing the email

Some mistakes in the email code don't raise an error. The email still sends,
but part of it stops working.

- Gmail falls back to the regular email if the AMP version breaks any AMP rule.
  After changing `build_amp()`, save a generated copy to a file and run
  `npx amphtml-validator --html_format AMP4EMAIL <file>`.
- Font names in `_FONT_STACK` and `_SERIF_STACK` need single quotes. The stacks
  go inside double-quoted `style` attributes, where a double quote ends the
  attribute and the font is dropped for the whole email.
  `test_font_stacks_survive_inside_style_attributes` catches this.
- `_COLLAPSE_CSS` can't contain CSS comments, because Yahoo ignores the rule
  after a comment. Its hiding rule has to depend on `input:checked`, so apps
  without `:checked` support leave stories open.
- Tests check the rule numbers, word limits and worked examples in the prompts.
  If you edit a prompt, update any numbers it quotes.

## Adding a model provider

`run_chain()` in `digest.py` tries each `(provider, model)` pair from
`build_model_chain()` in order. A provider needs two functions:

- `_yourprovider_request(model, prompt, system, schema)` returns
  `(url, headers, body)`.
- `_yourprovider_extract(data, label)` returns the model's reply as a JSON
  string, or `None`.

Register both in `_PROVIDERS` and add the provider's models to
`build_model_chain()`. Retries, the time budget and citation lookup already
work for any provider. One with an OpenAI-style chat completions API can mostly
copy the Groq functions. `BRIEF_SCHEMA` is written in Gemini's schema format,
and `_to_json_schema()` converts it for providers that take standard JSON
Schema.

`summarize_topic()` returns `None`, or a briefing like this:

```python
{
    "overview": "2-3 sentences on the topic as a whole",
    "stories": [
        {"subheading": "...", "detail": "...",
         "sources": [{"title": "...", "link": "...", "outlet": "..."}]},
    ],
}
```

## Feed presets

Before adding a feed to [FEEDS.md](FEEDS.md), make sure it returns entries and
has posted in the last week. The snippet under [checking them
yourself](FEEDS.md#checking-them-yourself) covers the first part.
