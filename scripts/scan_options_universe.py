"""
Options Universe & Index Options Observation Scanner Script.
Scans configured target symbols (SPY, QQQ, IWM, SPX, SPXW, XSP, DJX, VIX, VIXW),
evaluates market regime, IV rank, instrument metadata, strategy eligibility, candidate discovery,
opportunity quality scoring, and prints machine-readable telemetry.
STRICTLY READ-ONLY / OBSERVATIONAL.
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from trading_agent.data.alpaca_data import AlpacaDataFetcher
from trading_agent.strategy.regime import detect_market_regime
from trading_agent.strategy.strategy_selector import StrategySelector
from trading_agent.strategy.option_universe import get_instrument_metadata, is_index_option, is_volatility_option

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("scan_options_universe")


def scan_universe(symbols: list):
    logger.info("============================================================")
    logger.info("OPTIONS UNIVERSE & INDEX OPTIONS OBSERVATION SCANNER")
    logger.info("============================================================")
    logger.info(f"Target Symbols: {', '.join(symbols)}")
    logger.info(f"Timestamp:      {datetime.now(timezone.utc).isoformat()}")
    logger.info("============================================================")

    fetcher = AlpacaDataFetcher()
    selector = StrategySelector()

    summary_table = []

    for sym in symbols:
        meta = get_instrument_metadata(sym)
        logger.info(f"\n--- Scanning Symbol: {sym} ({meta.description}) ---")
        logger.info(f"Instrument Type: {meta.instrument_type} | Exercise: {meta.exercise_style} | Settlement: {meta.settlement_type}")

        # 1. Fetch bars & quote
        try:
            bars = fetcher.get_recent_bars(sym, timeframe="1Day", limit=30)
            quote = fetcher.get_underlying_quote(sym)
            price = quote.get("last", 100.0) if quote else 100.0
        except Exception as e:
            logger.warning(f"Market data fetch failed for {sym} (DATA_UNAVAILABLE): {e}")
            summary_table.append((sym, meta.instrument_type, "DATA_UNAVAILABLE", 0.0, "NO_TRADE", "DATA_UNAVAILABLE"))
            continue

        # 2. Detect regime
        regime = detect_market_regime(bars, iv_rank=25.0)

        # 3. Fetch option chain
        try:
            chain = fetcher.get_option_chain(sym)
        except Exception as e:
            logger.warning(f"Option chain fetch failed for {sym}: {e}")
            chain = None

        if not chain or not chain.contracts:
            logger.warning(f"No active option contracts for {sym}; fail-closed.")
            summary_table.append((sym, meta.instrument_type, regime.regime_class or "UNKNOWN", regime.iv_rank or 0.0, "NO_TRADE", "OPTION_CHAIN_UNAVAILABLE"))
            continue

        # 4. Evaluate Strategy Selector
        decision = selector.select_strategy(
            market_regime=regime,
            option_chain=chain,
            underlying_price=price,
        )

        why = decision.diagnostics.get("why_no_trade", {})
        reason = why.get("primary_reason", "QUALITY_TIER_NO_TRADE" if decision.decision == "NO_TRADE" else "TRADE_APPROVED")
        strat = decision.selected_strategy or "NONE"
        score = decision.selected_candidate.candidate_score if decision.selected_candidate else 0.0

        logger.info(f"Regime: {regime.regime_class} | IV Rank: {regime.iv_rank}% | Eligible Strats: {regime.eligible_strategies}")
        logger.info(f"Selector Decision: {decision.decision} | Strategy: {strat} | Score: {score} | Reason: {reason}")

        summary_table.append((sym, meta.instrument_type, regime.regime_class or "UNKNOWN", regime.iv_rank or 0.0, decision.decision, reason))

    print("\n\n============================================================")
    print("OPTIONS UNIVERSE SCANNER SUMMARY")
    print("============================================================")
    print(f"{'Symbol':<8} | {'Type':<22} | {'Regime':<12} | {'IV Rank':<8} | {'Decision':<10} | {'Reason':<25}")
    print("-" * 95)
    for sym, itype, reg, iv, dec, reas in summary_table:
        print(f"{sym:<8} | {itype:<22} | {reg:<12} | {iv:<8.1f} | {dec:<10} | {reas:<25}")
    print("============================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan Options Universe & Index Options")
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "IWM", "SPX", "SPXW", "XSP", "DJX", "VIX", "VIXW"], help="Target underlying symbols")
    args = parser.parse_args()
    scan_universe(args.symbols)
