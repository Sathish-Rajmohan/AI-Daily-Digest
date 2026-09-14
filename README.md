# Daily News Digest

**A free, fully automated daily email digest — built on GitHub Actions, RSS, and an LLM. No server, no hosting bill, no subscription.**

Every morning, this project pulls the latest stories from RSS feeds you choose, has an LLM pick the most significant ones per topic, writes short neutral summaries, and emails you an HTML digest with a link back to the original article for every story. Topics and sources are fully configurable through one JSON file — no code changes needed to add, remove, or retune what you follow.

> Fork this repo, plug in your own topics and two free API keys, and you have your own personal news briefing running forever at **$0/month**.

---

## How it works

```
GitHub Actions (daily)
  → digest.py
      → topics.json
      → RSS feeds
      → Gemini (rank + summarize)
      → Gmail SMTP
```

| Piece | Role | Cost |
|---|---|---|
| GitHub Actions | Runs the script on a schedule | Free on public repos (private repos use your included Actions minutes) |
| RSS feeds | Article titles, snippets, and canonical URLs | Free |
| Gemini API | Dedupes, ranks, and summarizes per topic | Free tier (check your project's limits in AI Studio) |
| Gmail SMTP | Sends the email | Free |

Every story in the digest links straight back to its original publisher, so anything the summary says is one click away from being checked against the source.

---

## Quickstart

### 1. Fork or clone

```bash
git clone https://github.com/Sathish-Rajmohan/AI-Daily-News-Digest.git
cd AI-Daily-News-Digest
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

The script defaults to `gemini-flash-latest`, Google's alias for the current
Flash model, so you don't have to chase dated model IDs when Google rotates
them. Pin a specific ID with the `GEMINI_MODEL` env var if you want.

One digest run is a handful of requests (one per topic). Exact free-tier RPM/RPD
numbers vary by model and project — check
[AI Studio rate limits](https://aistudio.google.com/rate-limit) for yours. If a
run returns `403 PERMISSION_DENIED`, look at the project status in AI Studio
before assuming the code is broken; some accounts need phone/account
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
Workspace account, Advanced Protection, or security-key-only 2SV — use a
personal Google account with phone/authenticator 2SV instead.

### 4. Repo secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret name | Value |
|---|---|
| `GEMINI_API_KEY` | Key from step 2 |
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
activity for 60 days. A commit or a manual Actions run wakes them back up.

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

Add a topic by copying a block. Delete a block to drop one. Tweak `max_stories`
or the `feeds` array as you like. Push and the next run picks it up.

`settings.lookback_hours` controls how far back feeds are scanned. Default is
`24` so an 8 AM Sydney run covers the previous day without dragging in a lot of
stale overlap. Bump it if Actions delays are eating stories; lower it if the
digest feels repetitive.

### Finding feeds

Search `"site name" RSS feed`, or try `/rss`, `/feed`, `/rss.xml` on the domain.
A browser showing XML with `<item>` or `<entry>` tags means you're good.

The stock config covers Tech & AI, International News, Geopolitics & Security,
Science & Health, and Markets & Business — BBC, Guardian, NYT, FT, WSJ, NPR,
Al Jazeera, ABC Australia, SCMP, Foreign Policy, Foreign Affairs, Defense One,
Nature, WHO, Bloomberg, and a few others. Dead or blocked feeds (notably the
old Reuters and AP public RSS URLs) were left out on purpose.

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

## Swapping the LLM

All Gemini traffic goes through `summarize_topic_with_gemini()` in `digest.py`.
Rewrite that function for Claude, OpenAI, Groq, etc., keep the same return
shape (`[{title, summary, link, source}, ...]`), and leave the rest alone.

---

## Troubleshooting

**No email** — start with the Actions log. Bad keys, App Password mistakes, and
SMTP auth failures show up there.

**Empty topic** — look for `found 0 raw articles`. Feed URL is probably dead;
open it in a browser.

**Gmail login rejected** — use an App Password, not your normal password, and
confirm 2-Step Verification is on.

**Gemini 429** — the script backs off and retries. With many topics, raise the
`time.sleep(2)` between topics in `digest.py`.

**Gemini 403** — account/project issue in AI Studio more often than a code bug.
Check [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).

---

## Security

Keep keys out of the repo. Put them in Actions secrets only.

Forking publicly exposes `topics.json` (your source list). Secrets stay private.

A Gmail App Password is scoped to SMTP for that app label; revoke it anytime
from your Google Account without changing your main password.

## License

[MIT](LICENSE)

## Contributing

Issues and PRs welcome — more topic presets, other LLM backends, Slack/Discord
delivery, whatever fits.
