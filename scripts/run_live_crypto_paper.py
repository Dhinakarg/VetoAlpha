"""
Live Autonomous Crypto Paper Daemon Loop.
Continuously runs autonomous crypto market scan cycles against live 24/7 market feeds,
evaluating regime classification, Featherless AI advisory, strategy candidates, 12-rule risk gate,
and paper simulation execution.
STRICTLY PAPER / SIMULATION MODE — PROTECTED AGAINST REAL-MONEY ORDER SUBMISSION.
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from scripts.simulate_crypto import CryptoSimulationRunner


def run_live_crypto_daemon(symbols: list, interval: int = 30, cycles: int = 100, paper: bool = True, submit_alpaca: bool = False, fresh: bool = False):
    """Run continuous live paper trading daemon loop."""
    print("\n" + "=" * 70)
    print("⚡ LIVE 24/7 AUTONOMOUS CRYPTO PAPER AGENT DAEMON")
    print("=" * 70)
    print("GUARANTEE: PAPER SIMULATION ONLY • ZERO REAL MONEY EXECUTION PATHWAY")
    print(f"Active Symbols:          {', '.join(symbols)}")
    print(f"Scan Interval:           Every {interval} seconds")
    print(f"Total Max Cycles:        {cycles}")
    print(f"Mode:                    {'ALPACA_PAPER_SUBMISSION' if submit_alpaca else ('PAPER_SIMULATION' if paper else 'DRY_RUN')}")
    if fresh:
        print(f"Fresh Reset:             TRUE (Clearing state & starting at Cycle 1)")
    print("=" * 70 + "\n")

    if fresh:
        import os
        for f in ["data/crypto_state.json", "data/crypto_portfolio.json"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    runner = CryptoSimulationRunner(
        symbols=symbols,
        cycles=cycles,
        paper=paper or submit_alpaca,
        resume=not fresh,
        submit_alpaca=submit_alpaca,
    )

    try:
        runner.run_simulation()
    except KeyboardInterrupt:
        print("\n[INFO] Live Crypto Agent Daemon stopped by user.")
    except Exception as e:
        print(f"\n[ERROR] Daemon loop encountered error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Live 24/7 Autonomous Crypto Paper Daemon")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USD", "ETH/USD", "SOL/USD"])
    parser.add_argument("--interval", type=int, default=30, help="Scan interval in seconds")
    parser.add_argument("--cycles", type=int, default=50, help="Max scan cycles to execute")
    parser.add_argument("--paper", action="store_true", default=True, help="Run in PAPER_SIMULATION mode")
    parser.add_argument("--submit-alpaca-crypto", action="store_true", default=False, help="Submit live paper orders directly to your Alpaca Account")
    parser.add_argument("--dry-run", action="store_false", dest="paper", help="Run in DRY_RUN mode")
    parser.add_argument("--fresh", action="store_true", default=False, help="Clear state and start fresh from Cycle 1")

    args = parser.parse_args()
    run_live_crypto_daemon(
        symbols=args.symbols,
        interval=args.interval,
        cycles=args.cycles,
        paper=args.paper or args.submit_alpaca_crypto,
        submit_alpaca=args.submit_alpaca_crypto,
        fresh=args.fresh,
    )


if __name__ == "__main__":
    main()
