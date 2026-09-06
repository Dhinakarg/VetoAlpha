"""
Unit tests for deterministic Risk Gate authorization layer.
Tests all 15 rules individually, multi-rule accumulation, and circuit-breaker enforcement.
No LLM calls. No network calls.
"""

from datetime import datetime, timezone
import pytest

from trading_agent.models import (
    TradeProposal,
    AccountSnapshot,
    OptionContract,
    CondorLeg,
    IronCondor,
    RiskDecision,
)
from trading_agent.risk.risk_gate import evaluate_trade, RiskGate


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_valid_trade_proposal(
    symbol: str = "SPY",
    max_risk: float = 250.0,
    net_credit: float = 2.50,
    quantity: int = 1,
    expiration: str = "2026-09-18",
    quote_timestamp: str = None,
) -> TradeProposal:
    ts = quote_timestamp or _make_fresh_timestamp()
    put_long = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918P00090000", underlying_symbol=symbol, strike_price=90.0,
            expiration_date=expiration, contract_type="put", bid=1.20, ask=1.25,
            open_interest=500, delta=-0.10, quote_timestamp=ts,
        ),
        side="buy", quantity=quantity, leg_type="put_long",
    )
    put_short = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918P00095000", underlying_symbol=symbol, strike_price=95.0,
            expiration_date=expiration, contract_type="put", bid=2.50, ask=2.55,
            open_interest=500, delta=-0.18, quote_timestamp=ts,
        ),
        side="sell", quantity=quantity, leg_type="put_short",
    )
    call_short = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918C00105000", underlying_symbol=symbol, strike_price=105.0,
            expiration_date=expiration, contract_type="call", bid=2.40, ask=2.45,
            open_interest=500, delta=0.18, quote_timestamp=ts,
        ),
        side="sell", quantity=quantity, leg_type="call_short",
    )
    call_long = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918C00110000", underlying_symbol=symbol, strike_price=110.0,
            expiration_date=expiration, contract_type="call", bid=1.10, ask=1.15,
            open_interest=500, delta=0.10, quote_timestamp=ts,
        ),
        side="buy", quantity=quantity, leg_type="call_long",
    )

    condor = IronCondor(
        underlying_symbol=symbol,
        put_long=put_long,
        put_short=put_short,
        call_short=call_short,
        call_long=call_long,
        net_credit=net_credit,
        max_risk=max_risk,
        max_profit=round(net_credit * 100.0, 2),
        wing_width=5.0,
        expiration_date=expiration,
        lower_breakeven=round(95.0 - net_credit, 2),
        upper_breakeven=round(105.0 + net_credit, 2),
    )

    return TradeProposal(
        proposal_id="PROP_TEST_001",
        symbol=symbol,
        condor=condor,
        quantity=quantity,
        created_at=ts,
        rationale="Ideal test condor proposal",
    )


def _make_valid_account(equity: float = 100000.0, buying_power: float = 100000.0) -> AccountSnapshot:
    return AccountSnapshot(
        account_id="ACC_TEST_999",
        cash=equity,
        buying_power=buying_power,
        equity=equity,
        portfolio_value=equity,
        status="ACTIVE",
    )


def test_valid_trade_approval():
    """Test clean proposal passing all 15 rules is approved with zero rejections."""
    proposal = _make_valid_trade_proposal()
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        open_positions=[],
        open_orders=[],
        daily_pnl=0.0,
    )

    assert decision.approved is True
    assert len(decision.rejections) == 0
    assert len(decision.rule_violations) == 0
    assert decision.adjusted_quantity == 1
    assert "All 15 risk gate rules passed." in decision.reason


def test_rule_1_max_loss_per_trade_usd():
    """Rule 1: Rejects when proposed max loss > MAX_LOSS_PER_TRADE_USD."""
    proposal = _make_valid_trade_proposal(max_risk=3000.0, quantity=1)
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        config={"MAX_LOSS_PER_TRADE_USD": 2500.0},
    )

    assert decision.approved is False
    assert any("Rule 1 Failed" in r for r in decision.rejections)


