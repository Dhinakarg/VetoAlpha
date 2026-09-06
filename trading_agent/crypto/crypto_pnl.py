"""
Crypto Performance Analytics & Buy & Hold Benchmark Engine.
Computes comprehensive trading statistics, strategy/regime attributions,
and benchmarks strategy returns against Buy & Hold asset performance.
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from trading_agent.crypto.crypto_portfolio import CryptoPosition

logger = logging.getLogger(__name__)


@dataclass
class CryptoPerformanceReport:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    avg_win_usd: float
    avg_loss_usd: float
    profit_factor: float
    expectancy_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    total_pnl_usd: float
    max_drawdown_pct: float
    sharpe_ratio: float  # Uncorrected naive trade-level Sharpe
    naive_sharpe_ratio: float
    corrected_daily_sharpe: float
    buy_and_hold_sharpe: float
    strategy_return_pct: float
    buy_and_hold_return_pct: float
    strategy_alpha_pct: float
    strategy_attribution: Dict[str, Dict[str, Any]]
    regime_attribution: Dict[str, Dict[str, Any]]
    sample_size_note: str
    sharpe_audit_info: Dict[str, Any] = field(default_factory=dict)


class CryptoPnLEngine:
    """Computes comprehensive performance statistics and benchmarks strategy alpha."""

    @staticmethod
    def calculate_performance(
        closed_positions: List[CryptoPosition],
        current_equity: float,
        starting_equity: float = 100000.0,
        unrealized_pnl: float = 0.0,
        benchmark_start_price: Optional[float] = None,
        benchmark_current_price: Optional[float] = None,
        candles: Optional[List[Any]] = None,
    ) -> CryptoPerformanceReport:
        """Compute performance analytics and benchmark against Buy & Hold."""
        total_trades = len(closed_positions)
        if total_trades == 0:
            return CryptoPerformanceReport(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                avg_win_usd=0.0,
                avg_loss_usd=0.0,
                profit_factor=0.0,
                expectancy_usd=0.0,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=unrealized_pnl,
                total_pnl_usd=unrealized_pnl,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                naive_sharpe_ratio=0.0,
                corrected_daily_sharpe=0.0,
                buy_and_hold_sharpe=0.0,
                strategy_return_pct=0.0,
                buy_and_hold_return_pct=0.0,
                strategy_alpha_pct=0.0,
                strategy_attribution={},
                regime_attribution={},
                sample_size_note="Sample size: 0 trades (No closed trades evaluated).",
                sharpe_audit_info={},
            )

        wins = [p for p in closed_positions if p.realized_pnl > 0]
        losses = [p for p in closed_positions if p.realized_pnl < 0]

        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate_pct = round((winning_trades / total_trades) * 100.0, 2)

        total_win_usd = sum(p.realized_pnl for p in wins)
        total_loss_usd = abs(sum(p.realized_pnl for p in losses))

        avg_win_usd = round(total_win_usd / winning_trades, 2) if winning_trades > 0 else 0.0
        avg_loss_usd = round(total_loss_usd / losing_trades, 2) if losing_trades > 0 else 0.0

        profit_factor = round(total_win_usd / total_loss_usd, 2) if total_loss_usd > 0 else (99.0 if total_win_usd > 0 else 0.0)

        win_prob = winning_trades / total_trades
        loss_prob = 1.0 - win_prob
        expectancy_usd = round((win_prob * avg_win_usd) - (loss_prob * avg_loss_usd), 2)

        realized_pnl_usd = round(sum(p.realized_pnl for p in closed_positions), 2)
        total_pnl_usd = round(realized_pnl_usd + unrealized_pnl, 2)

        # Max Drawdown & Equity Curve
        equity_curve = [starting_equity]
        curr = starting_equity
        peak = starting_equity
        max_dd = 0.0

        for p in closed_positions:
            curr += p.realized_pnl
            equity_curve.append(curr)
            if curr > peak:
                peak = curr
            dd = (peak - curr) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        max_drawdown_pct = round(max_dd * 100.0, 2)

        # Strategy Return
        strategy_return_pct = round((realized_pnl_usd / starting_equity) * 100.0, 2)

        # Buy & Hold Return Benchmark
        if benchmark_start_price and benchmark_current_price and benchmark_start_price > 0:
            buy_and_hold_return_pct = round(((benchmark_current_price - benchmark_start_price) / benchmark_start_price) * 100.0, 2)
        else:
            buy_and_hold_return_pct = 0.0

        strategy_alpha_pct = round(strategy_return_pct - buy_and_hold_return_pct, 2)

        # 1. Uncorrected Naive Trade-Level Sharpe
        pnl_series = [p.realized_pnl for p in closed_positions]
        mean_pnl = sum(pnl_series) / total_trades
        var_pnl = sum((x - mean_pnl) ** 2 for x in pnl_series) / total_trades if total_trades > 1 else 0.0
        std_pnl = math.sqrt(var_pnl)
        naive_sharpe_ratio = round((mean_pnl / std_pnl) * math.sqrt(252), 2) if std_pnl > 0 else 0.0

        # 2. Corrected Daily Bucket Sharpe (incorporating 0 P&L idle days)
        daily_pnl_map: Dict[str, float] = {}
        for p in closed_positions:
            day_key = p.exit_timestamp[:10] if p.exit_timestamp else "2026-08-01"
            daily_pnl_map[day_key] = daily_pnl_map.get(day_key, 0.0) + p.realized_pnl

        # Ensure all days in 30-day window are represented (including idle days with 0 return)
        daily_returns = [pnl / starting_equity for pnl in daily_pnl_map.values()]
        while len(daily_returns) < 30:
            daily_returns.append(0.0)

        mean_daily_ret = sum(daily_returns) / len(daily_returns)
        var_daily_ret = sum((r - mean_daily_ret) ** 2 for r in daily_returns) / len(daily_returns) if len(daily_returns) > 1 else 0.0
        std_daily_ret = math.sqrt(var_daily_ret)
        corrected_daily_sharpe = round((mean_daily_ret / std_daily_ret) * math.sqrt(365), 2) if std_daily_ret > 0 else 0.0

        # 3. Buy & Hold Daily Sharpe (apples-to-apples daily bucket * sqrt(365))
        if candles and len(candles) >= 96:
            daily_bh_returns = []
            for d in range(0, len(candles) - 96, 96):
                start_p = candles[d].open
                end_p = candles[d + 95].close
                ret = (end_p - start_p) / start_p if start_p > 0 else 0.0
                daily_bh_returns.append(ret)

            if daily_bh_returns:
                mean_bh = sum(daily_bh_returns) / len(daily_bh_returns)
                var_bh = sum((r - mean_bh) ** 2 for r in daily_bh_returns) / len(daily_bh_returns) if len(daily_bh_returns) > 1 else 0.0
                std_bh = math.sqrt(var_bh)
                buy_and_hold_sharpe = round((mean_bh / std_bh) * math.sqrt(365), 2) if std_bh > 0 else 0.0
            else:
                buy_and_hold_sharpe = 0.0
        else:
            mean_bh = (buy_and_hold_return_pct / 100.0) / 30.0
            std_bh = 0.025
            buy_and_hold_sharpe = round((mean_bh / std_bh) * math.sqrt(365), 2) if std_bh > 0 else 0.0

        sharpe_audit_info = {
            "trade_series_length": total_trades,
            "daily_series_length": len(daily_returns),
            "trade_mean_pnl_usd": round(mean_pnl, 2),
            "trade_std_pnl_usd": round(std_pnl, 2),
            "daily_mean_return_pct": round(mean_daily_ret * 100.0, 4),
            "daily_std_return_pct": round(std_daily_ret * 100.0, 4),
            "naive_annualization_factor": "sqrt(252) = 15.8745",
            "corrected_annualization_factor": "sqrt(365) = 19.1049",
            "naive_formula": "(mean_pnl / std_pnl) * sqrt(252)",
            "corrected_formula": "(mean_daily_return / std_daily_return) * sqrt(365)",
            "naive_sharpe": naive_sharpe_ratio,
            "corrected_daily_sharpe": corrected_daily_sharpe,
            "buy_and_hold_sharpe": buy_and_hold_sharpe,
        }

        # Strategy Attribution Breakdown
        strat_map: Dict[str, List[CryptoPosition]] = {}
        for p in closed_positions:
            strat_map.setdefault(p.strategy, []).append(p)

        strategy_attribution = {}
        for s_name, s_list in strat_map.items():
            s_wins = sum(1 for x in s_list if x.realized_pnl > 0)
            s_total = len(s_list)
            s_pnl = round(sum(x.realized_pnl for x in s_list), 2)
            strategy_attribution[s_name] = {
                "trades": s_total,
                "win_rate_pct": round((s_wins / s_total) * 100.0, 2),
                "total_pnl_usd": s_pnl,
            }

        # Regime Attribution Breakdown
        regime_map: Dict[str, List[CryptoPosition]] = {}
        for p in closed_positions:
            regime_map.setdefault(p.regime, []).append(p)

        regime_attribution = {}
        for r_name, r_list in regime_map.items():
            r_wins = sum(1 for x in r_list if x.realized_pnl > 0)
            r_total = len(r_list)
            r_pnl = round(sum(x.realized_pnl for x in r_list), 2)
            regime_attribution[r_name] = {
                "trades": r_total,
                "win_rate_pct": round((r_wins / r_total) * 100.0, 2),
                "total_pnl_usd": r_pnl,
            }

        note = f"Sample size: {total_trades} closed trades evaluated."
        if total_trades < 30:
            note += " (Warning: Small sample size; metrics require larger sample for statistical confidence)."

        return CryptoPerformanceReport(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=win_rate_pct,
            avg_win_usd=avg_win_usd,
            avg_loss_usd=avg_loss_usd,
            profit_factor=profit_factor,
            expectancy_usd=expectancy_usd,
            realized_pnl_usd=realized_pnl_usd,
            unrealized_pnl_usd=unrealized_pnl,
            total_pnl_usd=total_pnl_usd,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=naive_sharpe_ratio,
            naive_sharpe_ratio=naive_sharpe_ratio,
            corrected_daily_sharpe=corrected_daily_sharpe,
            buy_and_hold_sharpe=buy_and_hold_sharpe,
            strategy_return_pct=strategy_return_pct,
            buy_and_hold_return_pct=buy_and_hold_return_pct,
            strategy_alpha_pct=strategy_alpha_pct,
            strategy_attribution=strategy_attribution,
            regime_attribution=regime_attribution,
            sample_size_note=note,
            sharpe_audit_info=sharpe_audit_info,
        )
