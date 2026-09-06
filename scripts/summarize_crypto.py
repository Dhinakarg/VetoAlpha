"""
Crypto Performance & Readiness Summary Reporter.
Parses crypto telemetry (logs/crypto_run.jsonl) and state checkpoints (logs/crypto_state.json)
to present performance attribution, strategy statistics, and Buy & Hold benchmarks.
ISOLATED FROM OPTIONS TELEMETRY AND SUBMISSION METRICS.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def summarize_crypto_performance(
    telemetry_file: Path = Path("logs/crypto_run.jsonl"),
    state_file: Path = Path("logs/crypto_state.json"),
):
    """Parse crypto telemetry and generate human-readable performance summary."""
    records: List[Dict[str, Any]] = []
    if telemetry_file.exists():
        try:
            with open(telemetry_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line.strip()))
        except Exception:
            pass

    state = {}
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    cycles = state.get("current_cycle", 0)
    equity = state.get("equity", 100000.0)
    daily_pnl = state.get("daily_pnl", 0.0)
    cb_tripped = state.get("circuit_breaker_tripped", False)
    stats = state.get("stats", {})

    total_attempts = len(records)
    approved_trades = sum(1 for r in records if r.get("risk_approved"))
    rejected_trades = sum(1 for r in records if not r.get("risk_approved") and r.get("decision") == "trade")
    no_trades = sum(1 for r in records if r.get("decision") == "no_trade")

    lines = [
        "=" * 65,
        "CRYPTO VALIDATION PERFORMANCE SUMMARY",
        "⚠ ISOLATED FROM HACKATHON OPTIONS SUBMISSION",
        "=" * 65,
        f"Simulated Equity:       ${equity:,.2f}",
        f"Starting Equity:        $100,000.00",
        f"Total P&L:              ${equity - 100000.00:+,.2f}",
        f"Daily P&L:              ${daily_pnl:+,.2f}",
        "-" * 65,
        f"Cycles Executed:        {cycles}",
        f"Evaluations Logged:     {total_attempts}",
        f"Trades Approved:        {approved_trades}",
        f"Trades Rejected:        {rejected_trades}",
        f"NO_TRADE Decisions:     {no_trades}",
        "-" * 65,
        f"Circuit Breaker:        {'TRIPPED (' + state.get('circuit_breaker_reason', '') + ')' if cb_tripped else 'NORMAL'}",
        f"AI Calls:               {stats.get('ai_calls', 0)}",
        f"AI Calls Skipped:       {stats.get('ai_skipped', 0)}",
        f"AI Failures:            {stats.get('ai_failures', 0)}",
        "=" * 65,
    ]

    print("\n" + "\n".join(lines) + "\n")


def main():
    summarize_crypto_performance()


if __name__ == "__main__":
    main()
