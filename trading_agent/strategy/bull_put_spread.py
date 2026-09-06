"""
Deterministic Bull Put Credit Spread Strategy Engine.
Implements BaseOptionsStrategy protocol and build_bull_put_spread().
Strictly deterministic: NO LLM calls, NO broker execution endpoints.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Union, Tuple

from trading_agent.models import (
    OptionChainSnapshot,
    OptionContract,
    StrategyCandidate,
    StrategyLeg,
)
from trading_agent.strategy.base import BaseOptionsStrategy

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Parse date string YYYY-MM-DD to datetime object."""
    return datetime.strptime(date_str[:10], "%Y-%m-%d")


def _validate_leg_liquidity(
    contract: OptionContract,
    max_spread_pct: float = 0.20,
    min_oi: int = 50,
    max_quote_age_seconds: int = 900,
    now_utc: Optional[datetime] = None,
) -> Tuple[bool, Optional[str]]:
    """Validate single leg liquidity, market non-inversion, fresh quote, and Greek presence."""
    if contract.strike_price <= 0:
        return False, "Strike price must be positive."

    # 1. Valid bid/ask and non-inversion
    if contract.bid <= 0:
        return False, "Bid price must be > 0."
    if contract.ask <= contract.bid:
        return False, f"Inverted or zero bid/ask market (bid: {contract.bid}, ask: {contract.ask})."

    mid = (contract.bid + contract.ask) / 2.0
    if mid <= 0:
        return False, "Mid price must be > 0."

    # 2. Spread percentage check
    spread_pct = (contract.ask - contract.bid) / mid
    if spread_pct > max_spread_pct:
        return False, f"Bid/ask spread percentage ({spread_pct:.1%}) exceeds maximum ({max_spread_pct:.1%})."

    # 3. Open interest check
    if contract.open_interest < min_oi:
        return False, f"Open interest ({contract.open_interest}) below minimum requirement ({min_oi})."

    # 4. Greeks / Delta check
    if contract.delta is None:
        return False, "Missing delta / Greeks value."

    # 5. Quote freshness check
    if contract.quote_timestamp:
        try:
            ts_dt = datetime.fromisoformat(contract.quote_timestamp)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            ref_now = now_utc or datetime.now(timezone.utc)
            age_sec = (ref_now - ts_dt).total_seconds()
            if age_sec > max_quote_age_seconds:
                return False, f"Stale quote timestamp (age {int(age_sec)}s > {max_quote_age_seconds}s)."
        except Exception:
            return False, "Unparseable quote timestamp."

    return True, None


def build_bull_put_spread(
    option_chain: Union[OptionChainSnapshot, List[OptionContract]],
    underlying_price: float,
    target_short_delta: float = 0.20,
    wing_width: float = 5.0,
    target_dte_min: int = 20,
    target_dte_max: int = 45,
    current_date: Optional[str] = None,
    max_bid_ask_spread_pct: float = 0.20,
    min_open_interest: int = 50,
    min_net_credit: float = 0.10,
    **kwargs: Any,
) -> Optional[StrategyCandidate]:
    """Construct a deterministic Bull Put Credit Spread candidate.
    
    1. Filter expirations to target DTE range.
    2. Find liquid puts below underlying price.
    3. Select short put closest to target delta (~0.15-0.25).
    4. Select long put exactly wing_width below short put.
    5. Validate liquidity, non-inversion, Greeks, freshness.
    6. Calculate conservative natural credit: short_put.bid - long_put.ask.
    7. Calculate max_profit, max_loss, breakeven.
    8. Return StrategyCandidate or None.
    """
    candidate, _ = build_bull_put_spread_with_reasons(
        option_chain=option_chain,
        underlying_price=underlying_price,
        target_short_delta=target_short_delta,
        wing_width=wing_width,
        target_dte_min=target_dte_min,
        target_dte_max=target_dte_max,
        current_date=current_date,
        max_bid_ask_spread_pct=max_bid_ask_spread_pct,
        min_open_interest=min_open_interest,
        min_net_credit=min_net_credit,
        **kwargs,
    )
    return candidate


