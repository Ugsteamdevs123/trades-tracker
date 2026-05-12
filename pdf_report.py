"""ReportLab PDF builders for portfolio full report and change-only digest."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

MISSING_TXT = "No data found from API"

DISCLAIMER_INDIVIDUAL = (
    "Data sourced from STOCK Act congressional financial disclosure filings via "
    "Quiver Quantitative API. Values are estimates based on disclosed dollar ranges. "
    "Not investment advice."
)
DISCLAIMER_STRATEGY = (
    "Data sourced from STOCK Act filings via Quiver Quantitative API. "
    "Quiver-reported CAGR of 33.01% based on backtest from 2020-04-01. "
    "Past performance does not guarantee future results. Not investment advice."
)

# Margins — match full portfolio and changes PDFs; tight enough to use most of letter width.
_SIDE_MARGIN_IN = 0.38


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _table_usable_width_pt() -> float:
    return letter[0] - 2 * (_SIDE_MARGIN_IN * inch)


def _is_missing(val: Any) -> bool:
    if val is None:
        return True
    try:
        if pd.isna(val):
            return True
    except TypeError:
        pass
    if isinstance(val, str):
        t = val.strip().lower()
        if not t or t in ("nan", "nat", "none"):
            return True
    return False


def _display_str(val: Any) -> str:
    if _is_missing(val):
        return MISSING_TXT
    return str(val).strip()


def _fmt_money(val: Any) -> str:
    if _is_missing(val):
        return MISSING_TXT
    try:
        return f"${float(val):,.0f}"
    except (TypeError, ValueError):
        return MISSING_TXT


def _fmt_pct(val: Any, decimals: int = 2) -> str:
    if _is_missing(val):
        return MISSING_TXT
    try:
        return f"{float(val):.{decimals}f}%"
    except (TypeError, ValueError):
        return MISSING_TXT


def _fmt_int(val: Any) -> str:
    if _is_missing(val):
        return MISSING_TXT
    try:
        return str(int(val))
    except (TypeError, ValueError):
        return MISSING_TXT


def _fmt_date(val: Any) -> str:
    if _is_missing(val):
        return MISSING_TXT
    try:
        ts = pd.to_datetime(val, errors="coerce")
        if pd.isna(ts):
            return MISSING_TXT
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return MISSING_TXT


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    safe = escape(str(text), entities={"'": "&apos;", '"': "&quot;"})
    return Paragraph(safe, style)


def _p_hdr(text: str, style: ParagraphStyle) -> Paragraph:
    """Table header: keep label on one line (no mid-word wrap in narrow columns)."""
    safe = escape(str(text), entities={"'": "&apos;", '"': "&quot;"})
    return Paragraph(f"<nobr>{safe}</nobr>", style)


def _p_center_nobr(text: str, style: ParagraphStyle) -> Paragraph:
    """Centered body cell kept on one line (e.g. ISO dates without hyphen wraps)."""
    safe = escape(str(text), entities={"'": "&apos;", '"': "&quot;"})
    return Paragraph(f"<nobr>{safe}</nobr>", style)


def _styles():
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        name="DocTitle",
        parent=styles["Heading1"],
        fontSize=14,
        spaceAfter=6,
        textColor=colors.HexColor("#1A1A2E"),
    )
    section = ParagraphStyle(
        name="Section",
        parent=styles["Heading2"],
        fontSize=11,
        spaceBefore=12,
        spaceAfter=6,
        textColor=colors.HexColor("#16213E"),
    )
    small = ParagraphStyle(
        name="SmallGrey",
        parent=styles["Normal"],
        fontSize=7,
        textColor=colors.grey,
    )
    hdr = ParagraphStyle(
        name="TblHdr",
        parent=styles["Normal"],
        fontSize=5.5,
        leading=6.5,
        textColor=colors.whitesmoke,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )
    cell_c = ParagraphStyle(
        name="TblCellC",
        parent=styles["Normal"],
        fontSize=7,
        leading=8.5,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#333333"),
        fontName="Helvetica",
    )
    cell_stock = ParagraphStyle(
        name="TblCellStock",
        parent=styles["Normal"],
        fontSize=6,
        leading=7.5,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#333333"),
        fontName="Helvetica",
    )
    cell_notes = ParagraphStyle(
        name="TblCellNotes",
        parent=styles["Normal"],
        fontSize=6.5,
        leading=8,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#333333"),
        fontName="Helvetica",
    )
    cell_tick = ParagraphStyle(
        name="TblCellTick",
        parent=styles["Normal"],
        fontSize=6,
        leading=7,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#333333"),
        fontName="Helvetica",
    )
    return styles, title, section, small, hdr, cell_c, cell_stock, cell_notes, cell_tick


def _table_from_flowables(
    header_paras: list[Paragraph],
    body_rows: list[list[Paragraph]],
    col_widths: list[float],
    *,
    left_body_cols: set[int],
) -> Table:
    data: list[list[Any]] = [header_paras, *body_rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    ncols = len(col_widths)
    nrows = len(data)
    ts: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A1A2E")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 2),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F4F8")]),
    ]
    for c in range(ncols):
        body_align = "LEFT" if c in left_body_cols else "CENTER"
        ts.append(("ALIGN", (c, 0), (c, 0), "CENTER"))
        ts.append(("ALIGN", (c, 1), (c, nrows - 1), body_align))
    t.setStyle(TableStyle(ts))
    return t


def _stock_display(row: pd.Series) -> str:
    """Prefer StockName; fall back to Description when missing or NaN."""
    if hasattr(row, "get"):
        a = row.get("StockName")
        if not _is_missing(a):
            return _display_str(a)
        b = row.get("Description")
        return _display_str(b)
    return MISSING_TXT


def build_full_portfolio_pdf(
    politician_portfolios: dict[str, pd.DataFrame],
    politician_meta: dict[str, dict[str, str]],
    congress_strategy: pd.DataFrame,
    out_path: Path,
) -> bool:
    """Multi-section PDF: each politician + Congress Buys strategy."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _, title_st, section_st, small_st, hdr_st, cell_c, cell_stock, _, cell_tick = _styles()
    usable = _table_usable_width_pt()
    w_tick = 0.58 * inch
    w_est = 0.86 * inch
    w_pct = 0.58 * inch
    w_act = 0.76 * inch
    w_dt = 0.70 * inch
    w_stock_pol = max(0.25 * inch, usable - (w_tick + w_est + w_pct + w_act + w_dt))
    col_pol = [w_tick, w_stock_pol, w_est, w_pct, w_act, w_dt]

    w_tick2 = 0.56 * inch
    w_vol = 0.80 * inch
    w_pct2 = 0.56 * inch
    w_tc = 0.48 * inch
    w_mem = 0.50 * inch
    w_buy = 0.68 * inch
    w_stock_cb = max(0.25 * inch, usable - (w_tick2 + w_vol + w_pct2 + w_tc + w_mem + w_buy))
    col_cb = [w_tick2, w_stock_cb, w_vol, w_pct2, w_tc, w_mem, w_buy]

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        rightMargin=_SIDE_MARGIN_IN * inch,
        leftMargin=_SIDE_MARGIN_IN * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    story: list = []
    story.append(Paragraph("Congress Portfolio Report — Full", title_st))
    story.append(Paragraph(f"Generated {_utc_now_str()}", small_st))
    story.append(Spacer(1, 0.15 * inch))

    for name, portfolio in politician_portfolios.items():
        meta = politician_meta.get(name, {"party": "Unknown", "chamber": "Unknown"})
        story.append(Paragraph(f"{name} — Stock Portfolio", section_st))
        total_v = portfolio["EstimatedValue"].sum()
        total_s = _fmt_money(total_v) if not _is_missing(total_v) else MISSING_TXT
        story.append(
            Paragraph(
                f"{_display_str(meta.get('party'))} | {_display_str(meta.get('chamber'))} | "
                f"Open positions: {len(portfolio)} | "
                f"Total est. value: {total_s}",
                small_st,
            )
        )
        hdr_labels = ["Ticker", "Stock", "Est. $", "% Port.", "Last action", "Last trade"]
        header_paras = [_p_hdr(h, hdr_st) for h in hdr_labels]
        body_rows: list[list[Paragraph]] = []
        for _, row in portfolio.iterrows():
            body_rows.append(
                [
                    _p(_display_str(row.get("Ticker")), cell_tick),
                    _p(_stock_display(row), cell_stock),
                    _p(_fmt_money(row.get("EstimatedValue")), cell_c),
                    _p(_fmt_pct(row.get("PortfolioPct")), cell_c),
                    _p(_display_str(row.get("LastAction")), cell_c),
                    _p(_fmt_date(row.get("LastTradeDate")), cell_c),
                ]
            )
        story.append(_table_from_flowables(header_paras, body_rows, col_pol, left_body_cols={1}))
        story.append(Spacer(1, 0.08 * inch))
        story.append(Paragraph(DISCLAIMER_INDIVIDUAL, small_st))
        story.append(Spacer(1, 0.12 * inch))

    story.append(Paragraph("Congress Buys Strategy — All Holdings", section_st))
    total_vol = congress_strategy["TotalPurchaseAmount"].sum()
    vol_s = _fmt_money(total_vol) if not _is_missing(total_vol) else MISSING_TXT
    story.append(
        Paragraph(
            f"Rolling window holdings: {len(congress_strategy)} | "
            f"Total purchase volume (est.): {vol_s} | "
            f"Quiver backtest CAGR (since 2020-04-01): 33.01%",
            small_st,
        )
    )
    hdr2 = ["Ticker", "Stock", "Est.$ vol.", "% strat.", "Trades", "Members", "Last buy"]
    header2 = [_p_hdr(h, hdr_st) for h in hdr2]
    body2: list[list[Paragraph]] = []
    for _, row in congress_strategy.iterrows():
        body2.append(
            [
                _p(_display_str(row.get("Ticker")), cell_tick),
                _p(_stock_display(row), cell_stock),
                _p(_fmt_money(row.get("TotalPurchaseAmount")), cell_c),
                _p(_fmt_pct(row.get("PortfolioPct")), cell_c),
                _p(_fmt_int(row.get("TradeCount")), cell_c),
                _p(_fmt_int(row.get("UniqueMembers")), cell_c),
                _p(_fmt_date(row.get("LastPurchaseDate")), cell_c),
            ]
        )
    story.append(_table_from_flowables(header2, body2, col_cb, left_body_cols={1}))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Paragraph(DISCLAIMER_STRATEGY, small_st))

    doc.build(story)
    return True


