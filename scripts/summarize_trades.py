"""
Autonomous Trade Summary & Agent Performance Analytics.
Parses logs/agent_log.jsonl and outputs a presentation-ready ASCII performance report.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

DEFAULT_LOG_PATH = Path("logs/agent_log.jsonl")
STARTING_EQUITY = 100000.0
MAX_AI_CALLS_PER_DAY = 50


def parse_agent_log(log_path: Path = DEFAULT_LOG_PATH) -> Dict[str, Any]:
    """Parse JSONL log file and aggregate trade performance metrics."""
    events: List[Dict[str, Any]] = []

    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue

    # Metrics aggregation
    trades_attempted = 0
    trades_filled = 0
    trades_rejected = 0
    circuit_breaker_events = 0
    ai_calls_used = 0

    realized_pnls: List[float] = []
    unrealized_pnl = 0.0
    latest_equity = STARTING_EQUITY
    peak_equity = STARTING_EQUITY
    max_drawdown = 0.0
    latest_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for evt in events:
        etype = evt.get("event_type")
        payload = evt.get("payload", {})
        ts = evt.get("timestamp_utc", "")
        if ts:
            latest_timestamp = ts[:10]

        if etype in ("order_submitted", "order_dry_run", "condor_proposed"):
            trades_attempted += 1

        elif etype == "order_filled":
            trades_filled += 1

        elif etype == "order_rejected":
            trades_rejected += 1

        elif etype == "risk_gate_result":
            if not payload.get("approved", True):
                trades_rejected += 1

        elif etype == "sentiment_check":
            ai_calls_used += 1

        elif etype == "circuit_breaker_tripped":
            circuit_breaker_events += 1

        elif etype == "position_closed":
            pnl = float(payload.get("realized_pnl", 0.0))
            realized_pnls.append(pnl)

        elif etype == "position_reviewed":
            unrealized_pnl = float(payload.get("unrealized_pnl", unrealized_pnl))

        elif etype == "market_snapshot":
            eq = payload.get("equity")
            if eq:
                latest_equity = float(eq)
                if latest_equity > peak_equity:
                    peak_equity = latest_equity
                dd = peak_equity - latest_equity
                if dd > max_drawdown:
                    max_drawdown = dd

    realized_pnl = sum(realized_pnls)
    total_pnl = realized_pnl + unrealized_pnl

    if latest_equity == STARTING_EQUITY and (realized_pnl != 0 or unrealized_pnl != 0):
        latest_equity = STARTING_EQUITY + total_pnl

    # Win rate, Avg Winner, Avg Loser, Profit Factor
    winners = [p for p in realized_pnls if p > 0]
    losers = [p for p in realized_pnls if p < 0]
    closed_count = len(realized_pnls)

    win_rate = (len(winners) / closed_count * 100.0) if closed_count > 0 else 0.0
    avg_winner = (sum(winners) / len(winners)) if len(winners) > 0 else 0.0
    avg_loser = (sum(losers) / len(losers)) if len(losers) > 0 else 0.0

    sum_losses = abs(sum(losers))
    if sum_losses > 0:
        profit_factor = round(sum(winners) / sum_losses, 2)
    elif sum(winners) > 0:
        profit_factor = 99.99
    else:
        profit_factor = 0.00

    max_dd_pct = (max_drawdown / peak_equity * 100.0) if peak_equity > 0 else 0.0
    ai_calls_remaining = max(0, MAX_AI_CALLS_PER_DAY - ai_calls_used)

    return {
        "trading_date": latest_timestamp,
        "starting_equity": STARTING_EQUITY,
        "current_equity": latest_equity,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "trades_attempted": trades_attempted,
        "trades_filled": trades_filled,
        "trades_rejected": trades_rejected,
        "win_rate": win_rate,
        "avg_winner": avg_winner,
        "avg_loser": avg_loser,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_dd_pct,
        "circuit_breaker_events": circuit_breaker_events,
        "ai_calls_used": ai_calls_used,
        "ai_calls_remaining": ai_calls_remaining,
        "total_events_logged": len(events),
    }


def format_summary_report(data: Dict[str, Any]) -> str:
    """Format metrics into a clean, screenshot-ready presentation box."""
    pnl_sign = "+" if data["total_pnl"] >= 0 else "-"
    total_pnl_str = f"{pnl_sign}${abs(data['total_pnl']):,.2f}"
    realized_sign = "+" if data["realized_pnl"] >= 0 else "-"
    realized_str = f"{realized_sign}${abs(data['realized_pnl']):,.2f}"
    unrealized_sign = "+" if data["unrealized_pnl"] >= 0 else "-"
    unrealized_str = f"{unrealized_sign}${abs(data['unrealized_pnl']):,.2f}"

    start_eq_str = f"${data['starting_equity']:,.2f}"
    curr_eq_str = f"${data['current_equity']:,.2f}"
    avg_win_str = f"${data['avg_winner']:,.2f}"
    avg_loss_str = f"${data['avg_loser']:,.2f}"
    max_dd_str = f"${data['max_drawdown']:,.2f}"

    lines = [
        "+==============================================================================+",
        "|            ADAPTIVE AI IRON CONDOR AGENT - PERFORMANCE SUMMARY               |",
        "|                     Autonomous Alpaca Paper Trading                          |",
        "+==============================================================================+",
        f"|  Trading Date:        {data['trading_date']:<15}   Account Model:   Alpaca Paper (4-Leg)|",
        f"|  Starting Capital:    {start_eq_str:>12}       Current Equity:  {curr_eq_str:>12}   |",
        "+------------------------------------------------------------------------------+",
        f"|  Realized P&L:        {realized_str:>12}       Unrealized P&L:  {unrealized_str:>12}   |",
        f"|  Total Strategy P&L:  {total_pnl_str:>12}       Total Return:    {((data['current_equity']-data['starting_equity'])/data['starting_equity']*100):>+8.2f}%       |",
        "+------------------------------------------------------------------------------+",
        f"|  Trades Attempted:    {data['trades_attempted']:>12}       Trades Filled:   {data['trades_filled']:>12}   |",
        f"|  Trades Rejected:     {data['trades_rejected']:>12}       Win Rate:        {data['win_rate']:>11.1f}%   |",
        f"|  Average Winner:      {avg_win_str:>12}       Average Loser:   {avg_loss_str:>12}   |",
        f"|  Profit Factor:       {data['profit_factor']:>12.2f}       Max Drawdown:    {max_dd_str:>8} ({data['max_drawdown_pct']:.1f}%)|",
        "+------------------------------------------------------------------------------+",
        f"|  Circuit Breakers:    {data['circuit_breaker_events']:>12}       AI Calls Used:   {data['ai_calls_used']:>12}   |",
        f"|  Audit Log Events:    {data['total_events_logged']:>12}       AI Budget Left:  {data['ai_calls_remaining']:>12}   |",
        "+==============================================================================+",
    ]
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    log_file = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG_PATH
    metrics = parse_agent_log(log_file)
    report = format_summary_report(metrics)
    print("\n" + report + "\n")


if __name__ == "__main__":
    main()
