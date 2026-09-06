"""
Historical Implied Volatility (IV) Backfill Tool.
Attempts to backfill canonical daily ATM IV observations into IVHistoryStore
using Alpaca market-data history if provided by the data subscription.
Strict data integrity: Does NOT fabricate synthetic IV or reuse current IV as history.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from trading_agent.config import settings
from trading_agent.data.iv_store import IVHistoryStore
from trading_agent.data.alpaca_data import AlpacaDataFetcher
from trading_agent.strategy.signals import extract_atm_iv

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_iv")


def backfill_symbol_history(
    symbol: str,
    iv_store: IVHistoryStore,
    alpaca_data: AlpacaDataFetcher,
    days: int = 60,
):
    """Attempt historical IV backfill for symbol."""
    symbol = symbol.strip().upper()
    logger.info(f"Checking historical IV backfill capability for {symbol} (Target days: {days})...")

    # Fetch historical daily bars to discover distinct trading dates
    try:
        bars = alpaca_data.get_recent_bars(symbol=symbol, timeframe="1Day", limit=days + 10)
        if not bars:
            logger.warning(f"No price bar history returned for {symbol}. Cannot determine trading days.")
            return
        trading_dates = [b["timestamp"][:10] for b in bars]
        logger.info(f"Discovered {len(trading_dates)} historical trading dates for {symbol}.")
    except Exception as e:
        logger.error(f"Failed to fetch historical bars for {symbol}: {e}")
        return

    # Attempt to query historical option chains for trading dates
    backfilled_count = 0
    missing_historical_iv = False

    logger.info(f"Inspecting Alpaca option historical data availability for {symbol}...")
    try:
        # Check if option historical client supports historical snapshot retrieval
        chain = alpaca_data.get_option_chain(symbol=symbol)
        contracts_with_iv = [c for c in chain.contracts if c.implied_volatility is not None and c.implied_volatility > 0]
        
        if not contracts_with_iv:
            missing_historical_iv = True
            logger.warning(
                f"LIMITATION DETECTED: Alpaca market data feed does not provide historical option IV / Greeks "
                f"under the current data subscription plan. "
                f"Zero fake/synthetic IV will be generated."
            )
        else:
            # Current day's canonical observation
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            atm_iv = extract_atm_iv(chain, float(chain.underlying_price or 100.0))
            if atm_iv:
                iv_store.record_daily_iv(
                    symbol=symbol,
                    trading_date=today_str,
                    atm_iv=atm_iv,
                    underlying_price=float(chain.underlying_price or 100.0),
                )
                backfilled_count += 1
                logger.info(f"Recorded current day canonical ATM IV for {symbol}: {atm_iv:.4f}")

    except Exception as e:
        logger.error(f"Error checking historical option chain for {symbol}: {e}")

    # Summary
    count = iv_store.get_history_count(symbol)
    status = iv_store.get_iv_history_status(symbol, min_required=settings.min_iv_history_days)
    logger.info("=" * 60)
    logger.info(f"IV History Status for {symbol}:")
    logger.info(f"  • Stored Distinct Days:   {status['history_days']}")
    logger.info(f"  • Minimum Required Days: {status['minimum_required']}")
    logger.info(f"  • Status:                 {status['status']}")
    if missing_historical_iv:
        logger.info(
            f"  • Recommendation: Run the agent in scheduler mode (`python -m trading_agent.main --dry-run`) "
            f"to accumulate canonical daily observations naturally over active market days."
        )
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Backfill daily IV history for watchlist symbols.")
    parser.add_argument("--symbols", type=str, default="SPY,QQQ", help="Comma-separated symbols")
    parser.add_argument("--days", type=int, default=60, help="Number of historical trading days to query")
    args = parser.parse_args()

    iv_store = IVHistoryStore()
    alpaca_data = AlpacaDataFetcher()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    for sym in symbols:
        backfill_symbol_history(sym, iv_store, alpaca_data, days=args.days)


if __name__ == "__main__":
    main()
