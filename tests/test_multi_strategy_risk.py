"""
Multi-Strategy Risk Gate Unit and Adversarial Tests.
Tests strategy-agnostic authorization across Bull Put, Bear Call, and Iron Condor candidates.
"""

from datetime import datetime, timezone
import pytest

from trading_agent.models import (
    AccountSnapshot,
    OptionContract,
    StrategyCandidate,
    StrategyLeg,
)
from trading_agent.risk.risk_gate import evaluate_trade, RiskGate


@pytest.fixture
def base_account() -> AccountSnapshot:
    return AccountSnapshot(
        account_number="PA_TEST_123",
        equity=50000.0,
        cash=50000.0,
        buying_power=100000.0,
        currency="USD",
        status="ACTIVE",
    )


@pytest.fixture
def fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def valid_bull_put_candidate(fresh_ts) -> StrategyCandidate:
    leg_short = StrategyLeg(
        contract=OptionContract(
            symbol="SPY260918P00545000",
            underlying_symbol="SPY",
            strike_price=545.0,
            expiration_date="2026-09-18",
            contract_type="put",
            bid=2.00,
            ask=2.10,
            open_interest=500,
            delta=-0.18,
            quote_timestamp=fresh_ts,
        ),
        side="sell",
        quantity=1,
        leg_type="put_short",
        intent="SELL_TO_OPEN",
    )
    leg_long = StrategyLeg(
        contract=OptionContract(
            symbol="SPY260918P00540000",
            underlying_symbol="SPY",
            strike_price=540.0,
            expiration_date="2026-09-18",
            contract_type="put",
            bid=0.80,
            ask=0.90,
            open_interest=500,
            delta=-0.08,
            quote_timestamp=fresh_ts,
        ),
        side="buy",
        quantity=1,
        leg_type="put_long",
        intent="BUY_TO_OPEN",
    )
    return StrategyCandidate(
        strategy_name="bull_put_spread",
        underlying="SPY",
        underlying_symbol="SPY",
        expiration="2026-09-18",
        expiration_date="2026-09-18",
        legs=[leg_short, leg_long],
        entry_credit=1.10,
        net_credit=1.10,
        max_profit=110.0,
        max_loss=390.0,
        max_risk=390.0,
        breakeven=543.90,
        breakevens=[543.90],
        wing_width=5.0,
        dte=21,
        short_delta=-0.18,
    )


@pytest.fixture
def valid_bear_call_candidate(fresh_ts) -> StrategyCandidate:
    leg_short = StrategyLeg(
        contract=OptionContract(
            symbol="SPY260918C00560000",
            underlying_symbol="SPY",
            strike_price=560.0,
            expiration_date="2026-09-18",
            contract_type="call",
            bid=2.00,
            ask=2.10,
            open_interest=500,
            delta=0.18,
            quote_timestamp=fresh_ts,
        ),
        side="sell",
        quantity=1,
        leg_type="call_short",
        intent="SELL_TO_OPEN",
    )
    leg_long = StrategyLeg(
        contract=OptionContract(
            symbol="SPY260918C00565000",
            underlying_symbol="SPY",
            strike_price=565.0,
            expiration_date="2026-09-18",
            contract_type="call",
            bid=0.80,
            ask=0.90,
            open_interest=500,
            delta=0.08,
            quote_timestamp=fresh_ts,
        ),
        side="buy",
        quantity=1,
        leg_type="call_long",
        intent="BUY_TO_OPEN",
    )
    return StrategyCandidate(
        strategy_name="bear_call_spread",
        underlying="SPY",
        underlying_symbol="SPY",
        expiration="2026-09-18",
        expiration_date="2026-09-18",
        legs=[leg_short, leg_long],
        entry_credit=1.10,
        net_credit=1.10,
        max_profit=110.0,
        max_loss=390.0,
        max_risk=390.0,
        breakeven=561.10,
        breakevens=[561.10],
        wing_width=5.0,
        dte=21,
        short_delta=0.18,
    )