def build_changes_pdf(
    sections: list[tuple[str, list[dict[str, Any]]]],
    out_path: Path,
) -> bool:
    """One PDF with per-section tables of detected changes."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _, title_st, section_st, small_st, hdr_st, cell_c, cell_stock, cell_notes, cell_tick = _styles()
    usable = _table_usable_width_pt()
    w_tick = 0.52 * inch
    # Date: wide enough for YYYY-MM-DD at 7pt + table padding (avoid wrapping on hyphen).
    w_date = 0.80 * inch
    w_date_max = 1.02 * inch
    w_old = 0.50 * inch
    w_new = 0.50 * inch
    fixed_nm = w_tick + w_date + w_old + w_new
    rest = usable - fixed_nm
    w_min_notes = 0.28 * inch
    # Cap Notes so slack is not dumped into the last column; feed overflow into Date / Change.
    w_notes_max = 1.42 * inch
    w_chg = max(1.55 * inch, min(2.15 * inch, rest * 0.40))
    w_notes = usable - w_tick - w_chg - w_date - w_old - w_new
    if w_notes < w_min_notes:
        w_chg = max(1.12 * inch, usable - w_tick - w_date - w_old - w_new - w_min_notes)
        w_notes = usable - w_tick - w_chg - w_date - w_old - w_new
    if w_notes > w_notes_max:
        surplus = w_notes - w_notes_max
        w_notes = w_notes_max
        add_date = min(surplus, max(0.0, w_date_max - w_date))
        w_date += add_date
        w_chg += surplus - add_date
    col_ch = [w_tick, w_chg, w_date, w_old, w_new, w_notes]

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        rightMargin=_SIDE_MARGIN_IN * inch,
        leftMargin=_SIDE_MARGIN_IN * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    story: list = []
    story.append(Paragraph("Congress Portfolio — Position and strategy changes", title_st))
    story.append(Paragraph(f"Generated {_utc_now_str()}", small_st))
    story.append(Spacer(1, 0.15 * inch))

    for section_name, changes in sections:
        if not changes:
            continue
        story.append(Paragraph(section_name, section_st))
        hdr = ["Ticker", "Change", "Date", "Old %", "New %", "Notes"]
        header_paras = [_p_hdr(h, hdr_st) for h in hdr]
        body_rows: list[list[Paragraph]] = []
        for c in changes:
            notes = ""
            if "value" in c and c["value"] is not None and not _is_missing(c["value"]):
                try:
                    v = float(c["value"])
                    notes = f"Est. ${v:,.0f}" if v else ""
                except (TypeError, ValueError):
                    notes = _display_str(c.get("value"))
            if not notes.strip():
                notes = "—"
            old_s = _fmt_pct(c.get("old_pct"))
            new_s = _fmt_pct(c.get("new_pct"))
            date_s = "—" if c.get("date") is None else _fmt_date(c.get("date"))
            date_cell = (
                _p_center_nobr(date_s, cell_c)
                if date_s not in ("—", MISSING_TXT)
                else _p(date_s, cell_c)
            )
            body_rows.append(
                [
                    _p(_display_str(c.get("ticker")), cell_tick),
                    _p(_display_str(c.get("type")), cell_stock),
                    date_cell,
                    _p(old_s, cell_c),
                    _p(new_s, cell_c),
                    _p(notes, cell_notes),
                ]
            )
        story.append(_table_from_flowables(header_paras, body_rows, col_ch, left_body_cols={1, 5}))
        story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph(DISCLAIMER_INDIVIDUAL + " " + DISCLAIMER_STRATEGY, small_st))
    doc.build(story)
    return True
