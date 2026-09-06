"""
Unit Tests for BaseOptionsStrategy Protocol & IronCondorStrategy Implementation.
"""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from trading_agent.strategy.base import BaseOptionsStrategy
from trading_agent.strategy.iron_condor import IronCondorStrategy
from trading_agent.strategy.condor_builder import IronCondorBuilder
from trading_agent.models import IronCondor, OptionContract, CondorLeg
from scripts.validate_execution import load_test_condor_fixture


@pytest.fixture
def test_condor() -> IronCondor:
    path = Path("tests/fixtures/execution_condor.json")
    return load_test_condor_fixture(path)


@pytest.fixture
def strategy() -> IronCondorStrategy:
    return IronCondorStrategy(target_short_delta=0.15, wing_width=5.0)


# -----------------------------------------------------------------------------
# 1. Interface & Inheritance Compliance
# -----------------------------------------------------------------------------
def test_strategy_is_instance_of_base_strategy(strategy):
    assert isinstance(strategy, BaseOptionsStrategy)
    assert strategy.strategy_name() == "iron_condor"


def test_condor_builder_subclasses_iron_condor_strategy():
    builder = IronCondorBuilder()
    assert isinstance(builder, IronCondorStrategy)
    assert isinstance(builder, BaseOptionsStrategy)
    assert builder.strategy_name() == "iron_condor"


# -----------------------------------------------------------------------------
# 2. Market Regime Support
# -----------------------------------------------------------------------------
def test_supports_market_regime_range_bound(strategy):
    assert strategy.supports_market_regime({"adx": 18.5, "bb_width_percentile": 35.0}) is True
    assert strategy.supports_market_regime({"is_range_bound": True}) is True


def test_rejects_trending_market_regime(strategy):
    # High ADX >= 25.0 -> Trending
    assert strategy.supports_market_regime({"adx": 32.0, "bb_width_percentile": 35.0}) is False
    # Wide Bollinger Percentile >= 80%
    assert strategy.supports_market_regime({"adx": 18.0, "bb_width_percentile": 85.0}) is False
    assert strategy.supports_market_regime({"is_range_bound": False}) is False


# -----------------------------------------------------------------------------
# 3. Candidate Validation
# -----------------------------------------------------------------------------
def test_validate_valid_candidate(strategy, test_condor):
    is_valid, errors = strategy.validate_candidate(test_condor)
    assert is_valid is True
    assert len(errors) == 0


def test_validate_asymmetric_wings_rejected(strategy, test_condor):
    # Change put wing to $6 width
    test_condor.put_long.contract.strike_price = 539.0
    is_valid, errors = strategy.validate_candidate(test_condor)
    assert is_valid is False
    assert any("Wing asymmetry" in err for err in errors)


def test_validate_missing_greek_rejected(strategy, test_condor):
    test_condor.put_short.contract.delta = None
    is_valid, errors = strategy.validate_candidate(test_condor)
    assert is_valid is False
    assert any("Missing Greek" in err for err in errors)


# -----------------------------------------------------------------------------
# 4. Profit, Risk & Breakeven Calculations
# -----------------------------------------------------------------------------
def test_calculate_max_profit_and_loss(strategy, test_condor):
    # Net credit $2.30 -> Max Profit $230.00
    profit = strategy.calculate_max_profit(test_condor)
    assert profit == 230.0

    # Wing width $5.00 -> Max Risk ($500 - $230 = $270.00)
    loss = strategy.calculate_max_loss(test_condor)
    assert loss == 270.0


def test_calculate_breakevens(strategy, test_condor):
    # Short put 545.0 - 2.30 = 542.70, Short call 560.0 + 2.30 = 562.30
    lower_be, upper_be = strategy.calculate_breakevens(test_condor)
    assert lower_be == 542.70
    assert upper_be == 562.30


# -----------------------------------------------------------------------------
# 5. Candidate Scoring
# -----------------------------------------------------------------------------
def test_score_candidate(strategy, test_condor):
    score = strategy.score_candidate(test_condor, market_context={"iv_rank": 65.0})
    assert 0.0 <= score <= 100.0
    assert score > 50.0  # High-quality candidate with strong credit/width ratio


# -----------------------------------------------------------------------------
# 6. Safety Rule: No Order Submission in Strategy Layer
# -----------------------------------------------------------------------------
def test_strategy_layer_has_no_execution_endpoints(strategy):
    forbidden_methods = [
        "place_order",
        "submit_order",
        "execute_trade",
        "close_position",
        "cancel_order",
    ]
    for method in forbidden_methods:
        assert not hasattr(strategy, method), f"Strategy violates safety rule by exposing '{method}'"
