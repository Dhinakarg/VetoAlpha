"""
Active Multi-Strategy Options Position Lifecycle and Exit Decision Manager.
Evaluates multi-leg defined-risk positions (Iron Condor, Bull Put Spread, Bear Call Spread)
as unified structures across 4 deterministic exit criteria:
  1. Take Profit (current debit <= entry credit * (1 - TP_PCT))
  2. Stop Loss (current debit >= entry credit * SL_MULT)
  3. Time Exit (DTE <= TIME_EXIT_DTE)
  4. Delta Breach (short leg absolute delta >= DELTA_BREACH_THRESHOLD)

CRITICAL INVARIANTS:
- Evaluates complete multi-leg structures as unified packages.
- Strategy-aware: does NOT assume 4 legs or Iron Condor geometry.
- Order execution remains isolated behind broker safety locks.
- Contains NO single-leg exit logic and NO LLM calls.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union, Tuple

from trading_agent.models import PositionAction, IronCondor, OptionContract, StrategyCandidate

logger = logging.getLogger(__name__)


def _parse_date(d_val: Union[str, datetime, None]) -> Optional[datetime]:
    if not d_val:
        return None
    if isinstance(d_val, datetime):
        return d_val if d_val.tzinfo else d_val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.strptime(str(d_val)[:10], "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _get_contract_field(leg: Any, field_name: str, fallback: Any = None) -> Any:
    """Extract a field from an option leg contract object or dictionary."""
    if not leg:
        return fallback
    contract = getattr(leg, "contract", None) if hasattr(leg, "contract") else (leg.get("contract") if isinstance(leg, dict) else leg)
    if isinstance(contract, dict):
        return contract.get(field_name, fallback)
    return getattr(contract, field_name, fallback)


def _get_leg_sym(leg: Any) -> str:
    return str(_get_contract_field(leg, "symbol", "") or "")


def _get_leg_bid(leg: Any) -> float:
    return float(_get_contract_field(leg, "bid", 0.0) or 0.0)


def _get_leg_ask(leg: Any) -> float:
    return float(_get_contract_field(leg, "ask", 0.0) or 0.0)


def _get_leg_delta(leg: Any) -> Optional[float]:
    val = _get_contract_field(leg, "delta", None)
    return float(val) if val is not None else None


def calculate_position_metrics(
    position: Union[IronCondor, StrategyCandidate, Dict[str, Any], Any],
    current_quotes: Optional[Dict[str, Any]] = None,
    reference_date: Optional[Union[str, datetime]] = None,
) -> Dict[str, Any]:
    """Calculate core mark-to-market and Greek metrics for an active defined-risk options position.
    
    Supports:
      - iron_condor (4 legs)
      - bull_put_spread (2 legs)
      - bear_call_spread (2 legs)
    """
    quotes = current_quotes or {}
    ref_dt = _parse_date(reference_date) or datetime.now(timezone.utc)

    # 1. Identify Strategy Type and Metadata
    strategy_name = "iron_condor"
    pos_id = "POS_000"
    symbol = ""
    expiration = ""
    entry_credit = 0.0
    quantity = 1

    legs: List[Any] = []

    if isinstance(position, StrategyCandidate):
        strategy_name = position.strategy_name or "options_strategy"
        symbol = position.underlying or position.underlying_symbol or ""
        expiration = position.expiration or position.expiration_date or ""
        entry_credit = float(position.entry_credit or position.net_credit or 0.0)
        pos_id = f"POS_{symbol}_{expiration}_{strategy_name}"
        legs = position.legs or []

    elif isinstance(position, IronCondor):
        strategy_name = "iron_condor"
        symbol = position.underlying_symbol or ""
        expiration = position.expiration_date or ""
        entry_credit = float(position.net_credit or 0.0)
        pos_id = f"POS_{symbol}_{expiration}_iron_condor"
        legs = [position.put_long, position.put_short, position.call_short, position.call_long]

    elif isinstance(position, dict):
        pos_id = str(position.get("position_id", "POS_000"))
        strategy_name = str(position.get("strategy", position.get("strategy_name", "")))
        symbol = str(position.get("symbol", position.get("underlying", "")))
        expiration = str(position.get("expiration_date", position.get("expiration", "")))
        entry_credit = float(position.get("entry_credit", position.get("net_credit", 0.0)))
        quantity = int(position.get("quantity", 1))

        condor = position.get("condor")
        candidate = position.get("candidate", position.get("strategy_candidate"))
        legs_dict = position.get("legs")

        pl = position.get("put_long") or (getattr(condor, "put_long", None) if condor else None) or (condor.get("put_long") if isinstance(condor, dict) else None)
        ps = position.get("put_short") or (getattr(condor, "put_short", None) if condor else None) or (condor.get("put_short") if isinstance(condor, dict) else None)
        cs = position.get("call_short") or (getattr(condor, "call_short", None) if condor else None) or (condor.get("call_short") if isinstance(condor, dict) else None)
        cl = position.get("call_long") or (getattr(condor, "call_long", None) if condor else None) or (condor.get("call_long") if isinstance(condor, dict) else None)

        if pl and ps and cs and cl:
            strategy_name = "iron_condor"
            legs = [pl, ps, cs, cl]
        elif candidate and isinstance(candidate, StrategyCandidate):
            strategy_name = candidate.strategy_name
            symbol = candidate.underlying or symbol
            expiration = candidate.expiration or expiration
            entry_credit = candidate.entry_credit or entry_credit
            legs = candidate.legs or []
        elif legs_dict and isinstance(legs_dict, list):
            legs = legs_dict
            if not strategy_name:
                if len(legs) == 4:
                    strategy_name = "iron_condor"
                elif len(legs) == 2:
                    leg_types = [str(_get_contract_field(l, "contract_type", "")).lower() for l in legs]
                    strategy_name = "bull_put_spread" if all(t == "put" for t in leg_types) else "bear_call_spread"
        else:
            if not strategy_name:
                strategy_name = "iron_condor"

    else:
        # Generic object
        pos_id = str(getattr(position, "position_id", "POS_000"))
        strategy_name = str(getattr(position, "strategy_name", getattr(position, "strategy", "iron_condor")))
        symbol = str(getattr(position, "symbol", getattr(position, "underlying", "")))
        expiration = str(getattr(position, "expiration_date", getattr(position, "expiration", "")))
        entry_credit = float(getattr(position, "entry_credit", getattr(position, "net_credit", 0.0)))
        quantity = int(getattr(position, "quantity", 1))
        legs = getattr(position, "legs", [])
        if not legs:
            condor = getattr(position, "condor", None)
            if condor:
                pl = getattr(condor, "put_long", None)
                ps = getattr(condor, "put_short", None)
                cs = getattr(condor, "call_short", None)
                cl = getattr(condor, "call_long", None)
                legs = [pl, ps, cs, cl]

    # Helper quote query
    def _quote_val(sym: str, field: str, fallback: float = 0.0) -> float:
        q = quotes.get(sym)
        if q is None:
            return fallback
        val = q.get(field) if isinstance(q, dict) else getattr(q, field, None)
        return float(val) if val is not None else fallback

    def _quote_delta(sym: str, fallback: Optional[float] = None) -> Optional[float]:
        q = quotes.get(sym)
        if q is None:
            return fallback
        val = q.get("delta") if isinstance(q, dict) else getattr(q, "delta", None)
        return float(val) if val is not None else fallback

    # 2. Strategy-Specific Metrics Computation
    short_put_delta = None
    short_call_delta = None
    current_debit_to_close = 0.0

    if strategy_name == "iron_condor" and len(legs) >= 4:
        put_long, put_short, call_short, call_long = legs[0], legs[1], legs[2], legs[3]
        pl_sym = _get_leg_sym(put_long)
        ps_sym = _get_leg_sym(put_short)
        cs_sym = _get_leg_sym(call_short)
        cl_sym = _get_leg_sym(call_long)

        pl_bid = _quote_val(pl_sym, "bid", fallback=_get_leg_bid(put_long))
        ps_ask = _quote_val(ps_sym, "ask", fallback=_get_leg_ask(put_short))
        cs_ask = _quote_val(cs_sym, "ask", fallback=_get_leg_ask(call_short))
        cl_bid = _quote_val(cl_sym, "bid", fallback=_get_leg_bid(call_long))

        current_debit_to_close = round((ps_ask - pl_bid) + (cs_ask - cl_bid), 2)
        short_put_delta = _quote_delta(ps_sym, fallback=_get_leg_delta(put_short))
        short_call_delta = _quote_delta(cs_sym, fallback=_get_leg_delta(call_short))

    elif strategy_name == "bull_put_spread" and len(legs) >= 2:
        # Find short put and long put
        short_puts = [l for l in legs if str(getattr(l, "side", "")).lower() == "sell" or str(getattr(l, "leg_type", "")).endswith("short") or str(l.get("side", "") if isinstance(l, dict) else "").lower() == "sell"]
        long_puts = [l for l in legs if str(getattr(l, "side", "")).lower() == "buy" or str(getattr(l, "leg_type", "")).endswith("long") or str(l.get("side", "") if isinstance(l, dict) else "").lower() == "buy"]

        short_put = short_puts[0] if short_puts else legs[0]
        long_put = long_puts[0] if long_puts else legs[1]

        sp_sym = _get_leg_sym(short_put)
        lp_sym = _get_leg_sym(long_put)

        sp_ask = _quote_val(sp_sym, "ask", fallback=_get_leg_ask(short_put))
        lp_bid = _quote_val(lp_sym, "bid", fallback=_get_leg_bid(long_put))

        current_debit_to_close = round(sp_ask - lp_bid, 2)
        short_put_delta = _quote_delta(sp_sym, fallback=_get_leg_delta(short_put))

    elif strategy_name == "bear_call_spread" and len(legs) >= 2:
        # Find short call and long call
        short_calls = [l for l in legs if str(getattr(l, "side", "")).lower() == "sell" or str(getattr(l, "leg_type", "")).endswith("short") or str(l.get("side", "") if isinstance(l, dict) else "").lower() == "sell"]
        long_calls = [l for l in legs if str(getattr(l, "side", "")).lower() == "buy" or str(getattr(l, "leg_type", "")).endswith("long") or str(l.get("side", "") if isinstance(l, dict) else "").lower() == "buy"]

        short_call = short_calls[0] if short_calls else legs[0]
        long_call = long_calls[0] if long_calls else legs[1]

        sc_sym = _get_leg_sym(short_call)
        lc_sym = _get_leg_sym(long_call)

        sc_ask = _quote_val(sc_sym, "ask", fallback=_get_leg_ask(short_call))
        lc_bid = _quote_val(lc_sym, "bid", fallback=_get_leg_bid(long_call))

        current_debit_to_close = round(sc_ask - lc_bid, 2)
        short_call_delta = _quote_delta(sc_sym, fallback=_get_leg_delta(short_call))

    elif strategy_name in ("long_call", "long_put") and len(legs) >= 1:
        long_leg = legs[0]
        leg_sym = _get_leg_sym(long_leg)
        leg_bid = _quote_val(leg_sym, "bid", fallback=_get_leg_bid(long_leg))
        current_debit_to_close = round(leg_bid, 2)
        entry_debit = float(getattr(position, "entry_debit", 0.0) if hasattr(position, "entry_debit") else (position.get("entry_debit", 0.0) if isinstance(position, dict) else 0.0))
        if entry_debit <= 0 and entry_credit > 0:
            entry_debit = entry_credit
        unrealized_pnl = round((current_debit_to_close - entry_debit) * 100.0 * quantity, 2)

    else:
        # Fallback for arbitrary multi-leg package
        current_debit_to_close = entry_credit
        unrealized_pnl = round((entry_credit - current_debit_to_close) * 100.0 * quantity, 2)

    if strategy_name not in ("long_call", "long_put"):
        unrealized_pnl = round((entry_credit - current_debit_to_close) * 100.0 * quantity, 2)

    exp_dt = _parse_date(expiration)
    dte = max(0, (exp_dt.date() - ref_dt.date()).days) if exp_dt else 30

    return {
        "position_id": pos_id,
        "strategy": strategy_name,
        "symbol": symbol,
        "expiration": expiration,
        "entry_credit": entry_credit,
        "current_debit_to_close": current_debit_to_close,
        "unrealized_pnl": unrealized_pnl,
        "dte": dte,
        "short_put_delta": short_put_delta,
        "short_call_delta": short_call_delta,
        "quantity": quantity,
        "legs_count": len(legs),
    }


def calculate_condor_metrics(
    position: Union[IronCondor, Dict[str, Any], Any],
    current_quotes: Optional[Dict[str, Any]] = None,
    reference_date: Optional[Union[str, datetime]] = None,
) -> Dict[str, Any]:
    """Backwards-compatible wrapper for 4-leg Iron Condor metrics."""
    return calculate_position_metrics(position, current_quotes, reference_date)


def evaluate_position_exit(
    position: Union[IronCondor, StrategyCandidate, Dict[str, Any], Any],
    current_quotes: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate an active options position and return a structured exit decision."""
    cfg = config or {}
    TP_PCT_OF_MAX_CREDIT = float(cfg.get("TP_PCT_OF_MAX_CREDIT", cfg.get("tp_pct_of_max_credit", 0.50)))
    SL_MULT_OF_CREDIT = float(cfg.get("SL_MULT_OF_CREDIT", cfg.get("sl_mult_of_credit", 2.0)))
    TIME_EXIT_DTE = int(cfg.get("TIME_EXIT_DTE", cfg.get("time_exit_dte", 3)))
    DELTA_BREACH_THRESHOLD = float(cfg.get("DELTA_BREACH_THRESHOLD", cfg.get("delta_breach_threshold", 0.35)))
    ref_dt = cfg.get("REFERENCE_DATE")

    metrics = calculate_position_metrics(position, current_quotes=current_quotes, reference_date=ref_dt)

    pos_id = metrics["position_id"]
    strategy = metrics["strategy"]
    entry_credit = metrics["entry_credit"]
    current_debit = metrics["current_debit_to_close"]
    dte = metrics["dte"]
    short_put_delta = metrics["short_put_delta"]
    short_call_delta = metrics["short_call_delta"]

    action = "hold"
    reason = f"Holding: position within normal parameters for {strategy}."

    # Directional Debit Strategies Exits (Long Call / Long Put)
    if strategy in ("long_call", "long_put"):
        entry_debit = float(getattr(position, "entry_debit", 0.0) if hasattr(position, "entry_debit") else (position.get("entry_debit", 0.0) if isinstance(position, dict) else 0.0))
        if entry_debit <= 0 and entry_credit > 0:
            entry_debit = entry_credit

        current_val = current_debit  # holds current bid in debit positions
        tp_target = round(entry_debit * 1.50, 2)
        sl_target = round(entry_debit * 0.50, 2)

        if entry_debit > 0 and current_val >= tp_target:
            action = "close"
            reason = f"Take Profit (+50%) triggered for {strategy}: current value (${current_val:.2f}) >= target (${tp_target:.2f})."
        elif entry_debit > 0 and current_val <= sl_target:
            action = "close"
            reason = f"Stop Loss (-50%) triggered for {strategy}: current value (${current_val:.2f}) <= threshold (${sl_target:.2f})."
        elif dte <= 5:
            action = "close"
            reason = f"Time Exit triggered for {strategy}: {dte} DTE <= 5 days (avoid accelerated theta decay)."

    # Credit Strategies Exits (Iron Condor, Bull Put, Bear Call)
    else:
        # 1. Take Profit: current_debit <= entry_credit * (1 - TP_PCT)
        tp_threshold = round(entry_credit * (1.0 - TP_PCT_OF_MAX_CREDIT), 2)
        if entry_credit > 0 and current_debit <= tp_threshold:
            action = "close"
            reason = f"Take Profit target reached: current debit (${current_debit:.2f}) <= threshold (${tp_threshold:.2f}) for {strategy}."

        # 2. Stop Loss: current_debit >= entry_credit * SL_MULT
        sl_threshold = round(entry_credit * SL_MULT_OF_CREDIT, 2)
        if action == "hold" and entry_credit > 0 and current_debit >= sl_threshold:
            action = "close"
            reason = f"Stop Loss triggered: current debit (${current_debit:.2f}) >= threshold (${sl_threshold:.2f}) for {strategy}."

        # 3. Time Exit: DTE <= TIME_EXIT_DTE
        if action == "hold" and dte <= TIME_EXIT_DTE:
            action = "close"
            reason = f"Time Exit triggered: {dte} DTE <= threshold ({TIME_EXIT_DTE} DTE) for {strategy}."

        # 4. Delta Breach Exit (Strategy-Aware)
        if action == "hold":
            if strategy == "iron_condor":
                if (short_put_delta is not None and abs(short_put_delta) >= DELTA_BREACH_THRESHOLD) or (
                    short_call_delta is not None and abs(short_call_delta) >= DELTA_BREACH_THRESHOLD
                ):
                    action = "close"
                    reason = f"Delta Breach triggered on Iron Condor: short put delta ({short_put_delta}) or call delta ({short_call_delta}) >= {DELTA_BREACH_THRESHOLD}."
            elif strategy == "bull_put_spread":
                if short_put_delta is not None and abs(short_put_delta) >= DELTA_BREACH_THRESHOLD:
                    action = "close"
                    reason = f"Delta Breach triggered on Bull Put Spread: short put delta ({short_put_delta}) >= {DELTA_BREACH_THRESHOLD}."
            elif strategy == "bear_call_spread":
                if short_call_delta is not None and abs(short_call_delta) >= DELTA_BREACH_THRESHOLD:
                    action = "close"
                    reason = f"Delta Breach triggered on Bear Call Spread: short call delta ({short_call_delta}) >= {DELTA_BREACH_THRESHOLD}."

    return {
        "position_id": pos_id,
        "strategy": strategy,
        "action": action,
        "reason": reason,
        "metrics": metrics,
    }


