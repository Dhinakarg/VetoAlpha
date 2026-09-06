"""
Tests for Centralized Paper Execution Safety Locks.
Ensures real-money live execution is completely impossible and strictly rejected.
"""

from unittest.mock import MagicMock, patch
import pytest

from trading_agent.config import settings
from trading_agent.models import AccountSnapshot
from trading_agent.risk.safety import (
    validate_paper_execution_environment,
    PaperExecutionSafetyError,
)


def test_paper_execution_safety_approved_when_paper_true():
    mock_client = MagicMock()
    mock_client._paper = True
    account = AccountSnapshot(account_id="1495837a-32fc-42b2-a5e0-3a44fad4704e", equity=100000.0, buying_power=100000.0, status="ACTIVE")

    with patch.object(settings, "alpaca_paper", True):
        status = validate_paper_execution_environment(trading_client=mock_client, account=account)
        assert status["paper_verified"] is True
        assert status["alpaca_paper_config"] is True


def test_paper_execution_safety_fails_when_paper_false():
    mock_client = MagicMock()
    mock_client._paper = True
    account = AccountSnapshot(account_id="1495837a-32fc-42b2-a5e0-3a44fad4704e", equity=100000.0, buying_power=100000.0, status="ACTIVE")

    with patch.object(settings, "alpaca_paper", False):
        with pytest.raises(PaperExecutionSafetyError, match="ALPACA_PAPER is False"):
            validate_paper_execution_environment(trading_client=mock_client, account=account)


def test_paper_execution_safety_fails_when_client_live():
    mock_client = MagicMock()
    mock_client._paper = False  # Connected to Live API endpoint
    account = AccountSnapshot(account_id="LIVE_ACCOUNT_ID", equity=100000.0, buying_power=100000.0, status="ACTIVE")

    with patch.object(settings, "alpaca_paper", True):
        with pytest.raises(PaperExecutionSafetyError, match="_paper is False"):
            validate_paper_execution_environment(trading_client=mock_client, account=account)


def test_paper_execution_safety_fails_when_account_inactive():
    mock_client = MagicMock()
    mock_client._paper = True
    account = AccountSnapshot(account_id="1495837a-32fc-42b2-a5e0-3a44fad4704e", equity=100000.0, buying_power=100000.0, status="SUSPENDED")

    with patch.object(settings, "alpaca_paper", True):
        with pytest.raises(PaperExecutionSafetyError, match="status is 'SUSPENDED'"):
            validate_paper_execution_environment(trading_client=mock_client, account=account)


def test_no_force_live_override_exists():
    """Verify that settings does not contain any force_live override."""
    assert not hasattr(settings, "force_live")
    assert not hasattr(settings, "override_safety")
