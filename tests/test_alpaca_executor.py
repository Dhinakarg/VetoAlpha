"""
Unit tests for AlpacaExecutor multi-leg Iron Condor order execution.
Tests 4-leg structure, symbols, sides, position intents, credit limit pricing,
dry-run safety, successful submissions, rejections, partial fills, and closing orders.
All Alpaca API client calls are 100% mocked.
"""

from unittest.mock import MagicMock, patch
import pytest

from trading_agent.execution.alpaca_executor import AlpacaExecutor
from trading_agent.models import (
    OptionContract,
    CondorLeg,
    IronCondor,
    TradeProposal,
    RiskDecision,
    ExecutionResult,
)
from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce


def _make_sample_condor(symbol: str = "SPY", net_credit: float = 2.50) -> IronCondor:
    exp = "2026-09-18"
    put_long = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918P00090000", underlying_symbol=symbol, strike_price=90.0,
            expiration_date=exp, contract_type="put", bid=1.20, ask=1.25, open_interest=500, delta=-0.10,
        ),
        side="buy", quantity=1, leg_type="put_long",
    )
    put_short = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918P00095000", underlying_symbol=symbol, strike_price=95.0,
            expiration_date=exp, contract_type="put", bid=2.50, ask=2.55, open_interest=500, delta=-0.18,
        ),
        side="sell", quantity=1, leg_type="put_short",
    )
    call_short = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918C00105000", underlying_symbol=symbol, strike_price=105.0,
            expiration_date=exp, contract_type="call", bid=2.40, ask=2.45, open_interest=500, delta=0.18,
        ),
        side="sell", quantity=1, leg_type="call_short",
    )
    call_long = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918C00110000", underlying_symbol=symbol, strike_price=110.0,
            expiration_date=exp, contract_type="call", bid=1.10, ask=1.15, open_interest=500, delta=0.10,
        ),
        side="buy", quantity=1, leg_type="call_long",
    )

    return IronCondor(
        underlying_symbol=symbol,
        put_long=put_long,
        put_short=put_short,
        call_short=call_short,
        call_long=call_long,
        net_credit=net_credit,
        max_risk=250.0,
        max_profit=250.0,
        wing_width=5.0,
        expiration_date=exp,
        lower_breakeven=92.50,
        upper_breakeven=107.50,
    )


@pytest.fixture
def executor():
    return AlpacaExecutor(
        api_key="test_api_key",
        secret_key="test_secret_key",
        paper=True,
        dry_run=False,
    )


def test_place_condor_order_payload_and_intents(executor):
    """Test that place_condor_order builds exact 4-leg LimitOrderRequest with correct symbols, sides, and intents."""
    condor = _make_sample_condor()
    mock_trading_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "ORD_ALPAC_12345"
    mock_order.status = "new"
    mock_order.filled_at = None
    mock_trading_client.submit_order.return_value = mock_order
    executor._trading_client = mock_trading_client

    res = executor.place_condor_order(condor=condor, quantity=2, dry_run=False)

    assert isinstance(res, ExecutionResult)
    assert res.order_id == "ORD_ALPAC_12345"
    assert res.status == "new"

    # Verify submit_order was called exactly once
    assert mock_trading_client.submit_order.call_count == 1
    submitted_req = mock_trading_client.submit_order.call_args[0][0]

    # Verify order structure
    assert submitted_req.order_class == OrderClass.MLEG
    assert submitted_req.qty == 2
    assert submitted_req.limit_price == 2.50
    assert submitted_req.time_in_force == TimeInForce.DAY
    assert len(submitted_req.legs) == 4

    # Leg 1: Long Put -> BUY (BUY_TO_OPEN)
    assert submitted_req.legs[0].symbol == "SPY260918P00090000"
    assert submitted_req.legs[0].side == OrderSide.BUY
    assert submitted_req.legs[0].position_intent == PositionIntent.BUY_TO_OPEN

    # Leg 2: Short Put -> SELL (SELL_TO_OPEN)
    assert submitted_req.legs[1].symbol == "SPY260918P00095000"
    assert submitted_req.legs[1].side == OrderSide.SELL
    assert submitted_req.legs[1].position_intent == PositionIntent.SELL_TO_OPEN

    # Leg 3: Short Call -> SELL (SELL_TO_OPEN)
    assert submitted_req.legs[2].symbol == "SPY260918C00105000"
    assert submitted_req.legs[2].side == OrderSide.SELL
    assert submitted_req.legs[2].position_intent == PositionIntent.SELL_TO_OPEN

    # Leg 4: Long Call -> BUY (BUY_TO_OPEN)
    assert submitted_req.legs[3].symbol == "SPY260918C00110000"
    assert submitted_req.legs[3].side == OrderSide.BUY
    assert submitted_req.legs[3].position_intent == PositionIntent.BUY_TO_OPEN


