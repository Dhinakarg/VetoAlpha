"""
Unit tests for structured event logger and summarize_trades presentation dashboard.
Tests append-only JSON Lines logging, secret redaction, supported event types, and performance calculations.
"""

import json
from pathlib import Path
import pytest

from trading_agent.infrastructure.logger import (
    AgentEventLogger,
    SUPPORTED_EVENT_TYPES,
    redact_secrets,
)
from scripts.summarize_trades import parse_agent_log, format_summary_report


@pytest.fixture
def temp_logger(tmp_path):
    log_file = tmp_path / "test_agent_log.jsonl"
    return AgentEventLogger(log_path=log_file, default_run_id="test_run_001")


def test_jsonl_append_event_schema(temp_logger):
    """Test that events are appended in JSON Lines format with all 6 required top-level fields."""
    rec = temp_logger.log_event(
        event_type="scan_started",
        symbol="SPY",
        payload={"scan_id": 1, "target": "SPY"},
    )

    assert rec["event_type"] == "scan_started"
    assert rec["symbol"] == "SPY"
    assert rec["run_id"] == "test_run_001"
    assert "event_id" in rec
    assert "timestamp_utc" in rec
    assert rec["payload"]["scan_id"] == 1

    # Verify file content
    assert temp_logger.log_path.exists()
    lines = temp_logger.log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    saved = json.loads(lines[0])
    assert saved["event_id"] == rec["event_id"]
    assert saved["event_type"] == "scan_started"
    assert saved["symbol"] == "SPY"


def test_secret_redaction():
    """Test that API keys, secrets, authorization headers, and sensitive keys are redacted."""
    raw_payload = {
        "api_key": "PK_TEST_SECRET_KEY_12345",
        "secret_key": "SK_VERY_SECRET_ALPACA_98765",
        "featherless_api_key": "FL_SECRET_LLM_KEY",
        "headers": {"Authorization": "Bearer fl_sec_abc123xyz890"},
        "nested": {"token": "my_secret_token", "normal_field": "safe_value"},
        "message": "Connected with key PK_TEST_SECRET_KEY_12345 to Alpaca",
    }

    known = ["PK_TEST_SECRET_KEY_12345", "SK_VERY_SECRET_ALPACA_98765", "FL_SECRET_LLM_KEY"]
    cleaned = redact_secrets(raw_payload, known_secrets=known)

    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["secret_key"] == "[REDACTED]"
    assert cleaned["featherless_api_key"] == "[REDACTED]"
    assert cleaned["headers"]["Authorization"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert cleaned["nested"]["normal_field"] == "safe_value"
    assert "PK_TEST_SECRET_KEY_12345" not in cleaned["message"]
    assert "[REDACTED]" in cleaned["message"]


def test_supported_event_types(temp_logger):
    """Test that all 17 required event types can be logged successfully."""
    expected_types = [
        "scan_started",
        "market_clock",
        "market_snapshot",
        "regime_check",
        "sentiment_check",
        "condor_proposed",
        "risk_gate_result",
        "safety_check",
        "order_dry_run",
        "order_submitted",
        "order_partially_filled",
        "order_filled",
        "order_rejected",
        "position_reviewed",
        "position_closed",
        "circuit_breaker_tripped",
        "error",
    ]

    for etype in expected_types:
        assert etype in SUPPORTED_EVENT_TYPES
        rec = temp_logger.log_event(event_type=etype, symbol="SPY", payload={"status": "ok"})
        assert rec["event_type"] == etype

    lines = temp_logger.log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(expected_types)


def test_summarize_trades_metrics(tmp_path):
    """Test parse_agent_log and format_summary_report metric aggregations."""
    log_file = tmp_path / "test_trades.jsonl"
    logger = AgentEventLogger(log_path=log_file)

    logger.log_event("scan_started", symbol="SPY", payload={})
    logger.log_event("sentiment_check", symbol="SPY", payload={"score": 0.2})
    logger.log_event("condor_proposed", symbol="SPY", payload={"net_credit": 2.50})
    logger.log_event("order_submitted", symbol="SPY", payload={})
    logger.log_event("order_filled", symbol="SPY", payload={"order_id": "ORD_1"})
    logger.log_event("position_reviewed", symbol="SPY", payload={"unrealized_pnl": 50.0})
    logger.log_event("position_closed", symbol="SPY", payload={"realized_pnl": 120.0})
    logger.log_event("position_closed", symbol="SPY", payload={"realized_pnl": -40.0})
    logger.log_event("circuit_breaker_tripped", symbol="SPY", payload={})

    metrics = parse_agent_log(log_file)

    assert metrics["starting_equity"] == 100000.0
    assert metrics["realized_pnl"] == 80.0   # 120 - 40
    assert metrics["unrealized_pnl"] == 50.0
    assert metrics["total_pnl"] == 130.0     # 80 + 50
    assert metrics["current_equity"] == 100130.0
    assert metrics["trades_filled"] == 1
    assert metrics["win_rate"] == 50.0       # 1 winner out of 2 closed
    assert metrics["avg_winner"] == 120.0
    assert metrics["avg_loser"] == -40.0
    assert metrics["profit_factor"] == 3.0   # 120 / 40
    assert metrics["circuit_breaker_events"] == 1
    assert metrics["ai_calls_used"] == 1
    assert metrics["ai_calls_remaining"] == 49

    report_text = format_summary_report(metrics)
    assert "PERFORMANCE SUMMARY" in report_text
    assert "$100,000.00" in report_text
    assert "$100,130.00" in report_text
    assert "Win Rate:" in report_text
    assert "Profit Factor:" in report_text
