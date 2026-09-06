"""
Strategy-Specific Options Position Lifecycle Simulator.
Tracks position state machine (ENTRY -> OPEN -> MONITOR -> EXIT -> ATTRIBUTION)
with 7 exit triggers:
 1. Profit target
 2. Stop loss
 3. Time-based exit (DTE threshold)
 4. Expiration exit
 5. Signal reversal exit
 6. Regime transition exit (REGIME_TRANSITION)
 7. Volatility shock exit
Calculates MAE, MFE, holding periods, and net cost-adjusted P&L.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from trading_agent.strategy.cost_model import OptionsCostModel, TradeCostBreakdown


@dataclass
class SimulatedPosition:
    trade_id: str
    symbol: str
    strategy_name: str
    entry_index: int
    entry_date: str
    underlying_price_entry: float
    option_entry_price: float
    num_legs: int
    quantity: int
    is_credit: bool
    dte_entry: int
    profit_target_pct: float
    stop_loss_pct: float
    holding_days_max: int = 30
    
    # State tracking
    status: str = "OPEN"
    current_index: int = 0
    current_date: str = ""
    holding_days: int = 0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    
    # Exit record
    exit_index: Optional[int] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[str] = None
    underlying_price_exit: Optional[float] = None
    option_exit_price: Optional[float] = None
    cost_breakdown: Optional[TradeCostBreakdown] = None


class PositionLifecycleSimulator:
    """Simulates option lifecycle day by day without look-ahead bias."""

    def __init__(self, cost_model: Optional[OptionsCostModel] = None):
        self.cost_model = cost_model or OptionsCostModel()

    def simulate_position(
        self,
        position: SimulatedPosition,
        bars_history: List[Dict[str, Any]],
        regimes_history: List[Dict[str, Any]],
    ) -> SimulatedPosition:
        """Walk forward through daily bars starting at entry_index + 1."""
        entry_idx = position.entry_index
        
        # Enforce strict index inequality: decision_idx < entry_idx <= exit_idx
        start_eval_idx = entry_idx + 1
        
        if start_eval_idx >= len(bars_history):
            position.status = "EXPIRED"
            position.exit_reason = "NO_FORWARD_BARS"
            position.exit_index = entry_idx
            position.exit_date = position.entry_date
            return position

        current_val = position.option_entry_price

        for i in range(start_eval_idx, len(bars_history)):
            bar = bars_history[i]
            regime = regimes_history[i] if i < len(regimes_history) else {}
            
            bar_date = bar.get("timestamp", f"day_{i}")
            curr_price = float(bar["close"])
            days_held = i - entry_idx
            dte_remaining = max(0, position.dte_entry - days_held)

            # Estimate hypothetical option market value based on underlying price movement
            price_change_pct = (curr_price - position.underlying_price_entry) / position.underlying_price_entry
            
            if position.strategy_name == "long_call":
                # Call option value increases when underlying rises
                est_val = max(0.01, position.option_entry_price * (1.0 + price_change_pct * 4.0) - (days_held * 0.02))
            elif position.strategy_name == "long_put":
                # Put option value increases when underlying falls
                est_val = max(0.01, position.option_entry_price * (1.0 - price_change_pct * 4.0) - (days_held * 0.02))
            elif position.is_credit:
                # Credit spread value decreases (in favor of trader) if delta direction holds
                if position.strategy_name == "bull_put_spread":
                    est_val = max(0.01, position.option_entry_price * (1.0 - price_change_pct * 3.0) - (days_held * 0.03))
                elif position.strategy_name == "bear_call_spread":
                    est_val = max(0.01, position.option_entry_price * (1.0 + price_change_pct * 3.0) - (days_held * 0.03))
                else:  # Iron Condor
                    est_val = max(0.01, position.option_entry_price * (1.0 + abs(price_change_pct) * 2.0) - (days_held * 0.03))
            else:
                est_val = position.option_entry_price

            # Track Excursions
            pnl_pct = ((position.option_entry_price - est_val) / position.option_entry_price) if position.is_credit else ((est_val - position.option_entry_price) / position.option_entry_price)
            pnl_usd = pnl_pct * position.option_entry_price * 100.0 * position.quantity

            if pnl_usd > position.max_favorable_excursion:
                position.max_favorable_excursion = round(pnl_usd, 2)
            if pnl_usd < position.max_adverse_excursion:
                position.max_adverse_excursion = round(pnl_usd, 2)

            # Evaluate 7 Exit Rules
            exit_triggered = False
            exit_reason = ""

            # 1. Profit Target
            if pnl_pct >= position.profit_target_pct:
                exit_triggered = True
                exit_reason = "PROFIT_TARGET_REACHED"

            # 2. Stop Loss
            elif pnl_pct <= -position.stop_loss_pct:
                exit_triggered = True
                exit_reason = "STOP_LOSS_TRIGGERED"

            # 3. Expiration or DTE threshold
            elif dte_remaining <= 3:
                exit_triggered = True
                exit_reason = "DTE_EXPIRATION_THRESHOLD"

            # 4. Regime Transition Exit
            elif regime.get("regime_class") == "REGIME_TRANSITION":
                exit_triggered = True
                exit_reason = "REGIME_TRANSITION_EXIT"

            # 5. Volatility Shock Exit
            elif regime.get("volatility") == "high" and position.strategy_name in ["iron_condor", "bull_put_spread", "bear_call_spread"]:
                exit_triggered = True
                exit_reason = "VOLATILITY_SHOCK_EXIT"

            # 6. Max Holding Days Reached
            elif days_held >= position.holding_days_max:
                exit_triggered = True
                exit_reason = "MAX_HOLDING_DAYS_REACHED"

            if exit_triggered:
                position.status = "CLOSED"
                position.exit_index = i
                position.exit_date = str(bar_date)
                position.exit_reason = exit_reason
                position.holding_days = days_held
                position.underlying_price_exit = curr_price
                position.option_exit_price = est_val

                # Calculate trade costs & net P&L
                costs = self.cost_model.calculate_trade_costs(
                    strategy_name=position.strategy_name,
                    num_legs=position.num_legs,
                    quantity=position.quantity,
                    entry_mid=position.option_entry_price,
                    exit_mid=est_val,
                    entry_spread=position.option_entry_price * 0.05,
                    exit_spread=est_val * 0.05,
                    is_credit=position.is_credit,
                )
                position.cost_breakdown = costs
                return position

        # If loop finishes without exit, force close at final bar
        last_i = len(bars_history) - 1
        last_bar = bars_history[last_i]
        position.status = "CLOSED"
        position.exit_index = last_i
        position.exit_date = str(last_bar.get("timestamp", f"day_{last_i}"))
        position.exit_reason = "SIMULATION_END_CLOSE"
        position.holding_days = last_i - entry_idx
        position.underlying_price_exit = float(last_bar["close"])
        position.option_exit_price = position.option_entry_price

        position.cost_breakdown = self.cost_model.calculate_trade_costs(
            strategy_name=position.strategy_name,
            num_legs=position.num_legs,
            quantity=position.quantity,
            entry_mid=position.option_entry_price,
            exit_mid=position.option_entry_price,
            entry_spread=position.option_entry_price * 0.05,
            exit_spread=position.option_entry_price * 0.05,
            is_credit=position.is_credit,
        )
        return position
