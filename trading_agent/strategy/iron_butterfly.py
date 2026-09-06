"""
Iron Butterfly Strategy Builder & Evaluator.
Constructs a 4-leg defined-risk Iron Butterfly:
- Short Call: ATM
- Short Put: ATM (same strike as Short Call)
- Long Call: OTM Call wing (higher strike)
- Long Put: OTM Put wing (lower strike)

Max Profit = Net Credit Received
Max Loss   = Wing Width - Net Credit
"""

import logging
from typing import Dict, List, Optional, Any, Union, Tuple
from datetime import datetime

from trading_agent.models import OptionChainSnapshot, OptionContract, StrategyCandidate, OptionLeg
from trading_agent.strategy.base import BaseOptionsStrategy
from trading_agent.strategy.option_universe import get_instrument_metadata

logger = logging.getLogger(__name__)


class IronButterflyStrategy(BaseOptionsStrategy):
    """Iron Butterfly strategy implementation."""

    def __init__(self, target_dte_min: int = 20, target_dte_max: int = 45):
        self.target_dte_min = target_dte_min
        self.target_dte_max = target_dte_max

    def strategy_name(self) -> str:
        return "iron_butterfly"

    def supports_market_regime(self, regime_context: Dict[str, Any]) -> bool:
        trend = str(regime_context.get("trend", "")).lower()
        vol = str(regime_context.get("volatility", "")).lower()
        return trend in ("range",) and vol in ("normal", "high")

    def generate_candidate(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[StrategyCandidate]:
        """Construct optimal Iron Butterfly candidate."""
        contracts = option_chain.contracts if isinstance(option_chain, OptionChainSnapshot) else option_chain
        symbol = option_chain.underlying_symbol if isinstance(option_chain, OptionChainSnapshot) else (contracts[0].underlying_symbol if contracts else "UNKNOWN")

        if not contracts or underlying_price <= 0:
            return None

        meta = get_instrument_metadata(symbol)

        valid_contracts = [c for c in contracts if c.bid > 0 and c.ask > c.bid]
        if not valid_contracts:
            return None

        ref_dt = datetime.strptime(current_date, "%Y-%m-%d") if current_date else datetime.now()
        exp_groups: Dict[str, List[OptionContract]] = {}
        for c in valid_contracts:
            exp_groups.setdefault(c.expiration_date, []).append(c)

        best_exp = None
        best_dte_diff = 999
        for exp_str in exp_groups.keys():
            try:
                exp_dt = datetime.strptime(exp_str, "%Y-%m-%d")
                dte = (exp_dt - ref_dt).days
                if self.target_dte_min <= dte <= self.target_dte_max:
                    diff = abs(dte - 30)
                    if diff < best_dte_diff:
                        best_dte_diff = diff
                        best_exp = exp_str
            except Exception:
                continue

        if not best_exp:
            best_exp = list(exp_groups.keys())[0] if exp_groups else None

        if not best_exp:
            return None

        exp_contracts = exp_groups[best_exp]
        calls = [c for c in exp_contracts if c.contract_type == "call"]
        puts = [c for c in exp_contracts if c.contract_type == "put"]

        if not calls or not puts:
            return None

        atm_call = min(calls, key=lambda c: abs(c.strike_price - underlying_price))
        atm_put = min(puts, key=lambda c: abs(c.strike_price - atm_call.strike_price))

        wing_distance = 5.0 if underlying_price < 200.0 else 10.0
        otm_call = min(calls, key=lambda c: abs(c.strike_price - (atm_call.strike_price + wing_distance)))
        otm_put = min(puts, key=lambda c: abs(c.strike_price - (atm_put.strike_price - wing_distance)))

        if otm_call.strike_price <= atm_call.strike_price or otm_put.strike_price >= atm_put.strike_price:
            return None

        net_credit = (atm_call.bid + atm_put.bid) - (otm_call.ask + otm_put.ask)
        if net_credit <= 0:
            return None

        wing_width = otm_call.strike_price - atm_call.strike_price
        max_loss = (wing_width - net_credit) * meta.contract_multiplier
        max_profit = net_credit * meta.contract_multiplier

        if max_loss <= 0 or max_profit <= 0:
            return None

        legs = [
            OptionLeg(contract=otm_put, quantity=1, side="BUY", position_intent="BUY_TO_OPEN"),
            OptionLeg(contract=atm_put, quantity=1, side="SELL", position_intent="SELL_TO_OPEN"),
            OptionLeg(contract=atm_call, quantity=1, side="SELL", position_intent="SELL_TO_OPEN"),
            OptionLeg(contract=otm_call, quantity=1, side="BUY", position_intent="BUY_TO_OPEN"),
        ]

        return StrategyCandidate(
            underlying=symbol,
            strategy_name="iron_butterfly",
            expiration=best_exp,
            legs=legs,
            entry_credit=round(net_credit, 2),
            entry_debit=0.0,
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            wing_width=round(wing_width, 2),
            reward_risk_ratio=round(max_profit / max_loss, 2) if max_loss > 0 else 0.0,
            underlying_price=underlying_price,
        )

    def validate_candidate(self, candidate: Any) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if not isinstance(candidate, StrategyCandidate) or candidate.strategy_name != "iron_butterfly":
            return False, ["Not a valid IronButterfly StrategyCandidate."]
        if len(candidate.legs) != 4:
            reasons.append("Iron Butterfly requires exactly 4 legs.")
        if candidate.entry_credit <= 0:
            reasons.append("Entry credit must be strictly positive.")
        return len(reasons) == 0, reasons

    def calculate_max_profit(self, candidate: Any) -> float:
        return float(getattr(candidate, "max_profit", 0.0))

    def calculate_max_loss(self, candidate: Any) -> float:
        return float(getattr(candidate, "max_loss", 0.0))

    def calculate_breakevens(self, candidate: Any) -> Tuple[float, ...]:
        if not candidate or not candidate.legs:
            return (0.0, 0.0)
        center_strike = candidate.legs[1].contract.strike_price
        credit = candidate.entry_credit
        return (round(center_strike - credit, 2), round(center_strike + credit, 2))

    def score_candidate(self, candidate: Any, market_context: Optional[Dict[str, Any]] = None) -> float:
        if not candidate:
            return 0.0
        return 75.0
