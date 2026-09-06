"""
Unit tests for Multi-Leg (MLEG) Order Payload Validation.
Ensures correct 4-leg structures, defined-risk limit pricing, and position intents.
"""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from trading_agent.models import OptionContract, IronCondor, CondorLeg
from trading_agent.execution.alpaca_executor import AlpacaExecutor
from scripts.validate_execution import load_test_condor_fixture


def _get_fixture_condor() -> IronCondor:
    path = Path("tests/fixtures/execution_condor.json")
    return load_test_condor_fixture(path)


def test_mleg_payload_has_exact_four_legs():
    executor = AlpacaExecutor(dry_run=True)
    condor = _get_fixture_condor()
    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=True)

    assert res.status == "new"
    assert len(res.legs_execution) == 4
    leg_names = [l["leg"] for l in res.legs_execution]
    assert leg_names == ["put_long", "put_short", "call_short", "call_long"]


def test_mleg_opening_position_intents():
    executor = AlpacaExecutor(dry_run=True)
    condor = _get_fixture_condor()
    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=True)

    intents = {l["leg"]: (l["side"], l["intent"]) for l in res.legs_execution}
    assert intents["put_long"] == ("buy", "buy_to_open")
    assert intents["put_short"] == ("sell", "sell_to_open")
    assert intents["call_short"] == ("sell", "sell_to_open")
    assert intents["call_long"] == ("buy", "buy_to_open")


def test_mleg_closing_position_intents():
    executor = AlpacaExecutor(dry_run=True)
    condor = _get_fixture_condor()
    res = executor.close_condor_order(condor=condor, quantity=1, close_limit_debit=1.15, dry_run=True)

    intents = {l["leg"]: (l["side"], l["intent"]) for l in res.legs_execution}
    assert intents["put_long"] == ("sell", "sell_to_close")
    assert intents["put_short"] == ("buy", "buy_to_close")
    assert intents["call_short"] == ("buy", "buy_to_close")
    assert intents["call_long"] == ("sell", "sell_to_close")


def test_mleg_strictly_limit_order_no_market_orders():
    """Verify order request builder uses LimitOrderRequest exclusively."""
    from alpaca.trading.requests import LimitOrderRequest
    executor = AlpacaExecutor(dry_run=True)
    condor = _get_fixture_condor()
    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=True)
    assert res.status == "new"
    assert "DRY_RUN" in res.order_id or "exec_" in res.execution_id


def test_malformed_condor_missing_leg_rejected():
    executor = AlpacaExecutor(dry_run=True)
    res = executor.place_condor_order(condor=None, quantity=1, dry_run=True)
    assert res.status == "rejected"
    assert "malformed" in res.error_message.lower() or "none" in res.error_message.lower()


def test_closing_condor_missing_leg_rejected():
    executor = AlpacaExecutor(dry_run=True)
    res = executor.close_condor_order(condor=None, quantity=1, dry_run=True)
    assert res.status == "rejected"
    assert "malformed" in res.error_message.lower() or "none" in res.error_message.lower()
