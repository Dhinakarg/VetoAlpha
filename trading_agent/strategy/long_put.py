"""
Deterministic Long Put Directional Options Strategy Builder and Validator.
Constructs a single-leg Long Put debit candidate for Strong Bear / Bear regimes.

CRITICAL SAFETY & EXECUTION BOUNDARIES:
- Maximum loss is strictly finite and equal to premium paid (debit * 100).
- Zero undefined risk. No naked short legs.
- Fails closed on missing quotes, inverted markets, stale data, or missing Greeks.
- Does NOT submit orders or access execution endpoints directly.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Union, Tuple

from trading_agent.models import (
    OptionContract,
    OptionChainSnapshot,
    StrategyLeg,
    StrategyCandidate,
)
from trading_agent.strategy.base import BaseOptionsStrategy

logger = logging.getLogger(__name__)


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_quote_fresh(quote_ts: Optional[str], current_time: Optional[datetime] = None, max_age_seconds: int = 900) -> bool:
    if not quote_ts:
        return False
    dt = _parse_ts(quote_ts)
    if not dt:
        return False
    now_dt = current_time or datetime.now(timezone.utc)
    age = (now_dt - dt).total_seconds()
    return 0 <= age <= max_age_seconds


def build_long_put(
    option_chain: Union[OptionChainSnapshot, List[OptionContract]],
    underlying_price: float,
    target_delta: float = -0.50,
    target_dte_min: int = 20,
    target_dte_max: int = 45,
    max_bid_ask_spread_pct: float = 0.20,
    min_open_interest: int = 50,
    current_date: Optional[str] = None,
    current_time: Optional[datetime] = None,
) -> Optional[StrategyCandidate]:
    """Construct a deterministic Long Put debit candidate."""
    if not option_chain or underlying_price <= 0:
        return None

    contracts: List[OptionContract] = (
        option_chain.contracts if isinstance(option_chain, OptionChainSnapshot) else option_chain
    )
    if not contracts:
        return None

    symbol = option_chain.underlying_symbol if isinstance(option_chain, OptionChainSnapshot) else contracts[0].underlying_symbol
    now_utc = current_time or datetime.now(timezone.utc)
    today_dt = datetime.strptime(current_date, "%Y-%m-%d").date() if current_date else now_utc.date()

    # 1. Filter expirations by DTE range
    exp_candidates: Dict[str, List[OptionContract]] = {}
    for c in contracts:
        if c.contract_type != "put":
            continue
        try:
            exp_d = datetime.strptime(c.expiration_date, "%Y-%m-%d").date()
            dte = (exp_d - today_dt).days
            if target_dte_min <= dte <= target_dte_max:
                exp_candidates.setdefault(c.expiration_date, []).append(c)
        except Exception:
            continue

    if not exp_candidates:
        return None

    # Choose expiration closest to target DTE (30 days)
    target_dte = 30
    sorted_exp = sorted(
        exp_candidates.keys(),
        key=lambda exp: abs((datetime.strptime(exp, "%Y-%m-%d").date() - today_dt).days - target_dte),
    )
    chosen_exp = sorted_exp[0]
    chosen_contracts = exp_candidates[chosen_exp]
    chosen_dte = (datetime.strptime(chosen_exp, "%Y-%m-%d").date() - today_dt).days

    # 2. Filter liquid PUT contracts
    valid_puts: List[OptionContract] = []
    for c in chosen_contracts:
        if c.bid <= 0 or c.ask <= c.bid:
            continue
        mid = (c.bid + c.ask) / 2.0
        if mid <= 0:
            continue
        spread_pct = (c.ask - c.bid) / mid
        if spread_pct > max_bid_ask_spread_pct:
            continue
        if c.open_interest < min_open_interest:
            continue
        if c.delta is None:
            continue
        if not _is_quote_fresh(c.quote_timestamp, current_time=now_utc):
            continue
        valid_puts.append(c)

    if not valid_puts:
        return None

    # 3. Select put closest to target delta (e.g. -0.50 ATM)
    valid_puts.sort(key=lambda c: abs((c.delta or 0.0) - target_delta))
    long_put_contract = valid_puts[0]

    # 4. Calculate pricing & economics
    debit_per_share = round((long_put_contract.bid + long_put_contract.ask) / 2.0, 2)
    if debit_per_share <= 0:
        return None

    max_loss = round(debit_per_share * 100.0, 2)
    # Maximum theoretical profit for long put is (strike - debit) * 100
    max_profit = round((long_put_contract.strike_price - debit_per_share) * 100.0, 2)
    breakeven = round(long_put_contract.strike_price - debit_per_share, 2)

    leg = StrategyLeg(
        contract=long_put_contract,
        side="buy",
        quantity=1,
        leg_type="put_long",
        intent="open_exposure",
    )

    candidate = StrategyCandidate(
        strategy_name="long_put",
        underlying=symbol,
        underlying_symbol=symbol,
        expiration=chosen_exp,
        expiration_date=chosen_exp,
        legs=[leg],
        direction="bearish",
        entry_credit=0.0,
        entry_debit=debit_per_share,
        net_credit=0.0,
        net_credit_or_debit=-debit_per_share,
        max_profit=max_profit,
        max_loss=max_loss,
        max_risk=max_loss,
        breakeven=breakeven,
        breakevens=[breakeven],
        wing_width=0.0,
        dte=chosen_dte,
        short_delta=None,
        probability_estimate_if_available=abs(long_put_contract.delta),
        required_buying_power=max_loss,
        liquidity_metrics={
            "spread_pct": (long_put_contract.ask - long_put_contract.bid) / ((long_put_contract.bid + long_put_contract.ask) / 2.0),
            "open_interest": long_put_contract.open_interest,
        },
        quote_timestamps={"put_long": long_put_contract.quote_timestamp or ""},
        rationale=f"Long Put on {symbol} exp {chosen_exp} strike ${long_put_contract.strike_price:.2f} (debit ${debit_per_share:.2f}, delta {long_put_contract.delta:.2f})",
    )
    return candidate


class LongPutStrategy(BaseOptionsStrategy):
    """Encapsulated Long Put Directional Options Strategy."""

    def strategy_name(self) -> str:
        return "long_put"

    def supports_market_regime(self, regime_context: Dict[str, Any]) -> bool:
        trend = str(regime_context.get("trend", "")).lower()
        vol = str(regime_context.get("volatility", "")).lower()
        return trend in ("bearish", "strong_bear") and vol in ("low", "normal")

    def generate_candidate(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[StrategyCandidate]:
        cfg = dict(config or {})
        return build_long_put(
            option_chain=option_chain,
            underlying_price=underlying_price,
            target_delta=float(cfg.get("LONG_PUT_TARGET_DELTA", -0.50)),
            target_dte_min=int(cfg.get("TARGET_DTE_MIN", 20)),
            target_dte_max=int(cfg.get("TARGET_DTE_MAX", 45)),
            max_bid_ask_spread_pct=float(cfg.get("MAX_BID_ASK_SPREAD_PCT", 0.20)),
            min_open_interest=int(cfg.get("MIN_OPEN_INTEREST", 50)),
            current_date=current_date,
        )

    def validate_candidate(self, candidate: Any) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        if not isinstance(candidate, StrategyCandidate) or candidate.strategy_name != "long_put":
            return False, ["Not a valid LongPut StrategyCandidate."]
        if len(candidate.legs) != 1 or candidate.legs[0].side != "buy":
            reasons.append("Long Put requires exactly 1 long put leg.")
        if candidate.entry_debit <= 0:
            reasons.append("Entry debit must be strictly positive.")
        if candidate.max_loss <= 0:
            reasons.append("Max loss must be strictly positive.")
        return len(reasons) == 0, reasons

    def calculate_max_profit(self, candidate: Any) -> float:
        return float(getattr(candidate, "max_profit", 0.0))

    def calculate_max_loss(self, candidate: Any) -> float:
        return float(getattr(candidate, "max_loss", 0.0))

    def calculate_breakevens(self, candidate: Any) -> Tuple[float, ...]:
        be = getattr(candidate, "breakeven", 0.0)
        return (float(be),)

    def score_candidate(self, candidate: Any, market_context: Optional[Dict[str, Any]] = None) -> float:
        if not candidate:
            return 0.0
        return 80.0
