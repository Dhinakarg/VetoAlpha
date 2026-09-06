"""
Deterministic Iron Condor Options Strategy Implementation.
Conforms to BaseOptionsStrategy interface.
Contains NO LLM calls, NO broker execution endpoints.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union, Tuple

from trading_agent.models import (
    OptionChainSnapshot,
    OptionContract,
    CondorLeg,
    IronCondor,
)
from trading_agent.strategy.base import BaseOptionsStrategy

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Parse date string YYYY-MM-DD to datetime object."""
    return datetime.strptime(date_str[:10], "%Y-%m-%d")


class IronCondorStrategy(BaseOptionsStrategy):
    """Deterministic selection and valuation engine for 4-leg Iron Condor options strategies."""

    def __init__(
        self,
        target_short_delta: float = 0.15,
        wing_width: float = 5.0,
        target_dte_min: int = 20,
        target_dte_max: int = 45,
        max_bid_ask_spread_pct: float = 0.20,
        min_open_interest: int = 50,
        min_net_credit: float = 0.10,
    ):
        self.target_short_delta = target_short_delta
        self.wing_width = wing_width
        self.target_dte_min = target_dte_min
        self.target_dte_max = target_dte_max
        self.max_bid_ask_spread_pct = max_bid_ask_spread_pct
        self.min_open_interest = min_open_interest
        self.min_net_credit = min_net_credit

    def strategy_name(self) -> str:
        """Return the unique identifier for Iron Condor strategy."""
        return "iron_condor"

    def supports_market_regime(self, regime_context: Dict[str, Any]) -> bool:
        """Evaluate if market conditions support range-bound delta-neutral strategies."""
        adx = regime_context.get("adx")
        bb_percentile = regime_context.get("bb_width_percentile")
        is_range_bound = regime_context.get("is_range_bound")

        # Explicit range bound flag
        if is_range_bound is not None:
            return bool(is_range_bound)

        # Technical indicators fallback (ADX < 25.0 indicates non-trending / range-bound)
        if adx is not None and adx >= 25.0:
            return False

        if bb_percentile is not None and bb_percentile >= 80.0:
            return False

        return True

    def _validate_leg_liquidity(
        self,
        contract: OptionContract,
        max_spread_pct: float,
        min_oi: int,
    ) -> bool:
        """Validate single leg liquidity and Greek presence."""
        if contract.bid <= 0 or contract.ask <= contract.bid or contract.strike_price <= 0:
            return False

        if contract.delta is None:
            return False

        if contract.open_interest < min_oi:
            return False

        mid = (contract.bid + contract.ask) / 2.0
        if mid <= 0:
            return False

        spread_pct = (contract.ask - contract.bid) / mid
        if spread_pct > max_spread_pct:
            return False

        return True

    def generate_candidate(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[IronCondor]:
        """Construct a deterministic IronCondor candidate from option chain or return None."""
        condor, _ = self.build_condor_with_reasons(
            option_chain=option_chain,
            underlying_price=underlying_price,
            current_date=current_date,
            config=config,
            **kwargs,
        )
        return condor

    def build_condor_with_reasons(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        target_short_delta: Optional[float] = None,
        wing_width: Optional[float] = None,
        target_dte_min: Optional[int] = None,
        target_dte_max: Optional[int] = None,
        **kwargs: Any,
    ) -> Tuple[Optional[IronCondor], List[str]]:
        """Construct IronCondor and return tuple (IronCondor | None, rejection_reasons)."""
        reasons = []
        cfg = dict(config or {})
        if target_short_delta is not None:
            cfg["target_short_delta"] = target_short_delta
        if wing_width is not None:
            cfg["wing_width"] = wing_width
        if target_dte_min is not None:
            cfg["target_dte_min"] = target_dte_min
        if target_dte_max is not None:
            cfg["target_dte_max"] = target_dte_max

        target_delta = cfg.get("target_short_delta", self.target_short_delta)
        width = cfg.get("wing_width", self.wing_width)
        dte_min = cfg.get("target_dte_min", self.target_dte_min)
        dte_max = cfg.get("target_dte_max", self.target_dte_max)
        max_spread = cfg.get("max_bid_ask_spread_pct", self.max_bid_ask_spread_pct)
        min_oi = cfg.get("min_open_interest", self.min_open_interest)
        min_credit = cfg.get("min_net_credit", self.min_net_credit)

        if underlying_price <= 0:
            reasons.append("Invalid underlying price (<= 0).")
            return None, reasons

        # Extract contracts
        contracts: List[OptionContract] = []
        if isinstance(option_chain, OptionChainSnapshot):
            contracts = option_chain.contracts
        elif isinstance(option_chain, list):
            contracts = option_chain

        if not contracts:
            reasons.append("Empty option chain provided.")
            return None, reasons

        # Determine reference date
        ref_date = _parse_date(current_date) if current_date else datetime.now(timezone.utc).replace(tzinfo=None)

        # 1. Filter contracts by DTE range & basic leg liquidity
        valid_contracts = []
        for c in contracts:
            try:
                exp_dt = _parse_date(c.expiration_date)
                dte = (exp_dt - ref_date).days
                if dte_min <= dte <= dte_max:
                    if self._validate_leg_liquidity(c, max_spread, min_oi):
                        valid_contracts.append((c, dte))
            except Exception:
                continue

        if not valid_contracts:
            reasons.append(f"No valid liquid contracts found within DTE range [{dte_min}, {dte_max}].")
            return None, reasons

        # Group valid contracts by expiration date
        by_expiration: Dict[str, List[OptionContract]] = {}
        for c, dte in valid_contracts:
            by_expiration.setdefault(c.expiration_date, []).append(c)

        # Iterate over expiration dates to find candidate condor
        for exp_date, exp_contracts in by_expiration.items():
            puts = [c for c in exp_contracts if c.contract_type == "put" and c.strike_price < underlying_price]
            calls = [c for c in exp_contracts if c.contract_type == "call" and c.strike_price > underlying_price]

            if not puts or not calls:
                continue

            # 2. Select Short Put (abs(delta) closest to target_delta)
            short_put = min(puts, key=lambda c: abs(abs(c.delta) - target_delta))

            # 3. Select Short Call (abs(delta) closest to target_delta)
            short_call = min(calls, key=lambda c: abs(abs(c.delta) - target_delta))

            # 4. Select Long Put Wing (strike == short_put_strike - wing_width)
            target_long_put_strike = round(short_put.strike_price - width, 2)
            long_puts = [c for c in exp_contracts if c.contract_type == "put" and abs(c.strike_price - target_long_put_strike) < 0.01]
            if not long_puts:
                continue
            long_put = long_puts[0]

            # 5. Select Long Call Wing (strike == short_call_strike + wing_width)
            target_long_call_strike = round(short_call.strike_price + width, 2)
            long_calls = [c for c in exp_contracts if c.contract_type == "call" and abs(c.strike_price - target_long_call_strike) < 0.01]
            if not long_calls:
                continue
            long_call = long_calls[0]

            # 6. Verify Structural Geometry
            if not (long_put.strike_price < short_put.strike_price < underlying_price < short_call.strike_price < long_call.strike_price):
                continue

            # 7. Calculate Financial Metrics
            put_credit = short_put.bid - long_put.ask
            call_credit = short_call.bid - long_call.ask
            net_credit = round(put_credit + call_credit, 2)

            if net_credit <= 0 or net_credit < min_credit:
                reasons.append(f"Net credit (${net_credit:.2f}) is non-positive or below minimum requirement (${min_credit:.2f}).")
                continue

            max_profit = round(net_credit * 100.0, 2)
            max_risk = round((width * 100.0) - max_profit, 2)

            if max_risk <= 0:
                reasons.append(f"Max risk (${max_risk:.2f}) is non-positive.")
                continue

            lower_be = round(short_put.strike_price - net_credit, 2)
            upper_be = round(short_call.strike_price + net_credit, 2)

            # Build CondorLeg structures
            leg_long_put = CondorLeg(contract=long_put, side="buy", quantity=1, leg_type="put_long")
            leg_short_put = CondorLeg(contract=short_put, side="sell", quantity=1, leg_type="put_short")
            leg_short_call = CondorLeg(contract=short_call, side="sell", quantity=1, leg_type="call_short")
            leg_long_call = CondorLeg(contract=long_call, side="buy", quantity=1, leg_type="call_long")

            candidate = IronCondor(
                underlying_symbol=short_put.underlying_symbol,
                put_long=leg_long_put,
                put_short=leg_short_put,
                call_short=leg_short_call,
                call_long=leg_long_call,
                net_credit=net_credit,
                max_risk=max_risk,
                max_profit=max_profit,
                wing_width=width,
                expiration_date=exp_date,
                lower_breakeven=lower_be,
                upper_breakeven=upper_be,
            )

            is_valid, validation_errors = self.validate_candidate(candidate)
            if not is_valid:
                reasons.extend(validation_errors)
                continue

            return candidate, []

        reasons.append("No valid 4-leg Iron Condor structure matched all strike and delta constraints.")
        return None, reasons

    def validate_candidate(self, candidate: Any) -> Tuple[bool, List[str]]:
        """Validate structure, leg geometry, Greeks, and defined-risk constraints."""
        rejections = []
        if not isinstance(candidate, IronCondor):
            return False, ["Candidate is not an IronCondor instance."]

        pl = candidate.put_long.contract.strike_price
        ps = candidate.put_short.contract.strike_price
        cs = candidate.call_short.contract.strike_price
        cl = candidate.call_long.contract.strike_price

        if not (pl < ps < cs < cl):
            rejections.append(f"Geometry violation: Strikes must satisfy pl ({pl}) < ps ({ps}) < cs ({cs}) < cl ({cl}).")

        put_width = round(ps - pl, 2)
        call_width = round(cl - cs, 2)
        if put_width != call_width:
            rejections.append(f"Wing asymmetry: Put wing (${put_width:.2f}) != Call wing (${call_width:.2f}).")

        if candidate.net_credit <= 0:
            rejections.append(f"Credit violation: Net credit (${candidate.net_credit:.2f}) must be strictly positive.")

        if candidate.max_risk <= 0:
            rejections.append(f"Risk violation: Max risk (${candidate.max_risk:.2f}) must be strictly positive.")

        for leg_name, leg in [("put_long", candidate.put_long), ("put_short", candidate.put_short), ("call_short", candidate.call_short), ("call_long", candidate.call_long)]:
            if leg.contract.delta is None:
                rejections.append(f"Missing Greek: Leg '{leg_name}' has no delta value.")

        return len(rejections) == 0, rejections

    def calculate_max_profit(self, candidate: IronCondor) -> float:
        """Calculate maximum profit in dollars per contract ($ credit * 100)."""
        return round(float(candidate.net_credit) * 100.0, 2)

    def calculate_max_loss(self, candidate: IronCondor) -> float:
        """Calculate maximum loss in dollars per contract (($ width - credit) * 100)."""
        return round((float(candidate.wing_width) * 100.0) - self.calculate_max_profit(candidate), 2)

    def calculate_breakevens(self, candidate: IronCondor) -> Tuple[float, float]:
        """Calculate lower and upper breakeven underlying prices."""
        lower_be = round(candidate.put_short.contract.strike_price - candidate.net_credit, 2)
        upper_be = round(candidate.call_short.contract.strike_price + candidate.net_credit, 2)
        return (lower_be, upper_be)

    def score_candidate(
        self,
        candidate: IronCondor,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Score candidate attractiveness (0.0 to 100.0)."""
        ctx = market_context or {}
        # 1. Credit-to-width ratio (ideal >= 30% -> up to 50 points)
        credit_ratio = candidate.net_credit / candidate.wing_width if candidate.wing_width > 0 else 0.0
        credit_score = min(50.0, (credit_ratio / 0.35) * 50.0)

        # 2. Delta symmetry score (up to 25 points)
        ps_delta = abs(candidate.put_short.contract.delta or 0.15)
        cs_delta = abs(candidate.call_short.contract.delta or 0.15)
        delta_diff = abs(ps_delta - cs_delta)
        delta_score = max(0.0, 25.0 - (delta_diff * 100.0))

        # 3. IV Rank contribution (up to 25 points)
        iv_rank = float(ctx.get("iv_rank", 50.0))
        iv_score = min(25.0, (iv_rank / 100.0) * 25.0)

        total_score = round(credit_score + delta_score + iv_score, 2)
        return min(100.0, max(0.0, total_score))
