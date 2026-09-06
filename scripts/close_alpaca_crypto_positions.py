"""
Alpaca Paper Crypto Position Liquidation & Reset Script.
Closes all active paper crypto holdings (BTCUSD, ETHUSD, SOLUSD) on Alpaca Paper Account
and cancels all open orders to restore portfolio to clean flat state.
"""

import sys
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from trading_agent.config import settings
from alpaca.trading.client import TradingClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("close_crypto_positions")


def close_all_crypto_positions():
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        print("[ERROR] Alpaca API credentials not configured.")
        return

    client = TradingClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key, paper=True)

    print("\n" + "=" * 65)
    print("ALPACA PAPER CRYPTO POSITION LIQUIDATION & CLEANUP")
    print("=" * 65)

    try:
        positions = client.get_all_positions()
        print(f"Active Paper Positions Found: {len(positions)}")
        for p in positions:
            print(f"  - {p.symbol}: Qty {p.qty} ({p.side}) | Market Value: ${getattr(p, 'market_value', 0.0)}")

        if not positions:
            print("\n✅ Portfolio is already clean (0 open positions).")
            print("=" * 65 + "\n")
            return

        print("\nExecuting liquidation market orders to close all positions...")
        res = client.close_all_positions(cancel_orders=True)
        print(f"✅ Closed all open positions on Alpaca Paper Account ({len(res)} orders placed).")
        print("=" * 65 + "\n")
    except Exception as e:
        print(f"\n[ERROR] Liquidation error: {e}")
        print("Attempting individual symbol liquidations...")
        for sym in ["BTCUSD", "ETHUSD", "SOLUSD", "BTC/USD", "ETH/USD", "SOL/USD"]:
            try:
                client.close_position(sym)
                print(f"✅ Closed position for {sym}")
            except Exception:
                pass
        print("=" * 65 + "\n")


if __name__ == "__main__":
    close_all_crypto_positions()
