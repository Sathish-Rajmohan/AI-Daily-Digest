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
