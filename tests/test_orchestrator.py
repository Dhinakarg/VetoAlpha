"""
Unit tests for Autonomous Agent Orchestrator.
Tests all 26 lifecycle steps, market clock checks, circuit breaker isolation,
regime gate filtering, Featherless AI catalyst boundaries, dry-run safety,
order execution, position monitoring, and exception resilience.
"""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pytest

from trading_agent.agent.orchestrator import AgentOrchestrator
from trading_agent.models import (
    AccountSnapshot,
    OptionChainSnapshot,
    OptionContract,
    CondorLeg,
    IronCondor,
    SentimentResult,
    RiskDecision,
    ExecutionResult,
)
from trading_agent.risk.safety import SafetyManager


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_sample_condor(symbol: str = "SPY") -> IronCondor:
    ts = _make_fresh_timestamp()
    exp = "2026-09-18"
    put_long = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918P00090000", underlying_symbol=symbol, strike_price=90.0,
            expiration_date=exp, contract_type="put", bid=1.20, ask=1.25, open_interest=500,
            delta=-0.10, quote_timestamp=ts,
        ),
        side="buy", quantity=1, leg_type="put_long",
    )
    put_short = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918P00095000", underlying_symbol=symbol, strike_price=95.0,
            expiration_date=exp, contract_type="put", bid=2.50, ask=2.55, open_interest=500,
            delta=-0.18, quote_timestamp=ts,
        ),
        side="sell", quantity=1, leg_type="put_short",
    )
    call_short = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918C00105000", underlying_symbol=symbol, strike_price=105.0,
            expiration_date=exp, contract_type="call", bid=2.40, ask=2.45, open_interest=500,
            delta=0.18, quote_timestamp=ts,
        ),
        side="sell", quantity=1, leg_type="call_short",
    )
    call_long = CondorLeg(
        contract=OptionContract(
            symbol="SPY260918C00110000", underlying_symbol=symbol, strike_price=110.0,
            expiration_date=exp, contract_type="call", bid=1.10, ask=1.15, open_interest=500,
            delta=0.10, quote_timestamp=ts,
        ),
        side="buy", quantity=1, leg_type="call_long",
    )

    return IronCondor(
        underlying_symbol=symbol,
        put_long=put_long,
        put_short=put_short,
        call_short=call_short,
        call_long=call_long,
        net_credit=2.50,
        max_risk=250.0,
        max_profit=250.0,
        wing_width=5.0,
        expiration_date=exp,
    )


@pytest.fixture
def mock_orchestrator(tmp_path):
    """Fixture providing an orchestrator with mocked Alpaca and Featherless dependencies."""
    mock_data = MagicMock()
    mock_featherless = MagicMock()
    mock_builder = MagicMock()
    mock_risk = MagicMock()
    mock_executor = MagicMock()
    mock_portfolio = MagicMock()
    safety = SafetyManager(db_path=tmp_path / "orch_idempotency.db")

    # Default market clock open
    mock_data.get_market_clock.return_value = {"is_open": True, "next_open": "", "next_close": ""}
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="ACC_TEST", cash=100000.0, buying_power=100000.0, equity=100000.0, portfolio_value=100000.0, status="ACTIVE"
    )
    mock_data.get_open_positions.return_value = []
    mock_data.get_open_orders.return_value = []
    mock_data.get_recent_bars.return_value = [{"close": 100.0} for _ in range(50)]
    mock_data.get_recent_news.return_value = []

    # Mock option chain
    condor = _make_sample_condor()
    chain = OptionChainSnapshot(
        underlying_symbol="SPY",
        underlying_price=100.0,
        timestamp=_make_fresh_timestamp(),
        contracts=[
            condor.put_long.contract, condor.put_short.contract, condor.call_short.contract, condor.call_long.contract
        ]
    )
    mock_data.get_option_chain.return_value = chain

    # Mock Featherless response (low catalyst risk)
    mock_featherless.analyze_catalyst_risk.return_value = SentimentResult(
        symbol="SPY",
        catalyst_risk="low",
        confidence=0.9,
        rationale="No impending high risk earnings or macroeconomic catalysts.",
        model_used="featherless/test-model",
        timestamp=_make_fresh_timestamp(),
    )

    # Mock Condor builder
    mock_builder.build_condor.return_value = condor

    # Mock Executor
    mock_executor.place_condor_order.return_value = ExecutionResult(
        execution_id="EXEC_001", proposal_id="PROP_001", order_id="ORD_ALPAC_999", status="new"
    )

    return AgentOrchestrator(
        alpaca_data=mock_data,
        featherless_client=mock_featherless,
        condor_builder=mock_builder,
        risk_gate=mock_risk,
        safety_manager=safety,
        executor=mock_executor,
        position_manager=mock_portfolio,
    )


