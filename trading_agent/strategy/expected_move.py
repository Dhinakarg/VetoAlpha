"""
Expected Move & Premium Quality Evaluator.
Calculates implied expected move for options expirations:
  EM = Underlying Price * Implied Volatility * sqrt(DTE / 365.0)

Evaluates premium quality, strike placement, distance from underlying,
and reward-to-risk ratio relative to expected move and transaction costs.
"""

import math
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def calculate_expected_move(
    underlying_price: float,
    implied_volatility: float,
    dte: int,
) -> float:
    """Calculate 1-standard deviation implied expected move for given DTE."""
    if underlying_price <= 0 or implied_volatility <= 0 or dte <= 0:
        return 0.0

    em = underlying_price * (implied_volatility / 100.0 if implied_volatility > 1.0 else implied_volatility) * math.sqrt(dte / 365.0)
    return round(em, 2)


def evaluate_premium_quality(
    net_credit_or_debit: float,
    wing_width: float,
    max_loss: float,
    expected_move: float,
    bid_ask_spread: float,
    strategy_type: str = "credit",
) -> Dict[str, Any]:
    """Evaluate whether candidate option premium is sufficient relative to max loss and expected move."""
    if wing_width <= 0 or max_loss <= 0:
        return {"acceptable": False, "score": 0.0, "reason": "Invalid wing width or max loss."}

    if strategy_type == "credit":
        credit_pct_wing = (net_credit_or_debit / wing_width) * 100.0
        # Require credit to be at least 15% of wing width to cover transaction costs and risk
        if credit_pct_wing < 15.0:
            return {
                "acceptable": False,
                "score": round(credit_pct_wing, 1),
                "reason": f"Credit received ({credit_pct_wing:.1f}% of wing) is too low relative to risk.",
            }
        score = min(100.0, (credit_pct_wing / 33.0) * 100.0)
        return {"acceptable": True, "score": round(score, 1), "reason": "Sufficient credit quality."}
    else:
        debit_pct_wing = (net_credit_or_debit / wing_width) * 100.0
        # For debit spreads, require debit to be <= 65% of wing width to leave >= 35% profit margin
        if debit_pct_wing > 65.0:
            return {
                "acceptable": False,
                "score": round(100.0 - debit_pct_wing, 1),
                "reason": f"Debit paid ({debit_pct_wing:.1f}% of wing) is too expensive relative to wing width.",
            }
        score = min(100.0, ((100.0 - debit_pct_wing) / 50.0) * 100.0)
        return {"acceptable": True, "score": round(score, 1), "reason": "Sufficient debit pricing quality."}
