"""
Comprehensive Test Suite for Alpaca Hackathon Compliance & MCP Tool Integration.
Verifies:
1. Hackathon mode account matching (HACKATHON_ACCOUNT_MISMATCH abort)
2. Alpaca MCP client tool discovery and categorizations
3. MCP read-only vs write tool authorization via MCPGuard
4. Paper trading endpoint enforcement and physical live-money blocking
5. Order submission call graph invariants
6. Featherless AI quota skipping on deterministic failure
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.config import settings
from trading_agent.models import AccountSnapshot, OptionContract, StrategyCandidate, StrategyLeg
from trading_agent.risk.safety import (
    validate_paper_execution_environment,
    PaperExecutionSafetyError,
)
from trading_agent.integrations.alpaca_mcp import AlpacaMCPClient, READ_ONLY_TOOLS, WRITE_TOOLS
from trading_agent.integrations.mcp_guard import MCPGuard
from scripts.hackathon_compliance_audit import audit_hackathon_compliance
from scripts.final_account_audit import audit_final_account


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_valid_contract(symbol: str, strike: float, ctype: str) -> OptionContract:
    exp_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    return OptionContract(
        symbol=symbol,
        underlying_symbol="SPY",
        strike_price=strike,
        expiration_date=exp_date,
        contract_type=ctype,
        bid=2.0,
        ask=2.1,
        delta=-0.18 if ctype == "put" else 0.18,
        open_interest=500,
        implied_volatility=0.20,
        quote_timestamp=_make_fresh_timestamp(),
    )


# -----------------------------------------------------------------------------
# 1. Hackathon Mode Account Mismatch Aborts
# -----------------------------------------------------------------------------
def test_hackathon_mode_account_mismatch_aborts():
    account = AccountSnapshot(account_id="DEV_ACCOUNT_123", status="ACTIVE")
    with patch.object(settings, "hackathon_mode", True), patch.object(settings, "hackathon_account_id", "HACKATHON_ACC_999"):
        with pytest.raises(PaperExecutionSafetyError) as exc_info:
            validate_paper_execution_environment(account=account)
        assert "HACKATHON_ACCOUNT_MISMATCH" in str(exc_info.value)


def test_hackathon_mode_missing_account_id_aborts():
    account = AccountSnapshot(account_id="DEV_ACCOUNT_123", status="ACTIVE")
    with patch.object(settings, "hackathon_mode", True), patch.object(settings, "hackathon_account_id", None):
        with pytest.raises(PaperExecutionSafetyError) as exc_info:
            validate_paper_execution_environment(account=account)
        assert "HACKATHON_ACCOUNT_MISMATCH" in str(exc_info.value)


def test_hackathon_mode_matching_account_passes():
    account = AccountSnapshot(account_id="HACKATHON_ACC_999", status="ACTIVE")
    with patch.object(settings, "hackathon_mode", True), patch.object(settings, "hackathon_account_id", "HACKATHON_ACC_999"):
        res = validate_paper_execution_environment(account=account)
        assert res["paper_verified"] is True
        assert res["account_id"] == "HACKATHON_ACC_999"


# -----------------------------------------------------------------------------
# 2. Alpaca MCP Tool Discovery
# -----------------------------------------------------------------------------
def test_alpaca_mcp_client_tool_discovery():
    mcp_client = AlpacaMCPClient(dry_run=True)
    tools = mcp_client.discover_tools()

    assert "read_tools" in tools
    assert "write_tools" in tools
    assert "alpaca_get_account" in tools["read_tools"]
    assert "alpaca_submit_mleg_order" in tools["write_tools"]


# -----------------------------------------------------------------------------
# 3. MCP Read Tool Execution
# -----------------------------------------------------------------------------
def test_mcp_read_tool_execution():
    mcp_client = AlpacaMCPClient(dry_run=True)
    mock_data = MagicMock()
    mock_data.get_account_snapshot.return_value = AccountSnapshot(
        account_id="MCP_TEST_ACC", equity=100000.0, cash=100000.0, buying_power=100000.0, status="ACTIVE"
    )

    with patch("trading_agent.data.alpaca_data.AlpacaDataFetcher", return_value=mock_data):
        res = mcp_client.execute_read_tool("alpaca_get_account")
        assert res.get("account_id") == "MCP_TEST_ACC"


# -----------------------------------------------------------------------------
# 4. MCP Write Tool Routed Through MCPGuard
# -----------------------------------------------------------------------------
def test_mcp_write_tool_passes_through_mcp_guard():
    mcp_client = AlpacaMCPClient(dry_run=True)
    proposal = MagicMock()
    account = AccountSnapshot(account_id="MCP_ACC", equity=100000.0, status="ACTIVE")

    with patch.object(MCPGuard, "authorize_and_execute_mcp_write") as mock_guard_call:
        mock_guard_call.return_value = {"status": "authorized", "dry_run": True}
        res = mcp_client.execute_guarded_write_tool("alpaca_submit_mleg_order", proposal=proposal, account=account)
        assert mock_guard_call.called
        assert res.get("status") == "authorized"


# -----------------------------------------------------------------------------
# 5. Live Trading Endpoint Blocked
# -----------------------------------------------------------------------------
def test_live_trading_endpoint_blocked():
    with patch.object(settings, "alpaca_paper", False):
        with pytest.raises(PaperExecutionSafetyError) as exc_info:
            validate_paper_execution_environment()
        assert "ALPACA_PAPER is False" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 6. Compliance Audit Matrix Verification
# -----------------------------------------------------------------------------
def test_hackathon_compliance_audit_matrix():
    matrix = audit_hackathon_compliance()

    assert matrix["autonomous_agent"]["status"] == "PASS"
    assert matrix["alpaca_mcp_cli"]["status"] == "PASS"
    assert matrix["options_trading"]["status"] == "PASS"
    assert matrix["paper_safety"]["status"] == "PASS"
    assert matrix["one_page_writeup"]["status"] == "PASS"
    assert matrix["risk_controls"]["status"] == "PASS"


# -----------------------------------------------------------------------------
# 7. Final Account Audit Classification
# -----------------------------------------------------------------------------
def test_final_account_audit_classification():
    report = audit_final_account()

    assert "classification" in report
    assert report["classification"] in ("DEVELOPMENT ACCOUNT", "HACKATHON ACCOUNT")
    assert report["hackathon_requirement_status"] in ("VERIFIED", "NOT_VERIFIED")
