"""
Deterministic Risk Gate authorization layer for options trading.
Evaluates every trade against 15 strict risk, portfolio, market, liquidity,
and strategy-specific structural rules.
Strategy-agnostic: supports Iron Condor, Bull Put Spread, Bear Call Spread, and any StrategyCandidate.
Contains NO LLM calls and NO network calls.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union, Tuple

from trading_agent.models import (
    TradeProposal,
    AccountSnapshot,
    RiskDecision,
    IronCondor,
    CondorLeg,
    StrategyCandidate,
    StrategyLeg,
)

logger = logging.getLogger(__name__)


def _parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime object."""
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def _get_contract_attr(leg: Any, field_name: str) -> Any:
    """Extract an attribute from a leg's option contract object or dict."""
    if not leg:
        return None
    contract = getattr(leg, "contract", None) if hasattr(leg, "contract") else (leg.get("contract") if isinstance(leg, dict) else None)
    if contract is None:
        # Check if leg itself is contract
        contract = leg

    if isinstance(contract, dict):
        return contract.get(field_name)
    return getattr(contract, field_name, None)


def evaluate_trade(
    proposed_trade: Union[TradeProposal, StrategyCandidate, IronCondor, Dict[str, Any]],
    account: Union[AccountSnapshot, Dict[str, Any]],
    open_positions: Optional[List[Union[Dict[str, Any], Any]]] = None,
    open_orders: Optional[List[Union[Dict[str, Any], Any]]] = None,
    daily_pnl: float = 0.0,
    config: Optional[Dict[str, Any]] = None,
) -> RiskDecision:
    """Evaluate a proposed trade (Iron Condor, Bull Put Spread, Bear Call Spread, etc.)
    against all 15 deterministic risk and authorization rules.
    
    Evaluates EVERY rule (does not stop after first rejection) and accumulates all violations.
    """
    cfg = config or {}
    open_positions = open_positions or []
    open_orders = open_orders or []

    # Configurable parameter thresholds
    MAX_LOSS_PER_TRADE_USD = float(cfg.get("MAX_LOSS_PER_TRADE_USD", cfg.get("max_loss_per_trade_usd", 2500.0)))
    MAX_PCT_ACCOUNT_PER_TRADE = float(cfg.get("MAX_PCT_ACCOUNT_PER_TRADE", cfg.get("max_pct_account_per_trade", 0.02)))
    MAX_CONCURRENT_POSITIONS = int(cfg.get("MAX_CONCURRENT_POSITIONS", cfg.get("max_concurrent_positions", 5)))
    MAX_TOTAL_EXPOSURE_PCT = float(cfg.get("MAX_TOTAL_EXPOSURE_PCT", cfg.get("max_total_exposure_pct", 0.15)))
    MAX_DAILY_LOSS_USD = float(cfg.get("MAX_DAILY_LOSS_USD", cfg.get("max_daily_loss_usd", 3000.0)))
    MAX_BID_ASK_SPREAD_PCT = float(cfg.get("MAX_BID_ASK_SPREAD_PCT", cfg.get("max_bid_ask_spread_pct", 0.20)))
    MIN_OPEN_INTEREST = int(cfg.get("MIN_OPEN_INTEREST", cfg.get("min_open_interest", 50)))
    MAX_QUOTE_AGE_SECONDS = int(cfg.get("MAX_QUOTE_AGE_SECONDS", cfg.get("max_quote_age_seconds", 900)))
    MARKET_OPEN = bool(cfg.get("MARKET_OPEN", cfg.get("market_open", True)))

    rejections: List[str] = []
    warnings: List[str] = []

    # 1. Normalize proposal / candidate attributes
    proposal_id = "PROP_000"
    symbol = ""
    quantity = 1
    strategy_name = "iron_condor"
    expiration = ""
    max_risk = 0.0
    net_credit = 0.0
    legs_list: List[Tuple[str, Any]] = []

    if isinstance(proposed_trade, StrategyCandidate):
        strategy_name = proposed_trade.strategy_name or "options_strategy"
        symbol = proposed_trade.underlying or proposed_trade.underlying_symbol or ""
        expiration = proposed_trade.expiration or proposed_trade.expiration_date or ""
        max_risk = float(proposed_trade.max_loss or proposed_trade.max_risk or 0.0)
        net_credit = float(proposed_trade.entry_credit or proposed_trade.net_credit or 0.0)
        quantity = 1
        for i, leg in enumerate(proposed_trade.legs or []):
            legs_list.append((getattr(leg, "leg_type", f"leg_{i+1}"), leg))

    elif isinstance(proposed_trade, IronCondor):
        strategy_name = "iron_condor"
        symbol = proposed_trade.underlying_symbol or ""
        expiration = proposed_trade.expiration_date or ""
        max_risk = float(proposed_trade.max_risk or 0.0)
        net_credit = float(proposed_trade.net_credit or 0.0)
        quantity = 1
        legs_list = [
            ("put_long", proposed_trade.put_long),
            ("put_short", proposed_trade.put_short),
            ("call_short", proposed_trade.call_short),
            ("call_long", proposed_trade.call_long),
        ]

    elif isinstance(proposed_trade, TradeProposal):
        proposal_id = proposed_trade.proposal_id or "PROP_000"
        symbol = proposed_trade.symbol or ""
        quantity = int(proposed_trade.quantity or 1)

        # Check if proposal has StrategyCandidate or IronCondor
        candidate = getattr(proposed_trade, "candidate", None) or getattr(proposed_trade, "strategy_candidate", None)
        condor = getattr(proposed_trade, "condor", None)

        if candidate and isinstance(candidate, StrategyCandidate):
            strategy_name = candidate.strategy_name or "options_strategy"
            expiration = candidate.expiration or candidate.expiration_date or ""
            max_risk = float(candidate.max_loss or candidate.max_risk or 0.0)
            net_credit = float(candidate.entry_credit or candidate.net_credit or 0.0)
            for i, leg in enumerate(candidate.legs or []):
                legs_list.append((getattr(leg, "leg_type", f"leg_{i+1}"), leg))
        elif condor:
            strategy_name = "iron_condor"
            expiration = str(getattr(condor, "expiration_date", "") if hasattr(condor, "expiration_date") else condor.get("expiration_date", ""))
            max_risk = float(getattr(condor, "max_risk", 0.0) if hasattr(condor, "max_risk") else condor.get("max_risk", 0.0))
            net_credit = float(getattr(condor, "net_credit", 0.0) if hasattr(condor, "net_credit") else condor.get("net_credit", 0.0))
            pl = getattr(condor, "put_long", None) if hasattr(condor, "put_long") else condor.get("put_long")
            ps = getattr(condor, "put_short", None) if hasattr(condor, "put_short") else condor.get("put_short")
            cs = getattr(condor, "call_short", None) if hasattr(condor, "call_short") else condor.get("call_short")
            cl = getattr(condor, "call_long", None) if hasattr(condor, "call_long") else condor.get("call_long")
            legs_list = [("put_long", pl), ("put_short", ps), ("call_short", cs), ("call_long", cl)]
        else:
            rejections.append("Proposed trade does not contain a valid strategy candidate or Iron Condor structure.")

    elif isinstance(proposed_trade, dict):
        proposal_id = str(proposed_trade.get("proposal_id", "PROP_000"))
        symbol = str(proposed_trade.get("symbol", proposed_trade.get("underlying", "")))
        quantity = int(proposed_trade.get("quantity", 1))
        strategy_name = str(proposed_trade.get("strategy_name", "iron_condor"))
        expiration = str(proposed_trade.get("expiration", proposed_trade.get("expiration_date", "")))
        max_risk = float(proposed_trade.get("max_loss", proposed_trade.get("max_risk", 0.0)))
        net_credit = float(proposed_trade.get("entry_credit", proposed_trade.get("net_credit", 0.0)))

        condor = proposed_trade.get("condor")
        candidate = proposed_trade.get("candidate", proposed_trade.get("strategy_candidate"))
        legs_dict = proposed_trade.get("legs")

        if condor:
            strategy_name = "iron_condor"
            if not expiration:
                expiration = str(getattr(condor, "expiration_date", "") if hasattr(condor, "expiration_date") else condor.get("expiration_date", ""))
            if max_risk <= 0:
                max_risk = float(getattr(condor, "max_risk", 0.0) if hasattr(condor, "max_risk") else condor.get("max_risk", 0.0))
            if net_credit <= 0:
                net_credit = float(getattr(condor, "net_credit", 0.0) if hasattr(condor, "net_credit") else condor.get("net_credit", 0.0))
            pl = getattr(condor, "put_long", None) if hasattr(condor, "put_long") else condor.get("put_long")
            ps = getattr(condor, "put_short", None) if hasattr(condor, "put_short") else condor.get("put_short")
            cs = getattr(condor, "call_short", None) if hasattr(condor, "call_short") else condor.get("call_short")
            cl = getattr(condor, "call_long", None) if hasattr(condor, "call_long") else condor.get("call_long")
            legs_list = [("put_long", pl), ("put_short", ps), ("call_short", cs), ("call_long", cl)]
        elif candidate and isinstance(candidate, StrategyCandidate):
            strategy_name = candidate.strategy_name
            expiration = candidate.expiration or expiration
            max_risk = candidate.max_loss or max_risk
            net_credit = candidate.entry_credit or net_credit
            for i, leg in enumerate(candidate.legs or []):
                legs_list.append((getattr(leg, "leg_type", f"leg_{i+1}"), leg))
        elif legs_dict and isinstance(legs_dict, list):
            for i, leg in enumerate(legs_dict):
                leg_t = leg.get("leg_type", f"leg_{i+1}") if isinstance(leg, dict) else getattr(leg, "leg_type", f"leg_{i+1}")
                legs_list.append((leg_t, leg))
        else:
            rejections.append("Proposed trade does not contain a valid Iron Condor or StrategyCandidate structure.")

    if not symbol:
        rejections.append("Proposed trade is missing underlying symbol.")
    if not expiration:
        rejections.append("Proposed trade is missing expiration date.")

    # Extract account attributes
    equity = float(getattr(account, "equity", 0.0) if hasattr(account, "equity") else account.get("equity", 0.0))
    cash = float(getattr(account, "cash", 0.0) if hasattr(account, "cash") else account.get("cash", 0.0))
    buying_power = float(getattr(account, "buying_power", 0.0) if hasattr(account, "buying_power") else account.get("buying_power", 0.0))
    account_status = str(getattr(account, "status", "ACTIVE") if hasattr(account, "status") else account.get("status", "ACTIVE"))

    # Fail closed if critical structure missing
    if not legs_list and not rejections:
        rejections.append("Proposed trade does not contain any option legs.")

    for leg_name, leg in legs_list:
        c_sym = _get_contract_attr(leg, "symbol")
        if not c_sym:
            rejections.append(f"Rule 12 Failed: Missing option contract symbol on leg {leg_name}.")

    trade_max_loss = round(max_risk * quantity, 2)

    # -------------------------------------------------------------
    # RULE 1: Max loss per trade <= MAX_LOSS_PER_TRADE_USD
    # -------------------------------------------------------------
    if trade_max_loss > MAX_LOSS_PER_TRADE_USD:
        rejections.append(
            f"Rule 1 Failed: Proposed trade max loss (${trade_max_loss:.2f}) exceeds MAX_LOSS_PER_TRADE_USD (${MAX_LOSS_PER_TRADE_USD:.2f})."
        )

    # -------------------------------------------------------------
    # RULE 2: Max loss per trade <= MAX_PCT_ACCOUNT_PER_TRADE * account equity
    # -------------------------------------------------------------
    max_allowed_by_equity = round(MAX_PCT_ACCOUNT_PER_TRADE * equity, 2)
    if trade_max_loss > max_allowed_by_equity:
        rejections.append(
            f"Rule 2 Failed: Proposed trade max loss (${trade_max_loss:.2f}) exceeds {MAX_PCT_ACCOUNT_PER_TRADE*100:.1f}% of account equity (${max_allowed_by_equity:.2f})."
        )

    # -------------------------------------------------------------
    # RULE 3: Concurrent positions < MAX_CONCURRENT_POSITIONS
    # -------------------------------------------------------------
    current_position_count = len(open_positions)
    if current_position_count >= MAX_CONCURRENT_POSITIONS:
        rejections.append(
            f"Rule 3 Failed: Current open positions count ({current_position_count}) has reached MAX_CONCURRENT_POSITIONS limit ({MAX_CONCURRENT_POSITIONS})."
        )

    # -------------------------------------------------------------
    # RULE 4: Total defined-risk exposure <= MAX_TOTAL_EXPOSURE_PCT * account equity
    # -------------------------------------------------------------
    existing_exposure = 0.0
    for p in open_positions:
        if isinstance(p, dict):
            existing_exposure += float(p.get("max_risk", p.get("max_loss", p.get("cost_basis", 0.0))))
        else:
            existing_exposure += float(getattr(p, "max_risk", getattr(p, "max_loss", getattr(p, "cost_basis", 0.0))))

    total_exposure = round(existing_exposure + trade_max_loss, 2)
    max_allowed_total_exposure = round(MAX_TOTAL_EXPOSURE_PCT * equity, 2)
    if total_exposure > max_allowed_total_exposure:
        rejections.append(
            f"Rule 4 Failed: Total risk exposure (${total_exposure:.2f}) exceeds {MAX_TOTAL_EXPOSURE_PCT*100:.1f}% of account equity (${max_allowed_total_exposure:.2f})."
        )

    # -------------------------------------------------------------
    # RULE 5: Daily loss has NOT breached MAX_DAILY_LOSS_USD (Circuit Breaker)
    # -------------------------------------------------------------
    if daily_pnl < -MAX_DAILY_LOSS_USD:
        rejections.append(
            f"Rule 5 Failed: Daily loss circuit-breaker breached! Daily P&L (${daily_pnl:.2f}) breached maximum daily loss limit (-${MAX_DAILY_LOSS_USD:.2f})."
        )

    # -------------------------------------------------------------
    # RULE 6: No existing same underlying + expiration
    # -------------------------------------------------------------
    has_same_und_exp = False
    for item in list(open_positions) + list(open_orders):
        if isinstance(item, dict):
            item_sym = item.get("underlying_symbol") or item.get("underlying") or item.get("symbol") or ""
            item_exp = item.get("expiration_date") or item.get("expiration") or ""
        else:
            item_sym = getattr(item, "underlying_symbol", None) or getattr(item, "underlying", None) or getattr(item, "symbol", "") or ""
            item_exp = getattr(item, "expiration_date", None) or getattr(item, "expiration", "") or ""

        if str(item_sym).startswith(symbol) and len(symbol) > 0:
            item_sym = symbol
        if item_sym == symbol and item_exp == expiration and expiration != "":
            has_same_und_exp = True
            break

    if has_same_und_exp:
        rejections.append(
            f"Rule 6 Failed: Existing position or order already active for underlying '{symbol}' with expiration '{expiration}'."
        )

    # -------------------------------------------------------------
    # RULE 7: No duplicate trade fingerprint
    # -------------------------------------------------------------
    leg_strikes = []
    for _, leg in legs_list:
        strike = _get_contract_attr(leg, "strike_price")
        if strike is not None:
            leg_strikes.append(strike)

    if len(legs_list) == 4 and strategy_name == "iron_condor":
        # Legacy 4-leg condor fingerprint format
        pl_strike = _get_contract_attr(legs_list[0][1], "strike_price")
        ps_strike = _get_contract_attr(legs_list[1][1], "strike_price")
        cs_strike = _get_contract_attr(legs_list[2][1], "strike_price")
        cl_strike = _get_contract_attr(legs_list[3][1], "strike_price")
        fingerprint = f"{symbol}:{expiration}:{pl_strike}:{ps_strike}:{cs_strike}:{cl_strike}"
    else:
        fingerprint = f"{symbol}:{expiration}:{':'.join(str(s) for s in leg_strikes)}"

    is_duplicate_fp = False
    for item in list(open_positions) + list(open_orders):
        item_fp = item.get("fingerprint", "") if isinstance(item, dict) else getattr(item, "fingerprint", "")
        if item_fp == fingerprint and fingerprint != "":
            is_duplicate_fp = True
            break

    if is_duplicate_fp:
        rejections.append(
            f"Rule 7 Failed: Duplicate trade fingerprint detected ({fingerprint})."
        )

    # -------------------------------------------------------------
    # RULE 8: Buying power is sufficient
    # -------------------------------------------------------------
    if buying_power < trade_max_loss:
        rejections.append(
            f"Rule 8 Failed: Insufficient buying power (required ${trade_max_loss:.2f}, available ${buying_power:.2f})."
        )

    # -------------------------------------------------------------
    # RULE 9: Market is open
    # -------------------------------------------------------------
    if not MARKET_OPEN:
        rejections.append("Rule 9 Failed: Market is currently closed. New orders cannot be submitted.")

    # -------------------------------------------------------------
    # RULE 10: Account is active
    # -------------------------------------------------------------
    norm_status = str(account_status).replace("AccountStatus.", "").upper()
    if norm_status != "ACTIVE":
        rejections.append(f"Rule 10 Failed: Account is not in ACTIVE status (current status: '{account_status}').")

    # -------------------------------------------------------------
    # RULE 11: Quote timestamps are fresh
    # -------------------------------------------------------------
    now_utc = datetime.now(timezone.utc)
    stale_legs = []
    for leg_name, leg in legs_list:
        ts_str = _get_contract_attr(leg, "quote_timestamp")
        if not ts_str:
            stale_legs.append(f"{leg_name} (missing timestamp)")
        else:
            ts_dt = _parse_timestamp(ts_str)
            if ts_dt:
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                age_seconds = (now_utc - ts_dt).total_seconds()
                if age_seconds > MAX_QUOTE_AGE_SECONDS:
                    stale_legs.append(f"{leg_name} (age {int(age_seconds)}s > {MAX_QUOTE_AGE_SECONDS}s)")
            else:
                stale_legs.append(f"{leg_name} (unparseable timestamp)")

    if stale_legs:
        rejections.append(f"Rule 11 Failed: Stale quote timestamp on leg(s): {', '.join(stale_legs)}.")

    # -------------------------------------------------------------
    # RULE 12: All option legs are present and structurally valid
    # -------------------------------------------------------------
    missing_legs = []
    for leg_name, leg in legs_list:
        if not leg:
            missing_legs.append(leg_name)
        else:
            c = getattr(leg, "contract", None) if hasattr(leg, "contract") else (leg.get("contract") if isinstance(leg, dict) else leg)
            if not c:
                missing_legs.append(leg_name)

    if missing_legs:
        rejections.append(f"Rule 12 Failed: Missing or invalid option legs: {', '.join(missing_legs)}.")

    # Strategy-Specific Structural Checks
    if strategy_name == "iron_condor":
        if len(legs_list) != 4:
            rejections.append(f"Rule 12 Failed: Iron Condor requires exactly 4 legs (received {len(legs_list)}).")
        elif not missing_legs:
            # Check Iron Condor strike geometry: pl < ps < cs < cl
            strikes = [_get_contract_attr(l[1], "strike_price") for l in legs_list]
            if any(s is None for s in strikes):
                rejections.append("Rule 12 Failed: Iron Condor missing strike price on one or more legs.")
            elif not (strikes[0] < strikes[1] < strikes[2] < strikes[3]):
                rejections.append(
                    f"Rule 12 Failed: Iron Condor strike geometry violation ({strikes[0]} < {strikes[1]} < {strikes[2]} < {strikes[3]} must hold)."
                )
            else:
                put_width = round(strikes[1] - strikes[0], 2)
                call_width = round(strikes[3] - strikes[2], 2)
                if put_width != call_width:
                    rejections.append(
                        f"Rule 12 Failed: Iron Condor wing asymmetry (put wing {put_width} != call wing {call_width})."
                    )

    elif strategy_name == "bull_put_spread":
        if len(legs_list) != 2:
            rejections.append(f"Rule 12 Failed: Bull Put Spread requires exactly 2 legs (received {len(legs_list)}).")
        elif not missing_legs:
            strikes = [_get_contract_attr(l[1], "strike_price") for l in legs_list]
            types = [_get_contract_attr(l[1], "contract_type") for l in legs_list]
            if any(t != "put" for t in types if t is not None):
                rejections.append("Rule 12 Failed: Bull Put Spread legs must all be put contracts.")
            # Expect short put strike > long put strike
            short_puts = [l[1] for l in legs_list if str(getattr(l[1], "side", "")).lower() == "sell" or str(getattr(l[1], "leg_type", "")).endswith("short")]
            long_puts = [l[1] for l in legs_list if str(getattr(l[1], "side", "")).lower() == "buy" or str(getattr(l[1], "leg_type", "")).endswith("long")]
            if short_puts and long_puts:
                sp_strike = _get_contract_attr(short_puts[0], "strike_price")
                lp_strike = _get_contract_attr(long_puts[0], "strike_price")
                if sp_strike is not None and lp_strike is not None and lp_strike >= sp_strike:
                    rejections.append(
                        f"Rule 12 Failed: Bull Put Spread geometry violation (long put strike {lp_strike} must be below short put strike {sp_strike})."
                    )

    elif strategy_name == "bear_call_spread":
        if len(legs_list) != 2:
            rejections.append(f"Rule 12 Failed: Bear Call Spread requires exactly 2 legs (received {len(legs_list)}).")
        elif not missing_legs:
            strikes = [_get_contract_attr(l[1], "strike_price") for l in legs_list]
            types = [_get_contract_attr(l[1], "contract_type") for l in legs_list]
            if any(t != "call" for t in types if t is not None):
                rejections.append("Rule 12 Failed: Bear Call Spread legs must all be call contracts.")
            short_calls = [l[1] for l in legs_list if str(getattr(l[1], "side", "")).lower() == "sell" or str(getattr(l[1], "leg_type", "")).endswith("short")]
            long_calls = [l[1] for l in legs_list if str(getattr(l[1], "side", "")).lower() == "buy" or str(getattr(l[1], "leg_type", "")).endswith("long")]
            if short_calls and long_calls:
                sc_strike = _get_contract_attr(short_calls[0], "strike_price")
                lc_strike = _get_contract_attr(long_calls[0], "strike_price")
                if sc_strike is not None and lc_strike is not None and lc_strike <= sc_strike:
                    rejections.append(
                        f"Rule 12 Failed: Bear Call Spread geometry violation (long call strike {lc_strike} must be above short call strike {sc_strike})."
                    )

    elif strategy_name == "long_call":
        if len(legs_list) != 1:
            rejections.append(f"Rule 12 Failed: Long Call requires exactly 1 leg (received {len(legs_list)}).")
        elif not missing_legs:
            side = str(getattr(legs_list[0][1], "side", "")).lower()
            c_type = _get_contract_attr(legs_list[0][1], "contract_type")
            if side != "buy":
                rejections.append("Rule 12 Failed: Long Call leg must be a BUY order.")
            if c_type != "call":
                rejections.append("Rule 12 Failed: Long Call contract must be a call.")

    elif strategy_name == "long_put":
        if len(legs_list) != 1:
            rejections.append(f"Rule 12 Failed: Long Put requires exactly 1 leg (received {len(legs_list)}).")
        elif not missing_legs:
            side = str(getattr(legs_list[0][1], "side", "")).lower()
            c_type = _get_contract_attr(legs_list[0][1], "contract_type")
            if side != "buy":
                rejections.append("Rule 12 Failed: Long Put leg must be a BUY order.")
            if c_type != "put":
                rejections.append("Rule 12 Failed: Long Put contract must be a put.")

    # -------------------------------------------------------------
    # RULE 13: Maximum loss is strictly positive and finite
    # -------------------------------------------------------------
    if max_risk <= 0:
        rejections.append(f"Rule 13 Failed: Defined maximum risk (${max_risk:.2f}) must be strictly positive.")

    # -------------------------------------------------------------
    # RULE 14: Premium economics (Net credit > 0 for credit, debit > 0 for debit)
    # -------------------------------------------------------------
    if strategy_name in ("long_call", "long_put"):
        entry_debit = float(getattr(proposed_trade, "entry_debit", 0.0) if hasattr(proposed_trade, "entry_debit") else proposed_trade.get("entry_debit", 0.0) if isinstance(proposed_trade, dict) else 0.0)
        if entry_debit <= 0 and max_risk <= 0:
            rejections.append("Rule 14 Failed: Directional debit strategy entry debit must be strictly positive.")
    else:
        if net_credit <= 0:
            rejections.append(f"Rule 14 Failed: Strategy net credit (${net_credit:.2f}) must be strictly positive.")

    # -------------------------------------------------------------
    # RULE 15: Option spread / liquidity requirements satisfied
    # -------------------------------------------------------------
    illiquid_legs = []
    for leg_name, leg in legs_list:
        if not leg:
            continue
        bid = float(_get_contract_attr(leg, "bid") or 0.0)
        ask = float(_get_contract_attr(leg, "ask") or 0.0)
        oi = int(_get_contract_attr(leg, "open_interest") or 0)

        if bid <= 0:
            illiquid_legs.append(f"{leg_name} (bid <= 0: {bid})")
        elif ask <= bid:
            illiquid_legs.append(f"{leg_name} (ask <= bid: bid {bid}, ask {ask})")
        else:
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid
            if spread_pct > MAX_BID_ASK_SPREAD_PCT:
                illiquid_legs.append(f"{leg_name} (spread {spread_pct*100:.1f}% > {MAX_BID_ASK_SPREAD_PCT*100:.1f}%)")

        if oi < MIN_OPEN_INTEREST:
            illiquid_legs.append(f"{leg_name} (open interest {oi} < {MIN_OPEN_INTEREST})")

    if illiquid_legs:
        rejections.append(f"Rule 15 Failed: Option liquidity requirements violated on: {', '.join(illiquid_legs)}.")

    # Final Decision
    approved = len(rejections) == 0
    risk_score = 0.1 if approved else 1.0

    risk_metrics = {
        "strategy_name": strategy_name,
        "trade_max_loss": trade_max_loss,
        "max_allowed_loss_usd": MAX_LOSS_PER_TRADE_USD,
        "max_allowed_loss_equity": max_allowed_by_equity,
        "existing_exposure": existing_exposure,
        "total_exposure": total_exposure,
        "max_allowed_total_exposure": max_allowed_total_exposure,
        "current_position_count": current_position_count,
        "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
        "daily_pnl": daily_pnl,
        "max_daily_loss_usd": MAX_DAILY_LOSS_USD,
        "buying_power": buying_power,
        "market_open": MARKET_OPEN,
        "account_status": account_status,
        "fingerprint": fingerprint,
    }

    reason_str = "All 15 risk gate rules passed." if approved else f"{len(rejections)} risk gate rule(s) failed."

    return RiskDecision(
        proposal_id=proposal_id,
        approved=approved,
        rejections=rejections,
        warnings=warnings,
        risk_metrics=risk_metrics,
        adjusted_quantity=quantity if approved else 0,
        risk_score=risk_score,
        rule_violations=rejections,
        max_allowed_loss=MAX_LOSS_PER_TRADE_USD,
        reason=reason_str,
    )


class RiskGate:
    """Risk Gate controller wrapping trade proposal evaluation."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def evaluate_proposal(
        self,
        proposal: Union[TradeProposal, StrategyCandidate, IronCondor, Dict[str, Any]],
        account: Union[AccountSnapshot, Dict[str, Any]],
        open_positions: Optional[List[Any]] = None,
        open_orders: Optional[List[Any]] = None,
        daily_pnl: float = 0.0,
    ) -> RiskDecision:
        """Evaluate a TradeProposal or StrategyCandidate through the risk gate."""
        return evaluate_trade(
            proposed_trade=proposal,
            account=account,
            open_positions=open_positions,
            open_orders=open_orders,
            daily_pnl=daily_pnl,
            config=self.config,
        )
