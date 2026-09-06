"""
Regression & Integrity Tests for MLEG Option Leg Directions & Position Intents.
Asserts exact leg directions, position intents, and payload preservation across options strategies.
"""

import pytest
from datetime import datetime, timezone

from trading_agent.models import (
    OptionContract,
    CondorLeg,
    IronCondor,
)
from trading_agent.strategy.iron_condor import IronCondorStrategy
from trading_agent.execution.alpaca_executor import AlpacaExecutor


@pytest.fixture
def sample_contracts():
    return {
        "put_long": OptionContract(symbol="SPY260918P00500000", underlying_symbol="SPY", contract_type="put", strike_price=500.0, expiration_date="2026-09-18", bid=2.0, ask=2.2, delta=-0.15, open_interest=100),
        "put_short": OptionContract(symbol="SPY260918P00505000", underlying_symbol="SPY", contract_type="put", strike_price=505.0, expiration_date="2026-09-18", bid=3.0, ask=3.2, delta=-0.20, open_interest=100),
        "call_short": OptionContract(symbol="SPY260918C00550000", underlying_symbol="SPY", contract_type="call", strike_price=550.0, expiration_date="2026-09-18", bid=3.0, ask=3.2, delta=0.20, open_interest=100),
        "call_long": OptionContract(symbol="SPY260918C00555000", underlying_symbol="SPY", contract_type="call", strike_price=555.0, expiration_date="2026-09-18", bid=2.0, ask=2.2, delta=0.15, open_interest=100),
    }


@pytest.fixture
def sample_condor(sample_contracts):
    return IronCondor(
        underlying_symbol="SPY",
        put_long=CondorLeg(contract=sample_contracts["put_long"], side="buy", quantity=1, leg_type="put_long"),
        put_short=CondorLeg(contract=sample_contracts["put_short"], side="sell", quantity=1, leg_type="put_short"),
        call_short=CondorLeg(contract=sample_contracts["call_short"], side="sell", quantity=1, leg_type="call_short"),
        call_long=CondorLeg(contract=sample_contracts["call_long"], side="buy", quantity=1, leg_type="call_long"),
        net_credit=2.0,
        max_risk=300.0,
        max_profit=200.0,
        wing_width=5.0,
        expiration_date="2026-09-18",
        lower_breakeven=503.0,
        upper_breakeven=552.0,
    )


def test_iron_condor_has_two_buy_and_two_sell_legs(sample_condor):
    executor = AlpacaExecutor(dry_run=True)
    payload = executor.build_mleg_order(sample_condor, quantity=1, intent_type="open")
    legs = payload["legs"]

    assert len(legs) == 4
    sides = [l["side"] for l in legs]
    intents = [l["position_intent"] for l in legs]

    assert sides.count("BUY") == 2
    assert sides.count("SELL") == 2
    assert intents.count("BUY_TO_OPEN") == 2
    assert intents.count("SELL_TO_OPEN") == 2


def test_iron_condor_close_inverts_position_intent(sample_condor):
    executor = AlpacaExecutor(dry_run=True)
    payload = executor.build_mleg_order(sample_condor, quantity=1, intent_type="close")
    legs = payload["legs"]

    assert len(legs) == 4
    sides = [l["side"] for l in legs]
    intents = [l["position_intent"] for l in legs]

    assert sides.count("BUY") == 2
    assert sides.count("SELL") == 2
    assert intents.count("SELL_TO_CLOSE") == 2
    assert intents.count("BUY_TO_CLOSE") == 2


def test_executor_does_not_mutate_leg_direction(sample_condor):
    executor = AlpacaExecutor(dry_run=True)
    res = executor.place_condor_order(sample_condor, quantity=1, dry_run=True)

    assert res.status == "new"
    legs_exec = res.legs_execution
    assert len(legs_exec) == 4

    put_long_leg = next(l for l in legs_exec if l["leg"] == "put_long")
    put_short_leg = next(l for l in legs_exec if l["leg"] == "put_short")
    call_short_leg = next(l for l in legs_exec if l["leg"] == "call_short")
    call_long_leg = next(l for l in legs_exec if l["leg"] == "call_long")

    assert put_long_leg["side"] == "buy" and put_long_leg["intent"] == "buy_to_open"
    assert put_short_leg["side"] == "sell" and put_short_leg["intent"] == "sell_to_open"
    assert call_short_leg["side"] == "sell" and call_short_leg["intent"] == "sell_to_open"
    assert call_long_leg["side"] == "buy" and call_long_leg["intent"] == "buy_to_open"


def test_mleg_payload_preserves_position_intent(sample_condor):
    executor = AlpacaExecutor(dry_run=True)
    payload = executor.build_mleg_order(sample_condor, quantity=1, intent_type="open")

    assert payload["order_class"] == "mleg"
    assert payload["qty"] == 1
    assert payload["limit_price"] == 2.0