def build_bull_put_spread_with_reasons(
    option_chain: Union[OptionChainSnapshot, List[OptionContract]],
    underlying_price: float,
    target_short_delta: float = 0.20,
    wing_width: float = 5.0,
    target_dte_min: int = 20,
    target_dte_max: int = 45,
    current_date: Optional[str] = None,
    max_bid_ask_spread_pct: float = 0.20,
    min_open_interest: int = 50,
    min_net_credit: float = 0.10,
    **kwargs: Any,
) -> Tuple[Optional[StrategyCandidate], List[str]]:
    """Construct Bull Put Spread candidate and return (candidate, rejection_reasons)."""
    reasons: List[str] = []

    if underlying_price <= 0:
        reasons.append("Invalid underlying price (<= 0).")
        return None, reasons

    contracts: List[OptionContract] = []
    if isinstance(option_chain, OptionChainSnapshot):
        contracts = option_chain.contracts
    elif isinstance(option_chain, list):
        contracts = option_chain

    if not contracts:
        reasons.append("Empty option chain provided.")
        return None, reasons

    ref_date = _parse_date(current_date) if current_date else datetime.now(timezone.utc).replace(tzinfo=None)
    now_utc = datetime.now(timezone.utc)

    # 1. Filter puts in target DTE range
    valid_puts = []
    for c in contracts:
        if c.contract_type != "put":
            continue
        try:
            exp_dt = _parse_date(c.expiration_date)
            dte = (exp_dt - ref_date).days
            if target_dte_min <= dte <= target_dte_max:
                valid_puts.append((c, dte))
        except Exception:
            continue

    if not valid_puts:
        reasons.append(f"No put contracts found within target DTE range [{target_dte_min}, {target_dte_max}].")
        return None, reasons

    # Group by expiration date
    by_expiration: Dict[str, List[Tuple[OptionContract, int]]] = {}
    for c, dte in valid_puts:
        by_expiration.setdefault(c.expiration_date, []).append((c, dte))

    for exp_date, put_items in by_expiration.items():
        exp_puts = [c for c, _ in put_items]
        dte_val = put_items[0][1]

        # 2. Puts strictly below underlying price
        otm_puts = [c for c in exp_puts if c.strike_price < underlying_price]
        if not otm_puts:
            continue

        # 3. Filter liquid short put candidates
        liquid_short_puts = []
        for p in otm_puts:
            is_liq, err = _validate_leg_liquidity(p, max_bid_ask_spread_pct, min_open_interest, now_utc=now_utc)
            if is_liq:
                liquid_short_puts.append(p)
            else:
                reasons.append(f"Short put strike {p.strike_price} disqualified: {err}")

        if not liquid_short_puts:
            continue

        # Select short put closest to target delta (~0.15 - 0.25)
        short_put = min(liquid_short_puts, key=lambda c: abs(abs(c.delta or 0.0) - target_short_delta))

        # 4. Long Put wing exactly wing_width below short put
        target_long_strike = round(short_put.strike_price - wing_width, 2)
        matching_longs = [c for c in exp_puts if abs(c.strike_price - target_long_strike) < 0.01]

        if not matching_longs:
            reasons.append(f"Missing long put wing at strike {target_long_strike:.2f} (short put {short_put.strike_price:.2f} - width {wing_width:.2f}).")
            continue

        long_put = matching_longs[0]

        # Validate long put leg liquidity & Greeks
        is_long_liq, long_err = _validate_leg_liquidity(long_put, max_bid_ask_spread_pct, min_open_interest, now_utc=now_utc)
        if not is_long_liq:
            reasons.append(f"Long put wing strike {long_put.strike_price} disqualified: {long_err}")
            continue

        # 5. Geometry Check: long_put < short_put < underlying_price
        if not (long_put.strike_price < short_put.strike_price < underlying_price):
            reasons.append("Geometry violation: long put strike must be strictly below short put strike.")
            continue

        # 6. Wing width exact check
        actual_width = round(short_put.strike_price - long_put.strike_price, 2)
        if actual_width != round(wing_width, 2):
            reasons.append(f"Wing width mismatch: actual width ({actual_width}) != configured width ({wing_width}).")
            continue

        # 7. Conservative Natural Credit: short_put.bid - long_put.ask
        net_credit = round(short_put.bid - long_put.ask, 2)
        if net_credit <= 0 or net_credit < min_net_credit:
            reasons.append(f"Net credit (${net_credit:.2f}) is non-positive or below minimum requirement (${min_net_credit:.2f}).")
            continue

        # 8. Defined Risk Calculations
        max_profit = round(net_credit * 100.0, 2)
        max_loss = round((wing_width * 100.0) - max_profit, 2)

        if max_loss <= 0:
            reasons.append(f"Max loss (${max_loss:.2f}) is non-positive.")
            continue

        breakeven = round(short_put.strike_price - net_credit, 2)

        # 9. Build Standardized StrategyCandidate
        leg_short = StrategyLeg(
            contract=short_put,
            side="sell",
            quantity=1,
            leg_type="put_short",
            intent="SELL_TO_OPEN",
        )
        leg_long = StrategyLeg(
            contract=long_put,
            side="buy",
            quantity=1,
            leg_type="put_long",
            intent="BUY_TO_OPEN",
        )

        quote_ts = {}
        if short_put.quote_timestamp:
            quote_ts[short_put.symbol] = short_put.quote_timestamp
        if long_put.quote_timestamp:
            quote_ts[long_put.symbol] = long_put.quote_timestamp

        candidate = StrategyCandidate(
            strategy_name="bull_put_spread",
            underlying=short_put.underlying_symbol,
            underlying_symbol=short_put.underlying_symbol,
            expiration=exp_date,
            expiration_date=exp_date,
            legs=[leg_short, leg_long],
            entry_credit=net_credit,
            net_credit=net_credit,
            max_profit=max_profit,
            max_loss=max_loss,
            max_risk=max_loss,
            breakeven=breakeven,
            breakevens=[breakeven],
            wing_width=wing_width,
            dte=dte_val,
            short_delta=short_put.delta,
            liquidity_metrics={
                "short_put_spread_pct": round((short_put.ask - short_put.bid) / ((short_put.ask + short_put.bid) / 2.0), 3),
                "long_put_spread_pct": round((long_put.ask - long_put.bid) / ((long_put.ask + long_put.bid) / 2.0), 3),
                "short_put_oi": short_put.open_interest,
                "long_put_oi": long_put.open_interest,
            },
            quote_timestamps=quote_ts,
            rationale=f"Bull Put Spread selected at strikes {long_put.strike_price}/{short_put.strike_price} with net credit ${net_credit:.2f}.",
            diagnostics={
                "short_put_delta": short_put.delta,
                "long_put_delta": long_put.delta,
                "underlying_price": underlying_price,
            },
        )

        return candidate, []

    if not reasons:
        reasons.append("No valid Bull Put Spread candidate matched all strike and delta constraints.")
    return None, reasons


