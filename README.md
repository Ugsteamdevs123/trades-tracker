# trades-tracker

Automated **politician portfolio tracking** for U.S. congressional stock disclosures: it pulls data from the Quiver Quantitative API, rebuilds per-politician portfolios, tracks a Congress Buys–style strategy, detects changes against saved JSON snapshots, writes **PDF reports** (full portfolio and changes-only), and emails via the **SendGrid Web API** over HTTPS (port 443).

## Email behavior

- **Change alert:** sent only when a position or Congress Buys strategy weight change is detected **and** a non-empty prior snapshot exists for that scope (first run after deploy is **bootstrap** — snapshots are written, but no alert email so every ticker is not reported as “new”).
- **Daily digest:** at most **once per UTC calendar day**, on the first main cron run **on or after** `DAILY_DIGEST_HOUR_UTC` (default `8`), a separate email attaches the **full portfolio** PDF.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
# Install dependencies only with the venv activated (so packages land in .venv).
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Quiver token, CSRF token, SendGrid key, and email addresses
python politician_tracker.py
```

**Export both PDFs from a live run without sending email** (full portfolio + all detected diffs, including bootstrap rows):

```bash
source .venv/bin/activate
python politician_tracker.py --pdf-only
```

**Rebaseline snapshots + client PDF package (no email):** archives existing `*_snapshot.json` files under `snapshots/archive/pre_reset_<UTC>/`, deletes the live baselines, fetches fresh data, writes **both** PDFs (the changes PDF will list the full bootstrap diff — e.g. all `NEW POSITION` / `NEW IN STRATEGY` rows), then saves **new** JSON snapshots for future comparisons.

```bash
source .venv/bin/activate
python politician_tracker.py --reset-snapshots
```

**Preview PDF layout without API keys** (writes static sample data to `snapshots/`):

```bash
source .venv/bin/activate
python scripts/generate_sample_pdfs.py
# Open snapshots/sample_full_portfolio.pdf and snapshots/sample_position_changes.pdf
```

Standalone **daily digest** (same full PDF + digest email; updates snapshots). Use for a dedicated Railway cron or local testing:

```bash
python politician_tracker.py --digest
```

The legacy flag `--summary` is an alias for `--digest`.

## Environment variables

| Variable | Description |
|----------|-------------|
| `QUIVER_API_TOKEN` | Quiver Quantitative API token (Bearer-style `Token` auth). |
| `QUIVER_CSRF_TOKEN` | Value for the `X-CSRFToken` header on Quiver API requests. |
| `SENDGRID_API_KEY` | SendGrid API key with Mail Send; used as `Authorization: Bearer` for the v3 mail send API. |
| `SENDER_EMAIL` | From address; must be a verified sender in SendGrid. |
| `RECIPIENT_EMAILS` | Comma-separated list of recipient addresses. |
| `CC_EMAILS` | Optional comma-separated CC addresses. |
| `BCC_EMAILS` | Optional comma-separated BCC addresses. |
| `DAILY_DIGEST_HOUR_UTC` | Hour `0`–`23` (UTC). After this hour, the **main** job may send the daily digest if it has not already been sent that UTC date (default `8`). |
| `SKIP_DIGEST_IN_MAIN_CRON` | If `1` / `true` / `yes`, the main job **never** sends the digest (use when a second service runs `--digest` only). |

Runtime artifacts (JSON snapshots, `last_digest_date.txt`, generated PDFs) live under `snapshots/` and are git-ignored.

## Railway deployment

1. Create a project and deploy from this GitHub repository.
2. **Do not** rely on committing `.env` — set the same keys in the Railway service **Variables** tab (they become real OS environment variables at runtime), for example: `QUIVER_API_TOKEN`, `QUIVER_CSRF_TOKEN`, `SENDGRID_API_KEY`, `SENDER_EMAIL`, `RECIPIENT_EMAILS`, and optionally `CC_EMAILS`, `DAILY_DIGEST_HOUR_UTC`, `SKIP_DIGEST_IN_MAIN_CRON`.
3. Config-as-code: [`railway.toml`](railway.toml) sets the start command and cron schedule; this overrides dashboard values for deploy settings.
4. Mount a **Volume** on the service at the working directory path used for `snapshots/` (or the app root) so JSON snapshots and `last_digest_date.txt` survive between cron runs; otherwise change detection and digest de-duplication reset on every deploy.

### Cron schedule

- `cronSchedule` in `railway.toml` is a standard five-field crontab; **Railway evaluates all schedules in UTC**.
- The default is `0 */4 * * *` (every four hours at minute 0). Adjust for how quickly you want change alerts after new disclosures.
- Minimum allowed interval on Railway is **5 minutes**.
- If a run is still active when the next trigger fires, Railway **skips** the overlapping run. The script ends with `sys.exit(0)` so the process exits cleanly.

### Optional second service (digest-only)

To send the daily digest from a **separate** cron (e.g. `0 8 * * *`) and keep the main job focused on change checks:

- **Start command:** `python politician_tracker.py --digest`
- **Cron schedule:** e.g. `0 8 * * *` (08:00 UTC daily)
- **Restart policy:** `NEVER`

On the **frequent-check** service, set `SKIP_DIGEST_IN_MAIN_CRON=1` so only one digest email is sent per day.

## SendGrid (Web API)

- The app calls `POST https://api.sendgrid.com/v3/mail/send` over **HTTPS (443)** (no SMTP).
- Use a key with **Mail Send** permissions; set it as `SENDGRID_API_KEY` in the environment.
- Complete sender verification in the SendGrid dashboard before production runs.
