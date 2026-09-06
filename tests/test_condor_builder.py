"""
Unit tests for deterministic Iron Condor builder candidate selection engine.
Tests every rejection condition and valid 4-leg construction.
No LLM calls.
"""

from datetime import datetime, timedelta, timezone
import pytest

from trading_agent.strategy.condor_builder import IronCondorBuilder
from trading_agent.models import OptionContract, OptionChainSnapshot, IronCondor


def _make_expiration(days_out: int = 30) -> str:
    """Generate expiration date string YYYY-MM-DD."""
    dt = datetime.now(timezone.utc) + timedelta(days=days_out)
    return dt.strftime("%Y-%m-%d")


def _make_valid_contracts(days_out: int = 30) -> list[OptionContract]:
    """Helper to generate an ideal 4-leg option chain."""
    exp = _make_expiration(days_out)
    return [
        OptionContract(
            symbol="SPY_P90", underlying_symbol="SPY", strike_price=90.0, expiration_date=exp,
            contract_type="put", bid=1.00, ask=1.05, last_price=1.02, volume=200, open_interest=500,
            delta=-0.10, implied_volatility=0.20,
        ),
        OptionContract(
            symbol="SPY_P95", underlying_symbol="SPY", strike_price=95.0, expiration_date=exp,
            contract_type="put", bid=2.50, ask=2.55, last_price=2.52, volume=300, open_interest=600,
            delta=-0.18, implied_volatility=0.22,
        ),
        OptionContract(
            symbol="SPY_C105", underlying_symbol="SPY", strike_price=105.0, expiration_date=exp,
            contract_type="call", bid=2.40, ask=2.45, last_price=2.42, volume=250, open_interest=550,
            delta=0.18, implied_volatility=0.21,
        ),
        OptionContract(
            symbol="SPY_C110", underlying_symbol="SPY", strike_price=110.0, expiration_date=exp,
            contract_type="call", bid=0.95, ask=1.00, last_price=0.98, volume=180, open_interest=480,
            delta=0.10, implied_volatility=0.19,
        ),
    ]


@pytest.fixture
def builder():
    return IronCondorBuilder(
        target_short_delta=0.18,
        wing_width=5.0,
        target_dte_min=20,
        target_dte_max=45,
        max_bid_ask_spread_pct=0.20,
        min_open_interest=50,
        min_net_credit=0.10,
    )


def test_valid_condor_construction(builder):
    """Test 1: Ideal option chain constructs a valid IronCondor with exact credit and risk calculations."""
    contracts = _make_valid_contracts(days_out=30)
    condor = builder.build_condor(contracts, underlying_price=100.0)

    assert condor is not None
    assert isinstance(condor, IronCondor)

    # Put credit: 2.50 - 1.05 = 1.45; Call credit: 2.40 - 1.00 = 1.40; Net credit = 2.85
    assert condor.net_credit == 2.85
    assert condor.max_profit == 285.0
    # Wing width = 5.0 -> Max loss = (5.0 * 100) - 285.0 = 215.0
    assert condor.max_risk == 215.0

    # Geometry: 90 < 95 < 100 < 105 < 110
    assert condor.put_long.contract.strike_price == 90.0
    assert condor.put_short.contract.strike_price == 95.0
    assert condor.call_short.contract.strike_price == 105.0
    assert condor.call_long.contract.strike_price == 110.0

    # Breakevens
    assert condor.lower_breakeven == 92.15  # 95 - 2.85
    assert condor.upper_breakeven == 107.85 # 105 + 2.85


def test_reject_invalid_dte(builder):
    """Test 2: Rejects when option chain expiration is outside [target_dte_min, target_dte_max]."""
    contracts = _make_valid_contracts(days_out=5)  # 5 days out (min is 20)
    condor, reasons = builder.build_condor_with_reasons(contracts, underlying_price=100.0)

    assert condor is None
    assert any("DTE range" in r for r in reasons)


def test_reject_missing_greeks(builder):
    """Test 3: Rejects contracts where delta is missing (None)."""
    contracts = _make_valid_contracts(days_out=30)
    for c in contracts:
        c.delta = None

    condor, reasons = builder.build_condor_with_reasons(contracts, underlying_price=100.0)
    assert condor is None


def test_reject_missing_wings(builder):
    """Test 4: Rejects when required wing strikes (e.g. 90 put or 110 call) are missing from chain."""
    contracts = _make_valid_contracts(days_out=30)
    # Filter out long wings (90 put and 110 call)
    contracts = [c for c in contracts if c.strike_price not in (90.0, 110.0)]

    condor, reasons = builder.build_condor_with_reasons(contracts, underlying_price=100.0)
    assert condor is None


def test_reject_illiquid_leg_spread(builder):
    """Test 5: Rejects when bid/ask spread percentage exceeds max allowed (0.20)."""
    contracts = _make_valid_contracts(days_out=30)
    # Set short call spread to wide: bid 1.00, ask 2.50 -> mid 1.75 -> spread % = 1.50 / 1.75 = 85%
    for c in contracts:
        if c.strike_price == 105.0:
            c.bid = 1.00
            c.ask = 2.50

    condor, reasons = builder.build_condor_with_reasons(contracts, underlying_price=100.0)
    assert condor is None


def test_reject_illiquid_leg_open_interest(builder):
    """Test 6: Rejects when open interest is below minimum threshold (50)."""
    contracts = _make_valid_contracts(days_out=30)
    # Set short put open interest to 5
    for c in contracts:
        if c.strike_price == 95.0:
            c.open_interest = 5

    condor, reasons = builder.build_condor_with_reasons(contracts, underlying_price=100.0)
    assert condor is None


def test_reject_negative_or_zero_net_credit(builder):
    """Test 7: Rejects when net credit <= 0 (e.g. long wings cost more than short wings)."""
    contracts = _make_valid_contracts(days_out=30)
    # Set long put ask very high (3.50) so short put credit (2.50 - 3.50) is negative
    for c in contracts:
        if c.strike_price == 90.0:
            c.ask = 3.50

    condor, reasons = builder.build_condor_with_reasons(contracts, underlying_price=100.0)
    assert condor is None


def test_reject_invalid_geometry(builder):
    """Test 8: Rejects when underlying price violates geometry (e.g. short put strike >= underlying)."""
    contracts = _make_valid_contracts(days_out=30)
    # Set underlying price to 94 (short put strike 95 is > 94, violating put < underlying)
    condor, reasons = builder.build_condor_with_reasons(contracts, underlying_price=94.0)

    assert condor is None
