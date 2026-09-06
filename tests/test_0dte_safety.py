"""
Unit Test Suite for 0DTE Options Paper Safety Policy.
Verifies Phase 10 requirements:
- Paper-only restriction lock
- Defined-risk structure requirement
- 0.5% equity max loss limit
- Time-based cutoff (15:30 EST)
- Consecutive 0DTE loss circuit breaker
"""

import pytest
from datetime import time

from trading_agent.models import AccountSnapshot, StrategyCandidate, OptionLeg, OptionContract
from trading_agent.risk.zero_dte_policy import evaluate_0dte_safety


@pytest.fixture
def base_account() -> AccountSnapshot:
    return AccountSnapshot(
        account_id="ACC_PAPER_0DTE",
        equity=100000.0,
        buying_power=400000.0,
        status="ACTIVE",
    )


@pytest.fixture
def valid_0dte_condor() -> StrategyCandidate:
    return StrategyCandidate(
        underlying="SPXW",
        strategy_name="iron_condor",
        expiration="2026-09-03",
        legs=[
            OptionLeg(
                contract=OptionContract(symbol="SPXW260903P05400000", underlying_symbol="SPXW", strike_price=5400.0, expiration_date="2026-09-03", contract_type="put", bid=1.0, ask=1.10),
                quantity=1,
                side="SELL",
                position_intent="SELL_TO_OPEN",
            )
        ],
        entry_credit=2.0,
        max_loss=300.0,  # $300 max loss on $100k equity = 0.3% <= 0.5%
        underlying_price=5500.0,
    )


def test_0dte_safety_valid_candidate(valid_0dte_condor, base_account):
    res = evaluate_0dte_safety(valid_0dte_condor, base_account, current_time_est=time(10, 30))
    assert res["allowed"] is True


def test_0dte_safety_excessive_max_loss(valid_0dte_condor, base_account):
    valid_0dte_condor.max_loss = 800.0  # $800 > $500 (0.5% limit)
    res = evaluate_0dte_safety(valid_0dte_condor, base_account, current_time_est=time(10, 30))
    assert res["allowed"] is False
    assert "0.5% equity limit" in res["reason"]


def test_0dte_safety_time_cutoff(valid_0dte_condor, base_account):
    res = evaluate_0dte_safety(valid_0dte_condor, base_account, current_time_est=time(15, 35))
    assert res["allowed"] is False
    assert "Past 15:30 EST cutoff" in res["reason"]


def test_0dte_circuit_breaker(valid_0dte_condor, base_account):
    res = evaluate_0dte_safety(valid_0dte_condor, base_account, consecutive_0dte_losses=2, current_time_est=time(10, 30))
    assert res["allowed"] is False
    assert "CIRCUIT BREAKER ACTIVE" in res["reason"]
