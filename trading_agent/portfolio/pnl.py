"""
P&L performance and multi-strategy analytics tracker.
Tracks strategy-level P&L, win rates, average winners/losers, profit factor, drawdown, and holding times.
"""

from typing import Dict, Any, List, Optional
import math


class PnLTracker:
    """Tracks cumulative and strategy-level performance analytics."""

    def __init__(self, starting_capital: float = 100000.0):
        self.starting_capital = starting_capital
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.peak_equity: float = starting_capital
        self.max_drawdown: float = 0.0

        # Strategy-level breakdowns
        self.trades_by_strategy: Dict[str, int] = {}
        self.wins_by_strategy: Dict[str, int] = {}
        self.losses_by_strategy: Dict[str, int] = {}
        self.realized_pnl_by_strategy: Dict[str, float] = {}
        self.unrealized_pnl_by_strategy: Dict[str, float] = {}
        self.holding_times_by_strategy: Dict[str, List[float]] = {}
        self.winners_by_strategy: Dict[str, List[float]] = {}
        self.losers_by_strategy: Dict[str, List[float]] = {}

    def record_trade_close(
        self,
        strategy: str,
        realized_pnl: float,
        holding_time_hours: float = 0.0,
    ) -> None:
        """Record a closed trade and attribute performance to its strategy family."""
        strat = str(strategy).lower()
        self.realized_pnl += realized_pnl
        self.total_trades += 1

        self.trades_by_strategy[strat] = self.trades_by_strategy.get(strat, 0) + 1
        self.realized_pnl_by_strategy[strat] = self.realized_pnl_by_strategy.get(strat, 0.0) + realized_pnl
        self.holding_times_by_strategy.setdefault(strat, []).append(holding_time_hours)

        if realized_pnl > 0:
            self.winning_trades += 1
            self.wins_by_strategy[strat] = self.wins_by_strategy.get(strat, 0) + 1
            self.winners_by_strategy.setdefault(strat, []).append(realized_pnl)
        else:
            self.losses_by_strategy[strat] = self.losses_by_strategy.get(strat, 0) + 1
            self.losers_by_strategy.setdefault(strat, []).append(realized_pnl)

        # Update drawdown
        current_eq = self.starting_capital + self.realized_pnl + self.unrealized_pnl
        if current_eq > self.peak_equity:
            self.peak_equity = current_eq
        dd = (self.peak_equity - current_eq) / self.peak_equity if self.peak_equity > 0 else 0.0
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def get_strategy_performance(self, strategy: str) -> Dict[str, Any]:
        """Return detailed performance attribution for a specific strategy."""
        strat = str(strategy).lower()
        trades = self.trades_by_strategy.get(strat, 0)
        wins = self.wins_by_strategy.get(strat, 0)
        losses = self.losses_by_strategy.get(strat, 0)
        pnl = self.realized_pnl_by_strategy.get(strat, 0.0)
        win_rate = (wins / trades * 100.0) if trades > 0 else 0.0

        winners = self.winners_by_strategy.get(strat, [])
        losers = self.losers_by_strategy.get(strat, [])
        avg_win = sum(winners) / len(winners) if winners else 0.0
        avg_loss = sum(losers) / len(losers) if losers else 0.0

        gross_profit = sum(winners)
        gross_loss = abs(sum(losers))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

        holding_times = self.holding_times_by_strategy.get(strat, [])
        avg_hold = sum(holding_times) / len(holding_times) if holding_times else 0.0

        return {
            "strategy": strat,
            "total_trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "realized_pnl": round(pnl, 2),
            "avg_winner": round(avg_win, 2),
            "avg_loser": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_holding_time_hours": round(avg_hold, 1),
        }

    def get_summary(self) -> Dict[str, Any]:
        """Return aggregate and strategy-attributed performance analytics summary."""
        win_rate = (self.winning_trades / self.total_trades * 100.0) if self.total_trades > 0 else 0.0
        all_strategies = list(self.trades_by_strategy.keys())
        by_strat = {s: self.get_strategy_performance(s) for s in all_strategies}

        return {
            "starting_capital": self.starting_capital,
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "total_pnl": round(self.realized_pnl + self.unrealized_pnl, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate_pct": round(win_rate, 2),
            "max_drawdown_pct": round(self.max_drawdown * 100.0, 2),
            "strategies": by_strat,
        }
