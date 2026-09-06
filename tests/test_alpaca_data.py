"""
Unit tests for AlpacaDataFetcher read operations, retries, and option chain normalization.
All SDK calls are mocked. No live/paper network requests are sent during pytest.
"""

from unittest.mock import MagicMock, patch
import pytest

from trading_agent.data import (
    AlpacaDataFetcher,
    AlpacaAuthError,
    AlpacaRateLimitError,
    AlpacaDataError,
)
from trading_agent.models import AccountSnapshot, OptionChainSnapshot, OptionContract


@pytest.fixture
def fetcher():
    """Fixture returning an AlpacaDataFetcher instance configured for testing."""
    return AlpacaDataFetcher(
        api_key="test_alpaca_key", secret_key="test_alpaca_secret", paper=True
    )


def test_get_account_snapshot_mapping(fetcher):
    """Test get_account_snapshot accurately maps TradingClient response to AccountSnapshot model."""
    mock_acct = MagicMock()
    mock_acct.id = "ACC_ALPACA_999"
    mock_acct.cash = "50000.0"
    mock_acct.buying_power = "100000.0"
    mock_acct.equity = "105000.0"
    mock_acct.portfolio_value = "105000.0"
    mock_acct.status = "ACTIVE"
    mock_acct.currency = "USD"

    mock_trading_client = MagicMock()
    mock_trading_client.get_account.return_value = mock_acct
    fetcher._trading_client = mock_trading_client

    snapshot = fetcher.get_account_snapshot()

    assert isinstance(snapshot, AccountSnapshot)
    assert snapshot.account_id == "ACC_ALPACA_999"
    assert snapshot.cash == 50000.0
    assert snapshot.buying_power == 100000.0
    assert snapshot.equity == 105000.0
    assert snapshot.status == "ACTIVE"


def test_get_market_clock_mapping(fetcher):
    """Test get_market_clock maps clock timestamps and market open status."""
    mock_clock = MagicMock()
    mock_clock.is_open = True
    mock_clock.timestamp.isoformat.return_value = "2026-08-28T14:30:00Z"
    mock_clock.next_open.isoformat.return_value = "2026-08-31T13:30:00Z"
    mock_clock.next_close.isoformat.return_value = "2026-08-28T20:00:00Z"

    mock_trading_client = MagicMock()
    mock_trading_client.get_clock.return_value = mock_clock
    fetcher._trading_client = mock_trading_client

    clock = fetcher.get_market_clock()

    assert clock["is_open"] is True
    assert clock["timestamp"] == "2026-08-28T14:30:00Z"
    assert clock["next_open"] == "2026-08-31T13:30:00Z"


def test_get_option_chain_normalization_missing_greeks(fetcher):
    """Test get_option_chain normalizes contracts and preserves missing Greeks/IV as None (NEVER replacing with 0.0)."""
    # Mock Option contract 1 with full Greeks
    mock_snap_1 = MagicMock()
    mock_snap_1.strike_price = 90.0
    mock_snap_1.expiration_date = "2026-09-18"
    mock_snap_1.open_interest = 100
    mock_snap_1.latest_quote.bid_price = 1.50
    mock_snap_1.latest_quote.ask_price = 1.55
    mock_snap_1.latest_quote.timestamp.isoformat.return_value = "2026-08-28T14:30:00Z"
    mock_snap_1.latest_trade.price = 1.52
    mock_snap_1.latest_trade.size = 10
    mock_snap_1.implied_volatility = 0.25
    mock_snap_1.greeks.delta = -0.15
    mock_snap_1.greeks.gamma = 0.02
    mock_snap_1.greeks.theta = -0.05
    mock_snap_1.greeks.vega = 0.10

    # Mock Option contract 2 with MISSING Greeks & IV (None)
    mock_snap_2 = MagicMock()
    mock_snap_2.strike_price = 110.0
    mock_snap_2.expiration_date = "2026-09-18"
    mock_snap_2.open_interest = 50
    mock_snap_2.latest_quote.bid_price = 1.10
    mock_snap_2.latest_quote.ask_price = 1.15
    mock_snap_2.latest_quote.timestamp.isoformat.return_value = "2026-08-28T14:30:00Z"
    mock_snap_2.latest_trade.price = 1.12
    mock_snap_2.latest_trade.size = 5
    mock_snap_2.implied_volatility = None  # Missing IV
    mock_snap_2.greeks = None               # Missing Greeks object

    mock_chain_resp = {
        "SPY260918P00090000": mock_snap_1,
        "SPY260918C00110000": mock_snap_2,
    }

    mock_option_client = MagicMock()
    mock_option_client.get_option_chain.return_value = mock_chain_resp
    fetcher._option_data_client = mock_option_client

    chain_snapshot = fetcher.get_option_chain("SPY")

    assert isinstance(chain_snapshot, OptionChainSnapshot)
    assert len(chain_snapshot.contracts) == 2

    c1 = next(c for c in chain_snapshot.contracts if c.symbol == "SPY260918P00090000")
    assert c1.delta == -0.15
    assert c1.implied_volatility == 0.25

    c2 = next(c for c in chain_snapshot.contracts if c.symbol == "SPY260918C00110000")
    # Verify missing Greeks & IV are explicitly None and NOT 0.0
    assert c2.delta is None
    assert c2.gamma is None
    assert c2.theta is None
    assert c2.vega is None
    assert c2.implied_volatility is None


def test_auth_failure_immediate_raise(fetcher):
    """Test that authentication failure (401/403) raises AlpacaAuthError immediately without retrying indefinitely."""
    mock_trading_client = MagicMock()
    mock_trading_client.get_account.side_effect = Exception("401 Unauthorized API key invalid")
    fetcher._trading_client = mock_trading_client

    with pytest.raises(AlpacaAuthError) as exc_info:
        fetcher.get_account_snapshot()

    assert any(term in str(exc_info.value).lower() for term in ["401", "unauthorized", "auth"])
    # Check that call was made only ONCE (no retry on auth error)
    assert mock_trading_client.get_account.call_count == 1


def test_transient_retry_success(fetcher):
    """Test that transient network or rate-limit failure retries and succeeds on subsequent attempt."""
    mock_acct = MagicMock()
    mock_acct.id = "ACC_RETRY_SUCCESS"
    mock_acct.cash = "100000.0"
    mock_acct.buying_power = "100000.0"
    mock_acct.equity = "100000.0"
    mock_acct.portfolio_value = "100000.0"
    mock_acct.status = "ACTIVE"

    mock_trading_client = MagicMock()
    # Fail on first call with 429 rate limit, succeed on second call
    mock_trading_client.get_account.side_effect = [
        Exception("429 Too Many Requests"),
        mock_acct,
    ]
    fetcher._trading_client = mock_trading_client

    snapshot = fetcher.get_account_snapshot()

    assert snapshot.account_id == "ACC_RETRY_SUCCESS"
    assert mock_trading_client.get_account.call_count == 2
