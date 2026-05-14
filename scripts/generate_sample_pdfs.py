#!/usr/bin/env python3
"""Write sample full + changes PDFs under snapshots/ for local layout review (no API keys)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from pdf_report import build_changes_pdf, build_full_portfolio_pdf  # noqa: E402

SNAPSHOT_DIR = ROOT / "snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)


def main():
    pol = {
        "Nancy Pelosi": pd.DataFrame(
            [
                {
                    "Ticker": "NVDA",
                    "StockName": "NVIDIA Corporation",
                    "EstimatedValue": 250000,
                    "PortfolioPct": 42.5,
                    "LastAction": "Purchase",
                    "LastTradeDate": pd.Timestamp("2025-11-01"),
                },
                {
                    "Ticker": "AAPL",
                    "StockName": "Apple Inc.",
                    "EstimatedValue": 180000,
                    "PortfolioPct": 30.6,
                    "LastAction": "Purchase",
                    "LastTradeDate": pd.Timestamp("2025-10-15"),
                },
                {
                    "Ticker": "LONG",
                    "StockName": (
                        "PURCHASED 50 CALL OPTIONS WITH A STRIKE PRICE OF $120 AND AN "
                        "EXPIRATION DATE OF 12/20/2025 (ESTIMATED HOLDING: $50,000–$100,000) "
                        "— disclosure text can be very long and should wrap within the Stock column."
                    ),
                    "EstimatedValue": 75000,
                    "PortfolioPct": 12.7,
                    "LastAction": "Purchase",
                    "LastTradeDate": float("nan"),
                },
            ]
        ),
        "Daniel Meuser": pd.DataFrame(
            [
                {
                    "Ticker": "MSFT",
                    "StockName": "Microsoft Corporation",
                    "EstimatedValue": 95000,
                    "PortfolioPct": 55.0,
                    "LastAction": "Sale",
                    "LastTradeDate": pd.Timestamp("2025-09-20"),
                },
            ]
        ),
    }
    meta = {
        "Nancy Pelosi": {"party": "Democratic", "chamber": "House of Representatives"},
        "Daniel Meuser": {"party": "Republican", "chamber": "House of Representatives"},
    }
    cb = pd.DataFrame(
        [
            {
                "Ticker": "GOOGL",
                "StockName": "Alphabet Inc.",
                "TotalPurchaseAmount": 1_200_000,
                "PortfolioPct": 12.4,
                "TradeCount": 45,
                "UniqueMembers": 18,
                "LastPurchaseDate": pd.Timestamp("2025-12-01"),
            },
            {
                "Ticker": "META",
                "StockName": "Meta Platforms Inc.",
                "TotalPurchaseAmount": 980_000,
                "PortfolioPct": 10.1,
                "TradeCount": 32,
                "UniqueMembers": 14,
                "LastPurchaseDate": pd.Timestamp("2025-11-28"),
            },
        ]
    )
    full_path = SNAPSHOT_DIR / "sample_full_portfolio.pdf"
    build_full_portfolio_pdf(pol, meta, cb, full_path)

    changes = [
        (
            "Nancy Pelosi",
            [
                {
                    "ticker": "NVDA",
                    "type": "INCREASED",
                    "old_pct": 38.0,
                    "new_pct": 42.5,
                    "value": 250000,
                    "date": "2025-11-01",
                },
            ],
        ),
        (
            "Congress Buys Strategy",
            [
                {
                    "ticker": "GOOGL",
                    "type": "WEIGHT INCREASED",
                    "old_pct": 10.0,
                    "new_pct": 12.4,
                    "date": "2025-12-01",
                },
            ],
        ),
    ]
    ch_path = SNAPSHOT_DIR / "sample_position_changes.pdf"
    build_changes_pdf(changes, ch_path)

    print(f"Wrote: {full_path}")
    print(f"Wrote: {ch_path}")


if __name__ == "__main__":
    main()
