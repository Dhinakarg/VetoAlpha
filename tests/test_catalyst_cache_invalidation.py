"""
Focused Unit Tests for Smart Catalyst-Cache Invalidation.
Tests 8 required scenarios:
 1. Identical news -> Cache hit
 2. Reordered identical news -> Cache hit
 3. New headline added -> Fresh LLM assessment
 4. New metadata/polling timestamp alone -> Cache hit
 5. Material IV shock (>= 15%) -> Cache bypass
 6. Regime transition -> Deterministic NO_TRADE behavior
 7. Emergency position exit with stale/unavailable catalyst cache -> Exit still executes
 8. LLM failure after cache invalidation -> Fail-closed HIGH risk fallback
STRICTLY READ-ONLY — ZERO LIVE BROKER ENDPOINTS CALLED.
"""

from unittest.mock import patch, MagicMock
import pytest
import time

from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment
from trading_agent.strategy.regime import MarketRegime
from trading_agent.strategy.strategy_selector import StrategySelector
from trading_agent.portfolio.position_manager import review_open_positions
from trading_agent.models import OptionChainSnapshot, OptionContract, AccountSnapshot, TradeProposal
from trading_agent.risk.risk_gate import evaluate_trade


# Helper to build mock agent
def build_test_agent():
    agent = CatalystIntelligenceAgent(api_key="mock_key")
    return agent


# 1. Identical news -> Cache hit
def test_cache_invalidation_1_identical_news():
    agent = build_test_agent()
    news = [{"headline": "Fed holds rates steady", "source": "Bloomberg", "timestamp": "10:00:00"}]

    mock_llm_return = '{"catalyst_risk": "low", "directional_bias": "neutral", "expected_volatility": "low", "confidence": 0.9, "rationale": "Stable"}'
    with patch.object(agent, "_call_llm_endpoint", return_value=mock_llm_return) as mock_call:
        res1 = agent.assess_catalyst("SPY", recent_news=news, iv_rank=20.0, current_date="2026-08-31")
        assert res1.cached is False
        assert mock_call.call_count == 1

        res2 = agent.assess_catalyst("SPY", recent_news=news, iv_rank=20.0, current_date="2026-08-31")
        assert res2.cached is True
        assert mock_call.call_count == 1  # No extra LLM call


# 2. Reordered identical news -> Cache hit
def test_cache_invalidation_2_reordered_news():
    agent = build_test_agent()
    news_a = [
        {"headline": "Fed holds rates steady", "source": "Bloomberg"},
        {"headline": "Tech earnings beat expectations", "source": "Reuters"}
    ]
    news_b = [
        {"headline": "Tech earnings beat expectations", "source": "Reuters"},
        {"headline": "Fed holds rates steady", "source": "Bloomberg"}
    ]

    mock_llm_return = '{"catalyst_risk": "low", "directional_bias": "neutral", "expected_volatility": "low", "confidence": 0.9, "rationale": "Stable"}'
    with patch.object(agent, "_call_llm_endpoint", return_value=mock_llm_return) as mock_call:
        res1 = agent.assess_catalyst("SPY", recent_news=news_a, iv_rank=20.0, current_date="2026-08-31")
        assert res1.cached is False
        assert mock_call.call_count == 1

        res2 = agent.assess_catalyst("SPY", recent_news=news_b, iv_rank=20.0, current_date="2026-08-31")
        assert res2.cached is True
        assert mock_call.call_count == 1  # Reordered identical news hits cache


# 3. New headline added -> Fresh LLM assessment
def test_cache_invalidation_3_new_headline_added():
    agent = build_test_agent()
    news_initial = [{"headline": "Fed holds rates steady", "source": "Bloomberg"}]
    news_updated = [
        {"headline": "Fed holds rates steady", "source": "Bloomberg"},
        {"headline": "Emergency rate cut announced!", "source": "CNBC"}
    ]

    mock_llm_return = '{"catalyst_risk": "low", "directional_bias": "neutral", "expected_volatility": "low", "confidence": 0.9, "rationale": "Stable"}'
    with patch.object(agent, "_call_llm_endpoint", return_value=mock_llm_return) as mock_call:
        res1 = agent.assess_catalyst("SPY", recent_news=news_initial, iv_rank=20.0, current_date="2026-08-31")
        assert res1.cached is False
        assert mock_call.call_count == 1

        res2 = agent.assess_catalyst("SPY", recent_news=news_updated, iv_rank=20.0, current_date="2026-08-31")
        assert res2.cached is False
        assert mock_call.call_count == 2  # New headline triggers fresh LLM assessment


