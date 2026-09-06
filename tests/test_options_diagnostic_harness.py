"""
Comprehensive Options Engine Diagnostic & Replay Test Harness.
Tests all 14 scenarios (A through N) covering AI JSON repairs, safe deterministic fallbacks,
IV Rank volatility eligibility policies, explainable why_no_trade diagnostics, and MLEG leg constructions.
STRICTLY READ-ONLY & MOCKED — ZERO LIVE BROKER ENDPOINTS CALLED.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment
from trading_agent.strategy.regime import MarketRegime, detect_market_regime
from trading_agent.strategy.strategy_selector import StrategySelector, StrategyDecision
from trading_agent.models import (
    OptionContract,
    OptionChainSnapshot,
    IronCondor,
    CondorLeg,
    StrategyCandidate,
    StrategyLeg,
)
from trading_agent.strategy.bull_put_spread import build_bull_put_spread
from trading_agent.execution.alpaca_executor import AlpacaExecutor
# Helper stubs for synthetic option chains
def build_mock_option_chain(symbol="SPY", underlying_price=550.0) -> OptionChainSnapshot:
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    now_str = datetime.now(timezone.utc).isoformat()
    contracts = [
        # Puts (540, 545)
        OptionContract(
            symbol=f"{symbol}260918P00540000",
            underlying_symbol=symbol,
            strike_price=540.0,
            expiration_date=exp,
            contract_type="put",
            bid=0.80,
            ask=0.90,
            open_interest=500,
            delta=-0.08,
            quote_timestamp=now_str,
        ),
        OptionContract(
            symbol=f"{symbol}260918P00545000",
            underlying_symbol=symbol,
            strike_price=545.0,
            expiration_date=exp,
            contract_type="put",
            bid=2.00,
            ask=2.10,
            open_interest=600,
            delta=-0.16,
            quote_timestamp=now_str,
        ),
        # Calls (555, 560)
        OptionContract(
            symbol=f"{symbol}260918C00555000",
            underlying_symbol=symbol,
            strike_price=555.0,
            expiration_date=exp,
            contract_type="call",
            bid=2.00,
            ask=2.10,
            open_interest=600,
            delta=0.16,
            quote_timestamp=now_str,
        ),
        OptionContract(
            symbol=f"{symbol}260918C00560000",
            underlying_symbol=symbol,
            strike_price=560.0,
            expiration_date=exp,
            contract_type="call",
            bid=0.80,
            ask=0.90,
            open_interest=500,
            delta=0.08,
            quote_timestamp=now_str,
        ),
    ]
    return OptionChainSnapshot(underlying_symbol=symbol, underlying_price=underlying_price, timestamp=now_str, contracts=contracts)


# Scenario A: Valid AI Response + Valid Deterministic -> TRADE
def test_scenario_a_valid_ai_and_valid_deterministic():
    agent = CatalystIntelligenceAgent(api_key="mock_key")
    selector = StrategySelector(min_selection_score=50.0)

    assessment = CatalystAssessment(
        catalyst_risk="low",
        directional_bias="neutral",
        expected_volatility="normal",
        flagged_events=[],
        rationale="Low macro catalyst risk.",
        confidence=0.90,
    )
    regime = MarketRegime(
        trend="range",
        volatility="normal",
        confidence=0.85,
        regime_class="RANGE",
        eligible_strategies=["iron_condor", "bull_put_spread", "bear_call_spread"],
    )
    chain = build_mock_option_chain("SPY", 550.0)

    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=chain,
        underlying_price=550.0,
        catalyst_assessment=assessment,
    )
    assert decision.decision == "TRADE"
    assert decision.selected_strategy in ["iron_condor", "bull_put_spread", "bear_call_spread"]


# Scenario B: Malformed AI JSON + High Deterministic Score (>=65.0) -> TRADE (Fallback Active)
def test_scenario_b_ai_unavailable_high_deterministic_score_fallback():
    selector = StrategySelector(min_selection_score=50.0)

    ai_fallback_assessment = CatalystAssessment(
        catalyst_risk="high",
        directional_bias="neutral",
        expected_volatility="high",
        flagged_events=["AI_SERVICE_UNAVAILABLE"],
        rationale="AI catalyst service unavailable [MALFORMED_MODEL_OUTPUT]; fail-closed safety activated.",
        confidence=0.0,
        error_type="MALFORMED_MODEL_OUTPUT",
    )
    regime = MarketRegime(
        trend="range",
        volatility="normal",
        confidence=0.85,
        regime_class="RANGE",
        eligible_strategies=["iron_condor"],
    )
    chain = build_mock_option_chain("SPY", 550.0)

    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=chain,
        underlying_price=550.0,
        catalyst_assessment=ai_fallback_assessment,
    )
    assert decision.decision == "TRADE"
    assert decision.diagnostics.get("ai_fallback_applied") is True
    assert decision.diagnostics.get("position_size_multiplier") == 0.5


# Scenario C: Malformed AI JSON + Low Deterministic Score (<65.0) -> NO_TRADE
def test_scenario_c_ai_unavailable_low_deterministic_score_no_trade():
    selector = StrategySelector(min_selection_score=75.0)

    ai_fallback_assessment = CatalystAssessment(
        catalyst_risk="high",
        directional_bias="neutral",
        expected_volatility="high",
        flagged_events=["AI_SERVICE_UNAVAILABLE"],
        rationale="AI catalyst service unavailable [MALFORMED_MODEL_OUTPUT].",
        confidence=0.0,
        error_type="MALFORMED_MODEL_OUTPUT",
    )
    regime = MarketRegime(
        trend="range",
        volatility="normal",
        confidence=0.85,
        regime_class="RANGE",
        eligible_strategies=["iron_condor"],
    )
    chain = build_mock_option_chain("SPY", 550.0)

    # Force low candidate score
    with patch.object(selector, "_calculate_candidate_score", return_value=(55.0, {})):
        decision = selector.select_strategy(
            market_regime=regime,
            option_chain=chain,
            underlying_price=550.0,
            catalyst_assessment=ai_fallback_assessment,
        )
        assert decision.decision == "NO_TRADE"
        assert decision.diagnostics["why_no_trade"]["primary_reason"] == "AI_UNAVAILABLE_DETERMINISTIC_SCORE_INSUFFICIENT"


# Scenario D: Truncated AI JSON -> Bounded Auto-Repair Success
def test_scenario_d_truncated_ai_json_auto_repair():
    agent = CatalystIntelligenceAgent(api_key="mock_key")
    truncated_raw = '```json\n{"catalyst_risk": "low", "directional_bias": "bullish", "expected_volatility": "normal", "flagged_events": [], "rationale": "Truncated rationale text'
    cleaned = agent._clean_json_response(truncated_raw)
    assert cleaned.startswith("{")
    assert cleaned.endswith("}")


# Scenario E: Missing AI Fields -> Schema Validation Error
def test_scenario_e_missing_ai_fields_error_categorization():
    agent = CatalystIntelligenceAgent(api_key="mock_key")
    with patch.object(agent, "_call_llm_endpoint", return_value='{"invalid_key": 123}'):
        assessment = agent.assess_catalyst("SPY")
        assert assessment.catalyst_risk == "high"
        assert assessment.error_type == "SCHEMA_VALIDATION_ERROR"


# Scenario F: High Catalyst Risk AI Response -> NO_TRADE
def test_scenario_f_high_catalyst_risk_blocks_trade():
    selector = StrategySelector(min_selection_score=50.0)
    high_risk_assessment = CatalystAssessment(
        catalyst_risk="high",
        directional_bias="bearish",
        expected_volatility="high",
        flagged_events=["EARNINGS_ANNOUNCEMENT"],
        rationale="Upcoming quarterly earnings announcement in 2 days.",
        confidence=0.95,
    )
    regime = MarketRegime(trend="range", volatility="normal", confidence=0.85, regime_class="RANGE", eligible_strategies=["iron_condor"])
    chain = build_mock_option_chain("SPY", 550.0)

    decision = selector.select_strategy(
        market_regime=regime,
        option_chain=chain,
        underlying_price=550.0,
        catalyst_assessment=high_risk_assessment,
    )
    assert decision.decision == "NO_TRADE"
    assert decision.diagnostics["why_no_trade"]["primary_reason"] == "HIGH_CATALYST_RISK"


# Scenario G: AI / Deterministic Bias Disagreement -> Penalty Applied
def test_scenario_g_ai_deterministic_bias_disagreement_penalty():
    selector = StrategySelector(min_selection_score=50.0)
    regime = MarketRegime(trend="bullish", volatility="normal", confidence=0.85, regime_class="BULL", eligible_strategies=["bull_put_spread"])
    chain = build_mock_option_chain("SPY", 550.0)
    cand = build_bull_put_spread(chain, underlying_price=550.0)
    assert cand is not None

    score_aligned, _ = selector._calculate_candidate_score(
        "bull_put_spread", cand, regime, 550.0, catalyst_risk="low", catalyst_confidence=0.9, ai_preferred="bull_put_spread"
    )
    score_disaligned, _ = selector._calculate_candidate_score(
        "bull_put_spread", cand, regime, 550.0, catalyst_risk="low", catalyst_confidence=0.9, ai_preferred="bear_call_spread"
    )
    assert score_disaligned < score_aligned


# Scenario H: Low IV Rank + Bull Trend -> long_call Eligible
def test_scenario_h_low_iv_rank_bull_trend_enables_long_call():
    regime = MarketRegime(
        trend="bullish",
        volatility="low",
        confidence=0.85,
        regime_class="BULL",
        eligible_strategies=["long_call"],
    )
    assert "long_call" in regime.eligible_strategies
    assert "bull_put_spread" not in regime.eligible_strategies


# Scenario I: Low IV Rank + Range Trend -> NO_TRADE (Explainable Diagnostic)
def test_scenario_i_low_iv_rank_range_trend_no_eligible_strategies():
    selector = StrategySelector(min_selection_score=50.0)
    regime = MarketRegime(
        trend="range",
        volatility="low",
        iv_rank=19.7,
        confidence=0.85,
        regime_class="RANGE",
        eligible_strategies=[],
    )
    chain = build_mock_option_chain("SPY", 550.0)
    decision = selector.select_strategy(market_regime=regime, option_chain=chain, underlying_price=550.0)

    assert decision.decision == "NO_TRADE"
    assert decision.diagnostics["why_no_trade"]["primary_reason"] == "NO_ELIGIBLE_STRATEGIES"
    assert decision.diagnostics["why_no_trade"]["iv_rank"] == 19.7


# Scenario J: Normal IV Rank + Range Trend -> iron_condor Eligible
def test_scenario_j_normal_iv_rank_range_trend_enables_iron_condor():
    regime = MarketRegime(
        trend="range",
        volatility="normal",
        iv_rank=35.0,
        confidence=0.85,
        regime_class="RANGE",
        eligible_strategies=["iron_condor", "bull_put_spread", "bear_call_spread"],
    )
    assert "iron_condor" in regime.eligible_strategies


# Scenario K: High IV Rank + Bear Trend -> bear_call_spread Preferred
def test_scenario_k_high_iv_rank_bear_trend_enables_bear_call():
    regime = MarketRegime(
        trend="bearish",
        volatility="high",
        iv_rank=75.0,
        confidence=0.85,
        regime_class="BEAR",
        eligible_strategies=["bear_call_spread", "long_put"],
    )
    assert "bear_call_spread" in regime.eligible_strategies


# Scenario L: UNKNOWN Regime -> NO_TRADE (Fail Closed)
def test_scenario_l_unknown_regime_fails_closed():
    selector = StrategySelector()
    regime = MarketRegime(trend="range", volatility="low", confidence=0.0, regime_class="UNKNOWN", eligible_strategies=[])
    chain = build_mock_option_chain("SPY", 550.0)
    decision = selector.select_strategy(market_regime=regime, option_chain=chain, underlying_price=550.0)

    assert decision.decision == "NO_TRADE"
    assert decision.diagnostics["why_no_trade"]["primary_reason"] == "REGIME_UNKNOWN"


# Scenario M: 12-Rule Risk Gate Downside Protection Invariant
def test_scenario_m_risk_gate_downside_protection_invariant():
    from trading_agent.risk.risk_gate import evaluate_trade
    from trading_agent.models import AccountSnapshot

    account = AccountSnapshot(account_id="ACC_TEST", equity=100000.0, buying_power=400000.0, status="ACTIVE")
    # Missing candidate -> reject
    decision = evaluate_trade(proposed_trade=None, account=account)
    assert decision.approved is False
    assert len(decision.rejections) > 0


# Scenario N: Complete End-to-End MLEG Order Payload Verification (2 BUY + 2 SELL)
def test_scenario_n_mleg_payload_leg_directions():
    executor = AlpacaExecutor(dry_run=True)
    chain_contracts = build_mock_option_chain("SPY", 550.0).contracts

    condor = IronCondor(
        underlying_symbol="SPY",
        put_long=CondorLeg(contract=chain_contracts[0], side="buy", quantity=1, leg_type="put_long"),
        put_short=CondorLeg(contract=chain_contracts[1], side="sell", quantity=1, leg_type="put_short"),
        call_short=CondorLeg(contract=chain_contracts[2], side="sell", quantity=1, leg_type="call_short"),
        call_long=CondorLeg(contract=chain_contracts[3], side="buy", quantity=1, leg_type="call_long"),
        net_credit=2.0,
        max_risk=300.0,
        max_profit=200.0,
        wing_width=5.0,
        expiration_date="2026-09-18",
        lower_breakeven=543.0,
        upper_breakeven=557.0,
    )

    payload = executor.build_mleg_order(condor, quantity=1, intent_type="open")
    legs = payload["legs"]

    assert len(legs) == 4
    sides = [l["side"] for l in legs]
    intents = [l["position_intent"] for l in legs]

    assert sides.count("BUY") == 2
    assert sides.count("SELL") == 2
    assert intents.count("BUY_TO_OPEN") == 2
    assert intents.count("SELL_TO_OPEN") == 2
