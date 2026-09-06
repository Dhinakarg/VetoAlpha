"""
Pure unit tests for 4-leg Iron Condor position manager and exit decision rules.
Tests Take Profit, Stop Loss, Time Exit, Delta Breach, and Hold actions.
No LLM calls. No network calls.
"""

from datetime import datetime, timedelta, timezone
import pytest

from trading_agent.models import OptionContract, CondorLeg, IronCondor, PositionAction
from trading_agent.portfolio.position_manager import PositionManager, review_open_positions


def _make_sample_condor(
    symbol: str = "SPY",
    entry_credit: float = 2.00,
    days_out: int = 30,
) -> IronCondor:
    dt = datetime.now(timezone.utc) + timedelta(days=days_out)
    exp = dt.strftime("%Y-%m-%d")

    put_long = CondorLeg(
        contract=OptionContract(
            symbol="SPY_P90", underlying_symbol=symbol, strike_price=90.0,
            expiration_date=exp, contract_type="put", bid=0.50, ask=0.55, delta=-0.08,
        ),
        side="buy", quantity=1, leg_type="put_long",
    )
    put_short = CondorLeg(
        contract=OptionContract(
            symbol="SPY_P95", underlying_symbol=symbol, strike_price=95.0,
            expiration_date=exp, contract_type="put", bid=1.50, ask=1.55, delta=-0.18,
        ),
        side="sell", quantity=1, leg_type="put_short",
    )
    call_short = CondorLeg(
        contract=OptionContract(
            symbol="SPY_C105", underlying_symbol=symbol, strike_price=105.0,
            expiration_date=exp, contract_type="call", bid=1.50, ask=1.55, delta=0.18,
        ),
        side="sell", quantity=1, leg_type="call_short",
    )
    call_long = CondorLeg(
        contract=OptionContract(
            symbol="SPY_C110", underlying_symbol=symbol, strike_price=110.0,
            expiration_date=exp, contract_type="call", bid=0.50, ask=0.55, delta=0.08,
        ),
        side="buy", quantity=1, leg_type="call_long",
    )

    return IronCondor(
        underlying_symbol=symbol,
        put_long=put_long,
        put_short=put_short,
        call_short=call_short,
        call_long=call_long,
        net_credit=entry_credit,
        max_risk=300.0,
        max_profit=200.0,
        wing_width=5.0,
        expiration_date=exp,
    )


def test_take_profit_exit():
    """Test Take Profit rule: triggers close when debit to close <= 50% of entry credit."""
    condor = _make_sample_condor(entry_credit=2.00, days_out=25)

    # Quotes where options decayed: short put ask=0.50, long put bid=0.10, short call ask=0.50, long call bid=0.10
    # Debit to close = (0.50 - 0.10) + (0.50 - 0.10) = 0.80 (which is <= 2.00 * (1 - 0.50) = 1.00)
    current_quotes = {
        "SPY_P90": {"bid": 0.10, "ask": 0.15, "delta": -0.02},
        "SPY_P95": {"bid": 0.45, "ask": 0.50, "delta": -0.06},
        "SPY_C105": {"bid": 0.45, "ask": 0.50, "delta": 0.06},
        "SPY_C110": {"bid": 0.10, "ask": 0.15, "delta": 0.02},
    }

    actions = review_open_positions(
        positions=[condor],
        current_quotes=current_quotes,
        config={"TP_PCT_OF_MAX_CREDIT": 0.50},
    )

    assert len(actions) == 1
    act = actions[0]
    assert act.action == "close"
    assert "Take Profit" in act.reason
    assert act.current_debit_to_close == 0.80
    assert act.unrealized_pnl == 120.0  # (2.00 - 0.80) * 100


def test_stop_loss_exit():
    """Test Stop Loss rule: triggers close when debit to close >= 2.0x entry credit."""
    condor = _make_sample_condor(entry_credit=2.00, days_out=25)

    # Market moved heavily: short put ask=4.50, long put bid=0.30
    # Debit to close = (4.50 - 0.30) + (0.10 - 0.05) = 4.25 (>= 2.0 * 2.00 = 4.00)
    current_quotes = {
        "SPY_P90": {"bid": 0.30, "ask": 0.35, "delta": -0.15},
        "SPY_P95": {"bid": 4.40, "ask": 4.50, "delta": -0.65},
        "SPY_C105": {"bid": 0.05, "ask": 0.10, "delta": 0.02},
        "SPY_C110": {"bid": 0.01, "ask": 0.05, "delta": 0.01},
    }

    actions = review_open_positions(
        positions=[condor],
        current_quotes=current_quotes,
        config={"SL_MULT_OF_CREDIT": 2.0},
    )

    assert len(actions) == 1
    act = actions[0]
    assert act.action == "close"
    assert "Stop Loss" in act.reason
    assert act.current_debit_to_close == 4.29
    assert act.unrealized_pnl == -229.0  # (2.00 - 4.29) * 100


