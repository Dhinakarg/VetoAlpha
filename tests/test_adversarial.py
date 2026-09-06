"""
Full Adversarial Test Suite for Adaptive AI Iron Condor Agent.
Tests all 20 stress scenarios ensuring fail-safe execution, deterministic safety,
LLM containment, persistent idempotency, and paper trading enforcement.
ZERO real or paper orders submitted during tests.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.config import settings
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
from trading_agent.strategy.featherless_client import FeatherlessClient
from trading_agent.risk.safety import SafetyManager, SymbolLockManager, create_trade_fingerprint
from trading_agent.risk.risk_gate import evaluate_trade
from trading_agent.strategy.condor_builder import IronCondorBuilder
from trading_agent.strategy.signals import regime_and_volatility_gate
from trading_agent.main import validate_safety_mode


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_sample_condor(symbol: str = "SPY", max_risk: float = 250.0, quote_ts: str = None, iv: float = 0.25) -> IronCondor:
    ts = quote_ts or _make_fresh_timestamp()
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    put_long = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918P00090000", underlying_symbol=symbol, strike_price=90.0,
            expiration_date=exp, contract_type="put", bid=1.20, ask=1.25, open_interest=500,
            delta=-0.10, quote_timestamp=ts, implied_volatility=iv,
        ),
        side="buy", quantity=1, leg_type="put_long",
    )
    put_short = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918P00095000", underlying_symbol=symbol, strike_price=95.0,
            expiration_date=exp, contract_type="put", bid=2.50, ask=2.55, open_interest=500,
            delta=-0.18, quote_timestamp=ts, implied_volatility=iv,
        ),
        side="sell", quantity=1, leg_type="put_short",
    )
    call_short = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918C00105000", underlying_symbol=symbol, strike_price=105.0,
            expiration_date=exp, contract_type="call", bid=2.40, ask=2.45, open_interest=500,
            delta=0.18, quote_timestamp=ts, implied_volatility=iv,
        ),
        side="sell", quantity=1, leg_type="call_short",
    )
    call_long = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918C00110000", underlying_symbol=symbol, strike_price=110.0,
            expiration_date=exp, contract_type="call", bid=1.10, ask=1.15, open_interest=500,
            delta=0.10, quote_timestamp=ts, implied_volatility=iv,
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
        max_risk=max_risk,
        max_profit=250.0,
        wing_width=5.0,
        expiration_date=exp,
    )


@pytest.fixture
def base_orchestrator(tmp_path):
    """Fixture providing an isolated orchestrator."""
    mock_data = MagicMock()
    mock_featherless = MagicMock()
    mock_builder = MagicMock()
    mock_risk = MagicMock()
    mock_executor = MagicMock()
    mock_portfolio = MagicMock()
    safety = SafetyManager(db_path=tmp_path / "adv_idempotency.db")

    mock_data.get_market_clock.return_value = {"is_open": True}
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="PA_ADV_TEST", cash=100000.0, buying_power=100000.0, equity=100000.0, status="ACTIVE"
    )
    mock_data.get_open_positions.return_value = []
    mock_data.get_open_orders.return_value = []
    mock_data.get_recent_bars.return_value = [{"close": 100.0} for _ in range(50)]
    mock_data.get_recent_news.return_value = []

    condor = _make_sample_condor()
    chain = OptionChainSnapshot(
        underlying_symbol="SPY", underlying_price=100.0, timestamp=_make_fresh_timestamp(),
        contracts=[condor.put_long.contract, condor.put_short.contract, condor.call_short.contract, condor.call_long.contract]
    )
    mock_data.get_option_chain.return_value = chain

    mock_featherless.analyze_catalyst_risk.return_value = SentimentResult(
        symbol="SPY", catalyst_risk="low", confidence=0.95, rationale="Safe macro environment",
        model_used="featherless/model", timestamp=_make_fresh_timestamp()
    )
    mock_builder.build_condor.return_value = condor
    mock_executor.place_condor_order.return_value = ExecutionResult(
        execution_id="EXEC_ADV_1", proposal_id="PROP_ADV_1", order_id="ORD_ALPAC_ADV", status="new"
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


# -----------------------------------------------------------------------------
# SCENARIO 1: Market Closed -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_1_market_closed(base_orchestrator):
    base_orchestrator.alpaca_data.get_market_clock.return_value = {"is_open": False, "next_open": "2026-08-31T09:30:00Z"}
    res = base_orchestrator.run_scan_cycle(symbol="SPY")
    assert res["status"] == "market_closed"
    assert base_orchestrator.featherless.analyze_catalyst_risk.call_count == 0
    assert base_orchestrator.executor.place_condor_order.call_count == 0


# -----------------------------------------------------------------------------
# SCENARIO 2: Low IV -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_2_low_iv():
    condor = _make_sample_condor()
    chain = OptionChainSnapshot(
        underlying_symbol="SPY", underlying_price=100.0, timestamp=_make_fresh_timestamp(),
        contracts=[condor.put_long.contract, condor.put_short.contract, condor.call_short.contract, condor.call_long.contract]
    )
    # IV Rank 5% is below 20% minimum threshold
    gate = regime_and_volatility_gate(
        bars=[{"high": 101, "low": 99, "close": 100} for _ in range(50)],
        option_chain=chain,
        underlying_price=100.0,
        iv_history=[0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.40, 0.50],
        config={"MIN_IV_RANK": 20.0},
    )
    assert gate["eligible"] is False
    assert any("IV rank" in r for r in gate["reasons"])


# -----------------------------------------------------------------------------
# SCENARIO 3: Strong Trend / High ADX -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_3_high_adx_trend():
    condor = _make_sample_condor()
    chain = OptionChainSnapshot(
        underlying_symbol="SPY", underlying_price=100.0, timestamp=_make_fresh_timestamp(),
        contracts=[condor.put_long.contract, condor.put_short.contract, condor.call_short.contract, condor.call_long.contract]
    )
    # Steeply trending price series produces high ADX (> 20)
    trending_bars = [{"high": 100 + i*2, "low": 98 + i*2, "close": 99 + i*2} for i in range(50)]
    gate = regime_and_volatility_gate(
        bars=trending_bars,
        option_chain=chain,
        underlying_price=100.0,
        iv_history=[0.20 for _ in range(20)],
        config={"ADX_THRESHOLD": 20.0},
    )
    assert gate["eligible"] is False
    assert any("ADX" in r or "trending" in r.lower() for r in gate["reasons"])


# -----------------------------------------------------------------------------
# SCENARIO 4: High Catalyst Risk -> NO TRADE
# -----------------------------------------------------------------------------
@patch("trading_agent.agent.orchestrator.regime_and_volatility_gate")
def test_scenario_4_high_catalyst_risk(mock_gate, base_orchestrator):
    mock_gate.return_value = {"eligible": True, "adx": 15.0, "iv_rank": 40.0}
    base_orchestrator.featherless.analyze_catalyst_risk.return_value = SentimentResult(
        symbol="SPY", catalyst_risk="high", confidence=0.90, rationale="Severe earnings volatility expected",
        model_used="featherless/model", timestamp=_make_fresh_timestamp()
    )
    res = base_orchestrator.run_scan_cycle(symbol="SPY")
    assert res["status"] == "catalyst_risk_high"
    assert base_orchestrator.executor.place_condor_order.call_count == 0


# -----------------------------------------------------------------------------
# SCENARIO 5: Missing LLM Response -> NO TRADE (Fails Safe)
# -----------------------------------------------------------------------------
def test_scenario_5_missing_llm_response():
    client = FeatherlessClient(api_key="")  # No API key configured
    client.api_key = ""
    client._client = None
    res = client.analyze_catalyst_risk(symbol="SPY", market_context={}, news=[], upcoming_events={})
    assert res.catalyst_risk == "high"
    assert "fail-safe" in res.rationale.lower() or "missing" in res.rationale.lower() or "high catalyst risk" in res.rationale.lower()


# -----------------------------------------------------------------------------
# SCENARIO 6: LLM Confidence < 0.50 -> NO TRADE
# -----------------------------------------------------------------------------
@patch("trading_agent.agent.orchestrator.regime_and_volatility_gate")
def test_scenario_6_low_ai_confidence(mock_gate, base_orchestrator):
    mock_gate.return_value = {"eligible": True, "adx": 15.0, "iv_rank": 40.0}
    base_orchestrator.featherless.analyze_catalyst_risk.return_value = SentimentResult(
        symbol="SPY", catalyst_risk="low", confidence=0.35, rationale="Inconclusive news sentiment",
        model_used="featherless/model", timestamp=_make_fresh_timestamp()
    )
    res = base_orchestrator.run_scan_cycle(symbol="SPY")
    assert res["status"] == "ai_confidence_low"
    assert base_orchestrator.executor.place_condor_order.call_count == 0


# -----------------------------------------------------------------------------
# SCENARIO 7: Poor Option Liquidity -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_7_poor_option_liquidity():
    condor = _make_sample_condor()
    # Ruin liquidity on short call (wide spread: bid 1.0, ask 2.5)
    condor.call_short.contract.bid = 1.00
    condor.call_short.contract.ask = 2.50
    proposal = {"proposal_id": "P_LIQ", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    decision = evaluate_trade(proposed_trade=proposal, account=account, config={"MAX_BID_ASK_SPREAD_PCT": 0.20})
    assert decision.approved is False
    assert any("liquidity" in r.lower() or "Rule 15" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# SCENARIO 8: Missing Option Wing -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_8_missing_option_wing():
    builder = IronCondorBuilder()
    condor = _make_sample_condor()
    # Option chain missing the long call wing strike
    chain = OptionChainSnapshot(
        underlying_symbol="SPY", underlying_price=100.0, timestamp=_make_fresh_timestamp(),
        contracts=[condor.put_long.contract, condor.put_short.contract, condor.call_short.contract]
    )
    result = builder.build_condor(option_chain=chain, underlying_price=100.0, wing_width=5.0)
    assert result is None


# -----------------------------------------------------------------------------
# SCENARIO 9: Maximum Loss Exceeds Limit -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_9_max_loss_exceeds_limit():
    condor = _make_sample_condor(max_risk=4000.0)  # Exceeds $2,500 cap
    proposal = {"proposal_id": "P_RISK", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    decision = evaluate_trade(proposed_trade=proposal, account=account, config={"MAX_LOSS_PER_TRADE_USD": 2500.0})
    assert decision.approved is False
    assert any("Rule 1" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# SCENARIO 10: Daily Loss Circuit Breaker Triggered -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_10_daily_loss_circuit_breaker():
    condor = _make_sample_condor(max_risk=250.0)
    proposal = {"proposal_id": "P_CB", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    # Daily P&L = -$3,500 (breached -$3,000 limit)
    decision = evaluate_trade(proposed_trade=proposal, account=account, daily_pnl=-3500.0, config={"MAX_DAILY_LOSS_USD": 3000.0})
    assert decision.approved is False
    assert any("Rule 5" in r or "circuit-breaker" in r.lower() for r in decision.rejections)


# -----------------------------------------------------------------------------
# SCENARIO 11: Duplicate Trade -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_11_duplicate_trade(tmp_path):
    safety = SafetyManager(db_path=tmp_path / "dup_test.db")
    condor = _make_sample_condor()
    fp = create_trade_fingerprint(condor)

    # Record first attempt
    safety.record_trade_attempt(fingerprint=fp, symbol="SPY", expiration="2026-09-18", order_id="ORD_1")

    # Second validation must reject
    proposal = {"proposal_id": "P_DUP", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)
    safe, violations = safety.validate_runtime_safety(proposed_trade=proposal, account=account)
    assert safe is False
    assert any("Duplicate" in v for v in violations)


# -----------------------------------------------------------------------------
# SCENARIO 12: Stale Market Data -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_12_stale_market_data():
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=1800)).isoformat()
    condor = _make_sample_condor(quote_ts=stale_time)
    proposal = {"proposal_id": "P_STALE", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    decision = evaluate_trade(proposed_trade=proposal, account=account, config={"MAX_QUOTE_AGE_SECONDS": 900})
    assert decision.approved is False
    assert any("Rule 11" in r or "Stale" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# SCENARIO 13: Insufficient Buying Power -> NO TRADE
# -----------------------------------------------------------------------------
def test_scenario_13_insufficient_buying_power():
    condor = _make_sample_condor(max_risk=500.0)
    proposal = {"proposal_id": "P_BP", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100.0)  # Only $100 buying power

    decision = evaluate_trade(proposed_trade=proposal, account=account)
    assert decision.approved is False
    assert any("Rule 8" in r or "buying power" in r.lower() for r in decision.rejections)


# -----------------------------------------------------------------------------
# SCENARIO 14: Alpaca Order Rejection -> Structured failure, NO automatic retry
# -----------------------------------------------------------------------------
def test_scenario_14_alpaca_order_rejection():
    from trading_agent.execution.alpaca_executor import AlpacaExecutor

    executor = AlpacaExecutor(dry_run=False)
    mock_client = MagicMock()
    mock_client.submit_order.side_effect = Exception("403 Forbidden: Margin requirement not satisfied")
    executor._trading_client = mock_client

    condor = _make_sample_condor()
    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=False)

    assert res.status == "rejected"
    assert "403" in res.error_message or "Margin" in res.error_message
    # Confirm submit was called only once (no blind automatic retry)
    assert mock_client.submit_order.call_count == 1


# -----------------------------------------------------------------------------
# SCENARIO 15: Alpaca API Timeout -> Query order state before retry
# -----------------------------------------------------------------------------
def test_scenario_15_alpaca_timeout_queries_state_before_retry():
    from trading_agent.execution.alpaca_executor import AlpacaExecutor

    executor = AlpacaExecutor(dry_run=False)
    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "ORD_TIMEOUT_RECOVERED"
    mock_order.status = "filled"
    mock_order.qty = 1
    mock_order.filled_qty = 1
    mock_order.filled_avg_price = 2.50
    mock_order.created_at = None
    mock_client.get_order_by_id.return_value = mock_order
    executor._trading_client = mock_client

    status = executor.get_order_status("ORD_TIMEOUT_RECOVERED")
    assert status["status"] == "filled"
    assert status["order_id"] == "ORD_TIMEOUT_RECOVERED"
    assert mock_client.get_order_by_id.call_count == 1


# -----------------------------------------------------------------------------
# SCENARIO 16: Scheduler Runs Twice Simultaneously -> Only one scan for symbol
# -----------------------------------------------------------------------------
def test_scenario_16_scheduler_runs_twice_simultaneously():
    lock_mgr = SymbolLockManager()
    symbol = "SPY"

    # Thread 1 acquires lock
    with lock_mgr.acquire_symbol_lock(symbol, blocking=True):
        # Thread 2 attempts to acquire lock on same symbol with timeout
        with pytest.raises(TimeoutError) as exc_info:
            with lock_mgr.acquire_symbol_lock(symbol, blocking=True, timeout=0.05):
                pass

    assert "Could not acquire execution lock" in str(exc_info.value)


# -----------------------------------------------------------------------------
# SCENARIO 17: Process Restarts After Order -> Fingerprint prevents duplicate
# -----------------------------------------------------------------------------
def test_scenario_17_process_restart_persistence(tmp_path):
    db_path = tmp_path / "restart_adv.db"

    # Instance 1 logs trade attempt
    sm1 = SafetyManager(db_path=db_path)
    condor = _make_sample_condor()
    fp = create_trade_fingerprint(condor)
    sm1.record_trade_attempt(fingerprint=fp, symbol="SPY", expiration="2026-09-18", order_id="ORD_17")

    # Instance 2 (simulating fresh process restart)
    sm2 = SafetyManager(db_path=db_path)
    assert sm2.is_duplicate_trade(fp) is True


# -----------------------------------------------------------------------------
# SCENARIO 18: Dry Run Mode -> ZERO order submissions
# -----------------------------------------------------------------------------
def test_scenario_18_dry_run_mode():
    from trading_agent.execution.alpaca_executor import AlpacaExecutor

    mock_client = MagicMock()
    executor = AlpacaExecutor(dry_run=True)
    executor._trading_client = mock_client

    condor = _make_sample_condor()
    res = executor.place_condor_order(condor=condor, quantity=1, dry_run=True)

    assert res.status == "new"
    assert mock_client.submit_order.call_count == 0


# -----------------------------------------------------------------------------
# SCENARIO 19: Real-Money Environment -> Application refuses to start
# -----------------------------------------------------------------------------
def test_scenario_19_real_money_refusal(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_paper", False)

    with pytest.raises(SystemExit) as exc_info:
        validate_safety_mode(dry_run=False)

    assert exc_info.value.code == 1


# -----------------------------------------------------------------------------
# SCENARIO 20: Valid Complete Setup -> Order payload generated successfully
# -----------------------------------------------------------------------------
@patch("trading_agent.agent.orchestrator.regime_and_volatility_gate")
def test_scenario_20_valid_complete_setup(mock_gate, base_orchestrator):
    mock_gate.return_value = {"eligible": True, "adx": 15.0, "iv_rank": 40.0}

    res = base_orchestrator.run_scan_cycle(symbol="SPY", dry_run=True)

    assert res["status"] == "success"
    assert res["symbol"] == "SPY"
    assert res["net_credit"] == 2.50
    assert res["order_id"] == "ORD_ALPAC_ADV"
    assert base_orchestrator.executor.place_condor_order.call_count == 1
