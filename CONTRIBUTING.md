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
python -m pytest --cov=digest --cov=digestlib --cov-branch --cov-report=term-missing
```

## Where the code is

`digest.py` is the script the workflows run. It holds `main()`, the weekly
edition's `weekly_main()` and the command line. The rest is in `digestlib/`,
one module per job:

| Module | What it does |
|---|---|
| `config.py` | Reads `topics.json` and checks it for mistakes |
| `schedule.py` | Works out when a run was due and when to send, and holds until then |
| `feeds.py` | Fetches the feeds and picks the articles for each topic |
| `models.py` | Calls the models: the chain, retries, the time budget and 429 lines |
| `briefing.py` | The prompts and schemas for the briefing and the fact of the day |
| `render.py` | The HTML email for both editions, and the Gmail size check |
| `amp.py` | The AMP copy that folds in Gmail |
| `channels/email.py` | Sends the email through Gmail |
| `saved.py` | Saves each day's digest and reads the saved ones back |
| `weekly.py` | The weekly edition's days, stories, prompt and schema |

The tests import `digest`, and a name patched on `digest` also changes in
every `digestlib` module that has it. A new test can patch the module
directly instead.

## Checking the schedule

`python run_history.py` reads GitHub's public run history and prints how late
each scheduled run started, how long it took and when it finished, with a
summary. It needs no token. After changing the schedule, run it with
`--cron`, `--tz` and `--from` for the new entry and compare the summary with the
one before. [When the email
arrives](HOW-IT-WORKS.md#when-the-email-arrives) explains why the start is also
shown in UTC.

## Trying the weekly edition

A local run of `digest.py` saves its digest in `saved-digests/`. To build the
weekly edition from those files:

```bash
python digest.py --weekly --from saved-digests --any-day
```

`--any-day` sends it on any day, not only the `day` in the `weekly` block.
It needs the same keys and Gmail settings as a daily run. To try a workflow
run's files, download its `daily-digest` artifacts from the **Actions** tab
and unzip each one into its own folder under the `--from` folder.

## Before changing the email

Some mistakes in the email code don't raise an error. The email still sends,
but part of it stops working.

- Gmail falls back to the regular email if the AMP version breaks any AMP rule.
  After changing `build_amp()` in `digestlib/amp.py`, save a generated copy to a file and run
  `npx amphtml-validator --html_format AMP4EMAIL <file>`.
- Font names in `_FONT_STACK` and `_SERIF_STACK` (in `digestlib/render.py`)
  need single quotes. The stacks
  go inside double-quoted `style` attributes, where a double quote ends the
  attribute and the font is dropped for the whole email.
  `test_font_stacks_survive_inside_style_attributes` catches this.
- `_COLLAPSE_CSS` can't contain CSS comments, because Yahoo ignores the rule
  after a comment. Its hiding rule has to depend on `input:checked`, so apps
  without `:checked` support leave stories open.
- Tests check the rule numbers, word limits and worked examples in the prompts.
  If you edit a prompt, update any numbers it quotes.
- The weekly edition uses the same renderers, with `edition=WEEKLY`. Check a
  change in both editions. `tests/test_weekly.py` covers the weekly one.

## Adding a model provider

`run_chain()` in `digestlib/models.py` tries each `(provider, model)` pair from
`build_model_chain()` in order. A provider needs two functions:

- `_yourprovider_request(model, prompt, system, schema)` returns
  `(url, headers, body)`.
- `_yourprovider_extract(data, label)` returns the model's reply as a JSON
  string, or `None`.

Register both in `_PROVIDERS` and add the provider's models to
`build_model_chain()`. Retries, the time budget and citation lookup already
work for any provider. One with an OpenAI-style chat completions API can mostly
copy the Groq functions. `BRIEF_SCHEMA`, in `digestlib/briefing.py`, is
written in Gemini's schema format,
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
