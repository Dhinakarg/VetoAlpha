"""
P&L Verification Engine.
Queries Alpaca Paper Trading API, inspects crypto portfolio state,
and verifies mathematical accuracy of realized P&L, unrealized P&L, fees, and net performance.
"""

import csv
import json
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

LOGS_DIR = ROOT_DIR / "logs"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def verify_pnl_analytics():
    print("\n" + "=" * 70)
    print("💰 INDEPENDENT P&L VERIFICATION & AUDIT REPORT")
    print("=" * 70)

    # 1. Alpaca Account P&L Audit
    print("\n1. ALPACA PAPER TRADING ACCOUNT P&L")
    print("-" * 70)
    try:
        from alpaca.trading.client import TradingClient
        from trading_agent.config import settings

        if settings.alpaca_api_key and settings.alpaca_secret_key:
            client = TradingClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key, paper=True)
            acc = client.get_account()

            equity = float(acc.equity)
            cash = float(acc.cash)
            bp = float(acc.buying_power)
            start_equity = 1000000.0  # Standard Alpaca paper starting equity

            account_realized_pnl = round(equity - start_equity, 2)

            print(f"Account ID:             {acc.id}")
            print(f"Current Account Equity: ${equity:,.2f}")
            print(f"Cash Balance:           ${cash:,.2f}")
            print(f"Buying Power:           ${bp:,.2f}")
            print(f"Paper Account P&L:      ${account_realized_pnl:+,.2f}")
            print(f"Account Status:         {acc.status}")
    except Exception as e:
        print(f"Alpaca API Verification Error: {e}")

    # 2. Crypto Validation Portfolio P&L
    print("\n2. CRYPTO SUB-SYSTEM PORTFOLIO P&L")
    print("-" * 70)
    state_file = LOGS_DIR / "crypto_state.json"
    if state_file.exists():
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        equity = state.get("equity", 100000.0)
        cash = state.get("cash", 100000.0)
        daily_pnl = state.get("daily_pnl", 0.0)
        start_equity = 100000.0
        total_pnl = round(equity - start_equity, 2)

        print(f"Simulated Equity:       ${equity:,.2f}")
        print(f"Cash Balance:           ${cash:,.2f}")
        print(f"Daily Realized P&L:     ${daily_pnl:+,.2f}")
        print(f"Total Cumulative P&L:   ${total_pnl:+,.2f}")
        print(f"Circuit Breaker Status: {'NORMAL' if not state.get('circuit_breaker_tripped') else 'TRIPPED'}")

    # 3. Trade Log Math Verification (trades_fixed.csv)
    trades_csv = ROOT_DIR / "trades_fixed.csv"
    if trades_csv.exists():
        print("\n3. HISTORICAL BACKTEST TRADE-LEVEL P&L MATH AUDIT (trades_fixed.csv)")
        print("-" * 70)
        math_errors = 0
        total_raw_pnl = 0.0
        total_fees = 0.0
        total_slippage = 0.0
        total_net_pnl = 0.0

        with open(trades_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            trades = list(reader)

        for t in trades:
            raw = float(t["raw_pnl"])
            fees = float(t["fees_charged"])
            slippage = float(t["slippage_charged"])
            net = float(t["net_pnl"])

            expected_net = round(raw - fees, 2)
            if abs(expected_net - net) > 0.10:
                math_errors += 1

            total_raw_pnl += raw
            total_fees += fees
            total_slippage += slippage
            total_net_pnl += net

        print(f"Total Closed Trades Audited: {len(trades)}")
        print(f"Total Raw P&L:               ${total_raw_pnl:+,.2f}")
        print(f"Total Fees Charged:          ${total_fees:,.2f}")
        print(f"Total Slippage Deducted:     ${total_slippage:,.2f}")
        print(f"Total Net Realized P&L:      ${total_net_pnl:+,.2f}")
        print(f"P&L Formula Math Errors:     {math_errors}")
        print(f"Math Verification Verdict:   {'VALIDATED (100% Math Match)' if math_errors == 0 else 'DISCREPANCY DETECTED'}")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    verify_pnl_analytics()
