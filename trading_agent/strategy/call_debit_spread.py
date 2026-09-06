"""
Bull Call Debit Spread Strategy Builder & Evaluator.
Constructs a defined-risk bullish call debit spread:
- Long Call: Lower strike (ITM / ATM)
- Short Call: Higher strike (OTM)

Max Profit = (Short Strike - Long Strike) - Net Debit
Max Loss   = Net Debit
"""

import logging
from typing import Dict, List, Optional, Any, Union, Tuple
from datetime import datetime

from trading_agent.models import OptionChainSnapshot, OptionContract, StrategyCandidate, OptionLeg
from trading_agent.strategy.base import BaseOptionsStrategy
from trading_agent.strategy.option_universe import get_instrument_metadata

logger = logging.getLogger(__name__)


class CallDebitSpreadStrategy(BaseOptionsStrategy):
    """Bull Call Debit Spread strategy implementation."""

    def __init__(self, target_dte_min: int = 20, target_dte_max: int = 45):
        self.target_dte_min = target_dte_min
        self.target_dte_max = target_dte_max

    def strategy_name(self) -> str:
        return "call_debit_spread"

    def supports_market_regime(self, regime_context: Dict[str, Any]) -> bool:
        trend = str(regime_context.get("trend", "")).lower()
        vol = str(regime_context.get("volatility", "")).lower()
        return trend in ("bullish", "strong_bull", "range") and vol in ("low", "normal")

    def generate_candidate(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[StrategyCandidate]:
        """Construct optimal Bull Call Debit Spread candidate."""
        contracts = option_chain.contracts if isinstance(option_chain, OptionChainSnapshot) else option_chain
        symbol = option_chain.underlying_symbol if isinstance(option_chain, OptionChainSnapshot) else (contracts[0].underlying_symbol if contracts else "UNKNOWN")

        if not contracts or underlying_price <= 0:
            return None

        meta = get_instrument_metadata(symbol)

        valid_calls = [
            c for c in contracts
            if c.contract_type == "call" and c.bid > 0 and c.ask > c.bid
        ]

        if not valid_calls:
            return None

        ref_dt = datetime.strptime(current_date, "%Y-%m-%d") if current_date else datetime.now()
        exp_groups: Dict[str, List[OptionContract]] = {}
        for c in valid_calls:
            exp_groups.setdefault(c.expiration_date, []).append(c)

        best_exp = None
        best_dte_diff = 999
        for exp_str, exp_contracts in exp_groups.items():
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

        calls_in_exp = sorted(exp_groups[best_exp], key=lambda x: x.strike_price)
        if len(calls_in_exp) < 2:
            return None

        long_call = None
        for c in calls_in_exp:
            if c.strike_price <= underlying_price * 1.01:
                long_call = c

        if not long_call:
            long_call = calls_in_exp[0]

        short_call = None
        for c in calls_in_exp:
            if c.strike_price > long_call.strike_price:
                short_call = c
                break

        if not short_call or short_call.strike_price <= long_call.strike_price:
            return None

        entry_debit = (long_call.ask - short_call.bid)
        if entry_debit <= 0:
            return None

        wing_width = short_call.strike_price - long_call.strike_price
        max_profit = (wing_width - entry_debit) * meta.contract_multiplier
        max_loss = entry_debit * meta.contract_multiplier

        if max_profit <= 0 or max_loss <= 0:
            return None

        legs = [
            OptionLeg(contract=long_call, quantity=1, side="BUY", position_intent="BUY_TO_OPEN"),
            OptionLeg(contract=short_call, quantity=1, side="SELL", position_intent="SELL_TO_OPEN"),
        ]

        return StrategyCandidate(
            underlying=symbol,
            strategy_name="call_debit_spread",
            expiration=best_exp,
            legs=legs,
            entry_debit=round(entry_debit, 2),
            entry_credit=0.0,
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            wing_width=round(wing_width, 2),
            reward_risk_ratio=round(max_profit / max_loss, 2) if max_loss > 0 else 0.0,
            underlying_price=underlying_price,
        )

    def validate_candidate(self, candidate: Any) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if not isinstance(candidate, StrategyCandidate) or candidate.strategy_name != "call_debit_spread":
            return False, ["Not a valid CallDebitSpread StrategyCandidate."]
        if len(candidate.legs) != 2:
            reasons.append("Call Debit Spread requires exactly 2 legs.")
        if candidate.entry_debit <= 0:
            reasons.append("Entry debit must be strictly positive.")
        return len(reasons) == 0, reasons

    def calculate_max_profit(self, candidate: Any) -> float:
        return float(getattr(candidate, "max_profit", 0.0))

    def calculate_max_loss(self, candidate: Any) -> float:
        return float(getattr(candidate, "max_loss", 0.0))

    def calculate_breakevens(self, candidate: Any) -> Tuple[float, ...]:
        if not candidate or not candidate.legs:
            return (0.0,)
        long_strike = candidate.legs[0].contract.strike_price
        be = long_strike + candidate.entry_debit
        return (round(be, 2),)

    def score_candidate(self, candidate: Any, market_context: Optional[Dict[str, Any]] = None) -> float:
        if not candidate:
            return 0.0
        return 75.0
