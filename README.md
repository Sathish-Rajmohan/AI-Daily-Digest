# Daily News Digest

A GitHub Actions job that reads the RSS feeds you choose, has a language model
write a short briefing for each topic, and emails it to you every morning.
There's no server to run.

Each email starts with a fun fact. Every topic then gets an overview of the
day and its main stories, each with links to the articles it came from. When
several outlets cover the same event, their articles become one story. In most
mail apps a story shows as a single line that you tap to open.

It runs on free tiers:

| Piece | What it does | Cost |
|---|---|---|
| GitHub Actions | Runs the script on a schedule | Free for public repos |
| RSS feeds | Supply the headlines, summaries and links | Free |
| Gemini API | Writes the briefings | Free tier |
| Groq API | Optional backup when Gemini is down | Free tier |
| Gmail | Sends the email | Free |

Other docs:

- [FEEDS.md](FEEDS.md) has ready-made topics with checked feed URLs.
- [HOW-IT-WORKS.md](HOW-IT-WORKS.md) explains how articles are picked, how the
  writing is kept readable and what happens when something fails.
- [CONTRIBUTING.md](CONTRIBUTING.md) is for changing the code and running the
  tests.

## Setup

Allow about fifteen minutes, most of it on Google's account pages.

### 1. Fork the repo

Click **Fork** at the top of this page. The workflow runs in your fork, so you
need your own copy on GitHub. If you want to edit files on your computer, clone
the fork:

```bash
git clone https://github.com/<your-username>/AI-Daily-Digest.git
```

Public repos get unlimited Actions minutes. A private repo uses your plan's
monthly allowance, and a daily run only needs a few minutes of it.

### 2. Get a Gemini API key

1. Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
   and sign in.
2. Click **Create API key** and copy the key.

If a run later fails with `403 PERMISSION_DENIED`, open AI Studio and check the
project. Some accounts need to verify a phone number first.

### 3. Create a Gmail App Password

The script signs in to Gmail with an App Password, a separate password made
for one app. Google only offers them when 2-Step Verification is on.

