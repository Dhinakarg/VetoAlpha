"""
Deterministic Test Suite for Multi-Strategy StrategySelector.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List
import pytest

from trading_agent.models import (
    OptionContract,
    OptionChainSnapshot,
    SentimentResult,
    FeatherlessCatalystResponse,
)
from trading_agent.strategy.regime import MarketRegime
from trading_agent.strategy.strategy_selector import StrategySelector, StrategyDecision
from scripts.validate_execution import load_test_condor_fixture


@pytest.fixture
def base_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def multi_strategy_chain(base_timestamp) -> List[OptionContract]:
    """Rich option chain with 4-leg condor, bull put, and bear call strikes."""
    exp = "2026-09-18"
    return [
        # Puts (540, 545)
        OptionContract(
            symbol="SPY260918P00540000",
            underlying_symbol="SPY",
            strike_price=540.0,
            expiration_date=exp,
            contract_type="put",
            bid=0.80,
            ask=0.90,
            open_interest=500,
            delta=-0.08,  # Long Put wing
            quote_timestamp=base_timestamp,
        ),
        OptionContract(
            symbol="SPY260918P00545000",
            underlying_symbol="SPY",
            strike_price=545.0,
            expiration_date=exp,
            contract_type="put",
            bid=2.00,
            ask=2.10,
            open_interest=600,
            delta=-0.16,  # Short Put (closer to target 0.15)
            quote_timestamp=base_timestamp,
        ),
        # Calls (560, 565)
        OptionContract(
            symbol="SPY260918C00560000",
            underlying_symbol="SPY",
            strike_price=560.0,
            expiration_date=exp,
            contract_type="call",
            bid=2.00,
            ask=2.10,
            open_interest=600,
            delta=0.16,  # Short Call (closer to target 0.15)
            quote_timestamp=base_timestamp,
        ),
        OptionContract(
            symbol="SPY260918C00565000",
            underlying_symbol="SPY",
            strike_price=565.0,
            expiration_date=exp,
            contract_type="call",
            bid=0.80,
            ask=0.90,
            open_interest=500,
            delta=0.08,  # Long Call wing
            quote_timestamp=base_timestamp,
        ),
    ]


@pytest.fixture
def low_catalyst_sentiment() -> FeatherlessCatalystResponse:
    return FeatherlessCatalystResponse(
        catalyst_risk="low",
        confidence=0.95,
        rationale="No major earnings or macro catalysts detected.",
    )


# -----------------------------------------------------------------------------
# 1. Range Regime -> Iron Condor Preferred
# -----------------------------------------------------------------------------
def test_range_prefers_iron_condor(multi_strategy_chain, low_catalyst_sentiment):
    regime = MarketRegime(
        trend="range",
        volatility="normal",
        confidence=0.85,
        iv_rank=50.0,
        eligible_strategies=["iron_condor", "bull_put_spread", "bear_call_spread"],
    )
    selector = StrategySelector()
    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=multi_strategy_chain,
        underlying_price=552.5,
        catalyst_assessment=low_catalyst_sentiment,
        current_date="2026-08-28",
    )

    assert decision.decision == "TRADE"
    assert decision.selected_strategy == "iron_condor"
    assert decision.selected_candidate is not None
    assert decision.scores["iron_condor"] > decision.scores.get("bull_put_spread", 0.0)


# -----------------------------------------------------------------------------
# 2. Bullish Regime -> Bull Put Spread Preferred
# -----------------------------------------------------------------------------
def test_bullish_prefers_bull_put(multi_strategy_chain, low_catalyst_sentiment):
    regime = MarketRegime(
        trend="bullish",
        volatility="normal",
        confidence=0.90,
        iv_rank=45.0,
        eligible_strategies=["bull_put_spread"],
    )
    selector = StrategySelector()
    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=multi_strategy_chain,
        underlying_price=552.5,
        catalyst_assessment=low_catalyst_sentiment,
        current_date="2026-08-28",
    )

    assert decision.decision == "TRADE"
    assert decision.selected_strategy == "bull_put_spread"
    assert decision.selected_candidate is not None


# -----------------------------------------------------------------------------
# 3. Bearish Regime -> Bear Call Spread Preferred
# -----------------------------------------------------------------------------
def test_bearish_prefers_bear_call(multi_strategy_chain, low_catalyst_sentiment):
    regime = MarketRegime(
        trend="bearish",
        volatility="normal",
        confidence=0.90,
        iv_rank=45.0,
        eligible_strategies=["bear_call_spread"],
    )
    selector = StrategySelector()
    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=multi_strategy_chain,
        underlying_price=552.5,
        catalyst_assessment=low_catalyst_sentiment,
        current_date="2026-08-28",
    )

    assert decision.decision == "TRADE"
    assert decision.selected_strategy == "bear_call_spread"
    assert decision.selected_candidate is not None


# -----------------------------------------------------------------------------
# 4. No Valid Candidates -> NO_TRADE
# -----------------------------------------------------------------------------
def test_no_valid_candidates_returns_no_trade(low_catalyst_sentiment):
    regime = MarketRegime(
        trend="range",
        volatility="normal",
        confidence=0.85,
        iv_rank=50.0,
        eligible_strategies=["iron_condor"],
    )
    selector = StrategySelector()
    # Empty option chain
    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=[],
        underlying_price=552.5,
        catalyst_assessment=low_catalyst_sentiment,
        current_date="2026-08-28",
    )

    assert decision.decision == "NO_TRADE"
    assert decision.selected_strategy is None
    assert decision.selected_candidate is None


# -----------------------------------------------------------------------------
# 5. Low IV State -> Rejected -> NO_TRADE
# -----------------------------------------------------------------------------
def test_low_iv_rejects_credit_strategies(multi_strategy_chain, low_catalyst_sentiment):
    regime = MarketRegime(
        trend="range",
        volatility="low",  # Low IV state
        confidence=0.90,
        iv_rank=10.0,
        eligible_strategies=[],
    )
    selector = StrategySelector()
    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=multi_strategy_chain,
        underlying_price=552.5,
        catalyst_assessment=low_catalyst_sentiment,
        current_date="2026-08-28",
    )

    assert decision.decision == "NO_TRADE"
    assert any("Low volatility state" in r for r in decision.reasons)


# -----------------------------------------------------------------------------
# 6. High Catalyst Risk -> Credit Strategies Blocked -> NO_TRADE
# -----------------------------------------------------------------------------
def test_high_catalyst_risk_rejects_credit_strategies(multi_strategy_chain):
    regime = MarketRegime(
        trend="range",
        volatility="high",
        confidence=0.85,
        iv_rank=75.0,
        eligible_strategies=["iron_condor"],
    )
    high_catalyst = FeatherlessCatalystResponse(
        catalyst_risk="high",
        confidence=0.92,
        rationale="FOMC rate decision in 24 hours.",
    )
    selector = StrategySelector()
    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=multi_strategy_chain,
        underlying_price=552.5,
        catalyst_assessment=high_catalyst,
        current_date="2026-08-28",
    )

    assert decision.decision == "NO_TRADE"
    assert any("High catalyst risk" in r for r in decision.reasons)


# -----------------------------------------------------------------------------
# 7. Missing Data -> NO_TRADE
# -----------------------------------------------------------------------------
def test_missing_data_returns_no_trade():
    regime = MarketRegime(
        trend="range",
        volatility="low",
        confidence=0.0,
        iv_rank=None,
        eligible_strategies=[],
    )
    selector = StrategySelector()
    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=[],
        underlying_price=0.0,
        catalyst_assessment=None,
    )

    assert decision.decision == "NO_TRADE"


# -----------------------------------------------------------------------------
# 8. Protocol & Execution Isolation Safety Rule
# -----------------------------------------------------------------------------
def test_selector_never_invokes_executor():
    selector = StrategySelector()
    # Selector must have zero execution endpoints
    forbidden = ["place_order", "submit_order", "execute_trade", "close_position"]
    for method in forbidden:
        assert not hasattr(selector, method), f"Security violation: StrategySelector exposes '{method}'"