def test_time_exit_dte():
    """Test Time Exit rule: triggers close when DTE <= TIME_EXIT_DTE."""
    condor = _make_sample_condor(entry_credit=2.00, days_out=2) # 2 days out (<= 3 threshold)

    # Normal healthy quotes where neither TP nor SL nor Delta breach triggers
    current_quotes = {
        "SPY_P90": {"bid": 0.30, "ask": 0.35, "delta": -0.05},
        "SPY_P95": {"bid": 1.00, "ask": 1.05, "delta": -0.15},
        "SPY_C105": {"bid": 1.00, "ask": 1.05, "delta": 0.15},
        "SPY_C110": {"bid": 0.30, "ask": 0.35, "delta": 0.05},
    }

    actions = review_open_positions(
        positions=[condor],
        current_quotes=current_quotes,
        config={"TIME_EXIT_DTE": 3},
    )

    assert len(actions) == 1
    act = actions[0]
    assert act.action == "close"
    assert "Time Exit" in act.reason
    assert act.dte <= 3


def test_short_put_delta_breach():
    """Test Delta Breach rule: triggers close when short put delta >= DELTA_BREACH_THRESHOLD."""
    condor = _make_sample_condor(entry_credit=2.00, days_out=20)

    # Short put delta has blown out to -0.40 (abs >= 0.35)
    current_quotes = {
        "SPY_P90": {"bid": 0.40, "ask": 0.45, "delta": -0.15},
        "SPY_P95": {"bid": 1.80, "ask": 1.90, "delta": -0.40},
        "SPY_C105": {"bid": 0.20, "ask": 0.25, "delta": 0.05},
        "SPY_C110": {"bid": 0.05, "ask": 0.10, "delta": 0.02},
    }

    actions = review_open_positions(
        positions=[condor],
        current_quotes=current_quotes,
        config={"DELTA_BREACH_THRESHOLD": 0.35},
    )

    assert len(actions) == 1
    act = actions[0]
    assert act.action == "close"
    assert "Delta Breach" in act.reason
    assert act.short_put_delta == -0.40


def test_short_call_delta_breach():
    """Test Delta Breach rule: triggers close when short call delta >= DELTA_BREACH_THRESHOLD."""
    condor = _make_sample_condor(entry_credit=2.00, days_out=20)

    # Short call delta has blown out to +0.38 (>= 0.35)
    current_quotes = {
        "SPY_P90": {"bid": 0.05, "ask": 0.10, "delta": -0.02},
        "SPY_P95": {"bid": 0.20, "ask": 0.25, "delta": -0.05},
        "SPY_C105": {"bid": 1.80, "ask": 1.90, "delta": 0.38},
        "SPY_C110": {"bid": 0.40, "ask": 0.45, "delta": 0.15},
    }

    actions = review_open_positions(
        positions=[condor],
        current_quotes=current_quotes,
        config={"DELTA_BREACH_THRESHOLD": 0.35},
    )

    assert len(actions) == 1
    act = actions[0]
    assert act.action == "close"
    assert "Delta Breach" in act.reason
    assert act.short_call_delta == 0.38


def test_hold_position():
    """Test Hold action: position remains in 'hold' state when all metrics are within safe boundaries."""
    condor = _make_sample_condor(entry_credit=2.00, days_out=20)

    # Healthy middle state: debit to close = 1.40 (not <= 1.00 TP, not >= 4.00 SL, deltas safe, DTE safe)
    current_quotes = {
        "SPY_P90": {"bid": 0.30, "ask": 0.35, "delta": -0.05},
        "SPY_P95": {"bid": 1.00, "ask": 1.05, "delta": -0.18},
        "SPY_C105": {"bid": 0.95, "ask": 1.00, "delta": 0.18},
        "SPY_C110": {"bid": 0.30, "ask": 0.35, "delta": 0.05},
    }

    actions = review_open_positions(
        positions=[condor],
        current_quotes=current_quotes,
        config={"TP_PCT_OF_MAX_CREDIT": 0.50, "SL_MULT_OF_CREDIT": 2.0, "TIME_EXIT_DTE": 3, "DELTA_BREACH_THRESHOLD": 0.35},
    )

    assert len(actions) == 1
    act = actions[0]
    assert act.action == "hold"
    assert "Holding" in act.reason


def test_position_manager_class_lifecycle():
    """Test PositionManager class registration, evaluation, and removal."""
    manager = PositionManager(config={"TIME_EXIT_DTE": 3})
    condor = _make_sample_condor(symbol="SPY", days_out=2)

    manager.add_position("POS_SPY_001", condor)
    assert len(manager.active_positions) == 1

    actions = manager.evaluate_positions()
    assert len(actions) == 1
    assert actions[0].action == "close"

    manager.remove_position("POS_SPY_001")
    assert len(manager.active_positions) == 0
