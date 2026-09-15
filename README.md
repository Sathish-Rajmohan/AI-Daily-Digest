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

Everything happens in a web browser and takes about fifteen minutes, a little
longer if you create a second Gmail account in step 3. You'll need a GitHub
account and a Google account.

### 1. Fork the repo

Click **Fork** at the top of this page, then **Create fork**. The workflow runs
in your own copy on GitHub. You don't need to download anything, because every
file can be edited on GitHub (step 5 shows how).

A fork of a public repo is always public, and public repos get Actions minutes
for free. Anyone can see your `topics.json`, but the keys you add in step 4
stay hidden. For a private copy, create a new private repository on GitHub and
choose **Import a repository**, giving it this repo's URL. A private repo uses
your plan's monthly Actions minutes, and a daily run only needs a few.

### 2. Get a Gemini API key

1. Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
   and sign in.
2. Click **Create API key** and copy the key.

If a run later fails with `403 PERMISSION_DENIED`, open AI Studio and check the
project. Some accounts need to verify a phone number first.

### 3. Create a Gmail App Password

The digest is sent from a Gmail account. The script signs in to it with an App
Password, which is a separate password made for one app.

> [!IMPORTANT]
> **Do you read your email in Gmail?** Gmail only folds stories to one line
> when the digest comes from a different address than the one you read. If you
> want that, create the App Password in a second Gmail account that just sends
> the digest, not in the account you read. You can make a free one at
> [accounts.google.com/signup](https://accounts.google.com/signup).
> [Step 8](#8-collapsible-stories-in-gmail) finishes the setup.
>
> If you read your email in another app, or you don't mind every story showing
> open in Gmail, use your own account.

In the account that will send the digest:

1. Turn on 2-Step Verification at
   [myaccount.google.com/security](https://myaccount.google.com/security).
   App Passwords only appear once it's on.
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   name it `daily-digest` and click **Create**.
3. Copy the 16-character password straight away, because Google only shows it
   once. It works with or without the spaces.

If Google says App Passwords aren't available, the account is probably a work
or school account, or it uses Advanced Protection or security keys only. A
personal account that verifies with your phone or an authenticator app works.

### 4. Add the secrets

In your fork, open **Settings > Secrets and variables > Actions**. Click
**New repository secret** for each row below, and copy each name as it's
written.

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | The key from step 2 |
| `GMAIL_ADDRESS` | The sending Gmail address from step 3 |
| `GMAIL_APP_PASSWORD` | The App Password from step 3 |
| `RECIPIENT_EMAIL` | The address you want the digest delivered to. It can be any email address. |
| `GROQ_API_KEY` | Optional, see [a backup model](#a-backup-model) |

If you set up a second Gmail account to send from, `RECIPIENT_EMAIL` is the
address you read. Without `RECIPIENT_EMAIL`, the digest goes to
`GMAIL_ADDRESS`.

### 5. Set your time zone and delivery time

The digest comes set up for 8:00 AM Sydney time. To change that, edit two files
in your fork. On GitHub, open the file, click the pencil icon, make the change
and click **Commit changes**.

1. In `topics.json`, near the bottom, change `"timezone"` to your
   [time zone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones),
   such as `"Europe/London"` or `"America/New_York"`. It sets the date shown in
   the email.
2. In `.github/workflows/daily-digest.yml`, change `timezone` to the same name.
   To change the hour, edit `cron: "0 8 * * *"`. The first number is the minute
   and the second is the hour, so `"30 6 * * *"` sends at 6:30 AM.

### 6. Turn on the workflows and run it once

GitHub switches off the workflows in a new fork until you turn them on.

1. Open the **Actions** tab and click **I understand my workflows, go ahead and
   enable them**.
2. In the list on the left, click **Daily News Digest**. If a banner says the
   scheduled workflow is disabled, click **Enable workflow**. Do the same for
   **Keepalive**.
3. On **Daily News Digest**, click **Run workflow**, then the green **Run
   workflow** button that appears.
4. The run takes a few minutes. When it finishes, click it, click
   **send-digest**, and open the **Run digest script** step to see the log. The
   last line should read `Done.`
5. Check the inbox of `RECIPIENT_EMAIL`. The first digest may land in spam, so
   mark it as not spam to keep the next ones in your inbox.

After that, the digest arrives every morning at the time you set in step 5.

### 7. Let the keepalive job push

GitHub turns off scheduled workflows in a public repo after 60 days with no
activity. The **Keepalive** workflow makes an empty commit once a month to
prevent that, and it needs permission to push. In your fork, go to
**Settings > Actions > General**, choose **Read and write permissions** under
**Workflow permissions**, and click **Save**.

Without this setting the keepalive run fails, and you'd have to commit
something yourself every couple of months. A private copy doesn't need this
step.

### 8. Collapsible stories in Gmail

This step is optional. Apple Mail, Yahoo, Samsung Email, Thunderbird and
Fastmail fold each story to one line with no setup. Gmail needs the steps
below, and until then it shows every story open. You can read everything
either way. [Collapsible stories](HOW-IT-WORKS.md#collapsible-stories) lists
what each mail app does.

Gmail only folds stories in an email from a different address, and only from
a sender you've approved.

1. If you used the account you read in step 3, create a second Gmail account
   and make an App Password in it the same way. Update the `GMAIL_ADDRESS` and
   `GMAIL_APP_PASSWORD` secrets to that account, and set `RECIPIENT_EMAIL` to
   the address you read.
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

To change a file, open it in your fork on GitHub, click the pencil icon and
then **Commit changes**. The next run uses the new version.

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

Copy a block to add a topic and delete one to remove it. The blocks in the
`topics` list need a comma between them, with no comma after the last one. A
topic without `max_stories` gets up to five stories. Articles about the same
event are merged, so a quiet day can come in under the limit.

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
next to `topics.json`. Check in the **Actions** tab that the new workflow is
enabled.

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

A mistake in `topics.json` stops the run with a message that says what to fix.

### Delivery time

The schedule is set in
[.github/workflows/daily-digest.yml](.github/workflows/daily-digest.yml):

```yaml
- cron: "0 8 * * *"
  timezone: "Australia/Sydney"
```

[Step 5](#5-set-your-time-zone-and-delivery-time) covers changing it. Keep
`timezone` here and in `topics.json` the same, so the date in the email matches
the day it's sent. GitHub adjusts for daylight saving, and
[crontab.guru](https://crontab.guru) helps with cron syntax. Scheduled runs can
start late when GitHub is busy.

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

Start with the log of the run. In the **Actions** tab, click the run, then
**send-digest**, then the **Run digest script** step. Most problems are
reported there.

### No email arrived

If the run shows a red cross, open its log. If it succeeded, check the spam
folder of `RECIPIENT_EMAIL`. A wrong App Password or a rejected Gmail sign-in
is reported at the end of the log.

### It worked once but doesn't run every morning

In the **Actions** tab, click **Daily News Digest**. If a banner says the
workflow is disabled, click **Enable workflow**. GitHub disables scheduled
workflows in new forks, and in public repos after 60 days without activity,
which [step 7](#7-let-the-keepalive-job-push) prevents. Scheduled runs can
also start late when GitHub is busy.

### Gmail rejects the sign-in

Use the App Password, not your normal Google password. Check that
`GMAIL_ADDRESS` is the account the App Password was made in, and that 2-Step
Verification is still on.

### The run stops with a `JSONDecodeError`

`topics.json` isn't valid JSON. The message gives a line and column, and the
problem is usually a missing or extra comma or quote just before that point.

### The run stops with a `ValueError` about `topics.json`

Something in the file has the wrong type, such as `feeds` written as one
string instead of a list. The message names the topic and field.

### A topic is empty

Look for `found 0 raw articles` under the topic's name in the log. A feed may
have stopped working. [FEEDS.md](FEEDS.md#known-dead) lists feeds known to be
dead.

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

Go back over [step 8](#8-collapsible-stories-in-gmail). The log warns if
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
see `topics.json` in a public repo, but secrets stay hidden.

Treat the App Password like your Google password, because it can sign in to
your account through mail apps. You can revoke it from your Google Account at
any time without changing your main password. A second Gmail account used only
for sending keeps your main account out of this entirely.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md)
covers running the tests and adding a model provider.

## License

[MIT](LICENSE)