class BullPutSpreadStrategy(BaseOptionsStrategy):
    """Deterministic Bull Put Credit Spread Strategy Engine."""

    def __init__(
        self,
        target_short_delta: float = 0.20,
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
        return "bull_put_spread"

    def supports_market_regime(self, regime_context: Dict[str, Any]) -> bool:
        """Bull Put Spreads favor neutral to moderately bullish conditions."""
        catalyst_risk = str(regime_context.get("catalyst_risk", "low")).lower()
        if catalyst_risk == "high":
            return False
        return True

    def generate_candidate(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[StrategyCandidate]:
        cfg = dict(config or {})
        target_delta = cfg.get("target_short_delta", self.target_short_delta)
        width = cfg.get("wing_width", self.wing_width)
        dte_min = cfg.get("target_dte_min", self.target_dte_min)
        dte_max = cfg.get("target_dte_max", self.target_dte_max)
        max_spread = cfg.get("max_bid_ask_spread_pct", self.max_bid_ask_spread_pct)
        min_oi = cfg.get("min_open_interest", self.min_open_interest)
        min_credit = cfg.get("min_net_credit", self.min_net_credit)

        return build_bull_put_spread(
            option_chain=option_chain,
            underlying_price=underlying_price,
            target_short_delta=target_delta,
            wing_width=width,
            target_dte_min=dte_min,
            target_dte_max=dte_max,
            current_date=current_date,
            max_bid_ask_spread_pct=max_spread,
            min_open_interest=min_oi,
            min_net_credit=min_credit,
            **kwargs,
        )

    def validate_candidate(self, candidate: Any) -> Tuple[bool, List[str]]:
        rejections = []
        if not isinstance(candidate, StrategyCandidate):
            return False, ["Candidate is not a StrategyCandidate instance."]

        if candidate.strategy_name != "bull_put_spread":
            rejections.append(f"Invalid strategy name: {candidate.strategy_name}")

        if len(candidate.legs) != 2:
            rejections.append(f"Expected 2 legs, got {len(candidate.legs)}")
            return False, rejections

        short_leg = candidate.legs[0]
        long_leg = candidate.legs[1]

        if long_leg.contract.strike_price >= short_leg.contract.strike_price:
            rejections.append("Long put strike must be strictly below short put strike.")

        if candidate.entry_credit <= 0:
            rejections.append("Entry credit must be strictly positive.")

        if candidate.max_loss <= 0:
            rejections.append("Max loss must be strictly positive.")

        return len(rejections) == 0, rejections

    def calculate_max_profit(self, candidate: StrategyCandidate) -> float:
        return round(float(candidate.entry_credit) * 100.0, 2)

    def calculate_max_loss(self, candidate: StrategyCandidate) -> float:
        width = candidate.wing_width or 5.0
        return round((float(width) * 100.0) - self.calculate_max_profit(candidate), 2)

    def calculate_breakevens(self, candidate: StrategyCandidate) -> Tuple[float]:
        short_strike = candidate.legs[0].contract.strike_price
        return (round(short_strike - candidate.entry_credit, 2),)

    def score_candidate(
        self,
        candidate: StrategyCandidate,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        ctx = market_context or {}
        width = candidate.wing_width or 5.0
        credit_ratio = candidate.entry_credit / width if width > 0 else 0.0
        credit_score = min(60.0, (credit_ratio / 0.30) * 60.0)

        short_delta = abs(candidate.short_delta or 0.20)
        delta_score = max(0.0, 20.0 - abs(short_delta - self.target_short_delta) * 100.0)

        iv_rank = float(ctx.get("iv_rank", 50.0))
        iv_score = min(20.0, (iv_rank / 100.0) * 20.0)

        return min(100.0, max(0.0, round(credit_score + delta_score + iv_score, 2)))
