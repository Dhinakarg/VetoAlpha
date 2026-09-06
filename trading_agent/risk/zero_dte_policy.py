"""
0DTE Options Paper Safety Policy.
Enforces strict risk controls for Same-Day Expiration (DTE = 0) options trading:
- PAPER TRADING ONLY (ALPACA_PAPER=True)
- Defined-risk structures ONLY (No naked options)
- Maximum 0.5% equity risk per trade
- Mandatory time-based exit cutoff (15:30 EST / 30 mins before market close)
- Circuit breaker: Maximum 2 consecutive 0DTE losses per session
"""

import logging
from datetime import datetime, timezone, time
from typing import Dict, Any, Optional

from trading_agent.config import settings
from trading_agent.models import StrategyCandidate, AccountSnapshot

logger = logging.getLogger(__name__)


def evaluate_0dte_safety(
    candidate: StrategyCandidate,
    account: AccountSnapshot,
    consecutive_0dte_losses: int = 0,
    current_time_est: Optional[time] = None,
) -> Dict[str, Any]:
    """Evaluate whether 0DTE candidate satisfies strict paper safety policy."""
    # 1. Require Paper Trading Mode
    if not getattr(settings, "alpaca_paper", False):
        return {
            "allowed": False,
            "reason": "0DTE SAFETY VIOLATION: 0DTE options trading is strictly restricted to Alpaca Paper environment.",
        }

    # 2. Require Defined-Risk Strategy
    strategy_name = getattr(candidate, "strategy_name", "")
    defined_risk_strategies = {
        "iron_condor",
        "bull_put_spread",
        "bear_call_spread",
        "call_debit_spread",
        "put_debit_spread",
        "iron_butterfly",
    }

    if strategy_name not in defined_risk_strategies:
        return {
            "allowed": False,
            "reason": f"0DTE SAFETY VIOLATION: Strategy '{strategy_name}' is not a defined-risk multi-leg structure.",
        }

    # 3. Maximum 0.5% Equity Risk Sizing
    max_loss = float(getattr(candidate, "max_loss", 0.0) or 0.0)
    equity = float(getattr(account, "equity", 100000.0) or 100000.0)
    max_allowed_loss = equity * 0.005  # 0.5% limit

    if max_loss > max_allowed_loss:
        return {
            "allowed": False,
            "reason": f"0DTE SAFETY VIOLATION: Max loss (${max_loss:.2f}) exceeds 0.5% equity limit (${max_allowed_loss:.2f}).",
        }

    # 4. Mandatory Time-Based Cutoff (Must not enter after 15:30 EST)
    cutoff_time = time(15, 30)
    eval_time = current_time_est or datetime.now(timezone.utc).time()
    if eval_time >= cutoff_time and eval_time <= time(16, 0):
        return {
            "allowed": False,
            "reason": "0DTE SAFETY VIOLATION: Past 15:30 EST cutoff; entry prohibited within 30 mins of market close.",
        }

    # 5. Circuit Breaker on 2 Consecutive 0DTE Losses
    if consecutive_0dte_losses >= 2:
        return {
            "allowed": False,
            "reason": f"0DTE CIRCUIT BREAKER ACTIVE: {consecutive_0dte_losses} consecutive 0DTE losses in current session.",
        }

    return {"allowed": True, "reason": "0DTE safety checks passed cleanly."}
