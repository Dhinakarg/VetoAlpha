"""
Unit Tests for Multi-Strategy Position Lifecycle and Exit Logic.
Tests Take Profit, Stop Loss, Time Exit, Delta Breach, and Hold actions
for Bull Put Spreads and Bear Call Spreads.
"""

from datetime import datetime, timezone, timedelta
import pytest

from trading_agent.models import OptionContract, StrategyCandidate, StrategyLeg
from trading_agent.portfolio.position_manager import (
    calculate_position_metrics,
    evaluate_position_exit,
    review_open_positions,
    PositionManager,
)


@pytest.fixture
def fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def sample_bull_put(fresh_ts) -> StrategyCandidate:
    exp = (datetime.now(timezone.utc) + timedelta(days=25)).strftime("%Y-%m-%d")
    short_put = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_P545",
            underlying_symbol="SPY",
            strike_price=545.0,
            expiration_date=exp,
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
    )
    long_put = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_P540",
            underlying_symbol="SPY",
            strike_price=540.0,
            expiration_date=exp,
            contract_type="put",
            bid=0.90,
            ask=1.00,
            open_interest=500,
            delta=-0.08,
            quote_timestamp=fresh_ts,
        ),
        side="buy",
        quantity=1,
        leg_type="put_long",
    )
    return StrategyCandidate(
        strategy_name="bull_put_spread",
        underlying="SPY",
        expiration=exp,
        legs=[short_put, long_put],
        entry_credit=1.10,
        max_profit=110.0,
        max_loss=390.0,
        breakeven=543.90,
        wing_width=5.0,
        dte=25,
        short_delta=-0.18,
    )


@pytest.fixture
def sample_bear_call(fresh_ts) -> StrategyCandidate:
    exp = (datetime.now(timezone.utc) + timedelta(days=25)).strftime("%Y-%m-%d")
    short_call = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_C560",
            underlying_symbol="SPY",
            strike_price=560.0,
            expiration_date=exp,
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
    )
    long_call = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_C565",
            underlying_symbol="SPY",
            strike_price=565.0,
            expiration_date=exp,
            contract_type="call",
            bid=0.90,
            ask=1.00,
            open_interest=500,
            delta=0.08,
            quote_timestamp=fresh_ts,
        ),
        side="buy",
        quantity=1,
        leg_type="call_long",
    )
    return StrategyCandidate(
        strategy_name="bear_call_spread",
        underlying="SPY",
        expiration=exp,
        legs=[short_call, long_call],
        entry_credit=1.10,
        max_profit=110.0,
        max_loss=390.0,
        breakeven=561.10,
        wing_width=5.0,
        dte=25,
        short_delta=0.18,
    )


# -----------------------------------------------------------------------------
# 1. Bull Put Spread Exit Scenarios
# -----------------------------------------------------------------------------
def test_bull_put_take_profit(sample_bull_put):
    # Short put price decayed: short ask 0.50, long bid 0.10 -> debit 0.40 <= 1.10 * (1 - 0.50) = 0.55
    quotes = {
        "SPY_P545": {"bid": 0.45, "ask": 0.50, "delta": -0.05},
        "SPY_P540": {"bid": 0.10, "ask": 0.15, "delta": -0.02},
    }
    decision = evaluate_position_exit(sample_bull_put, current_quotes=quotes, config={"TP_PCT_OF_MAX_CREDIT": 0.50})

    assert decision["strategy"] == "bull_put_spread"
    assert decision["action"] == "close"
    assert "Take Profit" in decision["reason"]
    assert decision["metrics"]["current_debit_to_close"] == 0.40
    assert decision["metrics"]["unrealized_pnl"] == 70.0  # (1.10 - 0.40) * 100


def test_bull_put_stop_loss(sample_bull_put):
    # Underlying dropped sharply: short put ask 3.00, long bid 0.60 -> debit 2.40 >= 1.10 * 2.0 = 2.20
    quotes = {
        "SPY_P545": {"bid": 2.90, "ask": 3.00, "delta": -0.45},
        "SPY_P540": {"bid": 0.60, "ask": 0.70, "delta": -0.20},
    }
    decision = evaluate_position_exit(sample_bull_put, current_quotes=quotes, config={"SL_MULT_OF_CREDIT": 2.0})

    assert decision["strategy"] == "bull_put_spread"
    assert decision["action"] == "close"
    assert "Stop Loss" in decision["reason"]
    assert decision["metrics"]["current_debit_to_close"] == 2.40


