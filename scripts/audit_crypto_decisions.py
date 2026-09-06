"""
Crypto Decision Audit & Explainability Harness.
Evaluates crypto candidates and outputs human-readable decision logs and machine-readable JSONL audit traces.
Saved to logs/crypto_decision_audit.jsonl.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

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

logger = logging.getLogger("audit_crypto")


def audit_crypto_decisions(symbols: List[str], log_file: Path = Path("logs/crypto_decision_audit.jsonl")):
    """Run crypto decision audit across symbols and log JSONL traces."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fetcher = CryptoDataFetcher()
    selector = CryptoStrategySelector(min_score=65.0)
    risk_gate = CryptoRiskGate()

    print("\n" + "=" * 65)
    print("CRYPTO AUTONOMOUS DECISION AUDIT & EXPLAINABILITY HARNESS")
    print("=" * 65)

    for symbol in symbols:
        data = fetcher.fetch_market_data(symbol)
        regime_res = classify_crypto_regime(data)
        decision = selector.evaluate(data, regime_res)

        risk_dec = None
        if decision.selected_candidate:
            risk_dec = risk_gate.evaluate_candidate(
                candidate=decision.selected_candidate,
                equity=100000.0,
                daily_pnl=0.0,
                open_positions=[],
            )

        audit_trace = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "price": data.current_price,
            "regime": regime_res.regime.value,
            "regime_confidence": regime_res.confidence,
            "candidates": [
                {"strategy": c.strategy, "direction": c.direction, "score": c.raw_score}
                for c in decision.candidates_evaluated
            ],
            "ai_advisory": decision.ai_advisory,
            "ai_agreed": decision.ai_agreed,
            "selected_strategy": decision.selected_candidate.strategy if decision.selected_candidate else "NO_TRADE",
            "risk_gate": {
                "approved": risk_dec.approved if risk_dec else False,
                "violations": risk_dec.violations if risk_dec else ["SKIPPED"],
                "position_size": risk_dec.position_size if risk_dec else 0.0,
                "max_loss_usd": risk_dec.max_loss_usd if risk_dec else 0.0,
            },
            "final_decision": decision.action,
            "blocking_reasons": decision.reasons,
            "execution_mode": "CRYPTO_VALIDATION_MODE",
        }

        # Append to JSONL log
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(audit_trace) + "\n")
        except Exception as e:
            logger.error(f"Failed to write crypto audit log: {e}")

        # Terminal Print
        print(f"\nAsset:                 {symbol} (${data.current_price:,.2f})")
        print(f"Regime:                {regime_res.regime.value} (ADX: {data.adx_14 or 0.0:.1f}, RSI: {data.rsi_14 or 0.0:.1f})")
        print(f"Selected Strategy:     {audit_trace['selected_strategy']}")
        print(f"Decision:              {decision.action.upper()}")
        if risk_dec:
            print(f"Risk Gate Approved:    {risk_dec.approved} ({'No violations' if risk_dec.approved else ', '.join(risk_dec.violations)})")
        print(f"AI Agreement:          {'AGREED' if decision.ai_agreed else 'DISAGREED (Deterministic decision maintained)'}")
        print("-" * 65)

    print(f"\nCrypto Decision audit complete. Machine-readable audit trace saved to: {log_file.resolve()}")
    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Crypto Autonomous Decision Audit")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USD", "ETH/USD", "SOL/USD"])
    args = parser.parse_args()
    audit_crypto_decisions(args.symbols)


if __name__ == "__main__":
    main()
