"""
Adversarial Test Suite for Alpaca MCP Guard & Tool Authorization.
Verifies that AI models cannot directly trade or bypass risk controls,
and ensures paper execution safety, secret scrubbing, and dry-run interception.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.models import OptionContract, IronCondor, CondorLeg, AccountSnapshot
from trading_agent.integrations.mcp_guard import MCPGuard
from trading_agent.integrations.alpaca_mcp import AlpacaMCPClient
from trading_agent.risk.safety import SafetyManager, create_trade_fingerprint
from trading_agent.infrastructure.logger import redact_secrets
from scripts.validate_execution import load_test_condor_fixture


@pytest.fixture
def valid_condor() -> IronCondor:
    path = Path("tests/fixtures/execution_condor.json")
    return load_test_condor_fixture(path)


@pytest.fixture
def active_account() -> AccountSnapshot:
    return AccountSnapshot(
        account_id="1495837a-32fc-42b2-a5e0-3a44fad4704e",
        equity=100000.0,
        buying_power=100000.0,
        status="ACTIVE",
    )


# -----------------------------------------------------------------------------
# 1. Paper Account -> Allowed
# -----------------------------------------------------------------------------
def test_mcp_guard_paper_account_allowed(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    proposal = {"proposal_id": "P_PAPER", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is True
    assert res["orders_submitted"] == 0  # Dry-run interception


# -----------------------------------------------------------------------------
# 2. Live Account -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_live_account_rejected(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    guard.executor._trading_client = MagicMock()
    guard.executor._trading_client._paper = False  # Connected to Live API

    proposal = {"proposal_id": "P_LIVE", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is False
    assert any("Paper Safety Breach" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 3. Missing Paper Flag -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_missing_paper_flag_rejected(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    proposal = {"proposal_id": "P_NO_PAPER", "symbol": "SPY", "condor": valid_condor, "quantity": 1}

    from trading_agent.config import settings
    with patch.object(settings, "alpaca_paper", False):
        res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
        assert res["authorized"] is False
        assert any("ALPACA_PAPER is False" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 4. Risk Gate Rejection -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_risk_gate_rejection_blocked(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    # Condor with excessive risk $5,000 > max $2,500
    valid_condor.max_risk = 5000.0
    proposal = {"proposal_id": "P_HIGH_RISK", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is False
    assert any("Risk Gate Violation" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 5. Circuit Breaker Active -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_circuit_breaker_blocked(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    proposal = {"proposal_id": "P_CB", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    # Breached -$3,500 daily loss
    res = guard.authorize_and_execute_mcp_write(
        "alpaca_submit_mleg_order", proposal, active_account, daily_pnl=-3500.0
    )
    assert res["authorized"] is False
    assert any("Daily loss circuit-breaker" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 6. Duplicate Fingerprint -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_duplicate_fingerprint_blocked(tmp_path, valid_condor, active_account):
    safety = SafetyManager(db_path=tmp_path / "mcp_idemp.db")
    fp = create_trade_fingerprint(valid_condor)
    safety.record_trade_attempt(fp, "SPY", valid_condor.expiration_date, "ORD_PREV", "filled")

    guard = MCPGuard(dry_run=True)
    with patch("trading_agent.integrations.mcp_guard.safety_manager", safety):
        proposal = {"proposal_id": "P_DUP", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
        res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
        assert res["authorized"] is False
        assert any("Duplicate trade fingerprint" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 7. Invalid MLEG Geometry -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_invalid_mleg_rejected(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    # Invert short put and call strikes
    valid_condor.put_short.contract.strike_price = 570.0  # Put strike > Call strike
    proposal = {"proposal_id": "P_INV", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is False
    assert any("Geometry Violation" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 8. Market Order Attempt -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_market_order_rejected(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    proposal = {
        "proposal_id": "P_MKT",
        "symbol": "SPY",
        "condor": valid_condor,
        "quantity": 1,
        "order_type": "market",  # Market order attempt
    }
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is False
    assert any("Market orders are strictly prohibited" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 9. Missing Greek -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_missing_delta_rejected(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    valid_condor.put_short.contract.delta = None
    proposal = {"proposal_id": "P_NO_DELTA", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is False
    assert any("Greeks Violation" in r or "Risk Gate Violation" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 10. Stale Quote -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_stale_quote_rejected(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    valid_condor.put_short.contract.quote_timestamp = stale_time
    proposal = {"proposal_id": "P_STALE", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is False
    assert any("Stale quote timestamp" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 11. Unauthorized Direct AI Request (bypass_risk) -> Rejected
# -----------------------------------------------------------------------------
def test_mcp_guard_unauthorized_ai_bypass_blocked(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    ai_unauthorized_payload = {
        "decision": "BUY_SPY_NOW",
        "bypass_risk": True,  # Direct AI override flag
        "symbol": "SPY",
        "condor": valid_condor,
    }
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", ai_unauthorized_payload, active_account)
    assert res["authorized"] is False
    assert any("Direct AI bypass_risk attempt detected" in r for r in res["reasons"])


# -----------------------------------------------------------------------------
# 12. Valid Paper Order -> Authorized
# -----------------------------------------------------------------------------
def test_mcp_guard_valid_paper_order_authorized(valid_condor, active_account):
    guard = MCPGuard(dry_run=True)
    proposal = {"proposal_id": "P_VALID", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)
    assert res["authorized"] is True
    assert res["status"] == "new"


# -----------------------------------------------------------------------------
# 13. Dry-Run -> Intercepted and NOT Submitted
# -----------------------------------------------------------------------------
def test_mcp_guard_dry_run_intercepted(valid_condor, active_account):
    mock_trading_client = MagicMock()
    guard = MCPGuard(dry_run=True)
    guard.executor._trading_client = mock_trading_client

    proposal = {"proposal_id": "P_DRY", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)

    assert res["authorized"] is True
    assert res["orders_submitted"] == 0
    # Proof that Alpaca trading client was NEVER called
    assert mock_trading_client.submit_order.call_count == 0


# -----------------------------------------------------------------------------
# 14. API / MCP Timeout -> No Duplicate Retry
# -----------------------------------------------------------------------------
def test_mcp_guard_timeout_handles_safely(valid_condor, active_account):
    mock_trading_client = MagicMock()
    mock_trading_client.submit_order.side_effect = TimeoutError("Alpaca API connection timeout")
    guard = MCPGuard(dry_run=False)  # Live paper attempt
    guard.executor._trading_client = mock_trading_client

    proposal = {"proposal_id": "P_TIMEOUT", "symbol": "SPY", "condor": valid_condor, "quantity": 1}
    res = guard.authorize_and_execute_mcp_write("alpaca_submit_mleg_order", proposal, active_account)

    assert res["status"] == "rejected"
    # Ensure it did not loop or make duplicate retry calls
    assert mock_trading_client.submit_order.call_count == 1


# -----------------------------------------------------------------------------
# 15. Secret Redaction in Audit Logs
# -----------------------------------------------------------------------------
def test_mcp_secret_redaction():
    fake_key = "PKDAM6I5UOC3Z3EAICPF3VNNXN"
    fake_secret = "GTTrtf29LkeNC7zhrSx2Nr4t2N9iiKBAn4pfi8BwNn9h"
    raw_payload = {
        "tool": "alpaca_submit_mleg_order",
        "alpaca_api_key": fake_key,
        "alpaca_secret_key": fake_secret,
        "authorization": f"Bearer {fake_secret}",
        "symbol": "SPY",
    }
    cleaned = redact_secrets(raw_payload, known_secrets=[fake_key, fake_secret])

    assert cleaned["alpaca_api_key"] == "[REDACTED]"
    assert cleaned["alpaca_secret_key"] == "[REDACTED]"
    assert fake_key not in str(cleaned)
    assert fake_secret not in str(cleaned)
