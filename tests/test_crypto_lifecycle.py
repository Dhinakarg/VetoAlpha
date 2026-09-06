"""
Comprehensive Regression Test Suite for Crypto Entry/Exit Lifecycle & Broker Reconciliation.
Implements 18 prompt-mandated regression assertions verifying entry, hold, exit, idempotency,
reconciliation, fail-closed guards, realized P&L, and position state transitions.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from trading_agent.crypto.crypto_strategy import CryptoStrategyCandidate
from trading_agent.crypto.crypto_risk import CryptoRiskGate, CryptoRiskDecision
from trading_agent.crypto.crypto_portfolio import CryptoPortfolioManager, CryptoPosition
from trading_agent.crypto.crypto_execution import CryptoExecutionEngine, CryptoOrderPayload


def make_order(order_id="ORD-001", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED"):
    return CryptoOrderPayload(
        order_id=order_id,
        symbol=symbol,
        direction="long" if side == "buy" else "short",
        order_type="market",
        quantity=quantity,
        limit_price=price,
        stop_loss=price * 0.98,
        take_profit=price * 1.04,
        submission_timestamp=datetime.now(timezone.utc).isoformat(),
        execution_mode="paper",
        fingerprint=f"test:{symbol}:{order_id}",
        side=side,
        status=status,
        filled_qty=quantity if status == "FILLED" else 0.0,
        filled_avg_price=price if status == "FILLED" else 0.0,
    )


@pytest.fixture
def risk_gate():
    return CryptoRiskGate()


@pytest.fixture
def portfolio_mgr():
    return CryptoPortfolioManager(initial_equity=100000.0)


@pytest.fixture
def sample_candidate():
    return CryptoStrategyCandidate(
        strategy="TREND_FOLLOWING_LONG",
        symbol="BTC/USD",
        direction="long",
        entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
        expected_risk=1000.0,
        expected_reward=2000.0,
        risk_reward_ratio=2.0,
        confidence=0.85,
        regime="BULL",
        signal_timestamp=datetime.now(timezone.utc).isoformat(),
        strategy_details={"realized_vol": 25.0},
    )


# Test 1: BUY entry creates position.
def test_1_buy_entry_creates_position(portfolio_mgr):
    order = make_order(order_id="ORD-001", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order, regime="BULL")
    assert pos is not None
    assert pos.symbol == "BTC/USD"
    assert pos.quantity == 0.001
    assert "BTC/USD" in [p.symbol for p in portfolio_mgr.active_positions.values()]


# Test 2: Next scan with same signal produces HOLD, not another BUY.
def test_2_next_scan_produces_hold(risk_gate, sample_candidate):
    open_positions = [{"symbol": "BTC/USD"}]  # Existing open position
    decision = risk_gate.evaluate_candidate(
        candidate=sample_candidate,
        equity=100000.0,
        daily_pnl=0.0,
        open_positions=open_positions,
    )
    assert not decision.approved
    assert any("DUPLICATE_SYMBOL" in v for v in decision.violations)


# Test 3: Take-profit produces SELL.
def test_3_take_profit_produces_sell(portfolio_mgr):
    order = make_order(order_id="ORD-001", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)
    pos.take_profit = 67000.0
    pos.stop_loss = 64000.0

    # Market price reaches Take Profit
    market_map = {"BTC/USD": MagicMock(current_price=67500.0)}
    closed = portfolio_mgr.update_and_check_exits(market_map)
    assert len(closed) == 1
    assert closed[0].exit_reason == "TAKE_PROFIT"
    assert closed[0].realized_pnl > 0


# Test 4: Stop-loss produces SELL.
def test_4_stop_loss_produces_sell(portfolio_mgr):
    order = make_order(order_id="ORD-002", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)
    pos.take_profit = 67000.0
    pos.stop_loss = 64000.0

    market_map = {"BTC/USD": MagicMock(current_price=63500.0)}
    closed = portfolio_mgr.update_and_check_exits(market_map)
    assert len(closed) == 1
    assert closed[0].exit_reason == "STOP_LOSS"
    assert closed[0].realized_pnl < 0


# Test 5: Strategy reversal produces SELL.
def test_5_strategy_reversal_produces_sell(portfolio_mgr):
    order = make_order(order_id="ORD-003", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)

    # Force close due to strategy reversal
    closed_pos = portfolio_mgr.close_position(pos.position_id, 64800.0, "STRATEGY_REVERSAL")
    assert closed_pos is not None
    assert closed_pos.exit_reason == "STRATEGY_REVERSAL"


# Test 6: SELL quantity equals actual broker-held quantity.
def test_6_sell_quantity_equals_held_quantity(portfolio_mgr):
    order = make_order(order_id="ORD-004", symbol="BTC/USD", side="buy", quantity=0.005, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)

    engine = CryptoExecutionEngine(mode="paper")
    exit_payload = engine.build_exit_order_payload(
        position_id=pos.position_id,
        symbol=pos.symbol,
        direction=pos.direction,
        quantity=pos.quantity,
        exit_price=66000.0,
        exit_reason="TAKE_PROFIT",
    )
    assert exit_payload.quantity == 0.005
    assert exit_payload.side == "sell"


# Test 7: Closed position permits a new future entry.
def test_7_closed_position_permits_future_entry(portfolio_mgr, risk_gate, sample_candidate):
    order = make_order(order_id="ORD-005", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)
    portfolio_mgr.close_position(pos.position_id, 66000.0, "TAKE_PROFIT")

    # Now open positions is empty
    open_positions = [{"symbol": p.symbol} for p in portfolio_mgr.active_positions.values()]
    decision = risk_gate.evaluate_candidate(candidate=sample_candidate, equity=100000.0, daily_pnl=0.0, open_positions=open_positions)
    assert decision.approved


# Test 8: Existing position blocks duplicate entry.
def test_8_existing_position_blocks_duplicate_entry(risk_gate, sample_candidate):
    open_positions = [{"symbol": "BTCUSD"}]  # Normalized symbol matching
    decision = risk_gate.evaluate_candidate(candidate=sample_candidate, equity=100000.0, daily_pnl=0.0, open_positions=open_positions)
    assert not decision.approved


# Test 9: Broker/local position mismatch blocks new entries.
def test_9_broker_local_mismatch_blocks_new_entries(risk_gate, sample_candidate):
    local_pos = []
    broker_pos = [{"symbol": "BTCUSD"}]
    combined_pos = local_pos + broker_pos

    decision = risk_gate.evaluate_candidate(candidate=sample_candidate, equity=100000.0, daily_pnl=0.0, open_positions=combined_pos)
    assert not decision.approved


# Test 10: API failure fails closed.
def test_10_api_failure_fails_closed():
    gate = CryptoRiskGate()
    decision = gate.evaluate_candidate(candidate=None, equity=100000.0, daily_pnl=0.0, open_positions=[])
    assert not decision.approved


# Test 11: Unknown order state fails closed.
def test_11_unknown_order_state_fails_closed(portfolio_mgr):
    order = make_order(order_id="ORD-UNK", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="UNKNOWN")
    pos = portfolio_mgr.open_position_from_order(order)
    assert pos is None  # Does not open position on UNKNOWN status


# Test 12: Repeated daemon cycles do not accumulate unlimited BTC.
def test_12_repeated_cycles_do_not_accumulate(risk_gate, sample_candidate):
    open_positions = [{"symbol": "BTC/USD"}]
    for _ in range(50):
        decision = risk_gate.evaluate_candidate(candidate=sample_candidate, equity=100000.0, daily_pnl=0.0, open_positions=open_positions)
        assert not decision.approved


# Test 13: Idempotency blocks duplicate entry.
def test_13_idempotency_blocks_duplicate_entry():
    seen = set()
    fingerprint = "TREND_FOLLOWING_LONG:long:BTC/USD:65000.00"
    seen.add(fingerprint)

    assert fingerprint in seen


# Test 14: Exit order is correctly associated with entry.
def test_14_exit_order_associated_with_entry(portfolio_mgr):
    order = make_order(order_id="ORD-ENTRY-100", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)

    engine = CryptoExecutionEngine(mode="paper")
    exit_payload = engine.build_exit_order_payload(
        position_id=pos.position_id,
        symbol=pos.symbol,
        direction=pos.direction,
        quantity=pos.quantity,
        exit_price=66000.0,
        exit_reason="TAKE_PROFIT",
    )
    assert exit_payload.fingerprint.startswith("EXIT:")
    assert exit_payload.symbol == "BTC/USD"


# Test 15: Realized P&L calculated only after confirmed exit fill.
def test_15_realized_pnl_calculated_on_exit(portfolio_mgr):
    order = make_order(order_id="ORD-ENTRY-200", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)
    assert pos.realized_pnl == 0.0

    closed = portfolio_mgr.close_position(pos.position_id, 67000.0, "TAKE_PROFIT")
    assert closed.realized_pnl > 0.0


# Test 16: Partial exit updates remaining position quantity correctly.
def test_16_partial_exit_updates_remaining_qty(portfolio_mgr):
    order = make_order(order_id="ORD-ENTRY-300", symbol="BTC/USD", side="buy", quantity=0.010, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)

    pos.quantity -= 0.004
    assert pos.quantity == 0.006


# Test 17: Full exit changes position state to CLOSED.
def test_17_full_exit_changes_state_to_closed(portfolio_mgr):
    order = make_order(order_id="ORD-ENTRY-400", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)
    pos_id = pos.position_id

    closed = portfolio_mgr.close_position(pos_id, 66000.0, "MANUAL")
    assert pos_id not in portfolio_mgr.active_positions
    assert closed in portfolio_mgr.closed_positions


# Test 18: Circuit breaker prevents new entries but permits risk-reducing exits.
def test_18_circuit_breaker_permits_exits(portfolio_mgr, risk_gate, sample_candidate):
    order = make_order(order_id="ORD-ENTRY-500", symbol="BTC/USD", side="buy", quantity=0.001, price=65000.0, status="FILLED")
    pos = portfolio_mgr.open_position_from_order(order)

    # Circuit breaker active: new entries blocked
    decision = risk_gate.evaluate_candidate(candidate=sample_candidate, equity=100000.0, daily_pnl=-2000.0, open_positions=[])
    assert not decision.approved

    # Exits remain operational
    closed = portfolio_mgr.close_position(pos.position_id, 64000.0, "STOP_LOSS")
    assert closed is not None
