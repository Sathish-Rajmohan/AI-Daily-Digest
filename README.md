# Daily News Digest

GitHub Actions cron job that pulls RSS feeds you configure, asks a model to
write one synthesized brief per topic (drawing on several outlets so the same
story isn't repeated), and emails you the HTML digest. No server to babysit.

```
GitHub Actions (daily)
  → digest.py
      → topics.json
      → RSS feeds
      → Gemini, or Groq if Gemini is down (one brief per topic)
      → Gmail SMTP
```

| Piece | Role | Cost |
|---|---|---|
| GitHub Actions | Runs the script on a schedule | Free on public repos (private repos use your included Actions minutes) |
| RSS feeds | Article titles, snippets, and canonical URLs | Free |
| Gemini API | One multi-source briefing per topic | Free tier (check your project's limits in AI Studio) |
| Groq API | Optional backstop when Gemini is unreachable | Free tier (1,000 requests/day) |
| Gmail SMTP | Sends the email | Free |

Each topic opens with a short overview of what matters that day, then breaks
into a subheading per development with a few plain-language paragraphs under
each. Articles covering the same event are fused into one entry rather than
repeated as near-duplicate cards, and the sources behind each entry are listed
directly beneath it with links back to the publishers.

The prose is written to general-audience readability targets: sentences
averaging 15-20 words, active voice, and everyday vocabulary. Each run logs
the average sentence length it actually got, so you can see whether that is
holding.

### One thing worth knowing

Above the news, each email opens with a single fact and a short note on why
it's worth knowing: what it explains, what it connects to, or what it should
make you reconsider. It's there to give you one thing to think about on days
when the news gives you nothing.

The subject rotates by date through fourteen fields (physics, economics,
history, linguistics, geology, and so on) and comes back around after a
fortnight, because a model left to choose freely returns to the same handful
of chestnuts. The prompt also rules out the well-worn circuit of facts that
turn up on every list.

One caveat worth being clear about: this is the only part of the email not
grounded in a fetched article. It carries no sources, because it comes from
the model's own knowledge rather than from today's feeds. Treat it as a
prompt to go and read about something, not as a citation.

Turn it off by setting `fact_of_the_day` to `false` in `settings`. It runs
after the topics, so on a bad day for the API you lose the fact rather than
a topic.

---

## Quickstart

### 1. Fork or clone

```bash
git clone https://github.com/Sathish-Rajmohan/AI-Daily-Digest.git
cd AI-Daily-Digest
```

Or fork on GitHub and work from your copy. Public or private both work; public
repos get unlimited standard Actions minutes, private ones draw from your plan
quota (plenty for one short daily job).

### 2. Gemini API key

1. Open [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
   (same page as [aistudio.google.com/api-keys](https://aistudio.google.com/api-keys)).
2. Sign in and click **Create API key**. New keys from AI Studio are auth keys
   by default.
3. Copy the key for step 4.

The script tries `gemini-flash-latest`, Google's alias for the current Flash
model, then falls back through `gemini-3.6-flash`, `gemini-3.5-flash`, and
`gemini-3.5-flash-lite` if that one is overloaded. Each model sits on its own
serving capacity, so a model that returns 503 doesn't stop the run. Set
`GEMINI_MODELS` to a comma-separated list to change the chain, or
`GEMINI_MODEL` to pin a first choice and keep the rest as fallbacks. A model
ID your key can't use is dropped after one try, so an outdated entry in the
chain costs a fraction of a second rather than breaking the run.

### 2b. Groq API key (optional)

Every model above runs on Google's infrastructure, so an incident on their
side takes the whole chain with it. Adding Groq puts a non-Google model at the
bottom of the chain as a backstop. It's free, needs no card, and allows 1,000
requests a day against the 5 this uses.

1. Open [console.groq.com/keys](https://console.groq.com/keys) and sign in.
2. Create an API key and copy it.
3. Add it as the `GROQ_API_KEY` secret in step 4.

Skip this and the digest works exactly as before. The chain leaves Groq out
when the key isn't set, and only reaches it once every Gemini model has
already failed, so on a normal day nothing changes. On a day it does get used,
expect the writing to read noticeably different from Gemini's. Override the
models with `GROQ_MODELS`.

One digest run is a handful of requests (one per topic). Exact free-tier RPM/RPD
numbers vary by model and project; check
[AI Studio rate limits](https://aistudio.google.com/rate-limit) for yours. If a
run returns `403 PERMISSION_DENIED`, look at the project status in AI Studio
before assuming the code is broken. Some accounts need phone or account
verification first.

### 3. Gmail App Password

App Passwords let the script talk to Gmail SMTP without your real password.
You need 2-Step Verification on.

1. Turn on 2-Step Verification at
   [myaccount.google.com/security](https://myaccount.google.com/security) if it
   isn't already.
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   (docs:
   [Sign in with app passwords](https://support.google.com/accounts/answer/185833)).
3. Name it something like `daily-digest` and create it.
4. Copy the 16-character password.

If the App Passwords page says it's not available, you're usually on a
Workspace account, Advanced Protection, or security-key-only 2SV. Use a
personal Google account with phone/authenticator 2SV instead.

### 4. Repo secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret name | Value |
|---|---|
| `GEMINI_API_KEY` | Key from step 2 |
| `GROQ_API_KEY` | Optional. Key from step 2b, used only when Gemini is unreachable |
| `GMAIL_ADDRESS` | Gmail address that owns the App Password |
| `GMAIL_APP_PASSWORD` | 16-character App Password from step 3 |
| `RECIPIENT_EMAIL` | Inbox that should receive the digest (can match `GMAIL_ADDRESS`) |

### 5. Enable and test

1. Open the **Actions** tab and enable workflows if GitHub asks.
2. Select **Daily News Digest** → **Run workflow**.
3. Watch the run log: you should see per-topic fetch counts and "Sending email... Done."
4. Check the inbox (and spam, the first time).

After a successful manual run, the schedule takes over.

**Note:** on public repos, scheduled workflows go idle if the repo has no
activity for 60 days. `.github/workflows/keepalive.yml` handles this
automatically with a monthly empty commit, well inside that window, so you
shouldn't need to think about it. It needs the repo's default `GITHUB_TOKEN`
to be allowed to push: **Settings → Actions → General → Workflow
permissions → Read and write permissions**. If that's left on the default
read-only setting, the keepalive job's push step will fail (visible in its
Actions log) and you'd want to switch it, or just push a real commit every
couple of months yourself instead.

---

## Customizing topics

Edit `topics.json`. No code changes needed.

```json
{
  "name": "Tech & AI",
  "max_stories": 6,
  "feeds": [
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.theverge.com/rss/index.xml"
  ]
}
```

Add a topic by copying a block. Delete a block to drop one. `max_stories` caps
how many distinct developments that topic breaks out into its own subheading.
Articles about the same event still collapse into one entry, so this is a
ceiling on distinct stories, not a target to pad out to. Push and the next run
picks it up.

### How articles are chosen

A busy topic finds far more articles than fit in one prompt. International
News pulls over 400 in a normal 24 hours against a cap of 100, so what gets
dropped matters more than the cap does.

Articles are taken **one from each feed in turn**, freshest first within a
feed, rather than sorting everything by time and cutting at the cap. Sorting
by time loses whole outlets: a wire publishing every few minutes fills the
list, and a story the rest of the world led with falls off the end. Measured
on the same pool of articles:

| | Outlets represented | Spread |
|---|---|---|
| By time, cap 40 | 11 | 9 from one outlet, 1 from the BBC |
| Round robin, cap 100 | 17 | 5-6 from every outlet |

Since a major story is precisely the one several outlets all cover, spreading
the list across outlets is what protects against missing one. The prompt tells
the model this, so it treats repeated coverage as a significance signal rather
than something to deduplicate away.

Adding feeds is therefore cheap. More feeds means better coverage without one
of them taking over.

### How long the email can get

Gmail renders about 102KB of HTML and hides the rest behind a "View entire
message" link, which still shows everything but takes a click. Each run prints
the size it used. Rough guide:

| Config | Stories | Size | |
|---|---|---|---|
| stock, as shipped | 39 | ~75KB | 73% of the budget |
| 5 topics x 6 | 30 | ~60KB | fine |
| 8 topics x 6 | 48 | ~94KB | warns in the log |
| 10 topics x 8 | 80 | ~153KB | Gmail clips it |

Past the limit, drop `max_stories` or split the topics across two runs by
adding a second workflow with its own `topics.json`.

`settings.lookback_hours` controls how far back feeds are scanned. Default is
`24` so an 8 AM Sydney run covers the previous day without dragging in a lot of
stale overlap. Bump it if Actions delays are eating stories; lower it if the
digest feels repetitive.

### Finding feeds

Search `"site name" RSS feed`, or try `/rss`, `/feed`, `/rss.xml` on the domain.
A browser showing XML with `<item>` or `<entry>` tags means you're good.

The stock config ships 75 feeds across five topics, picked for range as well
as reputation, so a story isn't seen through one country's press alone:

| Topic | Feeds | Reach |
|---|---|---|
| Tech & AI | 18 | Ars, Verge, Wired, MIT Tech Review, BBC, NYT, Register, Guardian, IEEE Spectrum, 404 Media, Rest of World, plus OpenAI/DeepMind/HuggingFace/Google/Cloudflare source blogs |
| International News | 19 | BBC, Guardian, NYT, NPR, WaPo, LA Times, FT, Al Jazeera, ABC and SMH (AU), SCMP (HK), DW (DE), France 24 (FR), CBC (CA), The Hindu (IN), Japan Times (JP), Straits Times (SG), AllAfrica, MercoPress (LatAm) |
| Geopolitics & Security | 11 | Foreign Policy, Foreign Affairs, Economist, Defense One, Defense News, Breaking Defense, War on the Rocks, The Diplomat, Bellingcat, Lowy Institute, Atlantic Council |
| Science & Health | 13 | Nature, Science, Scientific American, New Scientist, Quanta, ScienceDaily, BBC, Guardian, NPR Health, STAT, KFF Health News, Ars Science, MIT Tech Review |
| Markets & Business | 14 | Bloomberg, FT (markets + companies), NYT, BBC, CNBC (markets + economy), Economist (finance + business), MarketWatch, Yahoo Finance, Guardian, Stratechery, Benedict Evans |

Every URL was probed for a live response with a recent item before being
added. Feeds left out on purpose because they are dead or abandoned: the
Reuters and AP public RSS URLs, the WSJ feeds at `feeds.a.dj.com` (last item
over 590 days old), UN and WHO news, and the RSS endpoints for CSIS, RUSI,
Chatham House, ISW, Carnegie, EurekAlert, Anthropic and Meta AI. The
International Crisis Group feed is alive but publishes roughly weekly, which
falls outside a 24-hour lookback most days.

If a feed goes stale later, watch for `found 0 raw articles` in that topic's
Actions log across several days in a row.

---

## Ready-made topics

Finding feeds is the tedious part of setting this up, and plenty of the
obvious URLs are dead. Every feed below was fetched and checked for a recent
item, so these are drop-in: copy a block into the `topics` array in
`topics.json` and push.

Dead as of the last check, so don't bother re-adding them: AP, Reuters (all
sections), Politico, Axios, FiveThirtyEight, a16z, VentureBeat, PitchBook,
Bleacher Report, The Athletic, Sports Illustrated, Fox Sports, IMF, VoxEU,
Brookings, World Bank, OECD, BIS, NYBooks, New Yorker section feeds, AFR,
Sky & Telescope, PortSwigger, AIGA Eye on Design, Autoblog, Vox, City
Journal.

<details>
<summary><b>Sports</b></summary>

```json
{
  "name": "Sports",
  "max_stories": 7,
  "feeds": [
    "https://www.espn.com/espn/rss/news",
    "https://feeds.bbci.co.uk/sport/rss.xml",
    "https://www.theguardian.com/sport/rss",
    "https://www.skysports.com/rss/12040",
    "https://www.cbssports.com/rss/headlines/",
    "https://sports.yahoo.com/rss/",
    "https://www.abc.net.au/news/feed/45910/rss.xml"
  ]
}
```
</details>

<details>
<summary><b>Finance & Economics</b></summary>

```json
{
  "name": "Finance & Economics",
  "max_stories": 7,
  "feeds": [
    "https://www.economist.com/finance-and-economics/rss.xml",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://www.investing.com/rss/news.rss",
    "https://seekingalpha.com/feed.xml",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://theconversation.com/au/business/articles.atom"
  ]
}
```
</details>

<details>
<summary><b>Climate & Energy</b></summary>

```json
{
  "name": "Climate & Energy",
  "max_stories": 7,
  "feeds": [
    "https://www.carbonbrief.org/feed/",
    "https://insideclimatenews.org/feed/",
    "https://www.theguardian.com/environment/climate-crisis/rss",
    "https://grist.org/feed/",
    "https://www.eia.gov/rss/todayinenergy.xml",
    "https://cleantechnica.com/feed/",
    "https://www.nature.com/nclimate.rss",
    "https://yaleclimateconnections.org/feed/"
  ]
}
```
</details>

<details>
<summary><b>Space & Astronomy</b></summary>

```json
{
  "name": "Space & Astronomy",
  "max_stories": 6,
  "feeds": [
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://spacenews.com/feed/",
    "https://www.space.com/feeds/all",
    "https://arstechnica.com/science/space/feed/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "https://phys.org/rss-feed/space-news/"
  ]
}
```
</details>

<details>
<summary><b>Cybersecurity</b></summary>

```json
{
  "name": "Cybersecurity",
  "max_stories": 7,
  "feeds": [
    "https://krebsonsecurity.com/feed/",
    "https://www.bleepingcomputer.com/feed/",
    "https://feeds.feedburner.com/TheHackersNews",
    "https://www.schneier.com/feed/atom/",
    "https://www.darkreading.com/rss.xml",
    "https://therecord.media/feed/",
    "https://www.cisa.gov/cybersecurity-advisories/all.xml"
  ]
}
```
</details>

<details>
<summary><b>Startups & Venture</b></summary>

```json
{
  "name": "Startups & Venture",
  "max_stories": 6,
  "feeds": [
    "https://techcrunch.com/feed/",
    "https://news.crunchbase.com/feed/",
    "https://sifted.eu/feed",
    "https://tech.eu/feed/",
    "https://www.eu-startups.com/feed/",
    "https://www.saastr.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>US Politics</b></summary>

Deliberately spans the spectrum, since a digest built from one side of it
will read as confirmation rather than information. Drop whichever you don't
want.

```json
{
  "name": "US Politics",
  "max_stories": 8,
  "feeds": [
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "https://feeds.washingtonpost.com/rss/politics",
    "https://feeds.npr.org/1014/rss.xml",
    "https://thehill.com/news/feed/",
    "https://www.realclearpolitics.com/index.xml",
    "https://www.propublica.org/feeds/propublica/main",
    "https://www.nationalreview.com/feed/",
    "https://reason.com/feed/",
    "https://thedispatch.com/feed/",
    "https://www.motherjones.com/politics/feed/"
  ]
}
```
</details>

<details>
<summary><b>Gaming</b></summary>

```json
{
  "name": "Gaming",
  "max_stories": 7,
  "feeds": [
    "https://www.eurogamer.net/feed",
    "https://www.polygon.com/rss/index.xml",
    "https://www.rockpapershotgun.com/feed",
    "https://kotaku.com/rss",
    "https://www.gamedeveloper.com/rss.xml",
    "https://www.ign.com/rss/articles/feed",
    "https://www.pcgamer.com/rss/"
  ]
}
```
</details>

<details>
<summary><b>Film & TV</b></summary>

```json
{
  "name": "Film & TV",
  "max_stories": 6,
  "feeds": [
    "https://variety.com/feed/",
    "https://www.hollywoodreporter.com/feed/",
    "https://deadline.com/feed/",
    "https://www.indiewire.com/feed/",
    "https://www.theguardian.com/film/rss",
    "https://www.rogerebert.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Music</b></summary>

```json
{
  "name": "Music",
  "max_stories": 6,
  "feeds": [
    "https://pitchfork.com/feed/feed-news/rss",
    "https://www.rollingstone.com/music/feed/",
    "https://www.theguardian.com/music/rss",
    "https://www.billboard.com/feed/",
    "https://consequence.net/feed/",
    "https://thequietus.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Books & Ideas</b></summary>

```json
{
  "name": "Books & Ideas",
  "max_stories": 6,
  "feeds": [
    "https://lithub.com/feed/",
    "https://aeon.co/feed.rss",
    "https://www.theguardian.com/books/rss",
    "https://feeds.npr.org/1032/rss.xml",
    "https://electricliterature.com/feed/",
    "https://www.theparisreview.org/blog/feed/",
    "https://fivebooks.com/feed/",
    "https://newrepublic.com/rss.xml"
  ]
}
```
</details>

<details>
<summary><b>Crypto & Web3</b></summary>

```json
{
  "name": "Crypto & Web3",
  "max_stories": 6,
  "feeds": [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml"
  ]
}
```
</details>

<details>
<summary><b>Design & Architecture</b></summary>

```json
{
  "name": "Design & Architecture",
  "max_stories": 5,
  "feeds": [
    "https://www.dezeen.com/feed/",
    "https://www.archdaily.com/rss/",
    "https://www.core77.com/blog/rss.xml",
    "https://www.designboom.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Cars & EVs</b></summary>

```json
{
  "name": "Cars & EVs",
  "max_stories": 6,
  "feeds": [
    "https://electrek.co/feed/",
    "https://insideevs.com/rss/articles/all/",
    "https://www.caranddriver.com/rss/all.xml/",
    "https://jalopnik.com/rss"
  ]
}
```
</details>

<details>
<summary><b>Australia</b></summary>

```json
{
  "name": "Australia",
  "max_stories": 7,
  "feeds": [
    "https://www.abc.net.au/news/feed/51120/rss.xml",
    "https://www.theguardian.com/australia-news/rss",
    "https://www.smh.com.au/rss/national.xml",
    "https://theconversation.com/au/articles.atom",
    "https://www.crikey.com.au/feed/"
  ]
}
```
</details>

---

## Finding feeds for a topic that isn't listed

Paste this into any chat assistant with web search, swapping in your topic.
It's written to make the assistant check the URLs rather than recall them,
which is where this usually goes wrong: plenty of plausible-looking feed
URLs stopped working years ago and a model will happily produce them from
memory.

```
I need RSS/Atom feed URLs for a daily news digest on: <YOUR TOPIC>.

Find 6-10 feeds and return them as a JSON array of URL strings, nothing else.

Requirements:
1. Verify each URL actually resolves right now and returns RSS or Atom XML.
   Do not give me a URL you have not checked. If you cannot check it, leave
   it out. Guessing /feed or /rss on a domain is not checking.
2. Each feed must have published something in the last 7 days. Say which
   ones you could not confirm.
3. Full-site or section feeds only. No search-query feeds, no per-author or
   per-tag feeds, no Google News or other aggregator-generated feeds, no
   Reddit, no YouTube.
4. The feed must carry a headline and a text summary or description per
   item. Title-only feeds are no use to me.
5. Prefer publications with an editorial masthead. Avoid content farms, SEO
   blogs and press-release wires.
6. Spread them across outlets, countries and, where the topic is contested,
   editorial perspective. I want a full picture, not one house view.
7. No feed behind a login or a hard paywall that strips the summary text.

For each one, tell me in a sentence: the outlet, roughly how often it
publishes, and its angle or specialism.
```

Then check what comes back before trusting it. Add the feeds, run the
workflow by hand, and look for `found 0 raw articles` or a topic with far
fewer outlets than feeds in the log.

Two things to know when adding your own:

- **Feeds with no dates on their items** are always treated as current,
  since there's no timestamp to compare against the lookback window. A feed
  like that can put the same items in your digest every day. Watch for
  repeats and drop it if it happens.
- **Adding feeds is cheap.** Articles are taken from each feed in turn, so a
  new feed takes a share of the slots rather than a high-volume one taking
  over.

---

## Delivery time

Cron lives in `.github/workflows/daily-digest.yml`. It is set to **8:00 AM
Australia/Sydney** every day via:

```yaml
- cron: "0 8 * * *"
  timezone: "Australia/Sydney"
```

GitHub evaluates `timezone` as an IANA zone, so AEST/AEDT shifts are handled
for you. Change the hour (or the timezone) there if you want a different slot.
[crontab.guru](https://crontab.guru) is handy for sanity-checking the expression.

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

To add a provider, write a `_yourprovider_request(model, prompt)` returning
`(url, headers, body)` and a `_yourprovider_extract(data, topic_name)`
returning the model's raw JSON text, register both in `_PROVIDERS`, and add
its models in `build_model_chain()`. Retries, backoff, the shared time budget,
and the citation resolution are all provider-agnostic, so there's nothing else
to touch. Anything speaking the OpenAI chat-completions format can copy the
Groq pair almost verbatim. `BRIEF_SCHEMA` is written in Gemini's schema
dialect and converted to plain JSON Schema by `_to_json_schema()`, so only one
definition needs to stay correct.

---

## Troubleshooting

**No email:** start with the Actions log. Bad keys, App Password mistakes, and
SMTP auth failures show up there.

**Empty topic:** look for `found 0 raw articles`. Feed URL is probably dead;
open it in a browser.

**Gmail login rejected:** use an App Password, not your normal password, and
confirm 2-Step Verification is on.

**Gemini 429:** the script backs off and retries. With many topics, raise the
`time.sleep(2)` between topics in `digest.py`.

**Gemini 401/403:** account/project issue in AI Studio more often than a code
bug. Check [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).
The run drops Gemini at that point and carries on with Groq if you've set a
key for it, so a dead Google key degrades the digest rather than ending it.

**Gemini 500/502/503/504:** Google's own capacity. A 503 means one model's
shared serving pool is saturated, which affects free and paid traffic alike,
so it isn't something a billing or quota change fixes. Asking the same model
again usually returns the same 503, so the script retries briefly and then
moves down the chain instead of waiting longer. Look for
`answered by fallback model ...` in the Actions log to see this working.

**Every model failing at once:** the topic still appears in the email as a
plain list of headlines under a "Top headlines" label rather than dropping out
of it. All summarization across the whole run shares one 8-minute budget, so a
broad outage means a short run that sends headlines, not a run that hits the
25-minute workflow timeout and sends nothing.

---

## Security

Keep keys out of the repo. Put them in Actions secrets only.

Forking publicly exposes `topics.json` (your source list). Secrets stay private.

A Gmail App Password is scoped to SMTP for that app label. Revoke it anytime
from your Google Account without changing your main password.

## License

[MIT](LICENSE)

## Contributing

Issues and PRs welcome: more topic presets, other LLM backends, Slack/Discord
delivery, whatever fits.
