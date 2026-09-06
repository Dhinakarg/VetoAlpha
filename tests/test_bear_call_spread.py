"""
Unit and Adversarial Tests for Bear Call Credit Spread Strategy Engine.
"""

from datetime import datetime, timezone, timedelta
from typing import List
import pytest

from trading_agent.models import OptionContract, StrategyCandidate
from trading_agent.strategy.bear_call_spread import (
    build_bear_call_spread,
    build_bear_call_spread_with_reasons,
    BearCallSpreadStrategy,
)
from trading_agent.strategy.base import BaseOptionsStrategy


@pytest.fixture
def base_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def valid_call_chain(base_timestamp) -> List[OptionContract]:
    exp = "2026-09-18"  # ~21 DTE
    return [
        # Short Call: strike 560.0, delta 0.20, bid 2.00, ask 2.10, OI 500
        OptionContract(
            symbol="SPY260918C00560000",
            underlying_symbol="SPY",
            strike_price=560.0,
            expiration_date=exp,
            contract_type="call",
            bid=2.00,
            ask=2.10,
            open_interest=500,
            delta=0.20,
            quote_timestamp=base_timestamp,
        ),
        # Long Call: strike 565.0, delta 0.12, bid 0.80, ask 0.90, OI 400
        OptionContract(
            symbol="SPY260918C00565000",
            underlying_symbol="SPY",
            strike_price=565.0,
            expiration_date=exp,
            contract_type="call",
            bid=0.80,
            ask=0.90,
            open_interest=400,
            delta=0.12,
            quote_timestamp=base_timestamp,
        ),
        # Extra OTM Call: strike 570.0
        OptionContract(
            symbol="SPY260918C00570000",
            underlying_symbol="SPY",
            strike_price=570.0,
            expiration_date=exp,
            contract_type="call",
            bid=0.30,
            ask=0.35,
            open_interest=300,
            delta=0.06,
            quote_timestamp=base_timestamp,
        ),
    ]


# -----------------------------------------------------------------------------
# 1. Valid Bear Call Spread Construction
# -----------------------------------------------------------------------------
def test_valid_bear_call_spread(valid_call_chain):
    underlying_price = 545.0
    candidate = build_bear_call_spread(
        option_chain=valid_call_chain,
        underlying_price=underlying_price,
        target_short_delta=0.20,
        wing_width=5.0,
        target_dte_min=10,
        target_dte_max=45,
        current_date="2026-08-28",
    )

    assert candidate is not None
    assert candidate.strategy_name == "bear_call_spread"
    assert candidate.underlying == "SPY"
    assert candidate.expiration == "2026-09-18"
    assert len(candidate.legs) == 2
    # Short Call 560, Long Call 565
    assert candidate.legs[0].contract.strike_price == 560.0
    assert candidate.legs[1].contract.strike_price == 565.0
    # Net Credit = 2.00 (short bid) - 0.90 (long ask) = 1.10
    assert candidate.entry_credit == 1.10
    assert candidate.max_profit == 110.0
    # Max Risk = (5.00 * 100) - 110.0 = 390.0
    assert candidate.max_loss == 390.0
    # Breakeven = 560.0 + 1.10 = 561.10
    assert candidate.breakeven == 561.10
    assert candidate.short_delta == 0.20
    assert "SPY260918C00560000" in candidate.quote_timestamps


# -----------------------------------------------------------------------------
# 2. No Valid Expiration (Outside DTE range)
# -----------------------------------------------------------------------------
def test_reject_no_valid_expiration(valid_call_chain):
    # Search in DTE range [50, 60] while contracts are ~21 DTE
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=valid_call_chain,
        underlying_price=545.0,
        target_dte_min=50,
        target_dte_max=60,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("No call contracts found within target DTE range" in r for r in reasons)


# -----------------------------------------------------------------------------
# 3. No Suitable Short Call (all strikes ITM)
# -----------------------------------------------------------------------------
def test_reject_no_suitable_short_call(valid_call_chain):
    # Underlying price is 580 (above all call strikes 560, 565, 570)
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=valid_call_chain,
        underlying_price=580.0,
        current_date="2026-08-28",
    )
    assert candidate is None


# -----------------------------------------------------------------------------
# 4. Missing Long Wing
# -----------------------------------------------------------------------------
def test_reject_missing_long_wing(valid_call_chain):
    # Remove long call (565.0)
    chain_no_long = [c for c in valid_call_chain if c.strike_price != 565.0]
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_no_long,
        underlying_price=545.0,
        wing_width=5.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Missing long call wing" in r for r in reasons)


