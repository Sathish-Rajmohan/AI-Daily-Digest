# Daily News Digest

GitHub Actions cron job that pulls RSS feeds you configure, asks a model to
write one synthesized brief per topic, and emails you the HTML digest. No
server to babysit.

```
GitHub Actions (daily)
  → digest.py
      → topics.json
      → RSS feeds
      → Gemini, or Groq if Gemini is down
      → Gmail SMTP
```

Each email opens with one fact worth knowing, then a section per topic: a
short overview of the day, then a subheading per development with a few
plain-language paragraphs and its sources underneath. Articles covering the
same event are fused into one entry rather than repeated as near-duplicates.
In most mail apps each development shows as a single line you tap to open,
so the email stays short without leaving anything out. Gmail needs a little
setup for this ([step 7](#7-collapsible-stories-in-gmail)), and apps that
can't fold stories at all show every one in full.

Everything runs on free tiers.

| Piece | Role | Cost |
|---|---|---|
| GitHub Actions | Runs the script on a schedule | Free on public repos |
| RSS feeds | Article titles, snippets, canonical URLs | Free |
| Gemini API | Writes the briefings | Free tier |
| Groq API | Optional backstop when Gemini is down | Free tier |
| Gmail SMTP | Sends the email | Free |

**Other docs:** [Feed catalogue](FEEDS.md) for ready-made topics covering
sports, finance, gaming, regions and more · [How it works](HOW-IT-WORKS.md)
for the reasoning behind the design.

---

## Quickstart

Roughly fifteen minutes, most of it waiting on Google's account pages.

### 1. Fork or clone

```bash
git clone https://github.com/Sathish-Rajmohan/AI-Daily-Digest.git
cd AI-Daily-Digest
```

Or fork on GitHub and work from your copy. Public or private both work.
Public repos get unlimited standard Actions minutes; private ones draw from
your plan quota, which is plenty for one short daily job.

### 2. Gemini API key

1. Open [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).
2. Sign in and click **Create API key**.
3. Copy it for step 4.

If a run later returns `403 PERMISSION_DENIED`, check the project status in
AI Studio before assuming the code is broken. Some accounts need phone or
account verification first.

### 3. Gmail App Password

App Passwords let the script use Gmail SMTP without your real password. You
need 2-Step Verification on.

1. Turn on 2-Step Verification at
   [myaccount.google.com/security](https://myaccount.google.com/security).
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Name it something like `daily-digest` and create it.
4. Copy the 16-character password.

If that page says it isn't available, you're usually on a Workspace account,
Advanced Protection, or security-key-only 2SV. Use a personal Google account
with phone or authenticator 2SV instead.

### 4. Repo secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | Key from step 2 |
| `GMAIL_ADDRESS` | Gmail address that owns the App Password |
| `GMAIL_APP_PASSWORD` | 16-character App Password from step 3 |
| `RECIPIENT_EMAIL` | Where the digest goes. Use a different address from `GMAIL_ADDRESS` for collapsible stories ([step 7](#7-collapsible-stories-in-gmail)) |
| `GROQ_API_KEY` | Optional, see [below](#optional-a-backup-model) |

### 5. Enable and test

1. Open the **Actions** tab and enable workflows if GitHub asks.
2. Select **Daily News Digest** → **Run workflow**.
3. Watch the log for per-topic fetch counts and `Sending email... Done.`
4. Check the inbox, and spam the first time.

After one successful manual run, the schedule takes over.

### 6. Allow the keepalive to push

On public repos, GitHub disables scheduled workflows after 60 days with no
repository activity. `.github/workflows/keepalive.yml` prevents that with a
monthly empty commit, but it needs permission to push:

**Settings → Actions → General → Workflow permissions → Read and write permissions**

Leave it on the default read-only setting and the keepalive job fails, which
you'd see in its Actions log. The alternative is pushing a real commit
yourself every couple of months.

### 7. Collapsible stories in Gmail

Stories fold to one line on their own in Apple Mail, iPhone Mail, Yahoo,
Samsung Email, Thunderbird and Fastmail, and probably in Outlook.com and
the Outlook apps for phone and Mac. Tapping a story's line opens its full
write-up and sources. Outlook for Windows, Proton Mail and HEY can't fold
stories, so they show every story in full and nothing is lost.

Gmail is the exception. Its regular view can't fold anything, so it needs
these steps to show the folded version on gmail.com and in the Gmail apps.
Skip them and Gmail shows every story in full.

Gmail only allows this from a sender other than yourself, and only from a
sender you've approved:

1. **Send from a second Gmail address.** Create one (or use one you have),
   turn on 2-Step Verification, and make an App Password for it as in step 3.
   Set `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` to that account, and
   `RECIPIENT_EMAIL` to the address you read.
2. **Approve that sender in the Gmail you read.** On a computer, open
   gmail.com, click the **Settings** gear at the top right, then **See all
   settings**. On the **General** tab:
   - Set **Images** to **Always display external images**. Gmail won't show
     dynamic email otherwise.
   - Find **Dynamic email**, tick **Enable dynamic email**, and click
     **Developer settings** next to it. Enter the sending address and click OK.
   - Click **Save changes** at the bottom of the page.

   **Developer settings** is on the web version, so do this step on a
   computer.
3. Run the workflow again. Tap a story's line to open it.

Gmail shows the collapsible version for 30 days after a digest arrives, then
switches that message to the full version.

---

## Customizing

### Topics and feeds

Edit [topics.json](topics.json). No code changes needed.

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

Copy a block to add a topic, delete one to drop it. `max_stories` caps how
many developments get their own subheading; same-event articles still
collapse into one entry, so it's a ceiling rather than a target.

**[The feed catalogue](FEEDS.md) has ready-made blocks to paste in**, every
URL checked. Broad topics (Sports, Finance, Gaming, US Politics, Climate,
Cybersecurity and more) and niche ones (Soccer, F1, AI research, Personal
finance, Anime, India, Aviation, and about thirty others). It also has a
prompt to hand an AI assistant for anything not listed, and a list of feeds
that are already known dead so you don't waste time on them.

Adding feeds is cheap: articles are taken from each feed in turn, so a new
feed takes a share of the slots rather than a busy one taking over.

### Settings

```json
"settings": {
  "lookback_hours": 24,
  "email_subject_prefix": "Daily Digest",
  "timezone": "Australia/Sydney",
  "fact_of_the_day": true,
  "collapsible_stories": true
}
```

| Setting | Does what |
|---|---|
| `lookback_hours` | How far back feeds are scanned. Raise it if Actions delays eat stories, lower it if the digest repeats itself. |
| `email_subject_prefix` | Subject line before the date. |
| `timezone` | IANA zone used for the date in the email. |
| `fact_of_the_day` | Set `false` to drop the fact block at the top. |
| `collapsible_stories` | Set `false` to send only the full, always-expanded email. |

### Delivery time

Cron lives in [.github/workflows/daily-digest.yml](.github/workflows/daily-digest.yml),
set to 8:00 AM Australia/Sydney:

```yaml
- cron: "0 8 * * *"
  timezone: "Australia/Sydney"
```

GitHub reads `timezone` as an IANA zone, so daylight-saving shifts are
handled for you. [crontab.guru](https://crontab.guru) is useful for checking
the expression.

### Optional: a backup model

Every Gemini model runs on Google's infrastructure, so one bad day there
takes the whole run with it. Adding Groq puts a non-Google model at the
bottom of the chain. It's free, needs no card, and allows 1,000 requests a
day against the five this uses.

1. Open [console.groq.com/keys](https://console.groq.com/keys) and sign in.
2. Create a key and add it as the `GROQ_API_KEY` secret.

Skip this and nothing changes. Groq is left out of the chain when the key
isn't set, and is only reached once every Gemini model has failed. On a day
it does get used the writing reads noticeably different.

Both chains are overridable with `GEMINI_MODELS` and `GROQ_MODELS`
(comma-separated), or `GEMINI_MODEL` to pin a first choice and keep the rest
as fallbacks.

---

## Troubleshooting

**No email:** start with the Actions log. Bad keys, App Password mistakes and
SMTP auth failures all show up there.

**Empty topic:** look for `found 0 raw articles`. The feed URL is probably
dead. Check it against the [known-dead list](FEEDS.md#known-dead).

**Run stops with a `ValueError` about topics.json:** there's a typo in the
file, such as `feeds` written as a single string instead of a list. The
message names the topic and the field to fix.

**Gmail login rejected:** use an App Password, not your normal password, and
confirm 2-Step Verification is on.

**Stories aren't collapsible in Gmail:** check the three things in [step
7](#7-collapsible-stories-in-gmail): `GMAIL_ADDRESS` and `RECIPIENT_EMAIL` are
different addresses, dynamic email is enabled, and the sender is saved under
Developer settings. The Actions log warns if the addresses match or the
collapsible version was too large to send.

**Gemini 429:** the script backs off and retries. With many topics, raise the
`time.sleep(2)` between topics in `digest.py`.

**Gemini 401/403:** an account or project issue in AI Studio more often than
a code bug. Check
[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey). The
run drops Gemini at that point and carries on with Groq if you've set a key,
so a dead Google key degrades the digest rather than ending it.

**Gemini 503:** Google's capacity, not your config. The script retries
briefly, then moves down the chain. Look for `answered by fallback model` in
the log. See [what happens when a model is
down](HOW-IT-WORKS.md#what-happens-when-a-model-is-down).

**Everything failing at once:** each topic falls back to a plain list of
headlines rather than disappearing, and the email still goes out.

**`[Message clipped]` in Gmail:** the email passed ~102KB. Lower
`max_stories`. See [how long the email can
get](HOW-IT-WORKS.md#how-long-the-email-can-get).

**Prose feels dense:** each run logs its average sentence length per topic.
Above 22 words means the instruction isn't landing; see [how the writing is
kept readable](HOW-IT-WORKS.md#how-the-writing-is-kept-readable).

---

## Security

Keep keys out of the repo. Put them in Actions secrets only.

Forking publicly exposes `topics.json`, which is your source list. Secrets
stay private.

A Gmail App Password is scoped to SMTP for that app label. Revoke it any time
from your Google Account without changing your main password.

## License

[MIT](LICENSE)

## Contributing

Issues and PRs welcome: more topic presets, other LLM backends, Slack or
Discord delivery, whatever fits.

### Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite takes a couple of seconds and needs no keys or network access.
HTTP, SMTP and the clock are all faked, so it runs the same offline as it
does anywhere else.
