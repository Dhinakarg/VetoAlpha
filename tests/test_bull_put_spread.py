"""
Unit and Adversarial Tests for Bull Put Credit Spread Strategy Engine.
"""

from datetime import datetime, timezone, timedelta
from typing import List
import pytest

from trading_agent.models import OptionContract, StrategyCandidate
from trading_agent.strategy.bull_put_spread import (
    build_bull_put_spread,
    build_bull_put_spread_with_reasons,
    BullPutSpreadStrategy,
)
from trading_agent.strategy.base import BaseOptionsStrategy


@pytest.fixture
def base_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def valid_put_chain(base_timestamp) -> List[OptionContract]:
    exp = "2026-09-18"  # ~21 DTE
    return [
        # Short Put: strike 545.0, delta -0.20, bid 2.00, ask 2.10, OI 500
        OptionContract(
            symbol="SPY260918P00545000",
            underlying_symbol="SPY",
            strike_price=545.0,
            expiration_date=exp,
            contract_type="put",
            bid=2.00,
            ask=2.10,
            open_interest=500,
            delta=-0.20,
            quote_timestamp=base_timestamp,
        ),
        # Long Put: strike 540.0, delta -0.12, bid 0.80, ask 0.90, OI 400
        OptionContract(
            symbol="SPY260918P00540000",
            underlying_symbol="SPY",
            strike_price=540.0,
            expiration_date=exp,
            contract_type="put",
            bid=0.80,
            ask=0.90,
            open_interest=400,
            delta=-0.12,
            quote_timestamp=base_timestamp,
        ),
        # Extra OTM Put: strike 535.0
        OptionContract(
            symbol="SPY260918P00535000",
            underlying_symbol="SPY",
            strike_price=535.0,
            expiration_date=exp,
            contract_type="put",
            bid=0.30,
            ask=0.35,
            open_interest=300,
            delta=-0.06,
            quote_timestamp=base_timestamp,
        ),
    ]


# -----------------------------------------------------------------------------
# 1. Valid Bull Put Spread Construction
# -----------------------------------------------------------------------------
def test_valid_bull_put_spread(valid_put_chain):
    underlying_price = 560.0
    candidate = build_bull_put_spread(
        option_chain=valid_put_chain,
        underlying_price=underlying_price,
        target_short_delta=0.20,
        wing_width=5.0,
        target_dte_min=10,
        target_dte_max=45,
        current_date="2026-08-28",
    )

    assert candidate is not None
    assert candidate.strategy_name == "bull_put_spread"
    assert candidate.underlying == "SPY"
    assert candidate.expiration == "2026-09-18"
    assert len(candidate.legs) == 2
    # Short Put 545, Long Put 540
    assert candidate.legs[0].contract.strike_price == 545.0
    assert candidate.legs[1].contract.strike_price == 540.0
    # Net Credit = 2.00 (short bid) - 0.90 (long ask) = 1.10
    assert candidate.entry_credit == 1.10
    assert candidate.max_profit == 110.0
    # Max Risk = (5.00 * 100) - 110.0 = 390.0
    assert candidate.max_loss == 390.0
    # Breakeven = 545.0 - 1.10 = 543.90
    assert candidate.breakeven == 543.90
    assert candidate.short_delta == -0.20
    assert "SPY260918P00545000" in candidate.quote_timestamps


# -----------------------------------------------------------------------------
# 2. No Valid Expiration (Outside DTE range)
# -----------------------------------------------------------------------------
def test_reject_no_valid_expiration(valid_put_chain):
    # Search in DTE range [50, 60] while contracts are ~21 DTE
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=valid_put_chain,
        underlying_price=560.0,
        target_dte_min=50,
        target_dte_max=60,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("No put contracts found within target DTE range" in r for r in reasons)


# -----------------------------------------------------------------------------
# 3. No Suitable Short Put (all strikes ITM)
# -----------------------------------------------------------------------------
def test_reject_no_suitable_short_put(valid_put_chain):
    # Underlying price is 530 (below all put strikes 545, 540, 535)
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=valid_put_chain,
        underlying_price=530.0,
        current_date="2026-08-28",
    )
    assert candidate is None


# -----------------------------------------------------------------------------
# 4. Missing Long Wing
# -----------------------------------------------------------------------------
def test_reject_missing_long_wing(valid_put_chain):
    # Remove long put (540.0)
    chain_no_long = [c for c in valid_put_chain if c.strike_price != 540.0]
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_no_long,
        underlying_price=560.0,
        wing_width=5.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Missing long put wing" in r for r in reasons)


# -----------------------------------------------------------------------------
# 5. Missing Delta / Greeks
# -----------------------------------------------------------------------------
def test_reject_missing_delta(valid_put_chain):
    chain_2_legs = valid_put_chain[:2]
    chain_2_legs[0].delta = None
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Missing delta" in r for r in reasons)


