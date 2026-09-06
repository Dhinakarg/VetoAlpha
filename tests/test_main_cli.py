"""
Unit tests for CLI argument parsing, startup dashboard banner, market-clock loop,
and safety validation in trading_agent/main.py.
"""

from io import StringIO
import sys
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.config import settings
from trading_agent.main import (
    parse_args,
    mask_account_id,
    print_startup_banner,
    validate_safety_mode,
    run_market_scan_job,
)


def test_cli_argument_defaults():
    """Test CLI default parameters."""
    args = parse_args([])
    assert args.dry_run is True
    assert args.once is False
    assert args.symbols == "SPY,QQQ"
    assert args.interval == 5


def test_cli_argument_flags():
    """Test CLI flag overrides for --once, --live, --symbols, and --interval."""
    args = parse_args(["--once", "--live", "--symbols", "SPY,IWM,DIA", "--interval", "10"])
    assert args.once is True
    assert args.dry_run is False
    assert args.symbols == "SPY,IWM,DIA"
    assert args.interval == 10


def test_mask_account_id():
    """Test secure masking of account IDs."""
    assert mask_account_id("PA3K99XYZ01") == "PA****01"
    assert mask_account_id("1234") == "****"
    assert mask_account_id(None) == "UNKNOWN_ACCOUNT"


def test_startup_banner_no_secrets(capsys):
    """Test startup banner renders cleanly with masked account and zero leaked API secrets."""
    print_startup_banner(
        mode_str="Paper / Dry Run",
        account_id="PA12345678",
        watchlist=["SPY", "QQQ"],
        interval_minutes=5,
        model_name="meta-llama/Meta-Llama-3.1-8B-Instruct",
        max_risk_usd=2500.0,
        daily_loss_limit_usd=3000.0,
    )
    captured = capsys.readouterr().out

    assert "ADAPTIVE AI IRON CONDOR AGENT" in captured
    assert "Paper / Dry Run" in captured
    assert "PA****78" in captured
    assert "SPY, QQQ" in captured
    assert "5 minutes" in captured
    assert "$2,500.00" in captured
    assert "$3,000.00" in captured


def test_refuse_live_real_money_execution(monkeypatch):
    """Test validate_safety_mode halts with SystemExit(1) if ALPACA_PAPER is False."""
    monkeypatch.setattr(settings, "alpaca_paper", False)

    with pytest.raises(SystemExit) as exc_info:
        validate_safety_mode(dry_run=False)

    assert exc_info.value.code == 1


def test_market_scan_job_market_closed():
    """Test scheduled job checks market clock and skips symbols when closed."""
    mock_orch = MagicMock()
    mock_data = MagicMock()
    mock_data.get_market_clock.return_value = {"is_open": False, "next_open": "2026-08-31T09:30:00-04:00"}

    run_market_scan_job(
        orchestrator=mock_orch,
        alpaca_data=mock_data,
        watchlist=["SPY", "QQQ"],
        dry_run=True,
    )

    # Ensure symbols are NOT scanned when market is closed
    assert mock_orch.run_scan_cycle.call_count == 0


def test_market_scan_job_market_open():
    """Test scheduled job iterates through all watchlist symbols when market is open."""
    mock_orch = MagicMock()
    mock_data = MagicMock()
    mock_data.get_market_clock.return_value = {"is_open": True}

    run_market_scan_job(
        orchestrator=mock_orch,
        alpaca_data=mock_data,
        watchlist=["SPY", "QQQ"],
        dry_run=True,
    )

    assert mock_orch.run_scan_cycle.call_count == 2
    mock_orch.run_scan_cycle.assert_any_call(symbol="SPY", dry_run=True)
    mock_orch.run_scan_cycle.assert_any_call(symbol="QQQ", dry_run=True)
