# How it works

Background on the decisions behind the digest. None of this is needed to run
it; [the README](README.md) covers setup. This is here for when you want to
change something and would rather know why it is the way it is.

- [How articles are chosen](#how-articles-are-chosen)
- [How the writing is kept readable](#how-the-writing-is-kept-readable)
- [The fact of the day](#the-fact-of-the-day)
- [What happens when a model is down](#what-happens-when-a-model-is-down)
- [How long the email can get](#how-long-the-email-can-get)
- [Collapsible stories](#collapsible-stories)
- [Swapping or adding an LLM](#swapping-or-adding-an-llm)

---

## How articles are chosen

A busy topic finds far more articles than fit in one prompt. International
News pulls over 400 in a normal 24 hours against a cap of 100, so what gets
dropped matters more than the cap does.

Articles are taken **one from each feed in turn**, freshest first within a
feed, rather than sorting everything by time and cutting at the cap. Sorting
by time loses whole outlets: a wire publishing every few minutes fills the
list, and a story the rest of the world led with falls off the end. Measured
on one day's articles:

| | Outlets represented | Spread |
|---|---|---|
| By time, cap 40 | 11 | 9 from one outlet, 1 from the BBC |
| Round robin, cap 100 | 17 | 5-6 from every outlet |

A major story is precisely the one several outlets all cover, so spreading
the list across outlets is what protects against missing one. The prompt says
this explicitly, so repeated coverage reads as a significance signal rather
than duplication to collapse.

Two consequences worth knowing:

- **Adding feeds is cheap.** A new feed takes a share of the slots instead of
  a high-volume one taking over.
- **Feeds are fetched in parallel.** Sequentially, 75 feeds against the
  per-host timeout put the worst case at 25 minutes, which is the whole job
  budget spent before anything is summarized. The dedup still runs in a fixed
  order, so the same inputs give the same digest.

---

## How the writing is kept readable

The prose targets ordinary general-audience readability: sentences averaging
15-20 words and never past 25, active voice, everyday vocabulary, at most one
subordinate clause per sentence, and the point at the start of each paragraph
rather than the end.

Those are written into the system instruction as numbered limits rather than
adjectives. "Keep it readable" and "average about 15-20 words" are not
equally followable, and only one of them can be checked afterwards.

The instruction ends with a worked before-and-after contrasting one dense
36-word sentence against the same content as three short ones. Density is the
specific failure mode here, and it is the one plain instructions are worst at
preventing on their own.

Both halves of that example are labelled with their own word counts, and both
labels are true: the dense version really is 36 words, and the rewrite really
averages 16, inside the 15-20 band the rules ask for. An example that misses
the target it illustrates teaches the miss, so the counts are checked rather
than asserted.

Each run prints the average sentence length it actually got, per topic. If
that starts reporting above 22 words, the instruction has stopped landing and
you will see it in the Actions log rather than having to notice it by reading.

### Structure

Each topic renders as an overview of the day, then a subheading per
development with its detail and its own sources beneath it. Articles covering
the same event are fused into one entry.

`max_stories` caps how many developments get their own subheading. It is a
ceiling, not a target: same-event articles still collapse into one entry, so
a quiet day produces fewer entries rather than padded ones.

---

## The fact of the day

Above the news, each email opens with one fact and a short note on why it is
worth knowing.

A model left to choose freely returns to the same handful of chestnuts, so
the request is narrowed on two axes at once. The date picks one of fourteen
fields, and separately one of eleven angles of approach: a hard limit and
what sets it, a historical accident that still shapes something, two things
that share a cause, and so on. Eleven and fourteen share no factors, so a
given field-and-angle pair does not come back for 154 days rather than
repeating every fortnight.

The angle is the part doing the work. A field on its own is a broad ask, and
asked the same broad way it returns that field's most famous fact; pairing it
with an angle usually makes the obvious answer not fit. The prompt also names
the well-worn circuit to avoid outright, and lets the model drop the angle
rather than force a bad match to it.

This is the only part of the email not grounded in a fetched article. It
carries no sources because it comes from the model's own knowledge rather
than today's feeds. Treat it as a prompt to go and read about something, not
as a citation.

It runs after the topics, so on a bad day for the API you lose the fact
rather than a topic, and a failure just leaves the block out. Set
`fact_of_the_day` to `false` in `settings` to turn it off.

---

## What happens when a model is down

A 503 from Gemini means that model's shared serving pool is out of capacity.
Free and paid traffic hit the same pool, so it is not something a billing or
quota change fixes, and asking the same model again usually returns the same
503.

So the script does not wait longer, it asks something else. It walks a chain:

```
gemini-flash-latest
gemini-3.6-flash
gemini-3.5-flash
gemini-3.5-flash-lite
groq/openai/gpt-oss-120b          only if GROQ_API_KEY is set
groq/llama-3.3-70b-versatile
```

Three jittered exponential attempts per model, then down to the next. Every
Gemini model runs on Google's infrastructure, so Groq is there as the
non-Google backstop for the case where the problem is Google-wide rather than
model-specific. It is only reached once every Gemini model has failed, which
keeps the digest's voice consistent on normal days. On a day it does get
used, expect the writing to read noticeably different.

Other behaviour worth knowing:

- **401/403** is credentials, not capacity, so that provider is dropped for
  the rest of the run instead of retried. A dead Gemini key degrades to Groq.
- **404** means the model name is wrong for your key. It is dropped after one
  try, so an outdated entry in the chain costs a fraction of a second.
- **`Retry-After`** is honoured when sent, capped so one response cannot
  swallow the run.
- **A total outage** still sends the email. Each topic falls back to a plain
  list of headlines under a "Top headlines" label, built from the articles
  already fetched, rather than dropping out.
- **One shared 8-minute budget** covers all summarization. A broad outage
  means a short run that sends headlines, not a run that hits the 25-minute
  workflow timeout and sends nothing.

Groq is sent a shorter article list than Gemini because its free tier meters
tokens per minute rather than per request, and a hundred articles would spend
most of a minute's allowance on one topic.

### Why these models

The Gemini free tier is Flash-only; Pro moved behind billing in 2026, so
there is no free upgrade above what the chain already uses. For condensing
short article snippets into cited paragraphs this is the right class of model
anyway. It is a read-and-compress job, not a reasoning one, and a larger model
buys better prose rather than a better digest.

No request sets `temperature`, `top_p` or `top_k`. Every model in the chain
is a Gemini 3.x, and Google's guidance for that generation is to drop the
sampling parameters entirely and steer with the system instruction instead;
these models are tuned around their defaults, and a low temperature is the
documented cause of looping and degraded output. Looping also happens to be
how they fail structured output, by repeating until the token limit cuts the
JSON off mid-string. Groq's gpt-oss wants its default of 1.0 for the same
reason, so leaving the parameter off suits both providers.

That puts the whole job of producing a different fact each day on the prompt
rather than on the sampler, which is why the fact rotates on two axes rather
than one.

The schema is kept deliberately shallow for the same reason the chain exists:
Flash-class models get unreliable on deeply nested schemas, and Google's docs
warn that large or deeply nested schemas may be rejected outright.
`article_ids` is a flat list of integers rather than a list of one-field
objects, so the output carries more structure than the old shape while
nesting one level less. `maxOutputTokens` is set explicitly because these
models fail structured output by truncating mid-JSON rather than erroring.

---

## How long the email can get

Gmail renders about 102KB of HTML and hides the rest behind a "View entire
message" link. That link still shows everything, but it takes a click. Each
run prints the size it used.

| Config | Stories | Size | |
|---|---|---|---|
| stock, as shipped | 39 | ~75KB | 73% of the budget |
| 5 topics x 6 | 30 | ~60KB | fine |
| 8 topics x 6 | 48 | ~94KB | warns in the log |
| 10 topics x 8 | 80 | ~153KB | Gmail clips it |

Past the limit, drop `max_stories`, or split the topics across two runs by
adding a second workflow with its own `topics.json`.

Styling is inline rather than in a `<style>` block because Gmail strips those
for non-Gmail recipients, and the layout is table-based because Outlook
renders mail with the Word engine. The email is deliberately light-only:
Gmail's mobile apps invert colours in dark mode regardless of any CSS, so the
palette avoids pure black and white, which invert worst.

---

## Collapsible stories

Gmail has no way to collapse part of an ordinary HTML email. It rewrites
`<details>` and `<summary>` into plain tags, it doesn't support the `:checked`
selector that CSS-only accordions depend on, and in-email jump links do
nothing in its mobile apps. The one format Gmail will collapse is AMP for
Email, so each digest carries two copies:

| Copy | Shown by | Stories |
|---|---|---|
| AMP (`text/x-amp-html`) | Gmail web and apps, for 30 days | One line each, tap to open |
| HTML (`text/html`) | Every other client, and Gmail after 30 days | Fully expanded |

Both are built from the same briefings, and a test checks that every
subheading, paragraph, source and link in the full copy also appears in the
collapsible one. The topic overviews and the fact of the day stay open in
both, since they're the quick read.

Gmail only renders the AMP copy when the sender and recipient are different
addresses and the recipient has approved the sender, which is why the setup
needs a second sending address. When the two addresses match, the AMP copy is
left out and the log says so. It's also left out if it passes AMP's 200KB
document limit, rather than being sent in a form Gmail would ignore.

Gmail quietly falls back to the full version if the AMP copy breaks any of
AMP's rules, so an invalid copy looks like the feature simply not working.
The markup passes the official validator, and it's worth rerunning after any
change to `build_amp()`: save a generated copy to a file and run
`npx amphtml-validator --html_format AMP4EMAIL <file>`. AMP also rejects inline
`style` attributes in favour of one stylesheet, so this copy uses classes
where the HTML copy uses inline styles, with the same palette.

---

## Swapping or adding an LLM

`summarize_topic()` in `digest.py` walks the chain from `build_model_chain()`
and returns `None` or:

```python
{
  "overview": "2-3 sentences on the topic as a whole",
  "stories": [
    {"subheading": "...", "detail": "...",
     "sources": [{"title": "...", "link": "...", "outlet": "..."}]}
  ],
}
```

To add a provider, write a `_yourprovider_request(model, prompt, system,
schema)` returning `(url, headers, body)` and a `_yourprovider_extract(data,
label)` returning the model's raw JSON text, register both in `_PROVIDERS`,
and add its models in `build_model_chain()`.

Retries, backoff, the shared time budget and the citation resolution are all
provider-agnostic, so there is nothing else to touch. Anything speaking the
OpenAI chat-completions format can copy the Groq pair almost verbatim.
`BRIEF_SCHEMA` is written in Gemini's schema dialect and converted to plain
JSON Schema by `_to_json_schema()`, so only one definition needs to stay
correct.

### Why citations can't be faked

The model never sees a link. It gets numbered articles with a title, outlet
and snippet, and cites by integer id. The title, link and outlet in the email
are looked up from the fetched articles by that id, so nothing the model
writes ends up in a URL. An id it invents resolves to nothing and is dropped
with a warning in the log.

The links themselves come from the feeds, so they get checked too. Only
absolute `http` and `https` links become clickable; a `javascript:` or
`data:` link, or a relative one that would lead nowhere from an inbox, is
dropped and the headline is listed without a link. Feed text is also fenced
off in the prompt so a headline can't pass itself off as an instruction.