def test_bull_put_time_exit(sample_bull_put):
    # 2 DTE remaining <= 3 DTE threshold
    exp_soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    sample_bull_put.expiration = exp_soon
    decision = evaluate_position_exit(sample_bull_put, config={"TIME_EXIT_DTE": 3})

    assert decision["strategy"] == "bull_put_spread"
    assert decision["action"] == "close"
    assert "Time Exit" in decision["reason"]


def test_bull_put_delta_breach(sample_bull_put):
    # Short put delta jumped to -0.40 (>= 0.35 threshold)
    quotes = {
        "SPY_P545": {"bid": 1.50, "ask": 1.60, "delta": -0.40},
        "SPY_P540": {"bid": 0.50, "ask": 0.60, "delta": -0.15},
    }
    decision = evaluate_position_exit(sample_bull_put, current_quotes=quotes, config={"DELTA_BREACH_THRESHOLD": 0.35})

    assert decision["strategy"] == "bull_put_spread"
    assert decision["action"] == "close"
    assert "Delta Breach" in decision["reason"]


def test_bull_put_hold(sample_bull_put):
    quotes = {
        "SPY_P545": {"bid": 1.20, "ask": 1.25, "delta": -0.15},
        "SPY_P540": {"bid": 0.40, "ask": 0.45, "delta": -0.06},
    }
    decision = evaluate_position_exit(sample_bull_put, current_quotes=quotes)

    assert decision["strategy"] == "bull_put_spread"
    assert decision["action"] == "hold"
    assert "Holding" in decision["reason"]


# -----------------------------------------------------------------------------
# 2. Bear Call Spread Exit Scenarios
# -----------------------------------------------------------------------------
def test_bear_call_take_profit(sample_bear_call):
    # Short call decayed: short ask 0.40, long bid 0.05 -> debit 0.35 <= 1.10 * 0.50 = 0.55
    quotes = {
        "SPY_C560": {"bid": 0.35, "ask": 0.40, "delta": 0.04},
        "SPY_C565": {"bid": 0.05, "ask": 0.10, "delta": 0.01},
    }
    decision = evaluate_position_exit(sample_bear_call, current_quotes=quotes, config={"TP_PCT_OF_MAX_CREDIT": 0.50})

    assert decision["strategy"] == "bear_call_spread"
    assert decision["action"] == "close"
    assert "Take Profit" in decision["reason"]


def test_bear_call_stop_loss(sample_bear_call):
    # Underlying rallied sharply: short ask 3.20, long bid 0.80 -> debit 2.40 >= 2.20
    quotes = {
        "SPY_C560": {"bid": 3.10, "ask": 3.20, "delta": 0.50},
        "SPY_C565": {"bid": 0.80, "ask": 0.90, "delta": 0.25},
    }
    decision = evaluate_position_exit(sample_bear_call, current_quotes=quotes, config={"SL_MULT_OF_CREDIT": 2.0})

    assert decision["strategy"] == "bear_call_spread"
    assert decision["action"] == "close"
    assert "Stop Loss" in decision["reason"]


def test_bear_call_delta_breach(sample_bear_call):
    # Short call delta breached 0.38 >= 0.35
    quotes = {
        "SPY_C560": {"bid": 1.60, "ask": 1.70, "delta": 0.38},
        "SPY_C565": {"bid": 0.60, "ask": 0.70, "delta": 0.16},
    }
    decision = evaluate_position_exit(sample_bear_call, current_quotes=quotes, config={"DELTA_BREACH_THRESHOLD": 0.35})

    assert decision["strategy"] == "bear_call_spread"
    assert decision["action"] == "close"
    assert "Delta Breach" in decision["reason"]


def test_bear_call_hold(sample_bear_call):
    quotes = {
        "SPY_C560": {"bid": 1.20, "ask": 1.25, "delta": 0.14},
        "SPY_C565": {"bid": 0.40, "ask": 0.45, "delta": 0.05},
    }
    decision = evaluate_position_exit(sample_bear_call, current_quotes=quotes)

    assert decision["strategy"] == "bear_call_spread"
    assert decision["action"] == "hold"
    assert "Holding" in decision["reason"]
