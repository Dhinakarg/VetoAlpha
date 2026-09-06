"""
Utility script to verify Alpaca Paper Trading connection and query account status.
Performs READ-ONLY operations. NEVER submits an order.
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_agent.config import settings
from trading_agent.data import AlpacaDataFetcher


def main():
    print("=== Alpaca Paper Trading Connection Check ===")
    print(f"Paper Mode: {settings.alpaca_paper}")
    print(f"Base URL: {settings.alpaca_base_url}")
    print(f"API Key Configured: {'YES' if settings.alpaca_api_key else 'NO (Missing)'}")
    print(f"Secret Key Configured: {'YES' if settings.alpaca_secret_key else 'NO (Missing)'}")

    fetcher = AlpacaDataFetcher()

    snapshot = fetcher.get_account_snapshot()
    clock = fetcher.get_market_clock()
    positions = fetcher.get_open_positions()
    orders = fetcher.get_open_orders()

    print("\n--- Account Snapshot ---")
    print(f"Account ID: {snapshot.account_id}")
    print(f"Account Status: {snapshot.status}")
    print(f"Equity: ${snapshot.equity:,.2f}")
    print(f"Cash: ${snapshot.cash:,.2f}")
    print(f"Buying Power: ${snapshot.buying_power:,.2f}")

    print("\n--- Market Clock ---")
    print(f"Is Market Open: {clock.get('is_open')}")
    print(f"Timestamp: {clock.get('timestamp')}")
    print(f"Next Open: {clock.get('next_open')}")
    print(f"Next Close: {clock.get('next_close')}")

    print("\n--- Portfolio Status ---")
    print(f"Number of Open Positions: {len(positions)}")
    print(f"Number of Open Orders: {len(orders)}")
    print("=============================================")
    print("READ-ONLY check complete. Zero orders placed.")


if __name__ == "__main__":
    main()
