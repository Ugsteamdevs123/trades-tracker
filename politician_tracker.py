#!/usr/bin/env python3
"""
Task 5: Automated Politician Portfolio Tracking & Email Notification System
=========================================================================
Fetches congressional trading data from Quiver Quantitative API,
reconstructs portfolios, detects position changes, generates PDF reports
(full portfolio and change-only), and emails via the SendGrid HTTP Web API
(HTTPS / port 443). Change alerts are suppressed on bootstrap (no prior
snapshot). A daily digest email is sent at most once per UTC day after
DAILY_DIGEST_HOUR_UTC; use ``python politician_tracker.py --digest`` for a
standalone digest job on Railway. Use ``python politician_tracker.py --pdf-only``
to write full + changes PDFs from live data without sending email.
Use ``python politician_tracker.py --reset-snapshots`` to archive and remove
saved JSON snapshots, then run a PDF-only pass so the changes PDF lists the
full bootstrap diff; new snapshots are written at the end for future runs.

Requirements:
    pip install -r requirements.txt

Configuration (env vars or .env file):
    QUIVER_API_TOKEN   — Quiver Quantitative API token
    QUIVER_CSRF_TOKEN  — X-CSRFToken for Quiver API requests
    SENDGRID_API_KEY   — SendGrid API key (Bearer token for v3/mail/send)
    SENDER_EMAIL       — From address
    RECIPIENT_EMAILS   — Comma-separated To addresses
    CC_EMAILS          — Optional comma-separated CC (omitted in API if empty)
    BCC_EMAILS         — Optional comma-separated BCC (omitted if empty; deduped vs to/cc)
    DAILY_DIGEST_HOUR_UTC — Hour (0–23) after which the main cron may send the daily
                        digest if not already sent that UTC date (default: 8).
"""

import base64
import json
import os
import shutil
import sys
import logging
import urllib.error
import urllib.request

import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

from pdf_report import build_changes_pdf, build_full_portfolio_pdf

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ============================================================
# TERMINAL STEP PRINTER
# ============================================================
_STEP_COUNTER = [0]

def step(msg: str):
    _STEP_COUNTER[0] += 1
    line = "=" * 60
    print(f"\n{line}")
    print(f"  STEP {_STEP_COUNTER[0]}: {msg}")
    print(f"{line}")

def substep(msg: str):
    print(f"    ✔  {msg}")


# ============================================================
# CONFIGURATION
# ============================================================
QUIVER_API_TOKEN   = os.getenv("QUIVER_API_TOKEN", "")
QUIVER_CSRF_TOKEN  = os.getenv("QUIVER_CSRF_TOKEN", "")
SENDGRID_API_KEY   = os.getenv("SENDGRID_API_KEY", "")
SENDER_EMAIL       = os.getenv("SENDER_EMAIL", "")
RECIPIENT_EMAILS   = [r.strip() for r in os.getenv("RECIPIENT_EMAILS", "").split(",") if r.strip()]
CC_EMAILS          = [r.strip() for r in os.getenv("CC_EMAILS", "").split(",") if r.strip()]
BCC_EMAILS         = [r.strip() for r in os.getenv("BCC_EMAILS", "").split(",") if r.strip()]

POLITICIANS  = ["Nancy Pelosi", "Daniel Meuser"]

# Persists JSON snapshots, digest sentinel, and generated PDFs under snapshots/ (fixed
# filenames, overwritten each run). Mount on a Railway Volume so change detection and the
# daily digest gate survive between cron runs.
SNAPSHOT_DIR = Path("snapshots")
SNAPSHOT_DIR.mkdir(exist_ok=True)
PDF_FULL_PATH = SNAPSHOT_DIR / "congress_portfolio_full.pdf"
PDF_CHANGES_PATH = SNAPSHOT_DIR / "congress_portfolio_changes.pdf"
LAST_DIGEST_PATH = SNAPSHOT_DIR / "last_digest_date.txt"
DAILY_DIGEST_HOUR_UTC = int(os.getenv("DAILY_DIGEST_HOUR_UTC", "8"))
# When using a separate Railway cron with ``python politician_tracker.py --digest``, set
# SKIP_DIGEST_IN_MAIN_CRON=1 on the frequent-check service to avoid duplicate digest emails.
SKIP_DIGEST_IN_MAIN_CRON = os.getenv("SKIP_DIGEST_IN_MAIN_CRON", "").lower() in ("1", "true", "yes")