# -----------------------------------------------------------------------------
# 1. Valid Bull Put & Bear Call Approvals
# -----------------------------------------------------------------------------
def test_bull_put_risk_approval(valid_bull_put_candidate, base_account):
    decision = evaluate_trade(
        proposed_trade=valid_bull_put_candidate,
        account=base_account,
        daily_pnl=0.0,
    )
    assert decision.approved
    assert len(decision.rejections) == 0
    assert decision.risk_score == 0.1


def test_bear_call_risk_approval(valid_bear_call_candidate, base_account):
    decision = evaluate_trade(
        proposed_trade=valid_bear_call_candidate,
        account=base_account,
        daily_pnl=0.0,
    )
    assert decision.approved
    assert len(decision.rejections) == 0
    assert decision.risk_score == 0.1


# -----------------------------------------------------------------------------
# 2. Structural Malformations
# -----------------------------------------------------------------------------
def test_bull_put_geometry_inversion_rejected(valid_bull_put_candidate, base_account):
    # Invert strikes so long put strike > short put strike (550 > 545)
    valid_bull_put_candidate.legs[1].contract.strike_price = 550.0
    decision = evaluate_trade(
        proposed_trade=valid_bull_put_candidate,
        account=base_account,
    )
    assert not decision.approved
    assert any("Bull Put Spread geometry violation" in r for r in decision.rejections)


def test_bear_call_geometry_inversion_rejected(valid_bear_call_candidate, base_account):
    # Invert strikes so long call strike < short call strike (555 < 560)
    valid_bear_call_candidate.legs[1].contract.strike_price = 555.0
    decision = evaluate_trade(
        proposed_trade=valid_bear_call_candidate,
        account=base_account,
    )
    assert not decision.approved
    assert any("Bear Call Spread geometry violation" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 3. Circuit Breaker Enforced Universally
# -----------------------------------------------------------------------------
def test_circuit_breaker_blocks_bull_put(valid_bull_put_candidate, base_account):
    decision = evaluate_trade(
        proposed_trade=valid_bull_put_candidate,
        account=base_account,
        daily_pnl=-3500.0,  # Breaches -$3000 max daily loss
    )
    assert not decision.approved
    assert any("Daily loss circuit-breaker breached" in r for r in decision.rejections)


def test_circuit_breaker_blocks_bear_call(valid_bear_call_candidate, base_account):
    decision = evaluate_trade(
        proposed_trade=valid_bear_call_candidate,
        account=base_account,
        daily_pnl=-3500.0,
    )
    assert not decision.approved
    assert any("Daily loss circuit-breaker breached" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 4. Exposure & Position Limits Apply Across Strategies
# -----------------------------------------------------------------------------
def test_max_concurrent_positions_blocks_strategy(valid_bull_put_candidate, base_account):
    existing_positions = [{"symbol": f"POS_{i}"} for i in range(5)]  # Limit is 5
    decision = evaluate_trade(
        proposed_trade=valid_bull_put_candidate,
        account=base_account,
        open_positions=existing_positions,
    )
    assert not decision.approved
    assert any("MAX_CONCURRENT_POSITIONS" in r for r in decision.rejections)


def test_duplicate_underlying_expiration_blocks_spread(valid_bull_put_candidate, base_account):
    existing_positions = [{"underlying_symbol": "SPY", "expiration_date": "2026-09-18"}]
    decision = evaluate_trade(
        proposed_trade=valid_bull_put_candidate,
        account=base_account,
        open_positions=existing_positions,
    )
    assert not decision.approved
    assert any("Existing position or order already active" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 5. Insufficient Buying Power Blocks Defined Risk
# -----------------------------------------------------------------------------
def test_insufficient_buying_power_blocks_candidate(valid_bear_call_candidate, base_account):
    low_bp_account = base_account.model_copy(update={"buying_power": 100.0})  # max_loss is 390.0
    decision = evaluate_trade(
        proposed_trade=valid_bear_call_candidate,
        account=low_bp_account,
    )
    assert not decision.approved
    assert any("Insufficient buying power" in r for r in decision.rejections)