def test_rule_2_max_loss_pct_equity():
    """Rule 2: Rejects when proposed max loss > MAX_PCT_ACCOUNT_PER_TRADE * equity."""
    # Equity = 10,000; 2% = $200. Proposed risk = $250.
    proposal = _make_valid_trade_proposal(max_risk=250.0, quantity=1)
    account = _make_valid_account(equity=10000.0, buying_power=10000.0)

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        config={"MAX_PCT_ACCOUNT_PER_TRADE": 0.02},
    )

    assert decision.approved is False
    assert any("Rule 2 Failed" in r for r in decision.rejections)


def test_rule_3_max_concurrent_positions():
    """Rule 3: Rejects when open positions count reaches MAX_CONCURRENT_POSITIONS."""
    proposal = _make_valid_trade_proposal()
    account = _make_valid_account()
    positions = [{"symbol": f"POS_{i}", "max_risk": 200.0} for i in range(5)]

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        open_positions=positions,
        config={"MAX_CONCURRENT_POSITIONS": 5},
    )

    assert decision.approved is False
    assert any("Rule 3 Failed" in r for r in decision.rejections)


def test_rule_4_total_exposure_pct():
    """Rule 4: Rejects when total portfolio defined risk > MAX_TOTAL_EXPOSURE_PCT * equity."""
    # Equity = 100,000; 15% limit = $15,000. Existing = $14,900. Proposed = $250 -> total $15,150.
    proposal = _make_valid_trade_proposal(max_risk=250.0)
    account = _make_valid_account(equity=100000.0)
    positions = [{"symbol": "POS_EXISTING", "max_risk": 14900.0}]

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        open_positions=positions,
        config={"MAX_TOTAL_EXPOSURE_PCT": 0.15},
    )

    assert decision.approved is False
    assert any("Rule 4 Failed" in r for r in decision.rejections)


def test_rule_5_daily_loss_circuit_breaker():
    """Rule 5: Rejects when daily PnL breaches MAX_DAILY_LOSS_USD."""
    proposal = _make_valid_trade_proposal()
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        daily_pnl=-3500.0,
        config={"MAX_DAILY_LOSS_USD": 3000.0},
    )

    assert decision.approved is False
    assert any("Rule 5 Failed" in r for r in decision.rejections)


def test_rule_6_duplicate_underlying_expiration():
    """Rule 6: Rejects when position with same underlying + expiration already exists."""
    proposal = _make_valid_trade_proposal(symbol="SPY", expiration="2026-09-18")
    account = _make_valid_account()
    positions = [{"symbol": "SPY", "expiration_date": "2026-09-18"}]

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        open_positions=positions,
    )

    assert decision.approved is False
    assert any("Rule 6 Failed" in r for r in decision.rejections)


def test_rule_7_duplicate_fingerprint():
    """Rule 7: Rejects when open order has identical 4-leg fingerprint."""
    proposal = _make_valid_trade_proposal(symbol="SPY", expiration="2026-09-18")
    account = _make_valid_account()
    fp = "SPY:2026-09-18:90.0:95.0:105.0:110.0"
    orders = [{"symbol": "SPY", "fingerprint": fp}]

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        open_orders=orders,
    )

    assert decision.approved is False
    assert any("Rule 7 Failed" in r for r in decision.rejections)


def test_rule_8_insufficient_buying_power():
    """Rule 8: Rejects when buying power is less than trade max loss."""
    proposal = _make_valid_trade_proposal(max_risk=500.0)
    account = _make_valid_account(equity=100000.0, buying_power=200.0)

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
    )

    assert decision.approved is False
    assert any("Rule 8 Failed" in r for r in decision.rejections)


def test_rule_9_market_closed():
    """Rule 9: Rejects when market is closed."""
    proposal = _make_valid_trade_proposal()
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        config={"MARKET_OPEN": False},
    )

    assert decision.approved is False
    assert any("Rule 9 Failed" in r for r in decision.rejections)


def test_rule_10_account_inactive():
    """Rule 10: Rejects when account is not in ACTIVE status."""
    proposal = _make_valid_trade_proposal()
    account = AccountSnapshot(
        account_id="ACC_SUSPENDED", cash=100000.0, buying_power=100000.0,
        equity=100000.0, portfolio_value=100000.0, status="RESTRICTED",
    )

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
    )

    assert decision.approved is False
    assert any("Rule 10 Failed" in r for r in decision.rejections)


