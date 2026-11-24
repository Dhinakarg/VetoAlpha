"""
Integration and Unit Tests for Complete Iron Condor Position Lifecycle.
Tests position metrics calculation, exit signals (Take Profit, Stop Loss, Time Exit, Delta Breach),
closing order generation, bounded order polling, and idempotency protection.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from trading_agent.models import OptionContract, IronCondor, CondorLeg, PositionAction
from trading_agent.portfolio.position_manager import (
    calculate_condor_metrics,
    review_open_positions,
)
from trading_agent.execution.alpaca_executor import AlpacaExecutor
from trading_agent.risk.safety import (
    create_trade_fingerprint,
    SafetyManager,
)
from scripts.validate_execution import load_test_condor_fixture


@pytest.fixture
def test_condor() -> IronCondor:
    path = Path("tests/fixtures/execution_condor.json")
    return load_test_condor_fixture(path)


@pytest.fixture
def mock_position(test_condor) -> dict:
    return {
        "position_id": "POS_SPY_001",
        "symbol": "SPY",
        "expiration": test_condor.expiration_date,
        "entry_credit": 2.30,
        "quantity": 1,
        "dte": 25,
        "short_put_delta": -0.18,
        "short_call_delta": 0.18,
        "put_long": test_condor.put_long.contract,
        "put_short": test_condor.put_short.contract,
        "call_short": test_condor.call_short.contract,
        "call_long": test_condor.call_long.contract,
    }


# -----------------------------------------------------------------------------
# 1. Position Detection & Financial Metrics Calculation
# -----------------------------------------------------------------------------
def test_full_lifecycle_detection_and_metrics(mock_position, test_condor):
    # Current market quotes where condor price has decayed to $1.15 debit (50% profit)
    quotes = {
        test_condor.put_long.contract.symbol: {"bid": 0.50, "ask": 0.55},
        test_condor.put_short.contract.symbol: {"bid": 0.90, "ask": 0.95},
        test_condor.call_short.contract.symbol: {"bid": 0.85, "ask": 0.90},
        test_condor.call_long.contract.symbol: {"bid": 0.45, "ask": 0.50},
    }
    metrics = calculate_condor_metrics(mock_position, quotes)

    assert metrics["entry_credit"] == 2.30
    assert metrics["current_debit_to_close"] > 0
    # Entry credit 2.30, debit ~0.90 -> positive unrealized P&L
    assert metrics["unrealized_pnl"] > 0


# -----------------------------------------------------------------------------
# 2. Take Profit Exit Signal (debit <= entry_credit * (1 - TP_PCT))
# -----------------------------------------------------------------------------
def test_take_profit_exit_signal(mock_position, test_condor):
    # Debit to close is very cheap ($0.53 <= 2.30 * (1 - 0.50) = 1.15)
    cheap_quotes = {
        test_condor.put_long.contract.symbol: {"bid": 0.10, "ask": 0.12},
        test_condor.put_short.contract.symbol: {"bid": 0.35, "ask": 0.38},
        test_condor.call_short.contract.symbol: {"bid": 0.30, "ask": 0.33},
        test_condor.call_long.contract.symbol: {"bid": 0.08, "ask": 0.10},
    }
    actions = review_open_positions(
        positions=[mock_position],
        current_quotes=cheap_quotes,
        config={"TP_PCT_OF_MAX_CREDIT": 0.50, "SL_MULT_OF_CREDIT": 2.0, "TIME_EXIT_DTE": 7},
    )
    assert len(actions) == 1
    assert actions[0].action == "close"
    assert "take profit" in actions[0].reason.lower()


# -----------------------------------------------------------------------------
# 3. Stop Loss Exit Signal (debit >= entry_credit * SL_MULT)
# -----------------------------------------------------------------------------
def test_stop_loss_exit_signal(mock_position, test_condor):
    # Debit to close exploded to $5.67 (>= 2.30 * 2.0 = 4.60)
    expensive_quotes = {
        test_condor.put_long.contract.symbol: {"bid": 1.00, "ask": 1.10},
        test_condor.put_short.contract.symbol: {"bid": 6.50, "ask": 6.60},
        test_condor.call_short.contract.symbol: {"bid": 0.05, "ask": 0.08},
        test_condor.call_long.contract.symbol: {"bid": 0.01, "ask": 0.02},
    }
    actions = review_open_positions(
        positions=[mock_position],
        current_quotes=expensive_quotes,
        config={"TP_PCT_OF_MAX_CREDIT": 0.50, "SL_MULT_OF_CREDIT": 2.0, "TIME_EXIT_DTE": 7},
    )
    assert len(actions) == 1
    assert actions[0].action == "close"
    assert "stop loss" in actions[0].reason.lower()


# -----------------------------------------------------------------------------
# 4. Time Exit Signal (DTE <= TIME_EXIT_DTE)
# -----------------------------------------------------------------------------
def test_time_exit_signal(mock_position, test_condor):
    neutral_quotes = {
        test_condor.put_long.contract.symbol: {"bid": 0.80, "ask": 0.85},
        test_condor.put_short.contract.symbol: {"bid": 1.80, "ask": 1.85},
        test_condor.call_short.contract.symbol: {"bid": 1.75, "ask": 1.80},
        test_condor.call_long.contract.symbol: {"bid": 0.75, "ask": 0.80},
    }
    # Set REFERENCE_DATE to 2026-09-15 (3 days before 2026-09-18 expiration -> DTE 3 <= 7)
    actions = review_open_positions(
        positions=[mock_position],
        current_quotes=neutral_quotes,
        config={"TP_PCT_OF_MAX_CREDIT": 0.50, "SL_MULT_OF_CREDIT": 2.0, "TIME_EXIT_DTE": 7, "REFERENCE_DATE": "2026-09-15"},
    )
    assert len(actions) == 1
    assert actions[0].action == "close"
    assert "time exit" in actions[0].reason.lower()


# -----------------------------------------------------------------------------
# 5. Delta Breach Exit Signal (abs(delta) >= threshold)
# -----------------------------------------------------------------------------
def test_delta_breach_exit_signal(mock_position, test_condor):
    neutral_quotes = {
        test_condor.put_long.contract.symbol: {"bid": 0.80, "ask": 0.85, "delta": -0.15},
        test_condor.put_short.contract.symbol: {"bid": 1.80, "ask": 1.85, "delta": -0.35},  # Breach >= 0.30
        test_condor.call_short.contract.symbol: {"bid": 1.75, "ask": 1.80, "delta": 0.18},
        test_condor.call_long.contract.symbol: {"bid": 0.75, "ask": 0.80, "delta": 0.10},
    }
    actions = review_open_positions(
        positions=[mock_position],
        current_quotes=neutral_quotes,
        config={"TP_PCT_OF_MAX_CREDIT": 0.50, "SL_MULT_OF_CREDIT": 2.0, "TIME_EXIT_DTE": 7, "DELTA_BREACH_THRESHOLD": 0.30, "REFERENCE_DATE": "2026-08-25"},
    )
    assert len(actions) == 1
    assert actions[0].action == "close"
    assert "delta breach" in actions[0].reason.lower()


# -----------------------------------------------------------------------------
# 6. Closing MLEG Order Generation
# -----------------------------------------------------------------------------
def test_closing_mleg_order_generation(test_condor):
    executor = AlpacaExecutor(dry_run=True)
    res = executor.close_condor_order(condor=test_condor, quantity=1, close_limit_debit=1.15, dry_run=True)

    assert res.status == "new"
    assert len(res.legs_execution) == 4
    for leg in res.legs_execution:
        assert "close" in leg["intent"]


# -----------------------------------------------------------------------------
# 7. Realized P&L Calculation
# -----------------------------------------------------------------------------
def test_pnl_calculation_gross_and_net():
    entry_credit_per_share = 2.30
    closing_debit_per_share = 1.15
    quantity = 2  # 2 contracts

    gross_pnl = round((entry_credit_per_share - closing_debit_per_share) * 100.0 * quantity, 2)
    estimated_fees = 0.00
    net_pnl = round(gross_pnl - estimated_fees, 2)

    assert gross_pnl == 230.0
    assert net_pnl == 230.0


# -----------------------------------------------------------------------------
# 8. Idempotency Duplicate Rejection
# -----------------------------------------------------------------------------
def test_idempotency_duplicate_submission_blocked(tmp_path, test_condor):
    safety = SafetyManager(db_path=tmp_path / "idemp_test.db")
    fp = create_trade_fingerprint(test_condor)

    # First attempt: not duplicate
    assert safety.is_duplicate_trade(fp) is False
    safety.record_trade_attempt(fingerprint=fp, symbol="SPY", expiration=test_condor.expiration_date, order_id="ORD_001", status="submitted")

    # Second attempt: duplicate detected and blocked
    assert safety.is_duplicate_trade(fp) is True


# -----------------------------------------------------------------------------
# 9. Bounded Order Polling
# -----------------------------------------------------------------------------
def test_bounded_status_polling_success():
    executor = AlpacaExecutor(dry_run=True)
    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "ORD_123"
    mock_order.status = "filled"
    mock_order.symbol = "SPY"
    mock_order.qty = 1
    mock_order.filled_qty = 1
    mock_order.filled_avg_price = 2.30
    mock_order.created_at = datetime.now(timezone.utc)
    mock_client.get_order_by_id.return_value = mock_order
    executor._trading_client = mock_client

    status = executor.poll_order_status(order_id="ORD_123", max_attempts=3, delay_seconds=0.01)
    assert status["status"] == "filled"
    assert status["filled_qty"] == 1


# -----------------------------------------------------------------------------
# 10. Dry Run Mode NEVER Calls submit_order
# -----------------------------------------------------------------------------
def test_dry_run_never_calls_submit_order(test_condor):
    mock_client = MagicMock()
    executor = AlpacaExecutor(dry_run=True)
    executor._trading_client = mock_client

    executor.place_condor_order(condor=test_condor, quantity=1, dry_run=True)
    assert mock_client.submit_order.call_count == 0

    executor.close_condor_order(condor=test_condor, quantity=1, dry_run=True)
    assert mock_client.submit_order.call_count == 0
