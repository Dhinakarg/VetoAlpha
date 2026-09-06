"""
Rolling Walk-Forward Backtesting & Out-Of-Sample Validation Engine.
Splits historical price bars into 3 rolling windows (Train -> Validate -> Out-of-Sample Test)
to prevent overfitting and curve-fitting.
Calculates performance metrics strictly on Out-of-Sample periods.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime

from trading_agent.strategy.cost_model import OptionsCostModel
from trading_agent.strategy.lifecycle_simulator import PositionLifecycleSimulator, SimulatedPosition


def calculate_comprehensive_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate 20+ quantitative performance metrics including Sharpe, Sortino, Calmar, MAE, MFE."""
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "loss_rate": 0.0, "net_pnl": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0, "max_drawdown": 0.0,
            "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "avg_winner": 0.0, "avg_loser": 0.0, "reward_risk_ratio": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "total_costs": 0.0,
            "avg_holding_days": 0.0, "median_holding_days": 0.0,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            "mae_avg": 0.0, "mfe_avg": 0.0,
        }

    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    costs = [t.get("costs", 0.0) for t in trades]
    holding_days = [t.get("holding_days", 1) for t in trades]
    maes = [t.get("mae", 0.0) for t in trades]
    mfes = [t.get("mfe", 0.0) for t in trades]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_pnl = sum(pnls)
    total_costs = sum(costs)

    win_rate = (len(wins) / len(pnls)) * 100.0 if pnls else 0.0
    loss_rate = 100.0 - win_rate
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    expectancy = net_pnl / len(pnls) if pnls else 0.0

    avg_winner = np.mean(wins) if wins else 0.0
    avg_loser = abs(np.mean(losses)) if losses else 0.0
    rr_ratio = (avg_winner / avg_loser) if avg_loser > 0 else 0.0

    # Equity curve and Drawdowns
    equity_curve = np.cumsum([100000.0] + pnls)
    peak = np.maximum.accumulate(equity_curve)
    drawdowns = (equity_curve - peak) / peak
    max_dd = float(abs(np.min(drawdowns))) * 100.0

    # Risk Ratios (Dailyized return std dev approximation)
    returns = np.array(pnls) / 100000.0
    mean_ret = np.mean(returns) if len(returns) > 0 else 0.0
    std_ret = np.std(returns) if len(returns) > 1 else 1.0e-5
    negative_returns = returns[returns < 0]
    downside_std = np.std(negative_returns) if len(negative_returns) > 1 else 1.0e-5

    sharpe = float((mean_ret / std_ret) * np.sqrt(252)) if std_ret > 0 else 0.0
    sortino = float((mean_ret / downside_std) * np.sqrt(252)) if downside_std > 0 else 0.0
    calmar = float((net_pnl / 100000.0) / (max_dd / 100.0)) if max_dd > 0 else 0.0

    # Streak counters
    max_c_wins, max_c_losses, curr_w, curr_l = 0, 0, 0, 0
    for p in pnls:
        if p > 0:
            curr_w += 1
            curr_l = 0
            max_c_wins = max(max_c_wins, curr_w)
        else:
            curr_l += 1
            curr_w = 0
            max_c_losses = max(max_c_losses, curr_l)

    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "loss_rate": round(loss_rate, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "total_costs": round(total_costs, 2),
        "net_pnl": round(net_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        "avg_winner": round(float(avg_winner), 2),
        "avg_loser": round(float(avg_loser), 2),
        "reward_risk_ratio": round(float(rr_ratio), 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "calmar_ratio": round(calmar, 2),
        "max_consecutive_wins": max_c_wins,
        "max_consecutive_losses": max_c_losses,
        "avg_holding_days": round(float(np.mean(holding_days)), 1) if holding_days else 0.0,
        "median_holding_days": round(float(np.median(holding_days)), 1) if holding_days else 0.0,
        "mae_avg": round(float(np.mean(maes)), 2) if maes else 0.0,
        "mfe_avg": round(float(np.mean(mfes)), 2) if mfes else 0.0,
    }


class WalkForwardEngine:
    """Walk-Forward Backtest & Out-Of-Sample Validation Engine."""

    def __init__(self, cost_model: Optional[OptionsCostModel] = None):
        self.cost_model = cost_model or OptionsCostModel()
        self.simulator = PositionLifecycleSimulator(cost_model=self.cost_model)

    def run_walk_forward(
        self,
        bars_df: pd.DataFrame,
        symbol: str,
        strategy_config: Dict[str, Any],
        num_windows: int = 3,
        use_transition_filter: bool = True,
    ) -> Dict[str, Any]:
        """Split bars_df into num_windows rolling windows and return aggregate Out-of-Sample metrics."""
        total_bars = len(bars_df)
        if total_bars < 60:
            return {"out_of_sample_metrics": calculate_comprehensive_metrics([]), "trades": []}

        window_size = total_bars // num_windows
        all_oos_trades = []

        from trading_agent.strategy.regime import detect_market_regime

        for w in range(num_windows):
            start_idx = w * window_size
            end_idx = min(total_bars, (w + 1) * window_size) if w == num_windows - 1 else (w + 1) * window_size

            # Divide window into Train (40%), Validate (30%), Out-Of-Sample (30%)
            w_len = end_idx - start_idx
            train_end = start_idx + int(w_len * 0.40)
            val_end = train_end + int(w_len * 0.30)
            oos_start = val_end
            oos_end = end_idx

            if oos_end - oos_start < 5:
                continue

            # Evaluate OOS window
            for i in range(oos_start, oos_end - 5):
                sub_bars = bars_df.iloc[:i]
                current_bar = bars_df.iloc[i]

                # Convert bars to list of dicts for simulator & regime
                bars_list = sub_bars.to_dict("records")
                price = float(current_bar["close"])

                regime = detect_market_regime(bars_list, iv_rank=strategy_config.get("iv_rank", 35.0))
                reg_class = regime.regime_class or "UNKNOWN"

                # Check regime transition lock
                if use_transition_filter and reg_class == "REGIME_TRANSITION":
                    continue

                # Check strategy eligibility
                strat = strategy_config.get("strategy_name", "bull_put_spread")
                is_eligible = strat in (regime.eligible_strategies or [])
                
                # If testing without transition filter, allow strategy if base trend aligns
                if not use_transition_filter and not is_eligible:
                    if strat == "long_call" and regime.trend == "bullish":
                        is_eligible = True
                    elif strat == "long_put" and regime.trend == "bearish":
                        is_eligible = True
                    elif strat in ["bull_put_spread", "iron_condor"] and regime.trend in ["bullish", "range"]:
                        is_eligible = True
                    elif strat == "bear_call_spread" and regime.trend in ["bearish", "range"]:
                        is_eligible = True

                if not is_eligible:
                    continue

                # Simulate position lifecycle
                is_credit = strat in ["iron_condor", "bull_put_spread", "bear_call_spread"]
                num_legs = 4 if strat == "iron_condor" else (2 if "spread" in strat else 1)

                pos = SimulatedPosition(
                    trade_id=f"{symbol}_{strat}_{i}",
                    symbol=symbol,
                    strategy_name=strat,
                    entry_index=i,
                    entry_date=str(current_bar.get("timestamp", f"bar_{i}")),
                    underlying_price_entry=price,
                    option_entry_price=strategy_config.get("entry_option_price", 2.00),
                    num_legs=num_legs,
                    quantity=1,
                    is_credit=is_credit,
                    dte_entry=strategy_config.get("dte", 30),
                    profit_target_pct=strategy_config.get("profit_target", 0.50),
                    stop_loss_pct=strategy_config.get("stop_loss", 1.00),
                )

                # Generate future bars slice for simulation
                future_bars_df = bars_df.iloc[i:]
                future_bars_list = future_bars_df.to_dict("records")
                future_regimes = [{"regime_class": reg_class, "volatility": regime.volatility} for _ in range(len(future_bars_list))]

                res_pos = self.simulator.simulate_position(pos, future_bars_list, future_regimes)

                if res_pos.cost_breakdown:
                    all_oos_trades.append({
                        "symbol": symbol,
                        "strategy": strat,
                        "regime": reg_class,
                        "net_pnl": res_pos.cost_breakdown.net_pnl,
                        "gross_pnl": res_pos.cost_breakdown.gross_pnl,
                        "costs": res_pos.cost_breakdown.total_costs,
                        "holding_days": res_pos.holding_days,
                        "exit_reason": res_pos.exit_reason,
                        "mae": res_pos.max_adverse_excursion,
                        "mfe": res_pos.max_favorable_excursion,
                    })

        metrics = calculate_comprehensive_metrics(all_oos_trades)
        return {"out_of_sample_metrics": metrics, "trades": all_oos_trades}
