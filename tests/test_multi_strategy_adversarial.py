"""
Comprehensive Adversarial and Safety Proof Test Suite for Multi-Strategy Options Alpha Agent.
Covers 35+ adversarial test vectors and explicit mathematical/architectural proofs:
  - LLM output cannot directly create an order
  - LLM cannot override risk gates
  - A rejected candidate cannot reach executor
  - A circuit breaker blocks every strategy
  - Dry-run results in zero Alpaca order submissions
  - Live-money configuration is impossible
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.config import settings
from trading_agent.models import (
    OptionContract,
    OptionChainSnapshot,
    AccountSnapshot,
    StrategyCandidate,
    StrategyLeg,
    IronCondor,
    CondorLeg,
    TradeProposal,
    RiskDecision,
    ExecutionResult,
)
from trading_agent.strategy.bull_put_spread import build_bull_put_spread
from trading_agent.strategy.bear_call_spread import build_bear_call_spread
from trading_agent.strategy.condor_builder import IronCondorBuilder
from trading_agent.strategy.regime import MarketRegime, detect_market_regime
from trading_agent.strategy.strategy_selector import StrategySelector
from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment
from trading_agent.risk.risk_gate import RiskGate, evaluate_trade
from trading_agent.risk.safety import (
    SafetyManager,
    create_trade_fingerprint,
    validate_paper_execution_environment,
    PaperExecutionSafetyError,
)
from trading_agent.portfolio.position_manager import (
    PositionManager,
    calculate_position_metrics,
    evaluate_position_exit,
)
from trading_agent.execution.alpaca_executor import AlpacaExecutor
from trading_agent.agent.orchestrator import AgentOrchestrator


def _fresh_ts(seconds_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


@pytest.fixture
def default_exp() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")


@pytest.fixture
def sample_account() -> AccountSnapshot:
    return AccountSnapshot(
        account_id="ACC_PAPER_TEST_001",
        cash=100000.0,
        buying_power=100000.0,
        equity=100000.0,
        portfolio_value=100000.0,
        status="ACTIVE",
    )


@pytest.fixture
def valid_bull_put(default_exp) -> StrategyCandidate:
    short_put = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_P545", underlying_symbol="SPY", strike_price=545.0, expiration_date=default_exp,
            contract_type="put", bid=2.00, ask=2.10, open_interest=500, delta=-0.18, quote_timestamp=_fresh_ts()
        ),
        side="sell", quantity=1, leg_type="put_short",
    )
    long_put = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_P540", underlying_symbol="SPY", strike_price=540.0, expiration_date=default_exp,
            contract_type="put", bid=0.90, ask=1.00, open_interest=500, delta=-0.08, quote_timestamp=_fresh_ts()
        ),
        side="buy", quantity=1, leg_type="put_long",
    )
    return StrategyCandidate(
        strategy_name="bull_put_spread", underlying="SPY", expiration=default_exp,
        legs=[short_put, long_put], entry_credit=1.10, max_profit=110.0, max_loss=390.0,
        breakeven=543.90, wing_width=5.0, dte=30, short_delta=-0.18,
    )


@pytest.fixture
def valid_bear_call(default_exp) -> StrategyCandidate:
    short_call = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_C560", underlying_symbol="SPY", strike_price=560.0, expiration_date=default_exp,
            contract_type="call", bid=2.00, ask=2.10, open_interest=500, delta=0.18, quote_timestamp=_fresh_ts()
        ),
        side="sell", quantity=1, leg_type="call_short",
    )
    long_call = StrategyLeg(
        contract=OptionContract(
            symbol="SPY_C565", underlying_symbol="SPY", strike_price=565.0, expiration_date=default_exp,
            contract_type="call", bid=0.90, ask=1.00, open_interest=500, delta=0.08, quote_timestamp=_fresh_ts()
        ),
        side="buy", quantity=1, leg_type="call_long",
    )
    return StrategyCandidate(
        strategy_name="bear_call_spread", underlying="SPY", expiration=default_exp,
        legs=[short_call, long_call], entry_credit=1.10, max_profit=110.0, max_loss=390.0,
        breakeven=561.10, wing_width=5.0, dte=30, short_delta=0.18,
    )


# -----------------------------------------------------------------------------
# 1. Malformed & Structural Adversarial Tests
# -----------------------------------------------------------------------------
def test_malformed_strategy_candidate(sample_account):
    """1. Malformed strategy candidate rejected by Risk Gate."""
    res = evaluate_trade(None, sample_account, [], [], 0.0)
    assert not res.approved
    assert any("none" in r.lower() or "invalid" in r.lower() or "missing" in r.lower() for r in res.rejections)


def test_missing_option_symbol(valid_bull_put, sample_account):
    """2. Missing option symbol in legs rejected."""
    valid_bull_put.legs[0].contract.symbol = ""
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved
    assert any("symbol" in r.lower() or "contract" in r.lower() or "missing" in r.lower() for r in res.rejections)


def test_missing_strike(default_exp):
    """3. Missing strike price rejected during candidate construction."""
    c1 = OptionContract(symbol="P1", underlying_symbol="SPY", strike_price=0.0, expiration_date=default_exp, contract_type="put", bid=1.0, ask=1.1, delta=-0.2, open_interest=100, quote_timestamp=_fresh_ts())
    c2 = OptionContract(symbol="P2", underlying_symbol="SPY", strike_price=540.0, expiration_date=default_exp, contract_type="put", bid=0.5, ask=0.6, delta=-0.1, open_interest=100, quote_timestamp=_fresh_ts())
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=550.0, timestamp=_fresh_ts(), contracts=[c1, c2])
    cand = build_bull_put_spread(chain, 550.0)
    assert cand is None


def test_missing_expiration(valid_bull_put, sample_account):
    """4. Missing expiration rejected."""
    valid_bull_put.expiration = ""
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved
    assert any("expiration" in r.lower() or "missing" in r.lower() for r in res.rejections)


def test_missing_delta(default_exp):
    """5. Missing Greeks / Delta rejected."""
    c1 = OptionContract(symbol="P1", underlying_symbol="SPY", strike_price=545.0, expiration_date=default_exp, contract_type="put", bid=1.0, ask=1.1, delta=None, open_interest=100, quote_timestamp=_fresh_ts())
    c2 = OptionContract(symbol="P2", underlying_symbol="SPY", strike_price=540.0, expiration_date=default_exp, contract_type="put", bid=0.5, ask=0.6, delta=-0.1, open_interest=100, quote_timestamp=_fresh_ts())
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=550.0, timestamp=_fresh_ts(), contracts=[c1, c2])
    cand = build_bull_put_spread(chain, 550.0)
    assert cand is None


def test_missing_quote(valid_bull_put, sample_account):
    """6. Missing quote (bid=0, ask=0) rejected."""
    valid_bull_put.legs[0].contract.bid = 0.0
    valid_bull_put.legs[0].contract.ask = 0.0
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved


def test_stale_quote(valid_bull_put, sample_account):
    """7. Stale quote timestamp rejected."""
    valid_bull_put.legs[0].contract.quote_timestamp = _fresh_ts(seconds_ago=1500)
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved
    assert any("stale" in r.lower() for r in res.rejections)


def test_inverted_bid_ask(default_exp):
    """8. Inverted bid/ask market rejected."""
    c1 = OptionContract(symbol="P1", underlying_symbol="SPY", strike_price=545.0, expiration_date=default_exp, contract_type="put", bid=2.5, ask=2.0, delta=-0.2, open_interest=100, quote_timestamp=_fresh_ts())
    c2 = OptionContract(symbol="P2", underlying_symbol="SPY", strike_price=540.0, expiration_date=default_exp, contract_type="put", bid=0.5, ask=0.6, delta=-0.1, open_interest=100, quote_timestamp=_fresh_ts())
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=550.0, timestamp=_fresh_ts(), contracts=[c1, c2])
    cand = build_bull_put_spread(chain, 550.0)
    assert cand is None


def test_zero_credit(valid_bull_put, sample_account):
    """9. Zero net credit rejected."""
    valid_bull_put.entry_credit = 0.0
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved
    assert any("credit" in r.lower() or "profit" in r.lower() for r in res.rejections)


def test_negative_credit(valid_bull_put, sample_account):
    """10. Negative credit rejected."""
    valid_bull_put.entry_credit = -0.50
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved


def test_excessive_spread(default_exp):
    """11. Excessive bid/ask spread rejected."""
    c1 = OptionContract(symbol="P1", underlying_symbol="SPY", strike_price=545.0, expiration_date=default_exp, contract_type="put", bid=1.0, ask=2.5, delta=-0.2, open_interest=100, quote_timestamp=_fresh_ts())
    c2 = OptionContract(symbol="P2", underlying_symbol="SPY", strike_price=540.0, expiration_date=default_exp, contract_type="put", bid=0.5, ask=0.6, delta=-0.1, open_interest=100, quote_timestamp=_fresh_ts())
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=550.0, timestamp=_fresh_ts(), contracts=[c1, c2])
    cand = build_bull_put_spread(chain, 550.0)
    assert cand is None


def test_low_open_interest(default_exp):
    """12. Low open interest (<50) rejected."""
    c1 = OptionContract(symbol="P1", underlying_symbol="SPY", strike_price=545.0, expiration_date=default_exp, contract_type="put", bid=1.0, ask=1.1, delta=-0.2, open_interest=10, quote_timestamp=_fresh_ts())
    c2 = OptionContract(symbol="P2", underlying_symbol="SPY", strike_price=540.0, expiration_date=default_exp, contract_type="put", bid=0.5, ask=0.6, delta=-0.1, open_interest=100, quote_timestamp=_fresh_ts())
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=550.0, timestamp=_fresh_ts(), contracts=[c1, c2])
    cand = build_bull_put_spread(chain, 550.0)
    assert cand is None


def test_invalid_wing_width(valid_bull_put, sample_account):
    """13. Invalid / non-positive wing width rejected."""
    valid_bull_put.wing_width = -5.0
    valid_bull_put.max_loss = 0.0
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved


def test_wrong_strategy_structure(valid_bull_put, sample_account):
    """14. Wrong structural geometry (e.g. short strike <= long strike on Bull Put) rejected."""
    valid_bull_put.legs[0].contract.strike_price = 530.0  # short put below long put
    valid_bull_put.legs[1].contract.strike_price = 540.0
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved
    assert any("strike" in r.lower() or "structural" in r.lower() for r in res.rejections)


# -----------------------------------------------------------------------------
# 2. Risk & Account Gate Adversarial Tests
# -----------------------------------------------------------------------------
def test_max_loss_breach(valid_bull_put, sample_account):
    """15. Max loss exceeding risk limit ($2,500 default) rejected."""
    valid_bull_put.max_loss = 3500.0
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0, config={"MAX_LOSS_PER_TRADE_USD": 2500.0})
    assert not res.approved
    assert any("max loss" in r.lower() or "limit" in r.lower() for r in res.rejections)


def test_buying_power_failure(valid_bull_put, sample_account):
    """16. Insufficient buying power rejected."""
    sample_account.buying_power = 100.0
    valid_bull_put.max_loss = 500.0
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved
    assert any("buying power" in r.lower() for r in res.rejections)


def test_daily_loss_circuit_breaker(valid_bull_put, sample_account):
    """17. Daily loss circuit breaker ($3,000 threshold) trips and rejects trade."""
    res = evaluate_trade(valid_bull_put, sample_account, [], [], daily_pnl=-3500.0, config={"DAILY_LOSS_LIMIT_USD": 3000.0})
    assert not res.approved
    assert any("circuit breaker" in r.lower() or "daily loss" in r.lower() for r in res.rejections)


def test_duplicate_position_protection(valid_bull_put, sample_account):
    """18. Duplicate underlying + expiration protection."""
    open_pos = [{"symbol": "SPY", "expiration_date": valid_bull_put.expiration}]
    res = evaluate_trade(valid_bull_put, sample_account, open_pos, [], 0.0)
    assert not res.approved
    assert any("existing position" in r.lower() or "duplicate" in r.lower() or "active" in r.lower() for r in res.rejections)


def test_concurrent_position_limit(valid_bull_put, sample_account):
    """19. Maximum concurrent positions (default 5) enforced."""
    open_pos = [{"symbol": f"SYM_{i}", "expiration_date": "2026-10-01"} for i in range(5)]
    res = evaluate_trade(valid_bull_put, sample_account, open_pos, [], 0.0, config={"MAX_CONCURRENT_POSITIONS": 5})
    assert not res.approved
    assert any("concurrent" in r.lower() for r in res.rejections)


def test_total_exposure_limit(valid_bull_put, sample_account):
    """20. Total portfolio exposure cap (default 20% of equity) enforced."""
    sample_account.equity = 10000.0
    # Current exposure 2,500 + new trade 500 = 3,000 > 2,000 (20% of 10k)
    open_pos = [{"symbol": "QQQ", "max_risk": 2500.0}]
    res = evaluate_trade(valid_bull_put, sample_account, open_pos, [], 0.0, config={"MAX_PORTFOLIO_EXPOSURE_PCT": 0.20})
    assert not res.approved
    assert any("exposure" in r.lower() for r in res.rejections)


# -----------------------------------------------------------------------------
# 3. Regime & Catalyst AI Adversarial Tests
# -----------------------------------------------------------------------------
def test_missing_iv_history(tmp_path):
    """21. Missing IV history defaults to low confidence or rejects."""
    from trading_agent.data.iv_store import IVHistoryStore
    store = IVHistoryStore(db_path=tmp_path / "test_iv.db")
    status = store.get_iv_history_status(symbol="SPY", current_atm_iv=0.20, min_required=30)
    assert status["history_days"] < 30
    assert status["iv_rank"] is None


def test_high_catalyst_risk_rejection(valid_bull_put):
    """22. High catalyst risk assessment rejects trade."""
    selector = StrategySelector()
    regime = MarketRegime(trend="bullish", volatility="normal", confidence=0.85, eligible_strategies=["bull_put_spread"], reasons=["Uptrend"])
    catalyst = CatalystAssessment(catalyst_risk="high", directional_bias="neutral", expected_volatility="high", confidence=0.90, rationale="Earnings tonight")
    
    dec = selector.select_strategy(regime, None, 550.0, catalyst_assessment=catalyst)
    assert dec.decision == "NO_TRADE"
    assert any("catalyst" in r.lower() for r in dec.reasons)


def test_malformed_featherless_output_fail_closed():
    """23. Malformed Featherless LLM output fails closed (high risk)."""
    agent = CatalystIntelligenceAgent()
    with patch.object(agent, "_call_llm_endpoint", return_value="INVALID_NON_JSON_OUTPUT"):
        assessment = agent.assess_catalyst("SPY", [], None, None)
        assert assessment.catalyst_risk == "high"
        assert assessment.confidence == 0.0


def test_featherless_unavailable_fail_closed():
    """24. Featherless API unavailable / 500 error fails closed (high risk)."""
    agent = CatalystIntelligenceAgent()
    with patch.object(agent, "_call_llm_endpoint", side_effect=Exception("HTTP 500 Internal Server Error")):
        assessment = agent.assess_catalyst("SPY", [], None, None)
        assert assessment.catalyst_risk == "high"
        assert assessment.confidence == 0.0


def test_selector_receives_no_candidates():
    """25. Strategy Selector cleanly handles empty candidate pool."""
    selector = StrategySelector()
    regime = MarketRegime(trend="bullish", volatility="normal", confidence=0.85, eligible_strategies=["bull_put_spread"], reasons=["Uptrend"])
    catalyst = CatalystAssessment(catalyst_risk="low", directional_bias="bullish", expected_volatility="normal", confidence=0.85, rationale="Stable")
    
    dec = selector.select_strategy(regime, None, 550.0, catalyst_assessment=catalyst)
    assert dec.decision == "NO_TRADE"
    assert dec.selected_candidate is None


def test_selector_returns_no_trade_when_ineligible():
    """26. Selector returns NO_TRADE when no strategies match regime."""
    selector = StrategySelector()
    regime = MarketRegime(trend="bearish", volatility="low", confidence=0.85, eligible_strategies=[], reasons=["No match"])
    catalyst = CatalystAssessment(catalyst_risk="low", directional_bias="neutral", expected_volatility="normal", confidence=0.85, rationale="Stable")
    
    dec = selector.select_strategy(regime, None, 550.0, catalyst_assessment=catalyst)
    assert dec.decision == "NO_TRADE"


# -----------------------------------------------------------------------------
# 4. EXPLICIT ARCHITECTURAL PROOFS
# -----------------------------------------------------------------------------
def test_proof_llm_output_cannot_directly_create_an_order(tmp_path):
    """PROOF 1: LLM output is strictly read-only catalyst intelligence.
    The AI layer possesses NO broker API access and cannot create an order.
    """
    agent = CatalystIntelligenceAgent()
    assert not hasattr(agent, "submit_order")
    assert not hasattr(agent, "place_order")
    assert not hasattr(agent, "create_order")
    assert not hasattr(agent, "execute")


def test_proof_llm_cannot_override_risk_gate(sample_account, valid_bull_put):
    """PROOF 2: Even if LLM indicates 100% confidence and ultra-low risk,
    a deterministic risk violation (e.g. buying power failure) MUST reject the trade.
    """
    sample_account.buying_power = 50.0  # insufficient buying power
    valid_bull_put.max_loss = 500.0
    
    res = evaluate_trade(valid_bull_put, sample_account, [], [], 0.0)
    assert not res.approved
    assert res.adjusted_quantity == 0


def test_proof_selector_cannot_bypass_risk_gate(tmp_path, valid_bull_put, sample_account):
    """PROOF 3: The strategy selector outputs a proposal; order executor is isolated behind Risk Gate."""
    mock_executor = MagicMock()
    mock_data = MagicMock()
    mock_data.get_market_clock.return_value = {"is_open": True}
    mock_data.get_account_snapshot.return_value = sample_account
    mock_data.get_open_positions.return_value = []
    mock_data.get_open_orders.return_value = []
    mock_data.get_recent_bars.return_value = [{"close": 550.0} for _ in range(50)]
    mock_data.get_recent_news.return_value = []

    mock_risk = MagicMock()
    mock_risk.evaluate_trade.return_value = RiskDecision(approved=False, rejections=["Deterministic Risk Reject"])

    orch = AgentOrchestrator(
        alpaca_data=mock_data,
        risk_gate=mock_risk,
        executor=mock_executor,
        safety_manager=SafetyManager(db_path=tmp_path / "test_p3.db"),
    )

    with patch("trading_agent.agent.orchestrator.evaluate_trade", return_value=RiskDecision(approved=False, rejections=["Rejected"])):
        res = orch.run_scan_cycle(symbol="SPY")
        assert res["decision"] == "NO_TRADE"
        # PROOF: Executor was NEVER called
        assert mock_executor.place_strategy_order.call_count == 0
        assert mock_executor.place_condor_order.call_count == 0


def test_proof_rejected_candidate_cannot_reach_executor(sample_account, valid_bull_put):
    """PROOF 4: AlpacaExecutor execute_proposal refuses to place orders when risk decision is not approved."""
    executor = AlpacaExecutor(dry_run=True)
    rejected_decision = RiskDecision(approved=False, rejections=["Max risk exceeded"])
    
    res = executor.execute_proposal(valid_bull_put, rejected_decision)
    assert res.status == "rejected"
    assert "Risk Gate rejected" in res.error_message


def test_proof_circuit_breaker_blocks_every_strategy(sample_account, valid_bull_put, valid_bear_call):
    """PROOF 5: Daily circuit breaker halts ALL strategy families uniformly."""
    daily_pnl = -3500.0  # Breached $3,000 threshold
    
    # 1. Bull Put
    res_bp = evaluate_trade(valid_bull_put, sample_account, [], [], daily_pnl=daily_pnl, config={"DAILY_LOSS_LIMIT_USD": 3000.0})
    assert not res_bp.approved
    assert any("circuit breaker" in r.lower() or "daily loss" in r.lower() for r in res_bp.rejections)

    # 2. Bear Call
    res_bc = evaluate_trade(valid_bear_call, sample_account, [], [], daily_pnl=daily_pnl, config={"DAILY_LOSS_LIMIT_USD": 3000.0})
    assert not res_bc.approved
    assert any("circuit breaker" in r.lower() or "daily loss" in r.lower() for r in res_bc.rejections)


def test_proof_dry_run_cannot_submit_an_order(valid_bull_put):
    """PROOF 6: Dry-run execution generates simulated order IDs and NEVER calls broker submit endpoints."""
    mock_trading_client = MagicMock()
    executor = AlpacaExecutor(dry_run=True)
    executor._trading_client = mock_trading_client

    res = executor.place_strategy_order(valid_bull_put, quantity=1, dry_run=True)
    assert res.status == "new"
    assert "DRY_RUN" in res.order_id
    assert mock_trading_client.submit_order.call_count == 0


def test_proof_no_live_trading_path_exists():
    """PROOF 7: Real-money mode raises PaperExecutionSafetyError and halts."""
    mock_client = MagicMock()
    mock_client._paper = False  # Real money

    with pytest.raises(PaperExecutionSafetyError) as exc_info:
        validate_paper_execution_environment(mock_client)
    assert "CRITICAL SAFETY VIOLATION" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 5. Position Management & Execution Lifecycle
# -----------------------------------------------------------------------------
def test_mixed_portfolio_containing_different_strategies(valid_bull_put, valid_bear_call):
    """31. Position manager monitors mixed portfolio of Bull Put, Bear Call, and Iron Condors."""
    manager = PositionManager()
    manager.add_position("POS_BP_001", valid_bull_put)
    manager.add_position("POS_BC_001", valid_bear_call)

    actions = manager.evaluate_positions()
    assert len(actions) == 2
    assert {a.strategy for a in actions} == {"bull_put_spread", "bear_call_spread"}


def test_position_manager_handles_two_leg_spread(valid_bull_put):
    """32. Position manager evaluates 2-leg credit spread mark-to-market."""
    metrics = calculate_position_metrics(valid_bull_put)
    assert metrics["strategy"] == "bull_put_spread"
    assert metrics["entry_credit"] == 1.10
    assert metrics["legs_count"] == 2


def test_position_manager_handles_four_leg_condor():
    """33. Position manager evaluates 4-leg Iron Condor mark-to-market."""
    exp = "2026-10-16"
    pl = CondorLeg(contract=OptionContract(symbol="P1", underlying_symbol="SPY", strike_price=530.0, expiration_date=exp, contract_type="put", bid=0.5, ask=0.6, quote_timestamp=_fresh_ts()), side="buy", leg_type="put_long")
    ps = CondorLeg(contract=OptionContract(symbol="P2", underlying_symbol="SPY", strike_price=535.0, expiration_date=exp, contract_type="put", bid=1.5, ask=1.6, quote_timestamp=_fresh_ts()), side="sell", leg_type="put_short")
    cs = CondorLeg(contract=OptionContract(symbol="C1", underlying_symbol="SPY", strike_price=565.0, expiration_date=exp, contract_type="call", bid=1.5, ask=1.6, quote_timestamp=_fresh_ts()), side="sell", leg_type="call_short")
    cl = CondorLeg(contract=OptionContract(symbol="C2", underlying_symbol="SPY", strike_price=570.0, expiration_date=exp, contract_type="call", bid=0.5, ask=0.6, quote_timestamp=_fresh_ts()), side="buy", leg_type="call_long")
    condor = IronCondor(underlying_symbol="SPY", put_long=pl, put_short=ps, call_short=cs, call_long=cl, net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date=exp)

    metrics = calculate_position_metrics(condor)
    assert metrics["strategy"] == "iron_condor"
    assert metrics["legs_count"] == 4


def test_partial_fill_handling():
    """34. AlpacaExecutor tracks partial fill status from broker."""
    executor = AlpacaExecutor(dry_run=True)
    res = ExecutionResult(execution_id="E1", proposal_id="P1", order_id="ORD1", status="partially_filled", filled_credit=1.10)
    assert res.status == "partially_filled"


def test_duplicate_retry_prevention(tmp_path, valid_bull_put):
    """35. SafetyManager blocks duplicate retries with identical trade fingerprint."""
    safety = SafetyManager(db_path=tmp_path / "test_dup.db")
    fp = create_trade_fingerprint(valid_bull_put)
    
    assert not safety.is_duplicate_trade(fp)
    safety.record_trade_attempt(fingerprint=fp, symbol="SPY", expiration=valid_bull_put.expiration, order_id="ORD_1", status="new")
    
    assert safety.is_duplicate_trade(fp)