def review_open_positions(
    positions: List[Union[IronCondor, StrategyCandidate, Dict[str, Any], Any]],
    current_quotes: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[PositionAction]:
    """Evaluate active positions across all strategies and generate typed PositionAction records."""
    actions: List[PositionAction] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for pos in positions:
        decision = evaluate_position_exit(pos, current_quotes=current_quotes, config=config)
        metrics = decision["metrics"]
        act_type = decision["action"]

        action = PositionAction(
            action_type=act_type,
            action=act_type,
            symbol=metrics["symbol"],
            position_id=metrics["position_id"],
            expiration=metrics["expiration"],
            reason=decision["reason"],
            entry_credit=metrics["entry_credit"],
            current_debit_to_close=metrics["current_debit_to_close"],
            unrealized_pnl=metrics["unrealized_pnl"],
            dte=metrics["dte"],
            short_put_delta=metrics["short_put_delta"],
            short_call_delta=metrics["short_call_delta"],
            strategy=metrics["strategy"],
            metrics=metrics,
            timestamp=now_iso,
        )
        actions.append(action)

    return actions


class PositionManager:
    """Position lifecycle manager overseeing defined-risk options holdings."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.active_positions: Dict[str, Any] = {}

    def add_position(self, position_id: str, position: Any) -> None:
        """Register an active position by ID."""
        self.active_positions[position_id] = position

    def remove_position(self, position_id: str) -> Optional[Any]:
        """Remove a closed position by ID."""
        return self.active_positions.pop(position_id, None)

    def evaluate_positions(
        self,
        current_quotes: Optional[Dict[str, Any]] = None,
    ) -> List[PositionAction]:
        """Evaluate all tracked active positions."""
        return review_open_positions(
            list(self.active_positions.values()),
            current_quotes=current_quotes,
            config=self.config,
        )

    def evaluate_open_positions(
        self,
        positions: List[Any],
        current_quotes: Optional[Dict[str, Any]] = None,
    ) -> List[PositionAction]:
        """Review provided list of open positions against defined-risk exit rules."""
        return review_open_positions(positions, current_quotes=current_quotes, config=self.config)

    def evaluate_position(
        self,
        position: Any,
        current_quotes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluate a single position and return strategy exit decision dict."""
        return evaluate_position_exit(position, current_quotes=current_quotes, config=self.config)
