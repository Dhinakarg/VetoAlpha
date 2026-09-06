"""
Integration and Unit Tests for Multi-Strategy Autonomous Decision Pipeline in AgentOrchestrator.
Tests end-to-end flow across:
  DATA -> REGIME -> AI CATALYST -> STRATEGY CANDIDATES -> STRATEGY SELECTOR -> RISK GATE -> SAFETY -> EXECUTOR
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.models import (
    OptionContract,
    OptionChainSnapshot,
    AccountSnapshot,
    StrategyCandidate,
    StrategyLeg,
)
from trading_agent.agent.orchestrator import AgentOrchestrator
from trading_agent.strategy.regime import MarketRegime
from trading_agent.ai.catalyst_agent import CatalystAssessment
from trading_agent.risk.safety import SafetyManager


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def mock_multi_strat_orchestrator(tmp_path):
    mock_data = MagicMock()
    mock_catalyst = MagicMock()
    mock_selector = MagicMock()
    mock_executor = MagicMock()
    safety = SafetyManager(db_path=tmp_path / "multi_orch_idempotency.db")

    mock_data.get_market_clock.return_value = {"is_open": True, "next_open": "", "next_close": ""}
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="ACC_TEST_PAPER",
        cash=100000.0,
        buying_power=100000.0,
        equity=100000.0,
        portfolio_value=100000.0,
        status="ACTIVE",
    )
    mock_data.get_open_positions.return_value = []
    mock_data.get_open_orders.return_value = []
    mock_data.get_recent_bars.return_value = [{"close": 550.0} for _ in range(50)]
    mock_data.get_recent_news.return_value = []

    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    sp_contract = OptionContract(
        symbol="SPY_P545", underlying_symbol="SPY", strike_price=545.0, expiration_date=exp, contract_type="put", bid=2.00, ask=2.10, delta=-0.18, open_interest=500, quote_timestamp=_make_fresh_timestamp()
    )
    lp_contract = OptionContract(
        symbol="SPY_P540", underlying_symbol="SPY", strike_price=540.0, expiration_date=exp, contract_type="put", bid=0.90, ask=1.00, delta=-0.08, open_interest=500, quote_timestamp=_make_fresh_timestamp()
    )
    chain = OptionChainSnapshot(
        underlying_symbol="SPY",
        underlying_price=550.0,
        timestamp=_make_fresh_timestamp(),
        contracts=[sp_contract, lp_contract],
    )
    mock_data.get_option_chain.return_value = chain

    mock_catalyst.assess_catalyst.return_value = CatalystAssessment(
        catalyst_risk="low",
        directional_bias="bullish",
        expected_volatility="normal",
        confidence=0.88,
        rationale="Strong macroeconomic fundamentals and no impending earnings catalysts.",
    )

    return AgentOrchestrator(
        alpaca_data=mock_data,
        catalyst_agent=mock_catalyst,
        strategy_selector=mock_selector,
        safety_manager=safety,
        executor=mock_executor,
    )


def test_orchestrator_selects_bull_put_spread(mock_multi_strat_orchestrator):
    """Test full pipeline selecting Bull Put Spread under Bullish regime."""
    orch = mock_multi_strat_orchestrator
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")

    short_put = StrategyLeg(
        contract=OptionContract(symbol="SPY_P545", underlying_symbol="SPY", strike_price=545.0, expiration_date=exp, contract_type="put", bid=2.00, ask=2.10, open_interest=500, delta=-0.18, quote_timestamp=_make_fresh_timestamp()),
        side="sell", quantity=1, leg_type="put_short",
    )
    long_put = StrategyLeg(
        contract=OptionContract(symbol="SPY_P540", underlying_symbol="SPY", strike_price=540.0, expiration_date=exp, contract_type="put", bid=0.90, ask=1.00, open_interest=500, delta=-0.08, quote_timestamp=_make_fresh_timestamp()),
        side="buy", quantity=1, leg_type="put_long",
    )
    cand = StrategyCandidate(
        strategy_name="bull_put_spread",
        underlying="SPY",
        expiration=exp,
        legs=[short_put, long_put],
        entry_credit=1.10,
        max_profit=110.0,
        max_loss=390.0,
        breakeven=543.90,
        wing_width=5.0,
        dte=30,
        short_delta=-0.18,
    )

    from trading_agent.strategy.strategy_selector import StrategyDecision
    orch.strategy_selector.select_strategy.return_value = StrategyDecision(
        decision="TRADE",
        selected_strategy="bull_put_spread",
        selected_candidate=cand,
        scores={"bull_put_spread": 87.0},
        reasons=["Regime Bullish fit"],
        candidates={"bull_put_spread": cand},
    )

    with patch("trading_agent.agent.orchestrator.detect_market_regime") as mock_regime:
        mock_regime.return_value = MarketRegime(
            trend="bullish", volatility="normal", adx=31.2, sma_20=545.0, sma_50=540.0, iv_rank=45.0,
            confidence=0.85, eligible_strategies=["bull_put_spread"], reasons=["Strong uptrend"],
        )

        res = orch.run_scan_cycle(symbol="SPY", dry_run=True)

        assert res["status"] == "success"
        assert res["decision"] == "TRADE"
        assert res["selected_strategy"] == "bull_put_spread"
        assert res["entry_credit"] == 1.10
        assert res["max_loss"] == 390.0
        assert res["dry_run"] is True


def test_orchestrator_handles_no_trade_decision(mock_multi_strat_orchestrator):
    """Test pipeline cleanly reports NO_TRADE when strategy selector rejects all strategies."""
    orch = mock_multi_strat_orchestrator

    from trading_agent.strategy.strategy_selector import StrategyDecision
    orch.strategy_selector.select_strategy.return_value = StrategyDecision(
        decision="NO_TRADE",
        selected_strategy=None,
        selected_candidate=None,
        scores={},
        reasons=["Iron Condor disqualified: Trend is strongly directional", "Bull Put Spread returned None"],
        candidates={},
    )

    with patch("trading_agent.agent.orchestrator.detect_market_regime") as mock_regime:
        mock_regime.return_value = MarketRegime(
            trend="bullish", volatility="high", adx=42.0, sma_20=550.0, sma_50=535.0, iv_rank=88.0,
            confidence=0.90, eligible_strategies=["bull_put_spread"], reasons=["Extreme trend"],
        )

        res = orch.run_scan_cycle(symbol="SPY", dry_run=True)

        assert res["status"] == "no_trade"
        assert res["decision"] == "NO_TRADE"
        assert len(res["reasons"]) > 0


def test_cli_summary_formatting(mock_multi_strat_orchestrator):
    """Test format_cli_summary output contains standard blocks."""
    orch = mock_multi_strat_orchestrator

    scan_res = {
        "status": "success",
        "symbol": "SPY",
        "decision": "TRADE",
        "market_regime": {
            "trend": "bullish",
            "adx": 31.2,
            "iv_rank": 67.0,
        },
        "catalyst_assessment": {
            "catalyst_risk": "low",
        },
        "strategy_decision": {
            "scores": {
                "bull_put_spread": 87.0,
            },
        },
        "selected_strategy": "bull_put_spread",
        "dry_run": True,
    }

    summary = orch.format_cli_summary(scan_res)
    assert "MARKET REGIME" in summary
    assert "Trend: BULLISH" in summary
    assert "ADX: 31.2" in summary
    assert "IV Rank: 67.0%" in summary
    assert "STRATEGIES" in summary
    assert "Bull Put Spread   87/100" in summary
    assert "Iron Condor       REJECTED" in summary
    assert "SELECTED" in summary
    assert "Bull Put Spread" in summary
    assert "RISK GATE" in summary
    assert "PASS" in summary
    assert "DRY RUN" in summary