1. Turn on 2-Step Verification at
   [myaccount.google.com/security](https://myaccount.google.com/security).
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   name it `daily-digest` and create it.
3. Copy the 16-character password.

If Google says App Passwords aren't available, the account is probably a work
or school account, or it uses Advanced Protection or security keys only. A
personal account that verifies with your phone or an authenticator app works.

### 4. Add the secrets

In your fork, open **Settings > Secrets and variables > Actions** and add each
of these with **New repository secret**:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | The key from step 2 |
| `GMAIL_ADDRESS` | The Gmail address the App Password belongs to |
| `GMAIL_APP_PASSWORD` | The App Password from step 3 |
| `RECIPIENT_EMAIL` | Where the digest goes. It can match `GMAIL_ADDRESS`, but step 7 needs a different address. |
| `GROQ_API_KEY` | Optional, see [a backup model](#a-backup-model) |

### 5. Run it once

1. Open the **Actions** tab and enable workflows if GitHub asks.
2. Choose **Daily News Digest**, then **Run workflow**.
3. When the run finishes, the last line of its log should read `Done.`
4. Check your inbox, and your spam folder the first time.

From then on it runs every morning.

### 6. Let the keepalive job push

GitHub turns off scheduled workflows in a public repo after 60 days with no
activity. The `Keepalive` workflow makes an empty commit once a month to
prevent that, and it needs permission to push:

**Settings > Actions > General > Workflow permissions > Read and write permissions**

Without this setting the keepalive run fails, and you'd have to push a commit
yourself every couple of months.

### 7. Collapsible stories in Gmail

This step is optional. Apple Mail, Yahoo, Samsung Email, Thunderbird and
Fastmail fold each story to one line with no setup. Gmail needs the steps
below, and until then it shows every story open. You can read everything
either way. [Collapsible stories](HOW-IT-WORKS.md#collapsible-stories) lists
what each mail app does.

Gmail only folds stories in an email from a different address, and only from
a sender you've approved.

1. Create a second Gmail account, or use one you already have, and make an App
   Password for it as in step 3. Set `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`
   to that account and `RECIPIENT_EMAIL` to the address you read.
2. On a computer, open gmail.com in the account you read. Go to **Settings >
   See all settings > General** and change these:
   - set **Images** to **Always display external images**
   - under **Dynamic email**, tick **Enable dynamic email**, click **Developer
     settings**, type the sending address and click **OK**
   - click **Save changes** at the bottom of the page
3. Run the workflow again and tap a story to open it.

Each digest stays folded in Gmail for 30 days after it arrives. After that,
Gmail shows the message fully open.

## Customising

### Topics and feeds

Topics live in [topics.json](topics.json). Each one has a name, the feed URLs
to read, and the most stories it can include:

```json
{
  "name": "Tech & AI",
  "max_stories": 8,
  "feeds": [
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.theverge.com/rss/index.xml"
  ]
}
```

Copy a block to add a topic and delete one to remove it. A topic without
`max_stories` gets up to five stories. Articles about the same event are
merged, so a quiet day can come in under the limit.

[FEEDS.md](FEEDS.md) has blocks to paste in for about fifty subjects, from
sport and finance down to single interests like Formula 1 or anime. It also
has a prompt you can give an AI assistant to find feeds for a subject that
isn't listed.

A topic can have plenty of feeds. The script takes articles from each feed in
turn, so a busy outlet can't push the others out.
[How articles are chosen](HOW-IT-WORKS.md#how-articles-are-chosen) has the
details.

### How many topics you can have

As many as you like, with one catch. Gmail displays roughly the first 102KB of
an email and hides the rest behind a **View entire message** link. You can
still read all of it, but it takes an extra tap. The five topics that come with
the repo fit. At around eight topics of six stories each, busy days start to
get clipped.

To avoid clipping you can lower `max_stories` on some topics, set
`collapsible_stories` to `false` (the folding version of the email is a little
bigger), or split your topics between two workflows. Each run's log reports how
much of the limit the email used and warns when it gets close. Other mail apps
don't clip. [Email length and Gmail
clipping](HOW-IT-WORKS.md#email-length-and-gmail-clipping) has measurements.

To run a second set of topics, copy `.github/workflows/daily-digest.yml` to a
new file and change its `name`. In its last step, add
`DIGEST_CONFIG_PATH: topics-2.json` under `env`, then create `topics-2.json`
next to `topics.json`.

### Settings

These go in the `settings` block at the end of `topics.json`:

```json
"settings": {
  "lookback_hours": 24,
  "email_subject_prefix": "Daily Digest",
  "timezone": "Australia/Sydney",
  "fact_of_the_day": true,
  "collapsible_stories": true
}
```

| Setting | What it does |
|---|---|
| `lookback_hours` | How far back to look for articles. Raise it if late runs miss stories, lower it if stories repeat between days. |
| `email_subject_prefix` | Text before the date in the subject line. |
| `timezone` | Time zone for the date in the email, as an IANA name such as `Europe/London`. |
| `fact_of_the_day` | `false` removes the fact from the top of the email. |
| `collapsible_stories` | `false` sends every story fully open. |

A mistake in `topics.json` stops the run with a message that names the topic
and the field to fix.

### Delivery time

The schedule is set in
[.github/workflows/daily-digest.yml](.github/workflows/daily-digest.yml). It
runs at 8:00 AM Sydney time:

```yaml
- cron: "0 8 * * *"
  timezone: "Australia/Sydney"
```

Change the hour and time zone to suit you, and set `timezone` in `topics.json`
to match so the date in the email lines up. GitHub adjusts for daylight saving.
[crontab.guru](https://crontab.guru) helps with cron syntax. Scheduled runs can
start a few minutes late when GitHub is busy.

### A backup model

All the Gemini models run on Google's servers, so an outage there can stop
every one of them. A free Groq key adds models from outside Google as a last
resort.

1. Sign in at [console.groq.com/keys](https://console.groq.com/keys) and create
   a key. You don't need a card.
2. Add it as the `GROQ_API_KEY` secret.

Groq is only used after every Gemini model has failed, and a run makes one
request per topic plus one for the fact. Its writing sounds a little
different, so you may notice the days it's used.

To pick models yourself, set `GEMINI_MODELS` or `GROQ_MODELS` to a
comma-separated list under `env` in the workflow's last step. `GEMINI_MODEL`
puts one model at the front and keeps the defaults behind it.

## Troubleshooting

Start with the log of the run in the **Actions** tab. Most problems are
reported there.

### No email arrived

Check your spam folder, then the end of the log. A wrong App Password or a
rejected Gmail sign-in is reported there.

### Gmail rejects the sign-in

Use the App Password, not your normal Google password, and check that 2-Step
Verification is still on.

### A topic is empty

Look for `found 0 raw articles` under the topic's name in the log. A feed may
have stopped working. [FEEDS.md](FEEDS.md#known-dead) lists feeds known to be
dead.

### The run stops with a `ValueError` about `topics.json`

Something in the file has the wrong type, such as `feeds` written as one
string instead of a list. The message names the topic and field.

### Gemini returns 429

The free rate limit was hit. The script waits and tries again. If you have a
lot of topics, raise the `time.sleep(2)` between topics in `main()` in
`digest.py`.

### Gemini returns 401 or 403

The key or the AI Studio project has a problem. Check it at
[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey). The
run stops using Gemini at that point and moves to Groq if you've added a key.

### Gemini returns 503

Google is short of capacity. The script tries the next model, and the log says
`answered by fallback model` when one works.
[When a model is down](HOW-IT-WORKS.md#when-a-model-is-down) explains the
order.

### Every topic shows plain headlines

No model answered. The email still goes out, with each topic's latest
headlines in place of a briefing.

### Stories don't fold in Gmail

Go back over [step 7](#7-collapsible-stories-in-gmail). The log warns if
`GMAIL_ADDRESS` and `RECIPIENT_EMAIL` are the same, or if the folding version
was too large to send.

### Gmail shows `[Message clipped]`

Tap **View entire message** to read the rest. To stop it happening, see
[how many topics you can have](#how-many-topics-you-can-have).

### The writing feels dense

The log prints each topic's average sentence length and flags anything over
22 words. [How the writing is kept
readable](HOW-IT-WORKS.md#how-the-writing-is-kept-readable) covers the rules
the model follows.

## Security

Keep keys out of the repo and store them only as Actions secrets. Anyone can
see `topics.json` in a public fork, but secrets stay hidden.

Treat the App Password like your Google password, because it can sign in to
your account through mail apps. You can revoke it from your Google Account at
any time without changing your main password.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md)
covers running the tests and adding a model provider.

## License

[MIT](LICENSE)
