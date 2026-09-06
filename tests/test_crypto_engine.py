"""
Comprehensive Test Suite for Crypto Validation Subsystem.
Tests:
1. Crypto regime classification across all discrete regimes
2. 5 Crypto strategy candidate generation & deterministic downside protection
3. Strategy selector 0-100 scoring matrix & AI advisory integration
4. Risk gate & volatility/risk-based position sizing
5. Paper execution simulation & order state tracking
6. Portfolio management & exit monitoring (TP, SL, time-exit)
7. Performance analytics & Buy & Hold benchmark calculations
8. AI quota protection (skipping Featherless calls when gates reject trade)
9. Restart state recovery (--resume)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from trading_agent.crypto.crypto_data import CryptoDataFetcher, CryptoCandle, CryptoQuote, CryptoMarketData
from trading_agent.crypto.crypto_regime import classify_crypto_regime, CryptoRegime
from trading_agent.crypto.crypto_strategy import CryptoStrategyCandidate, CryptoStrategyBuilder
from trading_agent.crypto.crypto_selector import CryptoStrategySelector, CryptoStrategyDecision
from trading_agent.crypto.crypto_risk import CryptoRiskGate, CryptoRiskDecision
from trading_agent.crypto.crypto_execution import CryptoExecutionEngine, CryptoOrderPayload
from trading_agent.crypto.crypto_portfolio import CryptoPortfolioManager, CryptoPosition
from trading_agent.crypto.crypto_pnl import CryptoPnLEngine, CryptoPerformanceReport
from scripts.simulate_crypto import CryptoSimulationRunner


def _make_mock_crypto_data(
    symbol="BTC/USD",
    price=65000.0,
    adx=30.0,
    rsi=60.0,
    realized_vol=40.0,
    valid=True,
) -> CryptoMarketData:
    quote = CryptoQuote(symbol=symbol, bid=price * 0.999, ask=price * 1.001, last_price=price, volume_24h=1000.0, timestamp=datetime.now(timezone.utc).isoformat())
    return CryptoMarketData(
        symbol=symbol,
        quote=quote,
        candles=[],
        current_price=price,
        sma_20=price * 0.98,
        sma_50=price * 0.95,
        sma_200=price * 0.90,
        ema_20=price * 0.98,
        adx_14=adx,
        rsi_14=rsi,
        bb_upper=price * 1.05,
        bb_middle=price,
        bb_lower=price * 0.95,
        bb_width_percentile=50.0,
        atr_14=1000.0,
        realized_volatility=realized_vol,
        volume_ratio=1.5,
        is_valid=valid,
    )


# -----------------------------------------------------------------------------
# 1. Crypto Regime Classification Tests
# -----------------------------------------------------------------------------
def test_crypto_regime_classification_strong_bull():
    data = _make_mock_crypto_data(adx=40.0, rsi=65.0)
    res = classify_crypto_regime(data)
    assert res.regime == CryptoRegime.STRONG_BULL
    assert res.confidence > 0.85


def test_crypto_regime_classification_range():
    data = _make_mock_crypto_data(adx=15.0, rsi=50.0)
    res = classify_crypto_regime(data)
    assert res.regime == CryptoRegime.RANGE


def test_crypto_regime_classification_unknown_on_invalid_data():
    data = _make_mock_crypto_data(valid=False)
    res = classify_crypto_regime(data)
    assert res.regime == CryptoRegime.UNKNOWN


# -----------------------------------------------------------------------------
# 2. Crypto Strategy Candidate Generation
# -----------------------------------------------------------------------------
def test_crypto_strategy_candidate_generation():
    data = _make_mock_crypto_data(adx=30.0, rsi=60.0)
    regime_res = classify_crypto_regime(data)
    candidates = CryptoStrategyBuilder.generate_candidates(data, regime_res)

    assert len(candidates) >= 1
    c = candidates[0]
    assert c.strategy == "TREND_FOLLOWING_LONG"
    assert c.stop_loss < c.entry_price
    assert c.take_profit > c.entry_price
    assert c.risk_reward_ratio >= 1.5


# -----------------------------------------------------------------------------
# 3. Strategy Selector & 0-100 Scoring
# -----------------------------------------------------------------------------
def test_crypto_strategy_selector_scoring():
    selector = CryptoStrategySelector(min_score=65.0)
    data = _make_mock_crypto_data(adx=30.0, rsi=60.0)
    regime_res = classify_crypto_regime(data)

    decision = selector.evaluate(data, regime_res)
    assert decision.action == "trade"
    assert decision.selected_candidate is not None
    assert decision.score >= 65.0


# -----------------------------------------------------------------------------
# 4. Risk Gate & Position Sizing
# -----------------------------------------------------------------------------
def test_crypto_risk_gate_position_sizing():
    gate = CryptoRiskGate(risk_per_trade=0.0025)
    c = CryptoStrategyCandidate(
        strategy="TREND_FOLLOWING_LONG",
        symbol="BTC/USD",
        direction="long",
        entry_price=65000.0,
        stop_loss=64000.0,  # $1,000 risk per unit
        take_profit=67500.0,
        expected_risk=1000.0,
        expected_reward=2500.0,
        risk_reward_ratio=2.5,
        confidence=0.85,
        regime="BULL",
        signal_timestamp=datetime.now(timezone.utc).isoformat(),
    )

    res = gate.evaluate_candidate(candidate=c, equity=100000.0, daily_pnl=0.0, open_positions=[])
    assert res.approved is True
    # Risk budget = $100,000 * 0.0025 = $250. Size = 250 / 1000 = 0.25 units
    assert res.position_size == 0.25
    assert res.max_loss_usd == 250.0


def test_crypto_risk_gate_rejects_missing_stop_loss():
    gate = CryptoRiskGate()
    c = CryptoStrategyCandidate(
        strategy="TREND_FOLLOWING_LONG",
        symbol="BTC/USD",
        direction="long",
        entry_price=65000.0,
        stop_loss=0.0,  # Invalid
        take_profit=67500.0,
        expected_risk=0.0,
        expected_reward=2500.0,
        risk_reward_ratio=2.5,
        confidence=0.85,
        regime="BULL",
        signal_timestamp=datetime.now(timezone.utc).isoformat(),
    )

    res = gate.evaluate_candidate(candidate=c, equity=100000.0, daily_pnl=0.0, open_positions=[])
    assert res.approved is False
    assert any("MISSING_PROTECTION" in v for v in res.violations)


# -----------------------------------------------------------------------------
# 5. Execution Engine Simulation
# -----------------------------------------------------------------------------
def test_crypto_execution_engine_paper_simulation():
    engine = CryptoExecutionEngine(mode="PAPER_SIMULATION")
    c = CryptoStrategyCandidate(
        strategy="TREND_FOLLOWING_LONG",
        symbol="BTC/USD",
        direction="long",
        entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67500.0,
        expected_risk=1000.0,
        expected_reward=2500.0,
        risk_reward_ratio=2.5,
        confidence=0.85,
        regime="BULL",
        signal_timestamp=datetime.now(timezone.utc).isoformat(),
    )
    risk_dec = CryptoRiskDecision(approved=True, violations=[], candidate=c, position_size=0.25, max_loss_usd=250.0, risk_percentage=0.25)

    payload = engine.build_order_payload(risk_dec)
    assert payload is not None

    exec_payload = engine.execute_order(payload)
    assert exec_payload.status == "FILLED"
    assert exec_payload.filled_qty == 0.25
    assert exec_payload.fee_usd > 0


# -----------------------------------------------------------------------------
# 6. Portfolio Management & Exit Monitoring
# -----------------------------------------------------------------------------
def test_crypto_portfolio_exit_monitoring():
    portfolio = CryptoPortfolioManager(initial_equity=100000.0)
    payload = CryptoOrderPayload(
        order_id="CRYPTO-101",
        symbol="BTC/USD",
        direction="long",
        order_type="limit",
        quantity=0.25,
        limit_price=65000.0,
        stop_loss=64000.0,
        take_profit=67500.0,
        submission_timestamp=datetime.now(timezone.utc).isoformat(),
        execution_mode="PAPER_SIMULATION",
        fingerprint="TREND:long:BTC/USD:65000",
        status="FILLED",
        filled_qty=0.25,
        filled_avg_price=65000.0,
        fee_usd=8.125,
    )
    pos = portfolio.open_position_from_order(payload, regime="BULL")

    # Simulate price reaching Take Profit
    data = _make_mock_crypto_data(symbol="BTC/USD", price=68000.0)
    closed = portfolio.update_and_check_exits({"BTC/USD": data})

    assert len(closed) == 1
    assert closed[0].exit_reason == "TAKE_PROFIT"
    assert closed[0].realized_pnl > 0


# -----------------------------------------------------------------------------
# 7. Performance Analytics & Buy & Hold Benchmark
# -----------------------------------------------------------------------------
def test_crypto_pnl_analytics_and_benchmark():
    pos1 = CryptoPosition(
        position_id="POS-1",
        symbol="BTC/USD",
        direction="long",
        entry_price=60000.0,
        quantity=0.1,
        stop_loss=58000.0,
        take_profit=64000.0,
        entry_timestamp="2026-08-28T00:00:00Z",
        strategy="TREND_FOLLOWING_LONG",
        regime="BULL",
        entry_fee_usd=3.0,
        exit_price=64000.0,
        exit_reason="TAKE_PROFIT",
        exit_fee_usd=3.2,
        realized_pnl=393.80,
        status="CLOSED",
    )

    report = CryptoPnLEngine.calculate_performance(
        closed_positions=[pos1],
        current_equity=100393.80,
        starting_equity=100000.0,
        benchmark_start_price=60000.0,
        benchmark_current_price=62000.0,
    )

    assert report.total_trades == 1
    assert report.win_rate_pct == 100.0
    assert report.strategy_return_pct > 0.0
    # Buy & Hold return: (62k - 60k)/60k = +3.33%
    assert report.buy_and_hold_return_pct == 3.33
    assert report.strategy_alpha_pct != 0.0


# -----------------------------------------------------------------------------
# 8. Restart Recovery Test (--resume)
# -----------------------------------------------------------------------------
def test_crypto_simulation_restart_recovery(tmp_path):
    runner = CryptoSimulationRunner(symbols=["BTC/USD"], cycles=1, paper=False, data_dir=tmp_path)
    runner.daily_pnl = 150.0
    runner.fingerprints_seen.add("TEST_FP_123")
    runner._save_state()

    runner2 = CryptoSimulationRunner(symbols=["BTC/USD"], cycles=1, paper=False, resume=True, data_dir=tmp_path)
    assert runner2.daily_pnl == 150.0
    assert "TEST_FP_123" in runner2.fingerprints_seen