def test_rule_11_stale_quotes():
    """Rule 11: Rejects when option leg quotes are stale (> 900s)."""
    stale_ts = "2026-01-01T00:00:00Z"
    proposal = _make_valid_trade_proposal(quote_timestamp=stale_ts)
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        config={"MAX_QUOTE_AGE_SECONDS": 900},
    )

    assert decision.approved is False
    assert any("Rule 11 Failed" in r for r in decision.rejections)


def test_rule_12_invalid_legs():
    """Rule 12: Rejects when option legs are missing or invalid."""
    proposal = _make_valid_trade_proposal()
    proposal.condor.call_long = None
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
    )

    assert decision.approved is False
    assert any("Rule 12 Failed" in r for r in decision.rejections)


def test_rule_13_non_positive_max_loss():
    """Rule 13: Rejects when max_risk <= 0."""
    proposal = _make_valid_trade_proposal(max_risk=0.0)
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
    )

    assert decision.approved is False
    assert any("Rule 13 Failed" in r for r in decision.rejections)


def test_rule_14_non_positive_net_credit():
    """Rule 14: Rejects when net_credit <= 0."""
    proposal = _make_valid_trade_proposal(net_credit=0.0)
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
    )

    assert decision.approved is False
    assert any("Rule 14 Failed" in r for r in decision.rejections)


def test_rule_15_leg_liquidity_violation():
    """Rule 15: Rejects when leg spread is too wide or open interest too low."""
    proposal = _make_valid_trade_proposal()
    # Widen short call spread: bid 1.0, ask 2.5 (spread % = 1.5/1.75 = 85% > 20%)
    proposal.condor.call_short.contract.bid = 1.00
    proposal.condor.call_short.contract.ask = 2.50
    # Lower long put open interest
    proposal.condor.put_long.contract.open_interest = 10
    account = _make_valid_account()

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        config={"MAX_BID_ASK_SPREAD_PCT": 0.20, "MIN_OPEN_INTEREST": 50},
    )

    assert decision.approved is False
    assert any("Rule 15 Failed" in r for r in decision.rejections)


def test_evaluate_every_rule_accumulates_all_rejections():
    """Verify that multiple simultaneous rule violations are all evaluated and collected without early exit."""
    proposal = _make_valid_trade_proposal(
        max_risk=5000.0,       # Violates Rule 1 ($5000 > $2500)
        net_credit=-1.0,       # Violates Rule 14 (<= 0)
        quote_timestamp="2020-01-01T00:00:00Z", # Violates Rule 11 (stale)
    )
    account = _make_valid_account(buying_power=100.0) # Violates Rule 8 (insufficient BP)

    decision = evaluate_trade(
        proposed_trade=proposal,
        account=account,
        config={"MARKET_OPEN": False}, # Violates Rule 9 (closed)
    )

    assert decision.approved is False
    # Verify at least 5 distinct rules failed
    assert len(decision.rejections) >= 5
    rejections_joined = " ".join(decision.rejections)
    assert "Rule 1 Failed" in rejections_joined
    assert "Rule 8 Failed" in rejections_joined
    assert "Rule 9 Failed" in rejections_joined
    assert "Rule 11 Failed" in rejections_joined
    assert "Rule 14 Failed" in rejections_joined


def test_circuit_breaker_blocks_perfect_trade():
    """Specific circuit-breaker test: once daily loss is breached, all new trades are rejected regardless of quality."""
    perfect_proposal = _make_valid_trade_proposal(
        symbol="SPY", max_risk=250.0, net_credit=2.50, quantity=1
    )
    account = _make_valid_account(equity=100000.0, buying_power=100000.0)

    # Breached daily loss limit: daily_pnl = -$4,000 against max loss limit of $3,000
    decision = evaluate_trade(
        proposed_trade=perfect_proposal,
        account=account,
        open_positions=[],
        open_orders=[],
        daily_pnl=-4000.0,
        config={"MAX_DAILY_LOSS_USD": 3000.0},
    )

    assert decision.approved is False
    assert any("Rule 5 Failed" in r for r in decision.rejections)
    assert any("circuit-breaker breached" in r.lower() for r in decision.rejections)


def test_riskgate_class_evaluate_proposal():
    """Test RiskGate wrapper class evaluate_proposal method."""
    gate = RiskGate(config={"MAX_LOSS_PER_TRADE_USD": 2500.0})
    proposal = _make_valid_trade_proposal()
    account = _make_valid_account()

    decision = gate.evaluate_proposal(proposal=proposal, account=account)
    assert decision.approved is True
