"""
Live Crypto Market Observation & Signal Scanner.
Scans configured crypto assets, classifies market regime, and outputs strategy candidate evaluations.
Zero execution / Read-only observation tool.
"""

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from trading_agent.crypto.crypto_data import CryptoDataFetcher
from trading_agent.crypto.crypto_regime import classify_crypto_regime
from trading_agent.crypto.crypto_selector import CryptoStrategySelector
from trading_agent.crypto.crypto_risk import CryptoRiskGate


def scan_crypto_markets(symbols: list):
    """Scan crypto assets and print deterministic regime & candidate evaluation."""
    fetcher = CryptoDataFetcher()
    selector = CryptoStrategySelector(min_score=65.0)
    risk_gate = CryptoRiskGate()

    print("\n" + "=" * 65)
    print("CRYPTO MARKET OBSERVATION & SIGNAL SCANNER [CRYPTO_VALIDATION_MODE]")
    print("=" * 65)

    for symbol in symbols:
        data = fetcher.fetch_market_data(symbol)
        regime_res = classify_crypto_regime(data)
        decision = selector.evaluate(data, regime_res)

        risk_decision = None
        if decision.action == "trade" and decision.selected_candidate:
            risk_decision = risk_gate.evaluate_candidate(
                candidate=decision.selected_candidate,
                equity=100000.0,
                daily_pnl=0.0,
                open_positions=[],
            )

        print(f"\nAsset:           {symbol}")
        print(f"Price:           ${data.current_price:,.2f}")
        print(f"Regime:          {regime_res.regime.value} (ADX: {data.adx_14 or 0.0:.1f}, RSI: {data.rsi_14 or 0.0:.1f})")
        print(f"Rationale:       {regime_res.rationale}")

        if decision.selected_candidate:
            c = decision.selected_candidate
            print(f"Selected Candidate: {c.strategy} ({c.direction.upper()}) | Score: {decision.score:.1f}/100")
            print(f"  Entry: ${c.entry_price:.2f} | Stop: ${c.stop_loss:.2f} | Take Profit: ${c.take_profit:.2f} | R:R: {c.risk_reward_ratio:.2f}")
        else:
            print(f"Selected Candidate: NO_TRADE")

        if risk_decision:
            status_str = "APPROVED" if risk_decision.approved else f"REJECTED ({', '.join(risk_decision.violations)})"
            print(f"Risk Gate:       {status_str} | Size: {risk_decision.position_size} units | Max Risk: ${risk_decision.max_loss_usd:.2f}")
        else:
            print(f"Risk Gate:       SKIPPED (No trade decision)")

        print("-" * 65)

    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Crypto Market Scanner")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USD", "ETH/USD", "SOL/USD"])
    args = parser.parse_args()
    scan_crypto_markets(args.symbols)


if __name__ == "__main__":
    main()
