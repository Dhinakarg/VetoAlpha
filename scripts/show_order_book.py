"""
Alpaca Paper Order Book & Order History Inspector.
Displays total orders placed, order statuses, filled/pending breakdowns,
and a detailed table of recent orders in the Alpaca broker order book.
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
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus


def show_order_book():
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        print("[ERROR] Alpaca API credentials not configured.")
        return

    client = TradingClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key, paper=True)

    print("\n" + "=" * 75)
    print("📖 ALPACA PAPER BROKER ORDER BOOK & ORDER HISTORY")
    print("=" * 75)

    try:
        # Fetch all orders (open, closed, filled, canceled)
        req = GetOrdersRequest(status=QueryOrderStatus.ALL)
        orders = client.get_orders(filter=req)

        total_orders = len(orders)
        filled_orders = [o for o in orders if str(o.status).lower() in ("filled", "orderstatus.filled")]
        open_orders = [o for o in orders if str(o.status).lower() in ("open", "new", "pending_new", "accepted", "orderstatus.new", "orderstatus.accepted")]
        canceled_orders = [o for o in orders if str(o.status).lower() in ("canceled", "rejected", "orderstatus.canceled", "orderstatus.rejected")]

        print(f"Total Orders Placed in Order Book:  {total_orders}")
        print(f"  - Filled / Executed Orders:     {len(filled_orders)}")
        print(f"  - Open / Pending Orders:       {len(open_orders)}")
        print(f"  - Canceled / Rejected Orders:   {len(canceled_orders)}")
        print("-" * 75)

        if orders:
            print(f"\nRECENT 15 ORDERS IN ORDER BOOK:")
            print("-" * 75)
            print(f"{'Order ID':<38} | {'Symbol':<8} | {'Side':<5} | {'Qty':<6} | {'Status':<15}")
            print("-" * 75)
            for o in orders[:15]:
                side_str = str(o.side.value) if hasattr(o.side, "value") else str(o.side)
                status_str = str(o.status.value) if hasattr(o.status, "value") else str(o.status)
                print(f"{str(o.id):<38} | {o.symbol:<8} | {side_str.upper():<5} | {float(o.qty):<6} | {status_str.upper():<15}")
        else:
            print("No orders currently found in the broker order book.")

        print("=" * 75 + "\n")

    except Exception as e:
        print(f"[ERROR] Failed to retrieve order book: {e}")


if __name__ == "__main__":
    show_order_book()
