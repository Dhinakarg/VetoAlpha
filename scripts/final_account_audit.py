"""
Final Alpaca Paper Account Audit Tool.
Inspects configured Alpaca paper account, reports equity/cash/buying power, and classifies
the account as DEVELOPMENT ACCOUNT or HACKATHON ACCOUNT. Zero trade execution or balance modification.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from trading_agent.config import settings
from trading_agent.data.alpaca_data import AlpacaDataFetcher


def audit_final_account() -> Dict[str, Any]:
    """Inspect Alpaca Paper Account and classify environment."""
    data_fetcher = AlpacaDataFetcher()

    try:
        account = data_fetcher.get_account_snapshot()
        acc_id = getattr(account, "account_id", "UNKNOWN")
        acc_status = getattr(account, "status", "UNKNOWN")
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        cash = float(getattr(account, "cash", 0.0) or 0.0)
        buying_power = float(getattr(account, "buying_power", 0.0) or 0.0)
    except Exception as e:
        acc_id = "DISCONNECTED"
        acc_status = f"ERROR: {e}"
        equity = 0.0
        cash = 0.0
        buying_power = 0.0

    try:
        positions = data_fetcher.get_open_positions() if hasattr(data_fetcher, "get_open_positions") else []
    except Exception:
        positions = []

    try:
        if data_fetcher._trading_client:
            open_orders = data_fetcher._trading_client.get_orders()
        else:
            open_orders = []
    except Exception:
        open_orders = []

    is_paper = getattr(settings, "alpaca_paper", False)
    h_mode = getattr(settings, "hackathon_mode", False)
    h_id = getattr(settings, "hackathon_account_id", None)

    # Classify account
    is_hackathon_account = False
    if h_mode and h_id and acc_id == h_id and abs(equity - 100000.0) < 1.0:
        is_hackathon_account = True
        account_type = "HACKATHON ACCOUNT"
        req_verified = "VERIFIED"
    else:
        account_type = "DEVELOPMENT ACCOUNT"
        req_verified = "NOT_VERIFIED"

    redacted_id = acc_id[:4] + "***" if len(acc_id) > 4 else acc_id

    report = {
        "account_id": redacted_id,
        "raw_account_id": acc_id,
        "account_status": acc_status,
        "paper_or_live": "PAPER" if is_paper else "LIVE",
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "open_positions_count": len(positions),
        "open_orders_count": len(open_orders),
        "classification": account_type,
        "hackathon_requirement_status": req_verified,
        "hackathon_mode_configured": h_mode,
        "hackathon_account_id_configured": h_id,
    }

    return report


def print_account_audit_report(report: Dict[str, Any]):
    """Format and print account audit report."""
    lines = [
        "=" * 60,
        "FINAL ALPACA ACCOUNT AUDIT REPORT",
        "=" * 60,
        f"Account ID: {report['account_id']}",
        f"Account Status: {report['account_status']}",
        f"Paper/Live: {report['paper_or_live']}",
        f"Equity: ${report['equity']:,.2f}",
        f"Cash: ${report['cash']:,.2f}",
        f"Buying Power: ${report['buying_power']:,.2f}",
        f"Open Positions: {report['open_positions_count']}",
        f"Open Orders: {report['open_orders_count']}",
        "-" * 60,
        f"ACCOUNT CLASSIFICATION: {report['classification']}",
        f"HACKATHON_ACCOUNT_REQUIREMENT: {report['hackathon_requirement_status']}",
        "-" * 60,
    ]

    if report["hackathon_requirement_status"] == "NOT_VERIFIED":
        lines.append("Note: Current account is classified as DEVELOPMENT ACCOUNT.")
        lines.append("To bind a fresh dedicated hackathon account, set environment variables:")
        lines.append("  HACKATHON_MODE=true")
        lines.append("  HACKATHON_ACCOUNT_ID=<brand_new_100k_paper_account_id>")

    lines.append("=" * 60)
    print("\n" + "\n".join(lines) + "\n")


def main():
    report = audit_final_account()
    print_account_audit_report(report)


if __name__ == "__main__":
    main()
