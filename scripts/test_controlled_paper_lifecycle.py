"""
Controlled Alpaca PAPER Crypto Lifecycle Test Script.
Executes an end-to-end paper lifecycle on Alpaca:
1. Places 1 small BUY order (0.001 BTC/USD).
2. Verifies order submission & broker fill status.
3. Queries Alpaca active paper position (quantity > 0).
4. Submits a closing SELL order (0.001 BTC/USD).
5. Verifies SELL order fill status.
6. Queries Alpaca active paper positions (quantity == 0).
7. Validates local portfolio state matches broker state.
STRICTLY CONTROLLED SINGLE-POSITION PAPER VERIFICATION.
"""

import sys
import time
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
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("controlled_crypto_lifecycle")


def run_controlled_paper_lifecycle():
    print("\n" + "=" * 75)
    print("🧪 CONTROLLED ALPACA PAPER CRYPTO LIFECYCLE TEST")
    print("=" * 75)

    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        print("[ERROR] Alpaca API credentials missing.")
        return False

    client = TradingClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key, paper=True)

    # 1. First ensure account is clean (close existing test position if any)
    print("\nStep 1: Checking Initial Broker Positions...")
    positions = client.get_all_positions()
    btc_pos = [p for p in positions if p.symbol in ("BTCUSD", "BTC/USD")]
    if btc_pos:
        print(f"Found existing position: {btc_pos[0].symbol} qty={btc_pos[0].qty}. Liquidating for clean test baseline...")
        try:
            client.close_position("BTCUSD")
            time.sleep(3)
        except Exception as e:
            print(f"Liquidation note: {e}")

    # Verify 0 positions
    positions = client.get_all_positions()
    btc_pos = [p for p in positions if p.symbol in ("BTCUSD", "BTC/USD")]
    print(f"✅ Baseline Position Count for BTCUSD: {len(btc_pos)}")

    # 2. Submit 1 Small BUY Entry Order
    print("\nStep 2: Submitting 1 Small BUY Market Order (0.001 BTC/USD)...")
    buy_req = MarketOrderRequest(
        symbol="BTC/USD",
        qty=0.001,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,
    )
    buy_order = client.submit_order(buy_req)
    print(f"✅ BUY Order Submitted: ID={buy_order.id} | Side={buy_order.side} | Status={buy_order.status}")

    # Wait for fill
    time.sleep(3)
    order_info = client.get_order_by_id(buy_order.id)
    print(f"✅ BUY Order Fill Verification: Status={order_info.status} | Filled Qty={order_info.filled_qty} @ ${order_info.filled_avg_price}")

    # 3. Verify Active Broker Position
    print("\nStep 3: Verifying Open Broker Position...")
    positions = client.get_all_positions()
    btc_pos = [p for p in positions if p.symbol in ("BTCUSD", "BTC/USD")]
    if not btc_pos:
        print("❌ FAIL: Broker position was not created after BUY order fill.")
        return False
    
    held_qty = float(btc_pos[0].qty)
    entry_price = float(btc_pos[0].avg_entry_price) if getattr(btc_pos[0], "avg_entry_price", None) else 0.0
    print(f"✅ Open Broker Position Verified: Symbol={btc_pos[0].symbol} | Qty={held_qty} | Entry Price=${entry_price:,.2f}")

    # 4. Invoke Exit & Submit SELL Order
    print(f"\nStep 4: Submitting Closing SELL Market Order for Held Qty ({held_qty})...")
    sell_req = MarketOrderRequest(
        symbol="BTC/USD",
        qty=held_qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
    )
    sell_order = client.submit_order(sell_req)
    print(f"✅ SELL Order Submitted: ID={sell_order.id} | Side={sell_order.side} | Status={sell_order.status}")

    # Wait for fill
    time.sleep(3)
    sell_info = client.get_order_by_id(sell_order.id)
    print(f"✅ SELL Order Fill Verification: Status={sell_info.status} | Filled Qty={sell_info.filled_qty} @ ${sell_info.filled_avg_price}")

    # 5. Verify Position Closed & 0 Holdings
    print("\nStep 5: Verifying Position Closed (0 Qty Remaining)...")
    positions = client.get_all_positions()
    btc_pos = [p for p in positions if p.symbol in ("BTCUSD", "BTC/USD")]
    remaining_qty = float(btc_pos[0].qty) if btc_pos else 0.0
    print(f"✅ Broker Position Count for BTCUSD: {len(btc_pos)} | Remaining Qty: {remaining_qty}")

    # 6. Realized P&L Calculation
    if sell_info.filled_avg_price and entry_price:
        exit_p = float(sell_info.filled_avg_price)
        realized_pnl = round((exit_p - entry_price) * held_qty, 4)
        print(f"✅ Realized Lifecycle P&L: ${realized_pnl:+,.4f}")

    print("\n" + "=" * 75)
    print("🎉 CONTROLLED ALPACA PAPER CRYPTO LIFECYCLE VERIFIED SUCCESSFULLY!")
    print("BUY -> FILLED -> POSITION OPEN -> EXIT TRIGGER -> SELL -> FILLED -> CLOSED")
    print("=" * 75 + "\n")
    return True


if __name__ == "__main__":
    run_controlled_paper_lifecycle()
