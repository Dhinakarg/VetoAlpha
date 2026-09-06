"""
Adversarial & Unit Test Suite for Multi-Strategy Decision Audit Harness.
Validates 12 decision audit scenarios:
1. Valid multi-strategy decision
2. All strategies rejected -> NO_TRADE
3. AI recommends ineligible strategy -> Deterministic override
4. AI/deterministic disagreement detection
5. Risk gate rejects selected candidate -> NO_TRADE
6. Missing market data -> fail-closed NO_TRADE
7. Missing greeks / delta -> candidate construction rejection
8. Stale quotes -> leg validation failure
9. High catalyst risk -> credit strategy block
10. Daily circuit breaker -> risk gate rejection
11. Deterministic repeatability check
12. Zero-order invariant (READ_ONLY execution mode)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.models import (
    AccountSnapshot,
    OptionChainSnapshot,
    OptionContract,
    IronCondor,
    CondorLeg,
    StrategyCandidate,
    StrategyLeg,
)
from trading_agent.strategy.regime import MarketRegime
from trading_agent.strategy.strategy_selector import StrategySelector, StrategyDecision
from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment
from trading_agent.strategy.ai_strategy_advisor import AIStrategyAdvisor, StrategyAdvice
from trading_agent.risk.risk_gate import evaluate_trade
from scripts.audit_decisions import (
    run_decision_audit_for_symbol,
    detect_disagreement,
    calculate_realized_volatility,
    verify_repeatability,
    redact_secrets,
)


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_stale_timestamp() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()


def _make_mock_bar(close: float, high: float = 0.0, low: float = 0.0, open_p: float = 0.0, volume: int = 1000) -> dict:
    return {
        "close": close,
        "high": high or close * 1.01,
        "low": low or close * 0.99,
        "open": open_p or close,
        "volume": volume,
        "timestamp": _make_fresh_timestamp(),
    }


def _make_mock_bars(count: int = 30, base_price: float = 500.0) -> list:
    bars = []
    for i in range(count):
        price = base_price + (i % 5) - 2.0
        bars.append(_make_mock_bar(price))
    return bars


def _make_valid_contract(
    symbol: str, strike: float, ctype: str, delta: float, bid: float, ask: float, oi: int = 500, ts: str = None
) -> OptionContract:
    exp_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    return OptionContract(
        symbol=symbol,
        underlying_symbol="SPY",
        strike_price=strike,
        expiration_date=exp_date,
        contract_type=ctype,
        bid=bid,
        ask=ask,
        delta=delta,
        open_interest=oi,
        implied_volatility=0.20,
        quote_timestamp=ts or _make_fresh_timestamp(),
    )


def _make_valid_candidate(
    strategy_name: str = "bull_put_spread",
    symbol: str = "SPY",
    max_loss: float = 500.0,
    max_profit: float = 200.0,
    entry_credit: float = 2.0,
) -> StrategyCandidate:
    exp_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    short_contract = _make_valid_contract(f"{symbol}_SHORT", 490.0, "put", -0.18, 2.50, 2.60)
    long_contract = _make_valid_contract(f"{symbol}_LONG", 485.0, "put", -0.10, 1.20, 1.30)
    legs = [
        StrategyLeg(contract=short_contract, side="sell", quantity=1, leg_type="put_short", intent="sell_to_open"),
        StrategyLeg(contract=long_contract, side="buy", quantity=1, leg_type="put_long", intent="buy_to_open"),
    ]
    return StrategyCandidate(
        strategy_name=strategy_name,
        underlying=symbol,
        underlying_symbol=symbol,
        expiration=exp_date,
        expiration_date=exp_date,
        legs=legs,
        direction="bullish",
        entry_credit=entry_credit,
        net_credit=entry_credit,
        max_profit=max_profit,
        max_loss=max_loss,
        max_risk=max_loss,
        dte=45,
        candidate_score=75.0,
    )


# -----------------------------------------------------------------------------
# 1. Valid Multi-Strategy Decision
# -----------------------------------------------------------------------------
def test_valid_multi_strategy_decision(tmp_path):
    jsonl_file = tmp_path / "test_trace.jsonl"
    mock_data = MagicMock()
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="MOCK_ACC", equity=100000.0, cash=100000.0, buying_power=100000.0, status="ACTIVE"
    )
    mock_data.get_underlying_quote.return_value = {"last": 500.0, "bid": 499.9, "ask": 500.1}
    mock_data.get_recent_bars.return_value = _make_mock_bars(50, 500.0)
    mock_data.get_recent_news.return_value = []

    cand = _make_valid_candidate("bull_put_spread", "SPY", max_loss=500.0, max_profit=200.0)
    mock_selector = MagicMock()
    mock_selector.select_strategy.return_value = StrategyDecision(
        selected_strategy="bull_put_spread",
        selected_candidate=cand,
        candidates={"bull_put_spread": cand},
        scores={"bull_put_spread": 75.0},
        reasons=["Valid candidate score"],
        decision="TRADE",
    )

    mock_catalyst = MagicMock()
    mock_catalyst.assess_catalyst.return_value = CatalystAssessment(
        catalyst_risk="low", directional_bias="bullish", expected_volatility="normal", flagged_events=[], rationale="Low risk", confidence=0.8
    )

    mock_advisor = MagicMock()
    mock_advisor.advise_strategy.return_value = StrategyAdvice(
        preferred_strategy="BULL_PUT_SPREAD", confidence=0.8, rationale="Bullish bias", risk_flags=[]
    )

    trace = run_decision_audit_for_symbol(
        symbol="SPY",
        alpaca_data=mock_data,
        selector=mock_selector,
        catalyst_agent=mock_catalyst,
        ai_advisor=mock_advisor,
        config={},
        jsonl_path=jsonl_file,
    )

    assert trace["symbol"] == "SPY"
    assert trace["final_decision"]["decision"] == "TRADE"
    assert trace["final_decision"]["selected_strategy"] == "bull_put_spread"
    assert jsonl_file.exists()


# -----------------------------------------------------------------------------
# 2. All Strategies Rejected -> Returns NO_TRADE
# -----------------------------------------------------------------------------
def test_all_strategies_rejected_returns_no_trade():
    mock_data = MagicMock()
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="MOCK_ACC", equity=100000.0, cash=100000.0, buying_power=100000.0, status="ACTIVE"
    )
    mock_data.get_underlying_quote.return_value = {"last": 500.0}
    mock_data.get_recent_bars.return_value = _make_mock_bars(50, 500.0)

    mock_selector = MagicMock()
    mock_selector.select_strategy.return_value = StrategyDecision(
        selected_strategy=None,
        selected_candidate=None,
        candidates={},
        scores={},
        reasons=["No viable candidates met delta/strike criteria."],
        decision="NO_TRADE",
    )

    mock_catalyst = MagicMock()
    mock_catalyst.assess_catalyst.return_value = CatalystAssessment(
        catalyst_risk="medium", directional_bias="neutral", expected_volatility="normal", flagged_events=[], rationale="Neutral", confidence=0.5
    )
    mock_advisor = MagicMock()
    mock_advisor.advise_strategy.return_value = StrategyAdvice(
        preferred_strategy="IRON_CONDOR", confidence=0.5, rationale="Neutral", risk_flags=[]
    )

    trace = run_decision_audit_for_symbol(
        symbol="SPY",
        alpaca_data=mock_data,
        selector=mock_selector,
        catalyst_agent=mock_catalyst,
        ai_advisor=mock_advisor,
        config={},
    )

    assert trace["final_decision"]["decision"] == "NO_TRADE"
    assert trace["final_decision"]["selected_strategy"] is None
    assert "No viable candidates" in " ".join(trace["final_decision"]["reasons"])


# -----------------------------------------------------------------------------
# 3. AI Recommends Ineligible Strategy -> Deterministic Safety Layer Overrides
# -----------------------------------------------------------------------------
def test_ai_recommends_ineligible_strategy():
    disagreement = detect_disagreement(
        ai_recommendation="LONG_CALL",
        deterministic_selection="bull_put_spread",
        eligible_strategies=["bull_put_spread", "iron_condor"],
        risk_approved=True,
    )

    assert disagreement["disagreement_detected"] is True
    assert disagreement["type"] == "AI_INELIGIBLE_STRATEGY"
    assert disagreement["rule_winner"] == "DETERMINISTIC_SAFETY_LAYER"


# -----------------------------------------------------------------------------
# 4. AI vs Deterministic Disagreement Detection
# -----------------------------------------------------------------------------
def test_ai_deterministic_disagreement():
    disagreement = detect_disagreement(
        ai_recommendation="LONG_PUT",
        deterministic_selection="bear_call_spread",
        eligible_strategies=["bear_call_spread", "long_put"],
        risk_approved=True,
    )

    assert disagreement["disagreement_detected"] is True
    assert disagreement["type"] == "STRATEGY_MISMATCH"
    assert "bear_call_spread" in disagreement["deterministic_selection"].lower()


# -----------------------------------------------------------------------------
# 5. Risk Gate Rejects Selected Candidate
# -----------------------------------------------------------------------------
def test_risk_gate_rejects_selected_candidate():
    mock_data = MagicMock()
    # Account buying power lower than candidate max loss
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="MOCK_ACC", equity=1000.0, cash=1000.0, buying_power=500.0, status="ACTIVE"
    )
    mock_data.get_underlying_quote.return_value = {"last": 500.0}
    mock_data.get_recent_bars.return_value = _make_mock_bars(50, 500.0)

    # Candidate max_loss (2000) > buying_power (500)
    cand = _make_valid_candidate("bull_put_spread", "SPY", max_loss=2000.0, max_profit=500.0)

    mock_selector = MagicMock()
    mock_selector.select_strategy.return_value = StrategyDecision(
        selected_strategy="bull_put_spread",
        selected_candidate=cand,
        candidates={"bull_put_spread": cand},
        scores={"bull_put_spread": 80.0},
        reasons=["Candidate selected"],
        decision="TRADE",
    )

    mock_catalyst = MagicMock()
    mock_catalyst.assess_catalyst.return_value = CatalystAssessment(
        catalyst_risk="low", directional_bias="bullish", expected_volatility="normal", flagged_events=[], rationale="Low risk", confidence=0.8
    )
    mock_advisor = MagicMock()
    mock_advisor.advise_strategy.return_value = StrategyAdvice(
        preferred_strategy="BULL_PUT_SPREAD", confidence=0.8, rationale="Bullish", risk_flags=[]
    )

    trace = run_decision_audit_for_symbol(
        symbol="SPY",
        alpaca_data=mock_data,
        selector=mock_selector,
        catalyst_agent=mock_catalyst,
        ai_advisor=mock_advisor,
        config={},
    )

    assert trace["final_decision"]["decision"] == "NO_TRADE"
    assert trace["risk_gate"]["approved"] is False


# -----------------------------------------------------------------------------
# 6. Missing Market Data -> Fail Closed
# -----------------------------------------------------------------------------
def test_missing_market_data():
    mock_data = MagicMock()
    mock_data.get_account_snapshot.side_effect = RuntimeError("API unavailable")
    mock_data.get_underlying_quote.side_effect = RuntimeError("API unavailable")
    mock_data.get_recent_bars.return_value = []

    mock_selector = StrategySelector()
    mock_catalyst = MagicMock()
    mock_catalyst.assess_catalyst.return_value = CatalystAssessment(
        catalyst_risk="low", directional_bias="neutral", expected_volatility="normal", flagged_events=[], rationale="", confidence=0.5
    )
    mock_advisor = MagicMock()
    mock_advisor.advise_strategy.return_value = StrategyAdvice(
        preferred_strategy="NO_TRADE", confidence=0.0, rationale="", risk_flags=[]
    )

    trace = run_decision_audit_for_symbol(
        symbol="SPY",
        alpaca_data=mock_data,
        selector=mock_selector,
        catalyst_agent=mock_catalyst,
        ai_advisor=mock_advisor,
        config={},
    )

    assert trace["final_decision"]["decision"] == "NO_TRADE"


# -----------------------------------------------------------------------------
# 7. Missing Greeks / Delta Rejection
# -----------------------------------------------------------------------------
def test_missing_greeks():
    contract_no_delta = _make_valid_contract("SPY_NODELTA", 500.0, "put", delta=None, bid=2.0, ask=2.1)
    chain = OptionChainSnapshot(
        underlying_symbol="SPY", underlying_price=500.0, timestamp=_make_fresh_timestamp(), contracts=[contract_no_delta]
    )

    selector = StrategySelector()
    regime = MarketRegime(
        trend="range", volatility="normal", confidence=0.8, regime_class="RANGE", eligible_strategies=["iron_condor"]
    )

    decision = selector.select_strategy(market_regime=regime, option_chain=chain, underlying_price=500.0)

    assert decision.decision == "NO_TRADE"


# -----------------------------------------------------------------------------
# 8. Stale Quotes Rejection
# -----------------------------------------------------------------------------
def test_stale_quotes():
    stale_contract = _make_valid_contract("SPY_STALE", 500.0, "put", delta=-0.18, bid=2.0, ask=2.1, ts=_make_stale_timestamp())
    chain = OptionChainSnapshot(
        underlying_symbol="SPY", underlying_price=500.0, timestamp=_make_fresh_timestamp(), contracts=[stale_contract]
    )

    selector = StrategySelector()
    regime = MarketRegime(
        trend="range", volatility="normal", confidence=0.8, regime_class="RANGE", eligible_strategies=["iron_condor"]
    )

    decision = selector.select_strategy(market_regime=regime, option_chain=chain, underlying_price=500.0)

    assert decision.decision == "NO_TRADE"


# -----------------------------------------------------------------------------
# 9. High Catalyst Risk Blocks Credit Selling
# -----------------------------------------------------------------------------
def test_high_catalyst_risk():
    selector = StrategySelector()
    regime = MarketRegime(
        trend="range", volatility="normal", confidence=0.8, regime_class="RANGE", eligible_strategies=["iron_condor", "bull_put_spread"]
    )
    catalyst_high = CatalystAssessment(
        catalyst_risk="high", directional_bias="neutral", expected_volatility="high", flagged_events=["Earnings in 1 day"], rationale="High event risk", confidence=0.9
    )

    decision = selector.select_strategy(
        market_regime=regime, option_chain=MagicMock(), underlying_price=500.0, catalyst_assessment=catalyst_high
    )

    assert decision.decision == "NO_TRADE"
    assert any("HIGH" in r.upper() for r in decision.reasons)


# -----------------------------------------------------------------------------
# 10. Daily Circuit Breaker Rejection
# -----------------------------------------------------------------------------
def test_circuit_breaker():
    account = AccountSnapshot(account_id="ACC", equity=100000.0, cash=100000.0, buying_power=100000.0, status="ACTIVE")
    cand = _make_valid_candidate("bull_put_spread", "SPY", max_loss=500.0, max_profit=200.0)

    # Daily loss breaches daily circuit breaker (-3500 < -2000)
    risk_dec = evaluate_trade(
        proposed_trade=cand,
        account=account,
        open_positions=[],
        open_orders=[],
        daily_pnl=-3500.0,
        config={"daily_loss_limit": 2000.0},
    )

    assert risk_dec.approved is False
    assert any("circuit breaker" in r.lower() or "daily loss" in r.lower() for r in risk_dec.rejections)


# -----------------------------------------------------------------------------
# 11. Deterministic Repeatability Check
# -----------------------------------------------------------------------------
def test_deterministic_repeatability():
    bars = _make_mock_bars(40, 500.0)
    v1 = calculate_realized_volatility(bars, window=20)
    v2 = calculate_realized_volatility(bars, window=20)
    assert v1 == v2

    secrets = {"apca_api_key_id": "SECRET123", "symbol": "SPY"}
    redacted1 = redact_secrets(secrets)
    redacted2 = redact_secrets(secrets)
    assert redacted1 == redacted2 == {"apca_api_key_id": "[REDACTED]", "symbol": "SPY"}


# -----------------------------------------------------------------------------
# 12. Zero-Order Execution Invariant
# -----------------------------------------------------------------------------
def test_zero_order_invariant():
    mock_data = MagicMock()
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="MOCK_ACC", equity=100000.0, cash=100000.0, buying_power=100000.0, status="ACTIVE"
    )
    mock_data.get_underlying_quote.return_value = {"last": 500.0}
    mock_data.get_recent_bars.return_value = _make_mock_bars(50, 500.0)

    cand = _make_valid_candidate("iron_condor", "SPY", max_loss=500.0, max_profit=200.0)
    mock_selector = MagicMock()
    mock_selector.select_strategy.return_value = StrategyDecision(
        selected_strategy="iron_condor",
        selected_candidate=cand,
        candidates={"iron_condor": cand},
        scores={"iron_condor": 85.0},
        reasons=["Top score"],
        decision="TRADE",
    )

    mock_catalyst = MagicMock()
    mock_catalyst.assess_catalyst.return_value = CatalystAssessment(
        catalyst_risk="low", directional_bias="neutral", expected_volatility="normal", flagged_events=[], rationale="", confidence=0.8
    )
    mock_advisor = MagicMock()
    mock_advisor.advise_strategy.return_value = StrategyAdvice(
        preferred_strategy="IRON_CONDOR", confidence=0.8, rationale="", risk_flags=[]
    )

    trace = run_decision_audit_for_symbol(
        symbol="SPY",
        alpaca_data=mock_data,
        selector=mock_selector,
        catalyst_agent=mock_catalyst,
        ai_advisor=mock_advisor,
        config={},
    )

    assert trace["execution"]["mode"] == "READ_ONLY"
    assert trace["execution"]["orders_submitted"] == 0
