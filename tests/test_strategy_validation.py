"""
Adversarial test suite for Strategy Validation and Condor Pipeline.
Verifies robust rejection of inverted quotes, missing Greeks, stale timestamps,
wing width mismatches, zero credits, risk violations, and proves order-submission is blocked.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.models import OptionContract, OptionChainSnapshot, IronCondor, CondorLeg, AccountSnapshot
from trading_agent.strategy.condor_builder import IronCondorBuilder
from trading_agent.risk.risk_gate import evaluate_trade
from trading_agent.execution.alpaca_executor import AlpacaExecutor
from scripts.validate_strategy import load_validation_iv_fixture, run_strategy_validation


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_valid_contract(
    symbol: str, strike: float, ctype: str, delta: float, bid: float, ask: float, oi: int = 500, ts: str = None, exp_date: str = None
) -> OptionContract:
    if exp_date is None:
        exp_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    return OptionContract(
        symbol=symbol,
        underlying_symbol="SPY",
        strike_price=strike,
        expiration_date=exp_date,
        contract_type=ctype,
        bid=bid,
        ask=ask,
        delta=delta,
        open_interest=oi,
        implied_volatility=0.20,
        quote_timestamp=ts or _make_fresh_timestamp(),
    )


# -----------------------------------------------------------------------------
# 1. Inverted Bid/Ask (Ask < Bid)
# -----------------------------------------------------------------------------
def test_inverted_bid_ask_rejected():
    builder = IronCondorBuilder()
    c = _make_valid_contract("SPY_INV", 500.0, "put", -0.18, bid=3.0, ask=2.5)  # Inverted
    assert builder._validate_leg_liquidity(c, max_spread_pct=0.20, min_oi=50) is False


# -----------------------------------------------------------------------------
# 2. Missing Bid or Missing Ask
# -----------------------------------------------------------------------------
def test_missing_bid_or_ask_rejected():
    builder = IronCondorBuilder()
    c_zero_bid = _make_valid_contract("SPY_ZB", 500.0, "put", -0.18, bid=0.0, ask=2.5)
    assert builder._validate_leg_liquidity(c_zero_bid, max_spread_pct=0.20, min_oi=50) is False

    c_zero_ask = _make_valid_contract("SPY_ZA", 500.0, "put", -0.18, bid=2.0, ask=0.0)
    assert builder._validate_leg_liquidity(c_zero_ask, max_spread_pct=0.20, min_oi=50) is False


# -----------------------------------------------------------------------------
# 3. Stale Quote (> 900s)
# -----------------------------------------------------------------------------
def test_stale_quote_rejected():
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    contract = _make_valid_contract("SPY_STALE", 500.0, "put", -0.18, 2.0, 2.1, ts=stale_ts)
    leg = CondorLeg(contract=contract, side="buy", quantity=1, leg_type="put_long")
    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg, put_short=leg, call_short=leg, call_long=leg,
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18"
    )
    decision = evaluate_trade(
        proposed_trade={"proposal_id": "P_STALE", "symbol": "SPY", "condor": condor, "quantity": 1},
        account=AccountSnapshot(equity=100000.0, buying_power=100000.0),
        config={"MAX_QUOTE_AGE_SECONDS": 900},
    )
    assert decision.approved is False
    assert any("Rule 11" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 4. Missing Delta
# -----------------------------------------------------------------------------
def test_missing_delta_rejected():
    builder = IronCondorBuilder()
    c = _make_valid_contract("SPY_ND", 500.0, "put", delta=None, bid=2.0, ask=2.1)
    assert builder._validate_leg_liquidity(c, max_spread_pct=0.20, min_oi=50) is False


# -----------------------------------------------------------------------------
# 5. Missing IV
# -----------------------------------------------------------------------------
def test_missing_iv_handled_safely():
    from trading_agent.strategy.signals import extract_atm_iv

    chain = OptionChainSnapshot(
        underlying_symbol="SPY",
        underlying_price=500.0,
        timestamp=_make_fresh_timestamp(),
        contracts=[_make_valid_contract("SPY_NIV", 500.0, "put", -0.18, 2.0, 2.1)],
    )
    chain.contracts[0].implied_volatility = None
    assert extract_atm_iv(chain, 500.0) is None


# -----------------------------------------------------------------------------
# 6. Insufficient Open Interest (< 50)
# -----------------------------------------------------------------------------
def test_insufficient_open_interest_rejected():
    builder = IronCondorBuilder()
    c = _make_valid_contract("SPY_LOW_OI", 500.0, "put", -0.18, 2.0, 2.1, oi=15)  # 15 < 50
    assert builder._validate_leg_liquidity(c, max_spread_pct=0.20, min_oi=50) is False


# -----------------------------------------------------------------------------
# 7. Excessive Spread (> 20%)
# -----------------------------------------------------------------------------
def test_excessive_spread_rejected():
    builder = IronCondorBuilder()
    # Bid 1.0, Ask 2.0 -> Mid 1.5 -> Spread (1.0 / 1.5) = 66.7% > 20%
    c = _make_valid_contract("SPY_WIDE", 500.0, "put", -0.18, bid=1.0, ask=2.0)
    assert builder._validate_leg_liquidity(c, max_spread_pct=0.20, min_oi=50) is False


# -----------------------------------------------------------------------------
# 8 & 9. Zero or Negative Net Credit
# -----------------------------------------------------------------------------
def test_zero_or_negative_credit_rejected():
    # Construct chain where wings cost more than short credit
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    contracts = [
        _make_valid_contract("SPY_PL", 480.0, "put", -0.10, bid=3.0, ask=3.2),   # Wing costs 3.2
        _make_valid_contract("SPY_PS", 485.0, "put", -0.18, bid=2.0, ask=2.1),   # Short sells for 2.0 -> negative credit
        _make_valid_contract("SPY_CS", 515.0, "call", 0.18, bid=2.0, ask=2.1),
        _make_valid_contract("SPY_CL", 520.0, "call", 0.10, bid=3.0, ask=3.2),
    ]
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=500.0, timestamp=_make_fresh_timestamp(), contracts=contracts)
    builder = IronCondorBuilder()
    condor, reasons = builder.build_condor_with_reasons(chain, underlying_price=500.0, wing_width=5.0)
    assert condor is None
    assert any("credit" in r.lower() for r in reasons)


# -----------------------------------------------------------------------------
# 10. Incorrect Wing Width (Gap != wing_width)
# -----------------------------------------------------------------------------
def test_incorrect_wing_width_rejected():
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    # Put wing gap is 3.0 (482 to 485) instead of required 5.0
    contracts = [
        _make_valid_contract("SPY_PL", 482.0, "put", -0.10, bid=0.5, ask=0.6),
        _make_valid_contract("SPY_PS", 485.0, "put", -0.18, bid=2.0, ask=2.1),
        _make_valid_contract("SPY_CS", 515.0, "call", 0.18, bid=2.0, ask=2.1),
        _make_valid_contract("SPY_CL", 520.0, "call", 0.10, bid=0.5, ask=0.6),
    ]
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=500.0, timestamp=_make_fresh_timestamp(), contracts=contracts)
    builder = IronCondorBuilder()
    condor = builder.build_condor(chain, underlying_price=500.0, wing_width=5.0)
    assert condor is None  # Rejects mismatch


# -----------------------------------------------------------------------------
# 11. Unavailable Wing Strike
# -----------------------------------------------------------------------------
def test_unavailable_wing_strike_rejected():
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    # Missing long call wing strike (520.0)
    contracts = [
        _make_valid_contract("SPY_PL", 480.0, "put", -0.10, bid=0.5, ask=0.6),
        _make_valid_contract("SPY_PS", 485.0, "put", -0.18, bid=2.0, ask=2.1),
        _make_valid_contract("SPY_CS", 515.0, "call", 0.18, bid=2.0, ask=2.1),
    ]
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=500.0, timestamp=_make_fresh_timestamp(), contracts=contracts)
    builder = IronCondorBuilder()
    condor = builder.build_condor(chain, underlying_price=500.0, wing_width=5.0)
    assert condor is None


# -----------------------------------------------------------------------------
# 12. Excessive Max Loss (> $2,500)
# -----------------------------------------------------------------------------
def test_excessive_max_loss_rejected():
    contract = _make_valid_contract("SPY_500", 500.0, "put", -0.18, 2.0, 2.1)
    leg = CondorLeg(contract=contract, side="buy", quantity=1, leg_type="put_long")
    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg, put_short=leg, call_short=leg, call_long=leg,
        net_credit=2.0, max_risk=3500.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18"
    )
    decision = evaluate_trade(
        proposed_trade={"proposal_id": "P_RISK", "symbol": "SPY", "condor": condor, "quantity": 1},
        account=AccountSnapshot(equity=100000.0, buying_power=100000.0),
        config={"MAX_LOSS_PER_TRADE_USD": 2500.0},
    )
    assert decision.approved is False
    assert any("Rule 1" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 13. Insufficient Buying Power
# -----------------------------------------------------------------------------
def test_insufficient_buying_power_rejected():
    contract = _make_valid_contract("SPY_500", 500.0, "put", -0.18, 2.0, 2.1)
    leg = CondorLeg(contract=contract, side="buy", quantity=1, leg_type="put_long")
    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg, put_short=leg, call_short=leg, call_long=leg,
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18"
    )
    decision = evaluate_trade(
        proposed_trade={"proposal_id": "P_BP", "symbol": "SPY", "condor": condor, "quantity": 1},
        account=AccountSnapshot(equity=100000.0, buying_power=50.0),  # Only $50 BP
    )
    assert decision.approved is False
    assert any("Rule 8" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 14. Duplicate Expiration Position
# -----------------------------------------------------------------------------
def test_duplicate_expiration_position_rejected():
    contract = _make_valid_contract("SPY_500", 500.0, "put", -0.18, 2.0, 2.1)
    leg = CondorLeg(contract=contract, side="buy", quantity=1, leg_type="put_long")
    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg, put_short=leg, call_short=leg, call_long=leg,
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18"
    )
    existing_positions = [
        {"symbol": "SPY260918P00500000", "underlying": "SPY", "expiration": "2026-09-18"}
    ]
    decision = evaluate_trade(
        proposed_trade={"proposal_id": "P_DUP_EXP", "symbol": "SPY", "condor": condor, "quantity": 1},
        account=AccountSnapshot(equity=100000.0, buying_power=100000.0),
        open_positions=existing_positions,
    )
    assert decision.approved is False
    assert any("Rule 6" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 15. Daily Circuit Breaker Active
# -----------------------------------------------------------------------------
def test_daily_circuit_breaker_rejected():
    contract = _make_valid_contract("SPY_500", 500.0, "put", -0.18, 2.0, 2.1)
    leg = CondorLeg(contract=contract, side="buy", quantity=1, leg_type="put_long")
    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg, put_short=leg, call_short=leg, call_long=leg,
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18"
    )
    decision = evaluate_trade(
        proposed_trade={"proposal_id": "P_CB", "symbol": "SPY", "condor": condor, "quantity": 1},
        account=AccountSnapshot(equity=100000.0, buying_power=100000.0),
        daily_pnl=-3500.0,  # Breached -$3,000 limit
        config={"MAX_DAILY_LOSS_USD": 3000.0},
    )
    assert decision.approved is False
    assert any("Rule 5" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 16. Proof that submit_order() is NEVER called in validation mode
# -----------------------------------------------------------------------------
def test_validation_mode_never_calls_submit_order():
    mock_trading_client = MagicMock()
    executor = AlpacaExecutor(dry_run=True)
    executor._trading_client = mock_trading_client

    c_pl = _make_valid_contract("SPY_PL", 480.0, "put", -0.10, 1.0, 1.1)
    c_ps = _make_valid_contract("SPY_PS", 485.0, "put", -0.18, 2.0, 2.1)
    c_cs = _make_valid_contract("SPY_CS", 515.0, "call", 0.18, 2.0, 2.1)
    c_cl = _make_valid_contract("SPY_CL", 520.0, "call", 0.10, 1.0, 1.1)

    leg_pl = CondorLeg(contract=c_pl, side="buy", quantity=1, leg_type="put_long")
    leg_ps = CondorLeg(contract=c_ps, side="sell", quantity=1, leg_type="put_short")
    leg_cs = CondorLeg(contract=c_cs, side="sell", quantity=1, leg_type="call_short")
    leg_cl = CondorLeg(contract=c_cl, side="buy", quantity=1, leg_type="call_long")

    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg_pl, put_short=leg_ps, call_short=leg_cs, call_long=leg_cl,
        net_credit=1.8, max_risk=320.0, max_profit=180.0, wing_width=5.0, expiration_date="2026-09-18"
    )

    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=True)
    assert res.status == "new"
    # STRICT INVARIANT: submit_order must NEVER be called when dry_run=True
    assert mock_trading_client.submit_order.call_count == 0


# -----------------------------------------------------------------------------
# 17. Malformed Order Payload Rejected
# -----------------------------------------------------------------------------
def test_malformed_order_payload_rejected():
    executor = AlpacaExecutor(dry_run=True)
    # Pass malformed condor (None or invalid object)
    res = executor.place_condor_order(condor=None, quantity=1, dry_run=True)
    assert res.status == "rejected"
    assert "missing" in res.error_message.lower() or "none" in res.error_message.lower() or "failed" in res.error_message.lower() or "malformed" in res.error_message.lower()
