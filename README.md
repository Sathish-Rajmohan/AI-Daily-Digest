# Daily News Digest

A GitHub Actions job that reads the RSS feeds you choose, has a language model
write a short briefing for each topic, and emails it to you every morning.
There's no server to run.

Each email starts with a fun fact. Every topic then gets an overview of the
day and its main stories, each with links to the articles it came from. When
several outlets cover the same event, their articles become one story. In most
mail apps a story shows as a single line that you tap to open.

On Sundays a second email reviews the week. It follows the stories that ran
over several days and lists the rest in a line each. It is written from that
week's daily emails, so it needs no extra setup.

The digest can also go to Telegram, Discord or Slack, as well as or in place
of email. See [chat channels](#chat-channels).

It runs on free tiers:

| Piece | What it does | Cost |
|---|---|---|
| GitHub Actions | Runs the script on a schedule | Free for public repos |
| RSS feeds | Supply the headlines, summaries and links | Free |
| Gemini API | Writes the briefings | Free tier |
| Groq API | Optional backup when Gemini is down | Free tier |
| Gmail | Sends the email | Free |
| Telegram, Discord, Slack | Optional chat delivery | Free |

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

A chat channel needs its own secrets. [Chat channels](#chat-channels) lists
them. If you send by chat only, you can leave out the Gmail secrets.

### 5. Set your time zone and delivery time

The digest comes set up for 8:00 AM Sydney time. To change that, edit two files
in your fork. On GitHub, open the file, click the pencil icon, make the change
and click **Commit changes**.

1. In `topics.json`, near the bottom, change `"timezone"` to your
   [time zone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones),
   such as `"Europe/London"` or `"America/New_York"`. It sets the date shown in
   the email.
2. In `.github/workflows/daily-digest.yml`, change `timezone` to the same name.
   To change the hour, edit `cron: "17 8 * * *"`. The first number is the minute
   and the second is the hour, so `"30 6 * * *"` starts the run at 6:30 AM.
   GitHub starts it late, often by hours, so the email arrives after that
   time. [Delivery time](#delivery-time) explains how to get it at a set time.

### 6. Turn on the workflows and run it once

GitHub switches off the workflows in a new fork until you turn them on.

1. Open the **Actions** tab and click **I understand my workflows, go ahead and
   enable them**.
2. In the list on the left, click **Daily News Digest**. If a banner says the
   scheduled workflow is disabled, click **Enable workflow**. Do the same for
   **Keepalive** and **Weekly News Digest**.
3. On **Daily News Digest**, click **Run workflow**, then the green **Run
   workflow** button that appears.
4. The run takes a few minutes. When it finishes, click it, click
   **send-digest**, and open the **Run digest script** step to see the log. The
   last line should read `Done.`
5. Check the inbox of `RECIPIENT_EMAIL`. The first digest may land in spam, so
   mark it as not spam to keep the next ones in your inbox.

After that, a digest is sent every morning. It will usually arrive some time
after the time you set in step 5, as [delivery time](#delivery-time) explains.

The weekly email comes on Sunday, after that morning's digest. Its first
editions cover only the days since you set up the fork, and say so.
[The weekly edition](#the-weekly-edition) explains the settings.

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
new file and change its `name`. In its **Run digest script** step, add
`DIGEST_CONFIG_PATH: topics-2.json` under `env`, then create `topics-2.json`
next to `topics.json`. In its **Keep this digest** step, change
`name: daily-digest` to `name: daily-digest-2`, so the weekly edition doesn't
mix the two sets. Check in the **Actions** tab that the new workflow is
enabled. The weekly edition covers only the first set.

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
| `deliver_at` | Optional, off unless you add it. A time such as `"08:00"` in your `timezone`. A scheduled run that finishes early waits until then to send. [Delivery time](#delivery-time) explains how to use it. |
| `fact_of_the_day` | `false` removes the fact from the top of the email. |
| `collapsible_stories` | `false` sends every story fully open. |

A mistake in `topics.json` stops the run with a message that says what to fix.

### Delivery time

The schedule is set in
[.github/workflows/daily-digest.yml](.github/workflows/daily-digest.yml):

```yaml
- cron: "17 8 * * *"
  timezone: "Australia/Sydney"
```

[Step 5](#5-set-your-time-zone-and-delivery-time) covers changing it. Keep
`timezone` here and in `topics.json` the same, so the date in the email matches
the day it's sent. GitHub adjusts for daylight saving, and
[crontab.guru](https://crontab.guru) helps with cron syntax.

**The email can arrive hours after the time you set.** GitHub starts a
scheduled run when it has room, not at the time written in the file. Across
this project's first 18 scheduled runs, from 15 September to 2 October 2026,
every run started between 110 and 208 minutes late, and the delay was growing.
Each run then took only 3 to 11 minutes, so the time is lost before the script
starts. GitHub's documentation says scheduled runs are delayed at busy times,
and that the start of each hour is the busiest. The exact cause here is still
being tested. [When the email arrives](HOW-IT-WORKS.md#when-the-email-arrives)
has the figures and what has been tried.

Each run's log says how late it started and when it sent. Look for the line
that begins `Timing:` near the end. Runs you start yourself from the **Actions**
tab have no scheduled time, so they show no lateness.

To get the email at the same time each day, start the run earlier and let it
wait:

1. In the workflow, set the schedule earlier than you want the email. Allow
   the longest start delay you see in the logs plus half an hour for the run
   itself.
2. In `topics.json`, add `"deliver_at": "08:00"` to `settings`, using the time
   you want the email, in your `timezone`.

A run that finishes before `deliver_at` waits and then sends. A run that
finishes after it sends at once and logs how late it was. For example, with
`cron: "0 5 * * *"` and `"deliver_at": "08:00"`, the run starts some time after
5:00, writes the briefings, and sends at 8:00 if it's ready by then.

Things to know:

- **The wait is capped at 3 hours.** A run that is ready more than 3 hours
  before `deliver_at` sends after 3 hours, which is early, and says so in the
  log. Starts that are hours late rarely reach the cap, so a schedule up to
  about 4 hours before `deliver_at` can work.
- **The wait uses Actions minutes.** It's free in a public repo. In a private
  repo it counts against your plan's monthly minutes, and a run that waits an
  hour a day uses about 30 hours a month.
- **Manual runs never wait.** **Run workflow** sends as soon as the digest is
  ready, whatever `deliver_at` says.
- **The news is as old as the start, not the delivery time.** A run that starts
  at 5:00 and sends at 8:00 holds stories from before 5:00.
- **The window follows the schedule.** With `deliver_at` set, `lookback_hours`
  counts back from the scheduled start and not the real one, so a day with a late
  start doesn't leave a gap after the day before.
- **The job's time limit is 215 minutes,** which is the longest run plus the
  longest wait. Raise it if you raise the cap.

### The weekly edition

Every Sunday a second email reviews the week in each topic. It has:

- **The week in brief:** three or four sentences on what mattered most.
- **Threads:** the stories that ran on two or more days. Each one says how the
  story started, what changed and where it stands, and names the days.
- **Also this week:** one line each for stories that came up on one day only.

The weekly email is written from the week's daily emails, not from the feeds.
Each daily run keeps a copy of its digest for 14 days. The **Weekly News
Digest** workflow starts each time a scheduled daily run finishes. It reads
those copies and sends on the day you set. On the other days it stops after a
minute and sends nothing.

The settings go in the `weekly` block at the end of `topics.json`:

```json
"weekly": {
  "enabled": true,
  "day": "Sunday",
  "max_threads": 3
}
```

| Setting | What it does |
|---|---|
| `enabled` | `false` turns the weekly email off. Without the block it's off. |
| `day` | The day it's sent, such as `"Saturday"`. The day is in your `timezone`. |
| `max_threads` | The most threads each topic can have. The default is 3. |
| `email_subject_prefix` | Optional. Text before the dates in the subject line. The default is `Weekly Digest`. |
| `deliver_at` | Optional. A time such as `"10:00"`. The weekly run waits until then to send, as the daily one does. |
| `skip_daily` | Optional. `true` sends no daily email on the weekly day, so you get one email that morning. That day's stories still go into the weekly. |

Things to know:

- **Missing days are stated.** The email says how many of the 7 days it
  covers and names the missing ones. A day is missing when its daily run
  failed or saved nothing. With no days at all, no email is sent, and the log
  says why.
- **It covers the 7 days that end on the send day.** In a new fork, the first
  editions cover fewer days.
- **It adds nothing new.** The model may only use what the daily emails said.
  The links come from the saved daily stories, so they can't be made up.
- **When no model answers,** each topic lists the week's headings by day.
- **To send it now,** open **Weekly News Digest** in the **Actions** tab and
  click **Run workflow**. With **Send this week's edition now** ticked, it
  sends on any day. A run by hand never waits for `deliver_at`.
- **Reruns don't send it twice.** Only the first attempt of a scheduled daily
  run starts the weekly. A daily run you start by hand doesn't start it, but
  its digest is still read.
- **It uses the same free allowance** as the daily email, from the same
  Gemini key. In a private repo it uses about a minute of Actions time each
  day, and more on the weekly day.

### Chat channels

The digest can go to Telegram, Discord or Slack, as well as or in place of
email. Add a `channels` list to `topics.json`, for example:

```json
"channels": [
  {"type": "email"},
  {"type": "telegram"},
  {"type": "discord", "editions": ["weekly"]},
  {"type": "slack", "story_text": true}
]
```

Without the list, the digest goes by email only. With it, the digest goes to
each channel listed, and by email only if `email` is listed.

| Key | What it does | Default |
|---|---|---|
| `type` | `email`, `telegram`, `discord` or `slack` | Required |
| `editions` | `["daily"]`, `["weekly"]` or both | Both |
| `story_text` | Adds each story's text under its heading in a chat | `false` |

A chat gets one message per topic: the overview, then each story's heading
linked to its first source. Chat apps can't fold stories, so the story text
is left out unless `story_text` is on. A topic too long for one message is
split between stories. The fact of the day comes last.

Each channel is sent to on its own. If one fails, the others still get the
digest, and the log says which one failed. The run only shows a red cross if
every channel failed.

#### Telegram

1. In Telegram, message **@BotFather**, send `/newbot` and follow the steps.
   It gives you a token.
2. Open a chat with your new bot and send it any message. To use a group,
   add the bot to the group and send a message there.
3. Open `https://api.telegram.org/bot<token>/getUpdates` in a browser, with
   your token in place of `<token>`. Copy the number after `"chat":{"id":`.
   A group's number starts with a minus sign.
4. Add the token as the `TELEGRAM_BOT_TOKEN` secret and the number as
   `TELEGRAM_CHAT_ID`.

#### Discord

1. In the Discord channel's settings, open **Integrations > Webhooks** and
   click **New Webhook**.
2. Click **Copy Webhook URL** and add it as the `DISCORD_WEBHOOK_URL` secret.

Everyone who can read the channel sees the digest.

#### Slack

1. At [api.slack.com/apps](https://api.slack.com/apps), click **Create New
   App**, choose **From scratch** and pick your workspace.
2. Open **Incoming Webhooks** and turn it on. Click **Add New Webhook to
   Workspace** and pick a channel.
3. Copy the webhook URL and add it as the `SLACK_WEBHOOK_URL` secret.

Everyone in the channel sees the digest.

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
comma-separated list under `env` in the workflow's **Run digest script** step. `GEMINI_MODEL`
puts one model at the front and keeps the defaults behind it.

## Troubleshooting

Start with the log of the run. In the **Actions** tab, click the run, then
**send-digest**, then the **Run digest script** step. Most problems are
reported there.

### No email arrived

If the run shows a red cross, open its log. If it succeeded, check the spam
folder of `RECIPIENT_EMAIL`. A wrong App Password or a rejected Gmail sign-in
is reported at the end of the log.

### A chat channel got nothing

The log has a line such as `[error] telegram failed:` with the reason.

- `missing required env vars: ... (the telegram channel needs them)`: a
  secret for a listed channel isn't set, so the run stopped before it began.
- Telegram `401: Unauthorized`: the token is wrong. `400: Bad Request: chat
  not found`: the chat id is wrong, or nobody has sent the bot a message yet.
- Discord `404: Unknown Webhook`: the webhook was deleted. Make a new one.
- Slack `404: no_service` or `403`: the webhook was removed or the app was
  uninstalled. Add a new webhook.

### It worked once but doesn't run every morning

In the **Actions** tab, click **Daily News Digest**. If a banner says the
workflow is disabled, click **Enable workflow**. GitHub disables scheduled
workflows in new forks, and in public repos after 60 days without activity,
which [step 7](#7-let-the-keepalive-job-push) prevents.

### It arrives hours after the time I set

GitHub starts scheduled runs late, by up to a few hours. The run's log shows how
late in its `Timing:` line, and [delivery time](#delivery-time) explains how to
have the email arrive at a set time.

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

### No weekly email arrived

In the **Actions** tab, click **Weekly News Digest**. If a banner says the
workflow is disabled, click **Enable workflow**. Open the latest run. If it
was skipped, the daily run before it was started by hand or was a rerun.
Otherwise open the **Run weekly edition** step:

- `Nothing to send` means today isn't the `day` in the `weekly` block. Check
  the day and your `timezone`.
- `The weekly edition is off` means `enabled` isn't `true`.
- `no saved daily digest` means no daily copy was found. Open the
  **Download the saved daily digests** step. It lists each file it found and
  warns about each one it couldn't download.

### The weekly email covers fewer than 7 days

A day is missing when its daily run failed or saved nothing. Look at the daily runs for the missing days in the **Actions** tab.

### Gemini returns 429

The free rate limit was hit. The script waits and tries again. If you have a
lot of topics, raise the `time.sleep(2)` between topics in `main()` in
`digest.py`. A log line starting `429 over` names the limit that was passed,
such as a per-minute or a per-day one.

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

### The log says `out of time budget`

Writing the briefings took longer than the 15-minute model budget, so the
topics left over were sent as headlines and the fact was skipped. Further up
the log, lines ending in `answered in Ns` show how long each model took, and
warnings show any retries or errors. A single slow day is usually Google's
servers being busy. If it keeps happening, remove a topic or add a
[backup model](#a-backup-model).

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

Each run keeps a copy of its digest as an artifact for 14 days. Anyone signed
in to GitHub can download artifacts from a public repo. The copy holds only
what the email showed, with no address or key. See
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#what-each-run-keeps). The weekly workflow
reads these copies with the run's own token, which can read Actions data but
can't change the repo. It keeps nothing of its own.

A bot token and a webhook address are secrets too. Anyone who has one can post
to your chat or channel as the digest. The log hides them, even inside error
messages. To revoke one, send `/revoke` to @BotFather, or delete the webhook.

Treat the App Password like your Google password, because it can sign in to
your account through mail apps. You can revoke it from your Google Account at
any time without changing your main password. A second Gmail account used only
for sending keeps your main account out of this entirely.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md)
covers running the tests, adding a model provider and adding a channel.

## License

[MIT](LICENSE)
