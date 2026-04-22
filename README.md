# trades-tracker

Automated **politician portfolio tracking** for U.S. congressional stock disclosures: it pulls data from the Quiver Quantitative API, rebuilds per-politician portfolios, tracks a Congress Buys–style strategy, detects changes against saved JSON snapshots, writes a three-sheet Excel report, and emails it over **SendGrid SMTP** (plain `smtplib`, no SendGrid SDK).

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Quiver token, CSRF token, SendGrid key, and email addresses
python politician_tracker.py
```

Weekly summary mode (same report, different email subject line):

```bash
python politician_tracker.py --summary
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `QUIVER_API_TOKEN` | Quiver Quantitative API token (Bearer-style `Token` auth). |
| `QUIVER_CSRF_TOKEN` | Value for the `X-CSRFToken` header on Quiver API requests. |
| `SENDGRID_API_KEY` | SendGrid API key; used as the SMTP password (username is always the literal `apikey`). |
| `SENDER_EMAIL` | From address; must be a verified sender in SendGrid. |
| `RECIPIENT_EMAILS` | Comma-separated list of recipient addresses. |
| `CC_EMAILS`        | Optional comma-separated CC addresses. |

Runtime artifacts (snapshots, generated `.xlsx`) are written under `snapshots/` and are git-ignored.

## Railway deployment

1. Create a project and deploy from this GitHub repository.
2. **Do not** rely on committing `.env` — set the same keys in the Railway service **Variables** tab (they become real OS environment variables at runtime).
3. Config-as-code: [`railway.toml`](railway.toml) sets the start command and cron schedule; this overrides dashboard values for deploy settings.

### Cron schedule

- `cronSchedule` in `railway.toml` is a standard five-field crontab; **Railway evaluates all schedules in UTC**.
- The default is `0 * * * *` (minute 0 of every hour, e.g. 00:00, 01:00, 02:00 UTC).
- Minimum allowed interval on Railway is **5 minutes**; hourly is within that limit.
- If a run is still active when the next trigger fires, Railway **skips** the overlapping run. The script ends with `sys.exit(0)` so the process exits cleanly.

### Weekly summary on Railway

A single `railway.toml` applies to one service. For a second job (e.g. Monday 09:00 UTC weekly email with `--summary`), add **another** service in the same project, point it at the same repo, and set:

- **Start command:** `python politician_tracker.py --summary`
- **Cron schedule:** `0 9 * * 1` (Monday 09:00 UTC)
- **Restart policy:** `NEVER`

## SendGrid (SMTP)

- Host: `smtp.sendgrid.net`, port **587** (STARTTLS).
- SMTP username: **`apikey`** (literal string for all accounts).
- SMTP password: your `SENDGRID_API_KEY`.
- Complete sender verification and API key creation in the SendGrid dashboard before production runs.