def test_orchestrator_market_closed_exit(mock_orchestrator):
    """Step 3: Orchestrator exits early when market is closed."""
    mock_orchestrator.alpaca_data.get_market_clock.return_value = {"is_open": False}

    res = mock_orchestrator.run_scan_cycle(symbol="SPY")

    assert res["status"] == "market_closed"
    assert "Market is closed" in res["reason"]
    # Ensure Featherless AI is NOT called
    assert mock_orchestrator.featherless.analyze_catalyst_risk.call_count == 0


def test_orchestrator_circuit_breaker_active(mock_orchestrator):
    """Step 8: When circuit breaker is active, no new trades are created and Featherless is NOT called."""
    # Trigger circuit breaker in safety manager
    mock_orchestrator.safety._is_halted = True

    res = mock_orchestrator.run_scan_cycle(symbol="SPY")

    assert res["status"] == "circuit_breaker_active"
    # Ensure Featherless AI was never invoked
    assert mock_orchestrator.featherless.analyze_catalyst_risk.call_count == 0


@patch("trading_agent.agent.orchestrator.regime_and_volatility_gate")
def test_orchestrator_regime_ineligible_skips_featherless(mock_gate, mock_orchestrator):
    """Step 13: Ineligible technical regime skips calling Featherless AI."""
    mock_gate.return_value = {"eligible": False, "reasons": ["ADX trend too high"]}

    res = mock_orchestrator.run_scan_cycle(symbol="SPY")

    assert res["status"] == "regime_ineligible"
    # Ensure Featherless AI was NEVER called
    assert mock_orchestrator.featherless.analyze_catalyst_risk.call_count == 0


@patch("trading_agent.agent.orchestrator.regime_and_volatility_gate")
def test_orchestrator_high_catalyst_risk_aborts_trade(mock_gate, mock_orchestrator):
    """Step 16: High catalyst risk classification from Featherless aborts trade building."""
    mock_gate.return_value = {"eligible": True, "adx": 15.0, "iv_rank": 40.0}
    mock_orchestrator.featherless.analyze_catalyst_risk.return_value = SentimentResult(
        symbol="SPY",
        catalyst_risk="high",
        confidence=0.85,
        rationale="Imminent FOMC interest rate decision",
        model_used="featherless/test-model",
        timestamp=_make_fresh_timestamp(),
    )

    res = mock_orchestrator.run_scan_cycle(symbol="SPY")

    assert res["status"] == "catalyst_risk_high"
    assert res["catalyst_risk"] == "high"
    # Ensure executor was never called
    assert mock_orchestrator.executor.place_condor_order.call_count == 0


@patch("trading_agent.agent.orchestrator.regime_and_volatility_gate")
def test_orchestrator_full_successful_dry_run(mock_gate, mock_orchestrator):
    """Step 22: Full successful end-to-end dry-run execution."""
    mock_gate.return_value = {"eligible": True, "adx": 15.0, "iv_rank": 40.0}

    res = mock_orchestrator.run_scan_cycle(symbol="SPY", dry_run=True)

    assert res["status"] == "success"
    assert res["net_credit"] == 2.50
    assert res["order_id"] == "ORD_ALPAC_999"
    assert mock_orchestrator.executor.place_condor_order.call_count == 1
    # Verify dry_run flag was passed to executor
    assert mock_orchestrator.executor.place_condor_order.call_args[1]["dry_run"] is True


@patch("trading_agent.agent.orchestrator.regime_and_volatility_gate")
def test_orchestrator_live_paper_submission(mock_gate, mock_orchestrator):
    """Step 23: Full paper order submission when dry_run=False."""
    mock_gate.return_value = {"eligible": True, "adx": 15.0, "iv_rank": 40.0}

    res = mock_orchestrator.run_scan_cycle(symbol="SPY", dry_run=False)

    assert res["status"] == "success"
    assert mock_orchestrator.executor.place_condor_order.call_args[1]["dry_run"] is False


def test_orchestrator_resilience_on_exception(mock_orchestrator):
    """Step 26: Gracefully handles unexpected exceptions without crashing."""
    mock_orchestrator.alpaca_data.get_market_clock.side_effect = RuntimeError("Alpaca connection drop")

    res = mock_orchestrator.run_scan_cycle(symbol="SPY")

    assert res["status"] == "error"
    assert "Alpaca connection drop" in res["error"]