# -----------------------------------------------------------------------------
# 5. Missing Delta / Greeks
# -----------------------------------------------------------------------------
def test_reject_missing_delta(valid_call_chain):
    chain_2_legs = valid_call_chain[:2]
    chain_2_legs[0].delta = None
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Missing delta" in r for r in reasons)


# -----------------------------------------------------------------------------
# 6. Stale Quote
# -----------------------------------------------------------------------------
def test_reject_stale_quote(valid_call_chain):
    chain_2_legs = valid_call_chain[:2]
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    chain_2_legs[0].quote_timestamp = stale_time
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Stale quote timestamp" in r for r in reasons)


# -----------------------------------------------------------------------------
# 7. Low Open Interest (< 50)
# -----------------------------------------------------------------------------
def test_reject_low_open_interest(valid_call_chain):
    chain_2_legs = valid_call_chain[:2]
    chain_2_legs[0].open_interest = 10  # Min is 50
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Open interest" in r for r in reasons)


# -----------------------------------------------------------------------------
# 8. Wide Bid/Ask Spread (> 20%)
# -----------------------------------------------------------------------------
def test_reject_wide_spread(valid_call_chain):
    # Bid 1.00, Ask 2.00 -> Spread 66.7% > 20%
    chain_2_legs = valid_call_chain[:2]
    chain_2_legs[0].bid = 1.00
    chain_2_legs[0].ask = 2.00
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Bid/ask spread percentage" in r for r in reasons)


# -----------------------------------------------------------------------------
# 9. Inverted Bid/Ask Market (Ask <= Bid)
# -----------------------------------------------------------------------------
def test_reject_inverted_bid_ask(valid_call_chain):
    chain_2_legs = valid_call_chain[:2]
    chain_2_legs[0].bid = 2.50
    chain_2_legs[0].ask = 2.00  # Inverted
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Inverted or zero bid/ask market" in r for r in reasons)


# -----------------------------------------------------------------------------
# 10. Zero Credit (short.bid == long.ask)
# -----------------------------------------------------------------------------
def test_reject_zero_credit(valid_call_chain):
    chain_2_legs = valid_call_chain[:2]
    chain_2_legs[0].bid = 1.00
    chain_2_legs[0].ask = 1.05
    chain_2_legs[1].bid = 0.95
    chain_2_legs[1].ask = 1.00  # 1.00 - 1.00 = 0.00
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Net credit" in r for r in reasons)


# -----------------------------------------------------------------------------
# 11. Negative Credit (short.bid < long.ask)
# -----------------------------------------------------------------------------
def test_reject_negative_credit(valid_call_chain):
    chain_2_legs = valid_call_chain[:2]
    chain_2_legs[0].bid = 0.80
    chain_2_legs[0].ask = 0.85
    chain_2_legs[1].bid = 0.95
    chain_2_legs[1].ask = 1.00  # 0.80 - 1.00 = -0.20
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Net credit" in r for r in reasons)


# -----------------------------------------------------------------------------
# 12. Incorrect Wing Width
# -----------------------------------------------------------------------------
def test_reject_incorrect_wing_width(valid_call_chain):
    # Only 560 and 565 exist (width 5.0). Request width 10.0
    chain_2_legs = valid_call_chain[:2]
    candidate, reasons = build_bear_call_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=545.0,
        wing_width=10.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Missing long call wing" in r for r in reasons)


# -----------------------------------------------------------------------------
# 13. Max-Loss & Breakeven Calculations
# -----------------------------------------------------------------------------
def test_max_loss_and_breakeven_calculations(valid_call_chain):
    strategy = BearCallSpreadStrategy(target_short_delta=0.20, wing_width=5.0)
    candidate = strategy.generate_candidate(
        option_chain=valid_call_chain,
        underlying_price=545.0,
        current_date="2026-08-28",
    )
    assert candidate is not None

    profit = strategy.calculate_max_profit(candidate)
    loss = strategy.calculate_max_loss(candidate)
    be = strategy.calculate_breakevens(candidate)

    assert profit == 110.0  # 1.10 * 100
    assert loss == 390.0    # (5.00 - 1.10) * 100
    assert be == (561.10,)  # 560.0 + 1.10


# -----------------------------------------------------------------------------
# 14. Protocol Compliance & Isolation Safety Rule
# -----------------------------------------------------------------------------
def test_bear_call_strategy_protocol_compliance():
    strategy = BearCallSpreadStrategy()
    assert isinstance(strategy, BaseOptionsStrategy)
    assert strategy.strategy_name() == "bear_call_spread"

    # Strategy must have NO order submission methods
    for forbidden in ["place_order", "submit_order", "execute_trade", "close_position"]:
        assert not hasattr(strategy, forbidden)
