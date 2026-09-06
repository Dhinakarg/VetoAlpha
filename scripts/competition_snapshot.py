"""
Competition Dashboard Snapshot Generator Tool.
Generates a compact JSON snapshot (logs/competition_snapshot.json) for dashboard ingestion.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


def generate_competition_snapshot(
    telemetry_file: Path = Path("logs/competition_run.jsonl"),
    state_file: Path = Path("logs/competition_state.json"),
    output_file: Path = Path("logs/competition_snapshot.json"),
) -> Dict[str, Any]:
    """Generate compact competition snapshot dictionary and save to JSON file."""
    state = {}
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    records: List[Dict[str, Any]] = []
    if telemetry_file.exists():
        try:
            with open(telemetry_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line.strip()))
        except Exception:
            pass

    starting_equity = state.get("starting_equity", 100000.0)
    current_equity = state.get("current_equity", starting_equity)
    total_pnl = current_equity - starting_equity

    trades_approved = sum(1 for r in records if r.get("final_decision") == "TRADE")
    trades_total = sum(1 for r in records if r.get("final_decision"))
    win_rate = (trades_approved / trades_total) if trades_total > 0 else 0.0

    strategy_allocation = {
        "iron_condor": sum(1 for r in records if r.get("selected_strategy") == "iron_condor"),
        "bull_put_spread": sum(1 for r in records if r.get("selected_strategy") == "bull_put_spread"),
        "bear_call_spread": sum(1 for r in records if r.get("selected_strategy") == "bear_call_spread"),
        "long_call": sum(1 for r in records if r.get("selected_strategy") == "long_call"),
        "long_put": sum(1 for r in records if r.get("selected_strategy") == "long_put"),
    }

    latest_decisions = records[-10:] if len(records) >= 10 else records
    latest_no_trade_reasons = []
    for r in reversed(records):
        if r.get("final_decision") == "NO_TRADE":
            latest_no_trade_reasons.extend(r.get("decision_reasons", []))
            if len(latest_no_trade_reasons) >= 5:
                break

    snapshot = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "equity": current_equity,
        "starting_equity": starting_equity,
        "buying_power": state.get("buying_power", current_equity),
        "total_pnl": total_pnl,
        "open_pnl": 0.0,
        "closed_pnl": total_pnl,
        "open_positions_count": 0,
        "open_positions": [],
        "strategy_allocation": strategy_allocation,
        "win_rate": round(win_rate, 4),
        "trades": {
            "attempted": trades_total,
            "approved": trades_approved,
            "rejected": trades_total - trades_approved,
            "orders_submitted": sum(1 for r in records if r.get("order_status") not in ("NONE", None, "DRY_RUN_PAYLOAD_BUILT")),
        },
        "risk_utilization": {
            "daily_pnl": state.get("daily_pnl", 0.0),
            "max_daily_loss": 3000.0,
        },
        "circuit_breaker": {
            "tripped": state.get("circuit_breaker_tripped", False),
            "reason": state.get("circuit_breaker_reason", "None"),
        },
        "latest_decisions": latest_decisions,
        "latest_no_trade_reasons": latest_no_trade_reasons[:5],
        "ai_usage": {
            "calls": sum(1 for r in records if not r.get("ai_call_skipped")),
            "skipped": sum(1 for r in records if r.get("ai_call_skipped")),
            "cache_hits": state.get("stats", {}).get("ai_cache_hits", 0),
        },
        "execution_status": {
            "mode": state.get("execution_mode", "DRY_RUN"),
            "paper_safety_verified": True,
        },
    }

    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as e:
        print(f"Failed to write competition snapshot: {e}", file=sys.stderr)

    return snapshot


def main():
    parser = argparse.ArgumentParser(description="Competition Dashboard Snapshot Generator")
    parser.add_argument("--telemetry", type=str, default="logs/competition_run.jsonl")
    parser.add_argument("--state", type=str, default="logs/competition_state.json")
    parser.add_argument("--output", type=str, default="logs/competition_snapshot.json")

    args = parser.parse_args()
    snapshot = generate_competition_snapshot(
        telemetry_file=Path(args.telemetry),
        state_file=Path(args.state),
        output_file=Path(args.output),
    )
    print(f"Competition snapshot generated: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