def test_dry_run_safety(executor):
    """Test that dry_run=True constructs order payload without calling submit_order."""
    condor = _make_sample_condor()
    mock_trading_client = MagicMock()
    executor._trading_client = mock_trading_client

    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=True)

    assert res.status == "new"
    assert "dry" in res.order_id.lower() or "sim" in res.order_id.lower()
    # Ensure zero external submit calls were made
    assert mock_trading_client.submit_order.call_count == 0


def test_close_condor_order_reversed_intents(executor):
    """Test that close_condor_order reverses sides and uses CLOSE position intents."""
    condor = _make_sample_condor()
    mock_trading_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "ORD_CLOSE_999"
    mock_order.status = "accepted"
    mock_trading_client.submit_order.return_value = mock_order
    executor._trading_client = mock_trading_client

    res = executor.close_condor_order(condor=condor, quantity=1, close_limit_debit=1.20, dry_run=False)

    assert res.order_id == "ORD_CLOSE_999"
    assert mock_trading_client.submit_order.call_count == 1
    submitted_req = mock_trading_client.submit_order.call_args[0][0]

    assert submitted_req.order_class == OrderClass.MLEG
    assert submitted_req.limit_price == 1.20

    # Reversed Leg 1: Long Put -> SELL (SELL_TO_CLOSE)
    assert submitted_req.legs[0].side == OrderSide.SELL
    assert submitted_req.legs[0].position_intent == PositionIntent.SELL_TO_CLOSE

    # Reversed Leg 2: Short Put -> BUY (BUY_TO_CLOSE)
    assert submitted_req.legs[1].side == OrderSide.BUY
    assert submitted_req.legs[1].position_intent == PositionIntent.BUY_TO_CLOSE

    # Reversed Leg 3: Short Call -> BUY (BUY_TO_CLOSE)
    assert submitted_req.legs[2].side == OrderSide.BUY
    assert submitted_req.legs[2].position_intent == PositionIntent.BUY_TO_CLOSE

    # Reversed Leg 4: Long Call -> SELL (SELL_TO_CLOSE)
    assert submitted_req.legs[3].side == OrderSide.SELL
    assert submitted_req.legs[3].position_intent == PositionIntent.SELL_TO_CLOSE


def test_order_rejection_handling(executor):
    """Test order rejection from Alpaca is captured cleanly in ExecutionResult."""
    condor = _make_sample_condor()
    mock_trading_client = MagicMock()
    mock_trading_client.submit_order.side_effect = Exception("403 Forbidden: Account buying power insufficient")
    executor._trading_client = mock_trading_client

    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=False)

    assert res.status == "rejected"
    assert "insufficient" in res.error_message.lower() or "403" in res.error_message


def test_partial_fill_handling(executor):
    """Test partially filled order status mapping."""
    condor = _make_sample_condor()
    mock_trading_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "ORD_PARTIAL_777"
    mock_order.status = "partially_filled"
    mock_order.filled_at = None
    mock_trading_client.submit_order.return_value = mock_order
    executor._trading_client = mock_trading_client

    res = executor.place_condor_order(condor=condor, quantity=5, dry_run=False)

    assert res.status == "partially_filled"
    assert res.order_id == "ORD_PARTIAL_777"


def test_get_order_status(executor):
    """Test get_order_status retrieves order details by ID."""
    mock_trading_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "ORD_123"
    mock_order.symbol = "SPY"
    mock_order.status = "filled"
    mock_order.qty = 2
    mock_order.filled_qty = 2
    mock_order.filled_avg_price = 2.50
    mock_order.created_at = None
    mock_trading_client.get_order_by_id.return_value = mock_order
    executor._trading_client = mock_trading_client

    status_dict = executor.get_order_status("ORD_123")

    assert status_dict["order_id"] == "ORD_123"
    assert status_dict["status"] == "filled"
    assert status_dict["filled_qty"] == 2


def test_execute_proposal_risk_rejected(executor):
    """Test execute_proposal blocks execution if RiskDecision is not approved."""
    condor = _make_sample_condor()
    proposal = TradeProposal(proposal_id="P_REJ", symbol="SPY", condor=condor, quantity=1, created_at="2026-08-28T00:00:00Z")
    decision = RiskDecision(proposal_id="P_REJ", approved=False, reason="Risk Gate rule 1 failed.")

    res = executor.execute_proposal(proposal=proposal, risk_decision=decision, dry_run=False)

    assert res.status == "rejected"
    assert "rejected" in res.error_message.lower()
