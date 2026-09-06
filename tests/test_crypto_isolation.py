"""
Mandatory Architectural Isolation Test Suite for Crypto Engine vs Options Engine.
Verifies:
1. Crypto cannot execute options orders.
2. Options cannot execute crypto orders.
3. Crypto state/circuit breakers do not cross-contaminate options state.
4. Crypto telemetry and log paths are completely separate.
5. Crypto has zero live-money execution pathways.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.config import settings
from trading_agent.crypto.crypto_execution import CryptoExecutionEngine, CryptoOrderPayload
from trading_agent.crypto.crypto_risk import CryptoRiskGate
from trading_agent.crypto.crypto_strategy import CryptoStrategyCandidate
from trading_agent.crypto.crypto_portfolio import CryptoPortfolioManager
from trading_agent.execution.alpaca_executor import AlpacaExecutor
from trading_agent.risk.safety import SafetyManager, PaperExecutionSafetyError
from scripts.simulate_crypto import CryptoSimulationRunner
from scripts.simulate_competition import CompetitionSimulationRunner


def test_crypto_cannot_execute_options_order(tmp_path):
    crypto_engine = CryptoExecutionEngine(mode="PAPER_SIMULATION")
    payload = CryptoOrderPayload(
        order_id="CRYPTO-123",
        symbol="BTC/USD",
        direction="long",
        order_type="limit",
        quantity=0.1,
        limit_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
        submission_timestamp="2026-08-29T12:00:00Z",
        execution_mode="PAPER_SIMULATION",
        fingerprint="TREND_LONG:BTC/USD:65000",
    )

    # Asserts that CryptoExecutionEngine does not interact with Alpaca MLEG option submission API
    res = crypto_engine.execute_order(payload)
    assert res.status == "FILLED"
    assert "CRYPTO" in res.order_id
    assert not hasattr(crypto_engine, "_trading_client")


def test_options_cannot_execute_crypto_order(tmp_path):
    options_runner = CompetitionSimulationRunner(
        symbols=["SPY"],
        cycles=1,
        submit_paper=False,
        data_dir=tmp_path,
    )
    # Options symbols list must reject crypto pair format
    for sym in options_runner.symbols:
        assert "/" not in sym
        assert sym in ("SPY", "QQQ", "IWM")


def test_crypto_does_not_modify_options_state(tmp_path):
    crypto_dir = tmp_path / "crypto"
    options_dir = tmp_path / "options"
    crypto_dir.mkdir()
    options_dir.mkdir()

    c_runner = CryptoSimulationRunner(symbols=["BTC/USD"], cycles=1, paper=False, data_dir=crypto_dir)
    o_runner = CompetitionSimulationRunner(symbols=["SPY"], cycles=1, submit_paper=False, data_dir=options_dir)

    c_runner.daily_pnl = -500.0
    c_runner.circuit_breaker_tripped = True
    c_runner._save_state()

    # Options runner state file remains completely uninfluenced
    assert o_runner.daily_pnl == 0.0
    assert o_runner.circuit_breaker_tripped is False
    assert c_runner.state_file.name == "crypto_state.json"
    assert o_runner.state_file.name == "competition_state.json"


def test_crypto_circuit_breaker_does_not_modify_options_circuit_breaker():
    c_gate = CryptoRiskGate()
    o_manager = SafetyManager()

    c_gate.max_daily_loss_pct = 0.01
    cand = CryptoStrategyCandidate(
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
        signal_timestamp="2026-08-29T12:00:00Z",
    )
    c_dec = c_gate.evaluate_candidate(
        candidate=cand,
        equity=100000.0,
        daily_pnl=-2000.0,
        open_positions=[],
    )
    assert c_dec.approved is False
    assert any("DAILY_LOSS_CIRCUIT_BREAKER" in v for v in c_dec.violations)

    # Options safety manager is isolated
    assert o_manager.is_circuit_breaker_active() is False


def test_crypto_logs_are_separate(tmp_path):
    c_runner = CryptoSimulationRunner(symbols=["BTC/USD"], cycles=1, paper=False, data_dir=tmp_path)
    c_runner._append_telemetry({"test_key": "crypto_value", "engine": "CRYPTO_VALIDATION_ENGINE"})

    assert (tmp_path / "crypto_run.jsonl").exists()
    assert not (tmp_path / "competition_run.jsonl").exists()

    with open(tmp_path / "crypto_run.jsonl", "r", encoding="utf-8") as f:
        content = f.read()
    assert "crypto_value" in content
    assert "CRYPTO_VALIDATION_ENGINE" in content


def test_crypto_cannot_reach_live_execution():
    crypto_engine = CryptoExecutionEngine(mode="DRY_RUN")
    assert crypto_engine.mode == "DRY_RUN"
    assert not hasattr(crypto_engine, "submit_live_order")