# 4. New metadata/polling timestamp alone -> Cache hit
def test_cache_invalidation_4_timestamp_alone_no_refresh():
    agent = build_test_agent()
    news_poll_1 = [{"headline": "Fed holds rates steady", "source": "Bloomberg", "polling_ts": "10:00:00", "url": "http://a.com/1"}]
    news_poll_2 = [{"headline": "Fed holds rates steady", "source": "Bloomberg", "polling_ts": "10:00:30", "url": "http://a.com/1?ref=poll2"}]

    mock_llm_return = '{"catalyst_risk": "low", "directional_bias": "neutral", "expected_volatility": "low", "confidence": 0.9, "rationale": "Stable"}'
    with patch.object(agent, "_call_llm_endpoint", return_value=mock_llm_return) as mock_call:
        res1 = agent.assess_catalyst("SPY", recent_news=news_poll_1, iv_rank=20.0, current_date="2026-08-31")
        assert res1.cached is False
        assert mock_call.call_count == 1

        res2 = agent.assess_catalyst("SPY", recent_news=news_poll_2, iv_rank=20.0, current_date="2026-08-31")
        assert res2.cached is True
        assert mock_call.call_count == 1  # Volatile timestamp/URL metadata changes do NOT invalidate cache


# 5. Material IV shock (>= 15%) -> Cache bypass
def test_cache_invalidation_5_material_iv_shock():
    agent = build_test_agent()
    news = [{"headline": "Fed holds rates steady", "source": "Bloomberg"}]

    mock_llm_return = '{"catalyst_risk": "low", "directional_bias": "neutral", "expected_volatility": "low", "confidence": 0.9, "rationale": "Stable"}'
    with patch.object(agent, "_call_llm_endpoint", return_value=mock_llm_return) as mock_call:
        res1 = agent.assess_catalyst("SPY", recent_news=news, iv_rank=20.0, current_date="2026-08-31")
        assert res1.cached is False
        assert mock_call.call_count == 1

        # IV Rank shifts from 20.0 to 40.0 (Delta 20.0% >= 15.0%)
        res2 = agent.assess_catalyst("SPY", recent_news=news, iv_rank=40.0, current_date="2026-08-31")
        assert res2.cached is False
        assert mock_call.call_count == 2  # Material IV shift triggers fresh LLM scan


# 6. Regime transition -> Deterministic NO_TRADE behavior
def test_cache_invalidation_6_regime_transition_no_trade():
    selector = StrategySelector()
    regime = MarketRegime(trend="range", volatility="normal", confidence=0.4, regime_class="REGIME_TRANSITION", eligible_strategies=[])
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=550.0, timestamp="now", contracts=[])

    # Even if catalyst advice claimed "low risk", regime transition strictly returns NO_TRADE
    decision = selector.select_strategy(market_regime=regime, option_chain=chain, underlying_price=550.0)
    assert decision.decision == "NO_TRADE"
    assert decision.diagnostics["why_no_trade"]["primary_reason"] == "REGIME_TRANSITION"


# 7. Emergency position exit with stale/unavailable catalyst cache -> Exit still executes
def test_cache_invalidation_7_emergency_exit_independent_of_cache():
    pos = {
        "position_id": "POS_EMERGENCY_01",
        "symbol": "SPY",
        "strategy": "iron_condor",
        "entry_credit": 2.0,
        "expiration_date": "2026-09-02",
        "dte": 2,  # Triggers DTE threshold exit
    }
    # Exits reviewed strictly via position_manager without calling catalyst LLM agent
    actions = review_open_positions([pos], current_quotes={"SPY": {"last": 570.0}})
    assert len(actions) == 1
    assert actions[0].action.lower() == "close"


# 8. LLM failure after cache invalidation -> Fail-closed HIGH risk fallback
def test_cache_invalidation_8_llm_failure_fail_closed():
    agent = build_test_agent()
    news_1 = [{"headline": "Fed holds rates steady", "source": "Bloomberg"}]
    news_2 = [{"headline": "Emergency rate change!", "source": "Bloomberg"}]

    mock_llm_return = '{"catalyst_risk": "low", "directional_bias": "neutral", "expected_volatility": "low", "confidence": 0.9, "rationale": "Stable"}'
    with patch.object(agent, "_call_llm_endpoint", return_value=mock_llm_return):
        res1 = agent.assess_catalyst("SPY", recent_news=news_1, iv_rank=20.0, current_date="2026-08-31")
        assert res1.catalyst_risk == "low"

    # Invalidate cache via new news, but LLM fails (TimeoutError)
    with patch.object(agent, "_call_llm_endpoint", side_effect=TimeoutError("API Timeout")):
        res2 = agent.assess_catalyst("SPY", recent_news=news_2, iv_rank=20.0, current_date="2026-08-31")
        assert res2.catalyst_risk == "high"  # Fail closed to HIGH risk
        assert "AI_SERVICE_UNAVAILABLE" in res2.flagged_events
