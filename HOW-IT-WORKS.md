# How it works

Background on why the digest works the way it does. None of it is needed for
setup, which the [README](README.md) covers.

- [How articles are chosen](#how-articles-are-chosen)
- [How the writing is kept readable](#how-the-writing-is-kept-readable)
- [The fact of the day](#the-fact-of-the-day)
- [When a model is down](#when-a-model-is-down)
- [How the email looks](#how-the-email-looks)
- [Email length and Gmail clipping](#email-length-and-gmail-clipping)
- [Collapsible stories](#collapsible-stories)
- [Why citations can't be faked](#why-citations-cant-be-faked)

## How articles are chosen

A busy topic finds far more articles than one prompt can hold. On a normal day
International News collects over 400, and the limit is 100.

The script takes one article from each feed in turn, newest first within each
feed, until it reaches the limit. The first version sorted everything by time
and cut at the limit. That lost whole outlets, because a wire service posting
every few minutes filled the list. On the same day's articles:

| Method | Outlets included | Spread |
|---|---|---|
| Newest first, limit 40 | 11 | 9 from one outlet, 1 from the BBC |
| One per feed in turn, limit 100 | 17 | 5 or 6 from every outlet |

Big stories are the ones many outlets cover, so keeping every outlet in the
list is the best protection against missing one. The prompt tells the model to
read coverage by several outlets as a sign that a story matters.

Feeds are fetched in parallel. One at a time, 75 feeds that all timed out would
take about 25 minutes. The results are still handled in the order the feeds
are listed, so the same article showing up in two feeds is resolved the same
way on every run.

## How the writing is kept readable

The briefing prompt sets limits that can be checked afterwards. Sentences
should average 15 to 20 words and stay under 25. The model is also asked for
the active voice, everyday words, at most one subordinate clause in a sentence,
and paragraphs that open with their main point.

Dense writing was the main problem, so the prompt ends with an example. One
36-word sentence carrying three ideas sits next to the same content written as
three sentences averaging 16 words. A test checks both word counts. If the
example broke its own rule, the model would copy the mistake.

Each run logs the average sentence length for every topic and flags anything
over 22 words.

Every topic has a short overview, then one subheading per story. Articles about
the same event become a single story. `max_stories` is an upper limit, and
quiet days have fewer stories.

## The fact of the day

Each email opens with a fun fact and a short answer to "how come?". It should
take about ten seconds to read.

The prompt is based on research into what makes facts stick. George
Loewenstein's information-gap theory describes curiosity as a response to a
gap in something you partly know, so the fact should be a surprise about
something familiar. It should be one concrete idea the reader can picture.
Plain-language guidance aims general writing at an 8th-grade reading level, and
the prompt asks for something a 12-year-old could follow, with no technical
terms. Numbers are rounded and paired with a familiar comparison. Barrio,
Goldstein and Hofman (2016) found that comparisons like these help readers
remember numbers they see in the news.

The fact is capped at 25 words and the explanation at 40. Each run logs both
counts and warns when either goes over.

The prompt also shows a fact the digest sent before these rules existed. It
was 79 words long, used the phrase "zero lower bound" and packed in three
ideas. Next to it is the same fact rewritten in 48 plain words. Tests check the
word counts and give the two versions a reading-level score.

Left to choose freely, a model keeps returning to a few famous facts. The date
picks one of fourteen subjects and, separately, one of eleven everyday angles,
such as "a number that sounds wrong but is true". Eleven and fourteen have no
common factor, so each pairing comes back every 154 days. The model can drop an
angle that doesn't suit the subject.

The fact is the only part of the email that doesn't come from the day's
articles, so it has no sources. It's written after all the topics, so
if the model budget runs out, the fact is what gets skipped. Set
`fact_of_the_day` to `false` to turn it off.

## When a model is down

A 503 from Gemini means that model has run out of capacity for everyone, paid
and free. Asking again usually gets another 503, so the script moves to a
different model. It works down this list:

```
gemini-flash-latest
gemini-3.6-flash
gemini-3.5-flash
gemini-3.5-flash-lite
groq/openai/gpt-oss-120b          only if GROQ_API_KEY is set
groq/llama-3.3-70b-versatile
```

Each model gets three attempts, with a short randomised wait between them.
Groq comes last because it's the only option outside Google, which matters when
the whole of Google is having trouble.

Other failures:

- A 401 or 403 means the key was rejected, and that provider is skipped for the
  rest of the run.
- A 404 means the model name is wrong, and that model is skipped after one try.
- A `Retry-After` header is followed, up to 60 seconds.
- A reply that cuts off partway through its JSON is thrown away and the next
  model is asked.
- If no model answers, each topic lists its latest headlines and the email
  still goes out.

All model calls share one 8-minute budget. When a wide outage uses it up, the
run ends early and sends headlines. Without it, a run could keep retrying until
the 25-minute workflow timeout and send nothing.

Groq only gets the first 45 articles of a topic. Its free tier limits tokens
per minute, and a full list of 100 would use most of a minute's allowance on
one topic.

### Why these models

Gemini's free tier only includes the Flash models. Condensing short article
summaries into a briefing is well within what they do reliably.

No request sets `temperature`, `top_p` or `top_k`. Google advises leaving these
at their defaults for Gemini 3 models, and a low temperature can make them
repeat themselves until the output is cut off. Groq's gpt-oss also expects its
default settings. With sampling left alone, the fact's rotation through
subjects and angles is what keeps it from repeating.

The JSON schema the model fills in is kept shallow, since Flash models are
unreliable with deeply nested schemas. `maxOutputTokens` is set high because
running out of tokens cuts the JSON off without an error.

## How the email looks

Each topic has its own colour: teal, oxblood, indigo, moss, ochre or plum,
chosen by the topic's position in `topics.json`. The colour is used for the
topic's name, its links and its "Read more" label, which helps on a long
scroll.

Under the title, a line gives the day's totals, for example "39 stories from
67 outlets · about 4 min to skim". Below that, a thin bar is split into the
topic colours by each topic's share of the stories, with a key underneath.
These numbers are all counted from the email itself.

The title, topic names and the fact use Georgia, a serif font every mail app
has. Everything else uses the reader's standard sans-serif font. Gmail and
Outlook ignore web fonts, so the email doesn't use any.

Styles are written on each element because Gmail's apps drop `<style>` blocks
when the account isn't a Gmail account. The layout uses tables for Outlook on
Windows. There's only a light version: Gmail's apps invert colours in dark mode
regardless of the email's code, and pure black and white invert worst, so the
palette avoids them. Every text colour meets WCAG AA contrast, and a test checks
this.

## Email length and Gmail clipping

Gmail shows about the first 102KB of an email's HTML and puts the rest behind a
"View entire message" link. Each run's log reports the size and warns above
92KB.

| Topics | Stories | Size with stories folded | In Gmail |
|---|---|---|---|
| The five that ship | 39 | ~89KB | Fits (87%) |
| 5 topics of 6 stories | 30 | ~70KB | Fits |
| 8 topics of 6 stories | 48 | ~110KB | Clipped |
| 10 topics of 8 stories | 80 | ~178KB | Clipped |

These sizes use heavy test content, with every topic at its limit and every
story long with three sources. Real days are usually smaller. With
`collapsible_stories` off, the same email is about a sixth smaller. The AMP
version for Gmail has its own 200KB limit and stays well under it.

The [README](README.md#how-many-topics-you-can-have) lists ways to stay under
the limit.

## Collapsible stories

No one technique folds stories in every mail app, so the email uses two. Apps
that support neither show every story open.

Gmail won't fold ordinary HTML. It turns `<details>` and `<summary>` into plain
tags and ignores the `:checked` selector. It does support AMP for Email, so each
digest includes an AMP version built with `amp-accordion`. Gmail only shows it
when the sender and recipient are different addresses and the recipient has
approved the sender. If the addresses match, or the AMP version is over AMP's
200KB limit, the script leaves it out and says so in the log.

Other apps get a hidden checkbox in the regular email. The story's heading is a
label for the checkbox, and a CSS rule hides the story while the box is ticked.
The box starts ticked, and the rule only matches ticked boxes, so an app that
doesn't support `:checked` shows every story open. The label wraps the
checkbox instead of pointing to it by id. Some apps rename ids, and that would
leave a story stuck closed.

| App | Stories |
|---|---|
| Gmail on the web and in its apps, after [setup](README.md#7-collapsible-stories-in-gmail) | Folded (AMP) for 30 days after arrival |
| Apple Mail on Mac, iPhone and iPad | Folded |
| Yahoo Mail, Samsung Email, Thunderbird, Fastmail | Folded |
| Outlook.com and Outlook for Mac, iPhone and Android | Probably folded, support is partial |
| Gmail without setup, Outlook for Windows, Proton Mail, HEY | Open |

The table is based on [caniemail.com](https://www.caniemail.com) data. The
checkbox version was tested in Chromium and in WebKit, the engine Apple Mail
uses, at desktop and phone widths. It hasn't been tried in every app on a real
device, so turn off `collapsible_stories` if one misbehaves.

Tapping a story's text closes it, the same as tapping its heading. Links inside
a story open as usual. Topic overviews and the fact never fold, and tests check
that everything in the open version of the email is also in both folding
versions.

## Why citations can't be faked

The model never sees a link. It gets numbered articles with a title, outlet and
summary, and refers to them by number. The titles, links and outlet names
under each story are looked up from the fetched articles by those numbers, so
the model can't put a made-up URL in the email. A number that matches no
article is dropped and logged.

Links from feeds are checked too. Only full `http` and `https` links become
clickable, and anything else, such as a `javascript:` or relative link, is
shown as plain text. In the prompt, the article list sits inside a tag with
any angle brackets in headlines escaped, so text from a feed can't pass itself
off as an instruction.