RANGE_MIDPOINTS = {
    1.0: 500, 9.0: 5000, 15.0: 7500, 1001.0: 8000,
    15001.0: 32500, 50001.0: 75000, 100001.0: 175000,
    250001.0: 375000, 500001.0: 750000, 1000001.0: 3000000,
    5000001.0: 15000000
}
CONGRESS_BUYS_LOOKBACK_DAYS = 365

def quiver_headers() -> dict:
    return {
        "accept": "application/json",
        "Authorization": f"Token {QUIVER_API_TOKEN}",
        "X-CSRFToken": QUIVER_CSRF_TOKEN,
    }

POLITICIAN_META = {
    "Nancy Pelosi":  {"party": "Democratic", "chamber": "House of Representatives"},
    "Daniel Meuser": {"party": "Republican", "chamber": "House of Representatives"}
}


# ============================================================
# MODULE 1: FETCH & RECONSTRUCT PORTFOLIO DATA
# ============================================================
def fetch_trades(politician_name: str) -> pd.DataFrame:
    encoded = politician_name.replace(" ", "%20")
    url = f"https://api.quiverquant.com/beta/bulk/congresstrading?representative={encoded}"
    logger.info(f"Fetching trades for {politician_name}")
    r = requests.get(url, headers=quiver_headers(), timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    substep(f"Fetched {len(df)} trade records for {politician_name}")
    return df


def reconstruct_portfolio(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Trade_Size_USD"] = df["Trade_Size_USD"].astype(float)
    df["Traded"]   = pd.to_datetime(df["Traded"], errors="coerce")
    df["midpoint"] = df["Trade_Size_USD"].map(RANGE_MIDPOINTS).fillna(df["Trade_Size_USD"])

    positions = {}
    for _, row in df.sort_values("Traded").iterrows():
        t = row["Ticker"]
        if t not in positions:
            positions[t] = {"value": 0, "last_date": None, "last_action": None, "name": None}
        if positions[t]["name"] is None:
            positions[t]["name"] = row.get("Description", "")
        if row["Transaction"] == "Purchase":
            positions[t]["value"] += row["midpoint"]
        elif row["Transaction"] in ["Sale", "Exchange"]:
            positions[t]["value"] -= row["midpoint"]
        positions[t]["last_date"]   = row["Traded"]
        positions[t]["last_action"] = row["Transaction"]

    holdings = [
        {"Ticker": t, "StockName": d["name"] or "", "EstimatedValue": d["value"],
         "LastTradeDate": d["last_date"], "LastAction": d["last_action"]}
        for t, d in positions.items() if d["value"] > 0
    ]
    result = pd.DataFrame(holdings)
    if len(result):
        total = result["EstimatedValue"].sum()
        result["PortfolioPct"] = (result["EstimatedValue"] / total * 100).round(2)
        result = result.sort_values("EstimatedValue", ascending=False).reset_index(drop=True)
    return result


# ============================================================
# MODULE 2: POSITION CHANGE MONITORING
# ============================================================
def get_snapshot_path(name: str) -> Path:
    return SNAPSHOT_DIR / f"{name.replace(' ', '_').lower()}_snapshot.json"


def archive_and_remove_snapshots() -> None:
    """
    Copy politician + Congress Buys JSON snapshots into snapshots/archive/<ts>/
    then delete the live files. Next portfolio run compares against empty state
    (bootstrap), then saves fresh snapshots.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    arch = SNAPSHOT_DIR / "archive" / f"pre_reset_{ts}"
    arch.mkdir(parents=True, exist_ok=True)
    paths = [get_snapshot_path(n) for n in POLITICIANS] + [get_congress_buys_snapshot_path()]
    copied = 0
    for p in paths:
        if not p.exists():
            continue
        dest = arch / p.name
        shutil.copy2(p, dest)
        p.unlink()
        copied += 1
        print(f"    ✔  Archived + removed → {p.name} (backup: {dest})")
    if copied == 0:
        print("    ✔  No prior snapshot JSON files found — starting from empty state.")
    else:
        print(f"    Snapshot backup directory: {arch.resolve()}")

def load_previous_snapshot(name: str) -> dict:
    # One file per politician: latest run only (no history); compared to current portfolio
    # for change detection (drives change-alert PDF when a prior non-empty snapshot exists).
    p = get_snapshot_path(name)
    return json.load(open(p)) if p.exists() else {}

def save_snapshot(name: str, portfolio: pd.DataFrame):
    # Overwrites the same JSON each run; next execution loads it as "previous".
    snapshot = {
        row["Ticker"]: {
            "value": row["EstimatedValue"], "pct": row["PortfolioPct"],
            "last_date": row["LastTradeDate"].strftime("%Y-%m-%d") if pd.notna(row["LastTradeDate"]) else "N/A",
            "last_action": row["LastAction"]
        }
        for _, row in portfolio.iterrows()
    }
    json.dump(snapshot, open(get_snapshot_path(name), "w"), indent=2)
    substep(f"Snapshot saved → {get_snapshot_path(name)}")

def _portfolio_row_last_trade_date(row) -> str | None:
    if pd.notna(row.get("LastTradeDate")):
        return row["LastTradeDate"].strftime("%Y-%m-%d")
    return None


def detect_changes(name: str, portfolio: pd.DataFrame) -> list:
    previous = load_previous_snapshot(name)
    curr, prev = set(portfolio["Ticker"]), set(previous)
    changes = []
    for t in curr - prev:
        row = portfolio[portfolio["Ticker"] == t].iloc[0]
        changes.append({"ticker": t, "type": "NEW POSITION",
                        "old_pct": 0, "new_pct": row["PortfolioPct"], "value": row["EstimatedValue"],
                        "date": _portfolio_row_last_trade_date(row)})
    for t in prev - curr:
        prev_row = previous[t]
        closed_d = prev_row.get("last_date")
        d = closed_d if isinstance(closed_d, str) and closed_d != "N/A" else None
        changes.append({"ticker": t, "type": "POSITION CLOSED",
                        "old_pct": prev_row["pct"], "new_pct": 0, "value": 0,
                        "date": d})
    for t in curr & prev:
        row = portfolio[portfolio["Ticker"] == t].iloc[0]
        old, new = previous[t]["pct"], row["PortfolioPct"]
        if abs(new - old) >= 0.5:
            changes.append({"ticker": t, "type": "INCREASED" if new > old else "DECREASED",
                            "old_pct": old, "new_pct": new, "value": row["EstimatedValue"],
                            "date": _portfolio_row_last_trade_date(row)})
    return changes


# ============================================================
# MODULE 3: CONGRESS BUYS STRATEGY
# ============================================================
def fetch_all_congress_trades() -> pd.DataFrame:
    r = requests.get("https://api.quiverquant.com/beta/live/congresstrading",
                     headers=quiver_headers(), timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    substep(f"Fetched {len(df)} Congress trade records")
    return df

def compute_congress_buys_strategy(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["TransactionDate"] = pd.to_datetime(df["TransactionDate"], errors="coerce")
    df["Amount"]   = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)
    df["midpoint"] = df["Amount"].map(RANGE_MIDPOINTS).fillna(df["Amount"])
    cutoff = df["TransactionDate"].max() - pd.Timedelta(days=CONGRESS_BUYS_LOOKBACK_DAYS)
    buys = df[df["Transaction"].str.contains("Purchase", case=False, na=False)
              & (df["TransactionDate"] >= cutoff)]
    s = buys.groupby("Ticker").agg(
        TotalPurchaseAmount=("midpoint",       "sum"),
        TradeCount=         ("midpoint",       "count"),
        UniqueMembers=      ("Representative", "nunique"),
        LastPurchaseDate=   ("TransactionDate","max"),
        StockName=          ("Description",    "first")
    ).reset_index()
    s = s[s["TotalPurchaseAmount"] > 0].sort_values(
        "TotalPurchaseAmount", ascending=False).reset_index(drop=True)
    s["PortfolioPct"] = (s["TotalPurchaseAmount"] / s["TotalPurchaseAmount"].sum() * 100).round(2)
    return s

def get_congress_buys_snapshot_path() -> Path:
    return SNAPSHOT_DIR / "congress_buys_snapshot.json"

def load_congress_buys_snapshot() -> dict:
    # Latest strategy snapshot only; empty dict on first run (or missing file).
    p = get_congress_buys_snapshot_path()
    return json.load(open(p)) if p.exists() else {}

def save_congress_buys_snapshot(strategy: pd.DataFrame):
    # Overwrites congress_buys_snapshot.json; used on the next run for change detection.
    snapshot = {
        row["Ticker"]: {
            "pct": row["PortfolioPct"], "amount": row["TotalPurchaseAmount"],
            "trades": int(row["TradeCount"]), "members": int(row["UniqueMembers"]),
            "last_date": pd.to_datetime(row["LastPurchaseDate"]).strftime("%Y-%m-%d")
                if pd.notna(row["LastPurchaseDate"]) else "N/A"
        }
        for _, row in strategy.iterrows()
    }
    json.dump(snapshot, open(get_congress_buys_snapshot_path(), "w"), indent=2)
    substep(f"Congress Buys snapshot saved → {get_congress_buys_snapshot_path()}")

def _cb_strategy_last_buy_date(row) -> str | None:
    if pd.notna(row.get("LastPurchaseDate")):
        return pd.to_datetime(row["LastPurchaseDate"]).strftime("%Y-%m-%d")
    return None


def detect_congress_buys_changes(strategy: pd.DataFrame) -> list:
    previous = load_congress_buys_snapshot()
    curr, prev = set(strategy["Ticker"]), set(previous)
    changes = []
    for t in curr - prev:
        row = strategy[strategy["Ticker"] == t].iloc[0]
        changes.append({"ticker": t, "type": "NEW IN STRATEGY",
                        "old_pct": 0.0, "new_pct": row["PortfolioPct"],
                        "date": _cb_strategy_last_buy_date(row)})
    for t in prev - curr:
        prev_row = previous.get(t) or {}
        ld = prev_row.get("last_date")
        d = ld if isinstance(ld, str) and ld != "N/A" else None
        changes.append({"ticker": t, "type": "DROPPED FROM STRATEGY",
                        "old_pct": previous[t]["pct"], "new_pct": 0.0, "date": d})
    for t in curr & prev:
        row = strategy[strategy["Ticker"] == t].iloc[0]
        old, new = previous[t]["pct"], row["PortfolioPct"]
        if abs(new - old) >= 0.5:
            changes.append({"ticker": t,
                            "type": "WEIGHT INCREASED" if new > old else "WEIGHT DECREASED",
                            "old_pct": old, "new_pct": new,
                            "date": _cb_strategy_last_buy_date(row)})
    return changes


def has_prior_politician_snapshot(name: str) -> bool:
    """True if a non-empty JSON snapshot exists (bootstrap runs skip change alerts)."""
    p = get_snapshot_path(name)
    if not p.exists():
        return False
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and len(data) > 0
    except (json.JSONDecodeError, OSError):
        return False


def has_prior_congress_buys_snapshot() -> bool:
    p = get_congress_buys_snapshot_path()
    if not p.exists():
        return False
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and len(data) > 0
    except (json.JSONDecodeError, OSError):
        return False


def should_send_daily_digest(now_utc: datetime) -> bool:
    if SKIP_DIGEST_IN_MAIN_CRON:
        return False
    if now_utc.hour < DAILY_DIGEST_HOUR_UTC:
        return False
    today = now_utc.strftime("%Y-%m-%d")
    if not LAST_DIGEST_PATH.exists():
        return True
    try:
        last = LAST_DIGEST_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return True
    return last != today


def mark_digest_sent(date_iso: str):
    LAST_DIGEST_PATH.write_text(date_iso + "\n", encoding="utf-8")


# ============================================================
# MODULE 4: PDF reports (see pdf_report.py)
# ============================================================


# ============================================================
# MODULE 5: EMAIL DELIVERY VIA SENDGRID WEB API
# ============================================================
def _dedupe_to_cc_bcc():
    """Build unique to/cc/bcc lists: same address at most once across all three."""
    to_order, seen = [], set()
    for e in RECIPIENT_EMAILS:
        if not e:
            continue
        k = e.lower()
        if k in seen:
            continue
        seen.add(k)
        to_order.append(e)
    to_lower = {e.lower() for e in to_order}
    to_list = [{"email": e} for e in to_order]

    cc_deduped, cc_seen = [], set()
    for e in CC_EMAILS:
        if not e:
            continue
        k = e.lower()
        if k in to_lower or k in cc_seen:
            continue
        cc_seen.add(k)
        cc_deduped.append(e)
    cc_list_objs = [{"email": e} for e in cc_deduped]
    cc_and_to = to_lower | {e.lower() for e in cc_deduped}

    bcc_deduped, bcc_seen = [], set()
    for e in BCC_EMAILS:
        if not e:
            continue
        k = e.lower()
        if k in cc_and_to or k in bcc_seen:
            continue
        bcc_seen.add(k)
        bcc_deduped.append(e)
    bcc_list_objs = [{"email": e} for e in bcc_deduped]

    return to_list, cc_deduped, cc_list_objs, bcc_deduped, bcc_list_objs


def send_email_sendgrid_pdf(subject: str, html_body: str, pdf_path: Path):
    """Send one PDF attachment via SendGrid Web API (HTTPS port 443). Returns HTTP status or None."""
    if not SENDGRID_API_KEY:
        print("\n  ⚠  SENDGRID_API_KEY missing — skipping email send.")
        return None

    if not RECIPIENT_EMAILS:
        print("\n  ⚠  No recipient emails configured — skipping send.")
        return None

    to_list, cc_deduped, cc_list_objs, bcc_deduped, bcc_list_objs = _dedupe_to_cc_bcc()
    if not to_list:
        print("\n  ⚠  No To addresses after deduplication — skipping send.")
        return None

    personalization = {"to": to_list}
    if cc_list_objs:
        personalization["cc"] = cc_list_objs
    if bcc_list_objs:
        personalization["bcc"] = bcc_list_objs

    payload = json.dumps({
        "personalizations": [personalization],
        "from": {"email": SENDER_EMAIL},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
        "attachments": [{
            "content": base64.b64encode(pdf_path.read_bytes()).decode(),
            "type": "application/pdf",
            "filename": pdf_path.name,
            "disposition": "attachment"
        }]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST"
    )

    print(f"    From       : {SENDER_EMAIL}")
    to_show = [p["email"] for p in to_list]
    print(f"    To         : {', '.join(to_show)}")
    if cc_list_objs:
        print(f"    CC         : {', '.join(cc_deduped)}")
    if bcc_list_objs:
        print(f"    BCC        : {', '.join(bcc_deduped)}")
    print(f"    Subject    : {subject}")
    print(f"    Attachment : {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)")
    print("    Sending via SendGrid Web API (HTTPS)...")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            st = resp.status
            print(f"    ✅ Email sent — HTTP {st}")
            logger.info(f"Email sent via SendGrid Web API — HTTP {st}")
            return st
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"    ❌ SendGrid API error {e.code}: {body[:300]}")
        logger.error(f"SendGrid HTTP {e.code}: {body}")
    except Exception as e:
        print(f"    ❌ Unexpected error: {e}")
        logger.error(f"SendGrid error: {e}", exc_info=True)
    return None


# ============================================================
# MAIN ORCHESTRATION
# ============================================================
def _html_email_shell(title: str, inner_html: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#f5f5f5;margin:0;padding:30px">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;
              overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,0.1)">
    <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);
                color:#fff;padding:30px">
      <h1 style="margin:0;font-size:22px">{title}</h1>
      <p style="margin:8px 0 0;opacity:0.8;font-size:13px">
        Automated Politician Portfolio Monitoring System
      </p>
    </div>
    <div style="padding:30px">
{inner_html}
    </div>
    <div style="padding:15px 30px;background:#1a1a2e;color:#888;
                font-size:11px;text-align:center">
      Automated Portfolio Tracking System | Quiver Quantitative API | SendGrid
    </div>
  </div>
</body></html>"""


def run_portfolio_check(pdf_export: bool = False):
    _STEP_COUNTER[0] = 0
    print("\n" + "█" * 60)
    print("  POLITICIAN PORTFOLIO TRACKING SYSTEM — STARTING RUN")
    if pdf_export:
        print("  (mode: --pdf-only — write PDFs only, no email)")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("█" * 60)

    politician_portfolios: dict = {}
    change_sections: list[tuple[str, list]] = []
    change_sections_export: list[tuple[str, list]] = []
    any_alert = False

    # ── Individual politician portfolios ─────────────────────────────────────
    for politician in POLITICIANS:
        step(f"PROCESSING POLITICIAN: {politician.upper()}")
        try:
            print("    Fetching trade history from Quiver API...")
            trades    = fetch_trades(politician)
            print("    Reconstructing current portfolio...")
            portfolio = reconstruct_portfolio(trades)

            if portfolio.empty:
                print(f"    ⚠  No open positions found for {politician} — skipping.")
                continue

            substep(f"Portfolio reconstructed — {len(portfolio)} open positions")

            prior_pol = has_prior_politician_snapshot(politician)
            print("    Detecting position changes vs. last snapshot...")
            changes = detect_changes(politician, portfolio)
            if changes:
                print(f"    ⚠  {len(changes)} change(s) detected:")
                for c in changes:
                    print(f"         {c['ticker']:6s}  {c['type']}  "
                          f"{c['old_pct']:.2f}% → {c['new_pct']:.2f}%")
                if prior_pol:
                    any_alert = True
                    change_sections.append((politician, changes))
                else:
                    print("    (No prior snapshot — bootstrap; no alert email for these rows.)")
                if pdf_export:
                    change_sections_export.append((politician, changes))
            else:
                substep(f"No portfolio changes detected for {politician}")

            politician_portfolios[politician] = portfolio
            print("    Saving snapshot...")
            save_snapshot(politician, portfolio)

        except requests.exceptions.RequestException as e:
            print(f"    ❌ API error for {politician}: {e}")
            logger.error(f"API error for {politician}: {e}")
        except Exception as e:
            print(f"    ❌ Unexpected error for {politician}: {e}")
            logger.error(f"Error processing {politician}: {e}", exc_info=True)

    # ── Congress Buys Strategy ────────────────────────────────────────────────
    step("PROCESSING CONGRESS BUYS STRATEGY")
    cb_strategy = None
    try:
        print("    Fetching all Congress trade records from Quiver API...")
        cb_trades = fetch_all_congress_trades()
        print("    Computing Congress Buys portfolio (12-month rolling window)...")
        cb_strategy = compute_congress_buys_strategy(cb_trades)
        substep(f"Congress Buys strategy computed — {len(cb_strategy)} holdings")

        prior_cb = has_prior_congress_buys_snapshot()
        print("    Detecting Congress Buys strategy changes...")
        cb_changes = detect_congress_buys_changes(cb_strategy)
        if cb_changes:
            print(f"    ⚠  {len(cb_changes)} strategy change(s) detected:")
            for c in cb_changes:
                print(f"         {c['ticker']:6s}  {c['type']}  "
                      f"{c['old_pct']:.2f}% → {c['new_pct']:.2f}%")
            if prior_cb:
                any_alert = True
                change_sections.append(("Congress Buys Strategy", cb_changes))
            else:
                print("    (No prior Congress Buys snapshot — bootstrap; no alert email.)")
            if pdf_export:
                change_sections_export.append(("Congress Buys Strategy", cb_changes))
        else:
            substep("No strategy changes detected")

        print("    Saving Congress Buys snapshot...")
        save_congress_buys_snapshot(cb_strategy)

    except Exception as e:
        print(f"    ❌ Error processing Congress Buys Strategy: {e}")
        logger.error(f"Error processing Congress Buys Strategy: {e}", exc_info=True)

    now_utc = datetime.now(timezone.utc)
    want_digest = should_send_daily_digest(now_utc) and not pdf_export
    alert_http: int | None = None
    digest_http: int | None = None

    if politician_portfolios and cb_strategy is not None:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d at %H:%M UTC")
        date_iso = now_utc.strftime("%Y-%m-%d")

        if pdf_export:
            step("GENERATING PDFs (--pdf-only, no email)")
            try:
                build_full_portfolio_pdf(
                    politician_portfolios, POLITICIAN_META, cb_strategy, PDF_FULL_PATH
                )
                sz = PDF_FULL_PATH.stat().st_size // 1024
                substep(f"Full PDF saved → {PDF_FULL_PATH.resolve()} ({sz} KB)")
            except Exception as e:
                print(f"    ❌ PDF (full) generation failed: {e}")
                logger.error(f"PDF full error: {e}", exc_info=True)
            try:
                export_sections = change_sections_export
                if not export_sections:
                    export_sections = [
                        (
                            "Status",
                            [
                                {
                                    "ticker": "—",
                                    "type": "No changes detected vs prior snapshot",
                                    "date": None,
                                    "old_pct": 0.0,
                                    "new_pct": 0.0,
                                    "value": None,
                                }
                            ],
                        )
                    ]
                build_changes_pdf(export_sections, PDF_CHANGES_PATH)
                szc = PDF_CHANGES_PATH.stat().st_size // 1024
                substep(f"Changes PDF saved → {PDF_CHANGES_PATH.resolve()} ({szc} KB)")
            except Exception as e:
                print(f"    ❌ PDF (changes) generation failed: {e}")
                logger.error(f"PDF changes error: {e}", exc_info=True)

        elif any_alert and change_sections:
            step("GENERATING CHANGES-ONLY PDF")
            try:
                build_changes_pdf(change_sections, PDF_CHANGES_PATH)
                sz = PDF_CHANGES_PATH.stat().st_size // 1024
                substep(f"Changes PDF saved → {PDF_CHANGES_PATH.resolve()} ({sz} KB)")
            except Exception as e:
                print(f"    ❌ PDF (changes) generation failed: {e}")
                logger.error(f"PDF changes error: {e}", exc_info=True)
            else:
                step("SENDING ALERT EMAIL (PDF: changes only)")
                bullets = "".join(
                    f"<li><strong>{name}</strong>: {len(rows)} row(s)</li>"
                    for name, rows in change_sections
                )
                inner = f"""      <p style="font-size:16px;color:#333;margin-top:0">
        Generated on <strong>{now_str}</strong>.
      </p>
      <p style="font-size:15px;color:#555">
        Open the <strong>attached PDF</strong> for <strong>modified positions and strategy weights only</strong>.
      </p>
      <ul style="color:#555;font-size:15px;line-height:2">{bullets}</ul>"""
                subject = f"ALERT: Position or strategy change — Congress Portfolio — {date_iso}"
                alert_http = send_email_sendgrid_pdf(
                    subject, _html_email_shell("ALERT: Position or strategy change", inner), PDF_CHANGES_PATH
                )

        if want_digest:
            step("GENERATING FULL PORTFOLIO PDF (daily digest)")
            try:
                build_full_portfolio_pdf(
                    politician_portfolios, POLITICIAN_META, cb_strategy, PDF_FULL_PATH
                )
                sz = PDF_FULL_PATH.stat().st_size // 1024
                substep(f"Full PDF saved → {PDF_FULL_PATH.resolve()} ({sz} KB)")
            except Exception as e:
                print(f"    ❌ PDF (full) generation failed: {e}")
                logger.error(f"PDF full error: {e}", exc_info=True)
            else:
                step("SENDING DAILY DIGEST EMAIL (PDF: full portfolio)")
                pol_lines = "".join(
                    f"<li><strong>{n}</strong> — individual stock portfolio</li>" for n in POLITICIANS
                )
                inner = f"""      <p style="font-size:16px;color:#333;margin-top:0">
        Generated on <strong>{now_str}</strong>.
      </p>
      <p style="font-size:15px;color:#555">
        Open the <strong>attached PDF</strong> for the <strong>full report</strong> (all tracked politicians and Congress Buys strategy).
      </p>
      <ul style="color:#555;font-size:15px;line-height:2">{pol_lines}
        <li><strong>Congress Buys Strategy</strong> — weighted by purchase volume</li>
      </ul>"""
                subject = f"Daily portfolio summary — Congress Portfolio — {date_iso}"
                digest_http = send_email_sendgrid_pdf(
                    subject, _html_email_shell("Daily portfolio summary", inner), PDF_FULL_PATH
                )
                if digest_http in (200, 202):
                    mark_digest_sent(date_iso)
                    substep(f"Digest date recorded → {LAST_DIGEST_PATH.name}")
                else:
                    print("    ⚠  Digest email not confirmed sent — last_digest_date not updated.")

        elif not pdf_export and not any_alert and not want_digest:
            substep("No alert (no qualifying changes) and digest not due this run — no emails sent.")

    print("\n--- Run summary ---")
    if PDF_CHANGES_PATH.exists():
        print(f"Changes PDF: {PDF_CHANGES_PATH.resolve()} ({PDF_CHANGES_PATH.stat().st_size // 1024} KB)")
    else:
        print("Changes PDF: (not written this run)")
    if PDF_FULL_PATH.exists():
        print(f"Full PDF:    {PDF_FULL_PATH.resolve()} ({PDF_FULL_PATH.stat().st_size // 1024} KB)")
    else:
        print("Full PDF:    (not written this run)")
    print(f"Alert email:  HTTP {alert_http}" if alert_http is not None else "Alert email:  (not sent)")
    print(f"Digest email: HTTP {digest_http}" if digest_http is not None else "Digest email: (not sent)")

    print("\n" + "█" * 60)
    print("  PORTFOLIO CHECK COMPLETE")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("█" * 60 + "\n")
    logger.info("Portfolio check complete.")
    sys.exit(0)


def run_daily_digest():
    """Standalone digest: refresh snapshots, full PDF, one digest email. Use with e.g. cron ``0 8 * * *``."""
    _STEP_COUNTER[0] = 0
    print("\n" + "█" * 60)
    print("  DAILY DIGEST RUN — STARTING")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("█" * 60)

    politician_portfolios = {}

    for politician in POLITICIANS:
        step(f"PROCESSING POLITICIAN: {politician.upper()}")
        try:
            print("    Fetching trade history...")
            trades    = fetch_trades(politician)
            print("    Reconstructing portfolio...")
            portfolio = reconstruct_portfolio(trades)
            if portfolio.empty:
                print(f"    ⚠  No open positions for {politician} — skipping.")
                continue
            substep(f"{len(portfolio)} open positions")
            politician_portfolios[politician] = portfolio
            print("    Saving snapshot...")
            save_snapshot(politician, portfolio)
        except Exception as e:
            print(f"    ❌ Error for {politician}: {e}")
            logger.error(f"Error for {politician}: {e}", exc_info=True)

    step("PROCESSING CONGRESS BUYS STRATEGY")
    cb_strategy = None
    try:
        print("    Fetching all Congress trade records...")
        cb_trades = fetch_all_congress_trades()
        print("    Computing strategy...")
        cb_strategy = compute_congress_buys_strategy(cb_trades)
        substep(f"{len(cb_strategy)} holdings")
        save_congress_buys_snapshot(cb_strategy)
    except Exception as e:
        print(f"    ❌ Error processing Congress Buys Strategy: {e}")
        logger.error(f"Error processing Congress Buys Strategy: {e}", exc_info=True)

    if politician_portfolios and cb_strategy is not None:
        step("GENERATING FULL PORTFOLIO PDF")
        try:
            build_full_portfolio_pdf(
                politician_portfolios, POLITICIAN_META, cb_strategy, PDF_FULL_PATH
            )
            print(f"    ✅ Full PDF → {PDF_FULL_PATH.resolve()}")
        except Exception as e:
            print(f"    ❌ PDF generation failed: {e}")
            logger.error(f"PDF full error: {e}", exc_info=True)
        else:
            step("SENDING DAILY DIGEST EMAIL")
            now_utc = datetime.now(timezone.utc)
            now_str = now_utc.strftime("%Y-%m-%d at %H:%M UTC")
            date_iso = now_utc.strftime("%Y-%m-%d")
            pol_lines = "".join(
                f"<li><strong>{n}</strong> — individual stock portfolio</li>" for n in POLITICIANS
            )
            inner = f"""      <p style="font-size:16px;color:#333;margin-top:0">
        Generated on <strong>{now_str}</strong>.
      </p>
      <p style="font-size:15px;color:#555">
        Open the <strong>attached PDF</strong> for the <strong>full report</strong>.
      </p>
      <ul style="color:#555;font-size:15px;line-height:2">{pol_lines}
        <li><strong>Congress Buys Strategy</strong> — weighted by purchase volume</li>
      </ul>"""
            subject = f"Daily portfolio summary — Congress Portfolio — {date_iso}"
            st = send_email_sendgrid_pdf(
                subject, _html_email_shell("Daily portfolio summary", inner), PDF_FULL_PATH
            )
            if st in (200, 202):
                mark_digest_sent(date_iso)

    print("\n" + "█" * 60)
    print("  DAILY DIGEST COMPLETE")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("█" * 60 + "\n")
    sys.exit(0)


if __name__ == "__main__":
    args = sys.argv[1:]
    reset = "--reset-snapshots" in args
    pdf_only = "--pdf-only" in args

    if "--digest" in args or "--summary" in args:
        if reset:
            print("\n  ⚠  --reset-snapshots is ignored when used with --digest / --summary.\n")
        run_daily_digest()
    elif reset:
        print("\n" + "=" * 60)
        print("  RESET SNAPSHOTS — archive + delete JSON baselines (then PDF export)")
        print("=" * 60)
        archive_and_remove_snapshots()
        run_portfolio_check(pdf_export=True)
    elif pdf_only:
        run_portfolio_check(pdf_export=True)
    else:
        run_portfolio_check()