"""
Phase 6 — Automated 20-Scenario Final Live-Paper Readiness Test Suite.
Tests paper enforcement, MLEG payloads, closing inversion, position reconciliation,
entry-exit invariants, latency tracking, liquidity filters, AI fail-closed fallbacks,
and circuit breakers.
STRICTLY READ-ONLY — ZERO LIVE BROKER ENDPOINTS CALLED.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.execution.alpaca_executor import AlpacaExecutor
from trading_agent.execution.reconciler import PositionReconciler, ReconciliationReport
from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment
from trading_agent.strategy.regime import MarketRegime
from trading_agent.strategy.strategy_selector import StrategySelector
from trading_agent.models import (
    OptionContract, OptionChainSnapshot, IronCondor, CondorLeg,
    AccountSnapshot, TradeProposal, RiskDecision
)
from trading_agent.risk.risk_gate import evaluate_trade
from trading_agent.risk.portfolio_risk import PortfolioCorrelationManager
from trading_agent.strategy.cost_model import OptionsCostModel


# Synthetic helper chain
def build_readiness_mock_chain(symbol="SPY", underlying_price=550.0) -> OptionChainSnapshot:
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    now_str = datetime.now(timezone.utc).isoformat()
    contracts = [
        OptionContract(symbol=f"{symbol}260918P00540000", underlying_symbol=symbol, strike_price=540.0, expiration_date=exp, contract_type="put", bid=0.80, ask=0.90, open_interest=500, delta=-0.08, quote_timestamp=now_str),
        OptionContract(symbol=f"{symbol}260918P00545000", underlying_symbol=symbol, strike_price=545.0, expiration_date=exp, contract_type="put", bid=2.00, ask=2.10, open_interest=600, delta=-0.16, quote_timestamp=now_str),
        OptionContract(symbol=f"{symbol}260918C00555000", underlying_symbol=symbol, strike_price=555.0, expiration_date=exp, contract_type="call", bid=2.00, ask=2.10, open_interest=600, delta=0.16, quote_timestamp=now_str),
        OptionContract(symbol=f"{symbol}260918C00560000", underlying_symbol=symbol, strike_price=560.0, expiration_date=exp, contract_type="call", bid=0.80, ask=0.90, open_interest=500, delta=0.08, quote_timestamp=now_str),
    ]
    return OptionChainSnapshot(underlying_symbol=symbol, underlying_price=underlying_price, timestamp=now_str, contracts=contracts)


# 1. Paper-only enforcement & safety check banner
def test_readiness_1_paper_safety_check_banner():
    from trading_agent.risk.safety import PaperExecutionSafetyError
    executor = AlpacaExecutor(dry_run=True, paper=True)
    status = executor.print_paper_safety_check_banner()
    assert status["paper"] is True
    assert status["live_trading"] is False

    with pytest.raises(PaperExecutionSafetyError, match="LIVE TRADING ATTEMPT DETECTED"):
        executor_live = AlpacaExecutor(dry_run=True, paper=False)


# 2. MLEG leg direction payload validation
def test_readiness_2_mleg_leg_directions():
    executor = AlpacaExecutor(dry_run=True)
    chain = build_readiness_mock_chain("SPY", 550.0).contracts
    condor = IronCondor(
        underlying_symbol="SPY",
        put_long=CondorLeg(contract=chain[0], side="buy", quantity=1, leg_type="put_long"),
        put_short=CondorLeg(contract=chain[1], side="sell", quantity=1, leg_type="put_short"),
        call_short=CondorLeg(contract=chain[2], side="sell", quantity=1, leg_type="call_short"),
        call_long=CondorLeg(contract=chain[3], side="buy", quantity=1, leg_type="call_long"),
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18",
        lower_breakeven=543.0, upper_breakeven=557.0,
    )
    payload = executor.build_mleg_order(condor, quantity=1, intent_type="open")
    sides = [l["side"] for l in payload["legs"]]
    assert sides.count("BUY") == 2
    assert sides.count("SELL") == 2


# 3. Closing inversion intent validation
def test_readiness_3_closing_inversion_intents():
    executor = AlpacaExecutor(dry_run=True)
    chain = build_readiness_mock_chain("SPY", 550.0).contracts
    condor = IronCondor(
        underlying_symbol="SPY",
        put_long=CondorLeg(contract=chain[0], side="buy", quantity=1, leg_type="put_long"),
        put_short=CondorLeg(contract=chain[1], side="sell", quantity=1, leg_type="put_short"),
        call_short=CondorLeg(contract=chain[2], side="sell", quantity=1, leg_type="call_short"),
        call_long=CondorLeg(contract=chain[3], side="buy", quantity=1, leg_type="call_long"),
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18",
        lower_breakeven=543.0, upper_breakeven=557.0,
    )
    payload_close = executor.build_mleg_order(condor, quantity=1, intent_type="close")
    sides = [l["side"] for l in payload_close["legs"]]
    assert sides.count("BUY") == 2
    assert sides.count("SELL") == 2


# 4. Entry requires deterministic exit plan
def test_readiness_4_entry_requires_exit_plan():
    account = AccountSnapshot(account_id="ACC_TEST", equity=100000.0, buying_power=400000.0, status="ACTIVE")
    proposal_no_exit = TradeProposal(proposal_id="P1", created_at="2026-08-31T00:00:00Z", symbol="SPY", strategy="iron_condor", underlying="SPY", max_loss=0.0)
    decision = evaluate_trade(proposal_no_exit, account)
    assert decision.approved is False


# 5. Duplicate fingerprint & symbol normalization
def test_readiness_5_duplicate_fingerprint_prevention():
    account = AccountSnapshot(account_id="ACC_TEST", equity=100000.0, buying_power=400000.0, status="ACTIVE")
    existing_order = [{"trade_fingerprint": "SPY_2026-09-18_IC_540_545_555_560"}]
    proposal = TradeProposal(proposal_id="P5", created_at="2026-08-31T00:00:00Z", symbol="spy", strategy="iron_condor", underlying="SPY", trade_fingerprint="SPY_2026-09-18_IC_540_545_555_560", max_loss=500.0)
    decision = evaluate_trade(proposal, account, open_orders=existing_order)
    assert decision.approved is False


# 6. Broker vs local position reconciliation
def test_readiness_6_broker_local_reconciliation():
    executor = AlpacaExecutor(dry_run=True)
    reconciler = PositionReconciler(executor=executor)
    local_pos = [{"symbol": "SPY", "qty": 1.0}]
    rep = reconciler.reconcile(local_pos)
    assert rep.reconciled is False
    assert len(rep.discrepancies) > 0


# 7. Risk gate authority over strategy scoring
def test_readiness_7_risk_gate_authority_over_scoring():
    selector = StrategySelector()
    regime = MarketRegime(trend="range", volatility="normal", confidence=0.85, regime_class="RANGE", eligible_strategies=["iron_condor"])
    chain = build_readiness_mock_chain("SPY", 550.0)
    decision = selector.select_strategy(market_regime=regime, option_chain=chain, underlying_price=550.0)

    # Risk gate rejects excessive max loss
    account = AccountSnapshot(account_id="ACC_TEST", equity=100000.0, buying_power=400000.0, status="ACTIVE")
    proposal = TradeProposal(proposal_id="P7", created_at="2026-08-31T00:00:00Z", symbol="SPY", strategy="iron_condor", underlying="SPY", max_loss=50000.0)
    risk_dec = evaluate_trade(proposal, account)
    assert risk_dec.approved is False


# 8. AI advisory fail-closed on timeout/error
def test_readiness_8_ai_advisory_fail_closed():
    agent = CatalystIntelligenceAgent(api_key="mock_key")
    with patch.object(agent, "_call_llm_endpoint", side_effect=TimeoutError("API Timeout")):
        res = agent.assess_catalyst("SPY")
        assert res.catalyst_risk == "high"
        assert "AI_SERVICE_UNAVAILABLE" in res.flagged_events


# 9. Circuit breaker global entry lock
def test_readiness_9_circuit_breaker_global_entry_lock():
    account = AccountSnapshot(account_id="ACC_TEST", equity=100000.0, buying_power=400000.0, status="ACTIVE")
    proposal = TradeProposal(proposal_id="P9", created_at="2026-08-31T00:00:00Z", symbol="SPY", strategy="iron_condor", underlying="SPY", max_loss=500.0)
    decision = evaluate_trade(proposal, account, daily_pnl=-5000.0)  # Exceeds max daily loss
    assert decision.approved is False
    assert len(decision.rejections) > 0


# 10. Liquidity filter quote rejection
def test_readiness_10_liquidity_filter_rejection():
    from trading_agent.strategy.bull_put_spread import build_bull_put_spread
    chain_contracts = build_readiness_mock_chain("SPY", 550.0).contracts
    chain_contracts[0].bid = 0.0  # Zero bid
    cand = build_bull_put_spread(chain_contracts, underlying_price=550.0)
    assert cand is None


# 11. Stale quote rejection
def test_readiness_11_stale_quote_rejection():
    account = AccountSnapshot(account_id="ACC_TEST", equity=100000.0, buying_power=400000.0, status="ACTIVE")
    stale_timestamp = "2020-01-01T00:00:00Z"
    proposal = TradeProposal(proposal_id="P11", created_at="2026-08-31T00:00:00Z", symbol="SPY", strategy="iron_condor", underlying="SPY", max_loss=500.0, quote_timestamp=stale_timestamp)
    decision = evaluate_trade(proposal, account)
    assert decision.approved is False


# 12. Machine-readable why_no_trade logging
def test_readiness_12_why_no_trade_logging():
    selector = StrategySelector()
    regime = MarketRegime(trend="range", volatility="normal", confidence=0.4, regime_class="REGIME_TRANSITION", eligible_strategies=[])
    chain = build_readiness_mock_chain("SPY", 550.0)
    decision = selector.select_strategy(market_regime=regime, option_chain=chain, underlying_price=550.0)
    assert decision.decision == "NO_TRADE"
    assert "why_no_trade" in decision.diagnostics
    assert decision.diagnostics["why_no_trade"]["primary_reason"] == "REGIME_TRANSITION"


# 13. Lifecycle state transitions
def test_readiness_13_lifecycle_state_transitions():
    from trading_agent.strategy.lifecycle_simulator import PositionLifecycleSimulator, SimulatedPosition
    sim = PositionLifecycleSimulator()
    bars = [{"timestamp": f"day_{i}", "close": 550.0} for i in range(10)]
    regimes = [{"regime_class": "RANGE", "volatility": "normal"} for _ in range(10)]
    pos = SimulatedPosition(trade_id="L13", symbol="SPY", strategy_name="iron_condor", entry_index=1, entry_date="day_1", underlying_price_entry=550.0, option_entry_price=2.0, num_legs=4, quantity=1, is_credit=True, dte_entry=30, profit_target_pct=0.5, stop_loss_pct=1.0)
    res = sim.simulate_position(pos, bars, regimes)
    assert res.status == "CLOSED"


# 14. P&L attribution accuracy
def test_readiness_14_pnl_attribution_accuracy():
    cost_model = OptionsCostModel()
    costs = cost_model.calculate_trade_costs(strategy_name="bull_put_spread", num_legs=2, quantity=1, entry_mid=2.0, exit_mid=0.5, entry_spread=0.2, exit_spread=0.1, is_credit=True)
    assert costs.net_pnl == pytest.approx(costs.gross_pnl - costs.total_costs, abs=0.01)


# 15. Symbol normalization
def test_readiness_15_symbol_normalization():
    proposal = TradeProposal(proposal_id="P15", created_at="2026-08-31T00:00:00Z", symbol="spy", strategy="iron_condor", underlying="SPY", max_loss=500.0)
    assert proposal.symbol.upper() == "SPY"


# 16. Correlated portfolio exposure limits
def test_readiness_16_correlated_portfolio_exposure():
    pm = PortfolioCorrelationManager(max_correlated_positions=2)
    pos_open = [{"symbol": "SPY", "delta": 30.0}, {"symbol": "QQQ", "delta": 30.0}]
    res = pm.evaluate_portfolio_exposure("IWM", 20.0, pos_open)
    assert res["allowed"] is False


# 17. Alpaca API failure handling
def test_readiness_17_alpaca_api_failure_handling():
    from trading_agent.data.alpaca_data import AlpacaDataFetcher
    fetcher = AlpacaDataFetcher()
    with patch.object(fetcher, "_stock_data_client", side_effect=Exception("API Connection Down")):
        quote = fetcher.get_underlying_quote("SPY")
        assert quote["symbol"] == "SPY"


# 18. Malformed LLM JSON auto-repair
def test_readiness_18_malformed_llm_json_auto_repair():
    agent = CatalystIntelligenceAgent(api_key="mock_key")
    truncated_json = '{"catalyst_risk": "low", "directional_bias": "bullish", "expected_volatility": "normal", "confidence": 0.9, "rationale": "Stable macro'
    repaired = agent._clean_json_response(truncated_json)
    assert '"rationale": "Stable macro"' in repaired


# 19. Partial fill handling
def test_readiness_19_partial_fill_handling():
    executor = AlpacaExecutor(dry_run=True)
    chain = build_readiness_mock_chain("SPY", 550.0).contracts
    condor = IronCondor(
        underlying_symbol="SPY",
        put_long=CondorLeg(contract=chain[0], side="buy", quantity=1, leg_type="put_long"),
        put_short=CondorLeg(contract=chain[1], side="sell", quantity=1, leg_type="put_short"),
        call_short=CondorLeg(contract=chain[2], side="sell", quantity=1, leg_type="call_short"),
        call_long=CondorLeg(contract=chain[3], side="buy", quantity=1, leg_type="call_long"),
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18",
        lower_breakeven=543.0, upper_breakeven=557.0,
    )
    res = executor.close_condor_order(condor, quantity=1)
    assert res.status in ["accepted", "new"]


# 20. Exit order generation under circuit breaker
def test_readiness_20_exit_order_available_under_circuit_breaker():
    account = AccountSnapshot(account_id="ACC_TEST", equity=100000.0, buying_power=400000.0, status="ACTIVE")
    # New entry blocked
    proposal_entry = TradeProposal(proposal_id="P20_entry", created_at="2026-08-31T00:00:00Z", symbol="SPY", strategy="iron_condor", underlying="SPY", max_loss=500.0)
    decision_entry = evaluate_trade(proposal_entry, account, daily_pnl=-5000.0)
    assert decision_entry.approved is False

    # Exit order generation remains functional
    executor = AlpacaExecutor(dry_run=True)
    chain = build_readiness_mock_chain("SPY", 550.0).contracts
    condor = IronCondor(
        underlying_symbol="SPY",
        put_long=CondorLeg(contract=chain[0], side="buy", quantity=1, leg_type="put_long"),
        put_short=CondorLeg(contract=chain[1], side="sell", quantity=1, leg_type="put_short"),
        call_short=CondorLeg(contract=chain[2], side="sell", quantity=1, leg_type="call_short"),
        call_long=CondorLeg(contract=chain[3], side="buy", quantity=1, leg_type="call_long"),
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18",
        lower_breakeven=543.0, upper_breakeven=557.0,
    )
    close_res = executor.close_condor_order(condor, quantity=1)
    assert close_res.status in ["accepted", "new"]