# -----------------------------------------------------------------------------
# 6. Stale Quote
# -----------------------------------------------------------------------------
def test_reject_stale_quote(valid_put_chain):
    chain_2_legs = valid_put_chain[:2]
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    chain_2_legs[0].quote_timestamp = stale_time
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Stale quote timestamp" in r for r in reasons)


# -----------------------------------------------------------------------------
# 7. Low Open Interest (< 50)
# -----------------------------------------------------------------------------
def test_reject_low_open_interest(valid_put_chain):
    chain_2_legs = valid_put_chain[:2]
    chain_2_legs[0].open_interest = 10  # Min is 50
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Open interest" in r for r in reasons)


# -----------------------------------------------------------------------------
# 8. Wide Bid/Ask Spread (> 20%)
# -----------------------------------------------------------------------------
def test_reject_wide_spread(valid_put_chain):
    # Bid 1.00, Ask 2.00 -> Spread 66.7% > 20%
    chain_2_legs = valid_put_chain[:2]
    chain_2_legs[0].bid = 1.00
    chain_2_legs[0].ask = 2.00
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Bid/ask spread percentage" in r for r in reasons)


# -----------------------------------------------------------------------------
# 9. Inverted Bid/Ask Market (Ask <= Bid)
# -----------------------------------------------------------------------------
def test_reject_inverted_bid_ask(valid_put_chain):
    chain_2_legs = valid_put_chain[:2]
    chain_2_legs[0].bid = 2.50
    chain_2_legs[0].ask = 2.00  # Inverted
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Inverted or zero bid/ask market" in r for r in reasons)


# -----------------------------------------------------------------------------
# 10. Zero Credit (short.bid == long.ask)
# -----------------------------------------------------------------------------
def test_reject_zero_credit(valid_put_chain):
    chain_2_legs = valid_put_chain[:2]
    chain_2_legs[0].bid = 1.00
    chain_2_legs[0].ask = 1.05
    chain_2_legs[1].bid = 0.95
    chain_2_legs[1].ask = 1.00  # 1.00 - 1.00 = 0.00
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Net credit" in r for r in reasons)


# -----------------------------------------------------------------------------
# 11. Negative Credit (short.bid < long.ask)
# -----------------------------------------------------------------------------
def test_reject_negative_credit(valid_put_chain):
    chain_2_legs = valid_put_chain[:2]
    chain_2_legs[0].bid = 0.80
    chain_2_legs[0].ask = 0.85
    chain_2_legs[1].bid = 0.95
    chain_2_legs[1].ask = 1.00  # 0.80 - 1.00 = -0.20
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Net credit" in r for r in reasons)


# -----------------------------------------------------------------------------
# 12. Incorrect Wing Width
# -----------------------------------------------------------------------------
def test_reject_incorrect_wing_width(valid_put_chain):
    # Only 545 and 540 exist (width 5.0). Request width 10.0
    chain_2_legs = valid_put_chain[:2]
    candidate, reasons = build_bull_put_spread_with_reasons(
        option_chain=chain_2_legs,
        underlying_price=560.0,
        wing_width=10.0,
        current_date="2026-08-28",
    )
    assert candidate is None
    assert any("Missing long put wing" in r for r in reasons)


# -----------------------------------------------------------------------------
# 13. Max-Loss & Breakeven Calculations
# -----------------------------------------------------------------------------
def test_max_loss_and_breakeven_calculations(valid_put_chain):
    strategy = BullPutSpreadStrategy(target_short_delta=0.20, wing_width=5.0)
    candidate = strategy.generate_candidate(
        option_chain=valid_put_chain,
        underlying_price=560.0,
        current_date="2026-08-28",
    )
    assert candidate is not None

    profit = strategy.calculate_max_profit(candidate)
    loss = strategy.calculate_max_loss(candidate)
    be = strategy.calculate_breakevens(candidate)

    assert profit == 110.0  # 1.10 * 100
    assert loss == 390.0    # (5.00 - 1.10) * 100
    assert be == (543.90,)  # 545.0 - 1.10


# -----------------------------------------------------------------------------
# 14. Protocol Compliance & Isolation Safety Rule
# -----------------------------------------------------------------------------
def test_bull_put_strategy_protocol_compliance():
    strategy = BullPutSpreadStrategy()
    assert isinstance(strategy, BaseOptionsStrategy)
    assert strategy.strategy_name() == "bull_put_spread"

    # Strategy must have NO order submission methods
    for forbidden in ["place_order", "submit_order", "execute_trade", "close_position"]:
        assert not hasattr(strategy, forbidden)
