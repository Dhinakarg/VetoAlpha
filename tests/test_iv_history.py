"""
Unit tests for Historical IV Store, IV Rank calculations, cold-start status,
option-chain DTE optimization, and quote freshness.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from trading_agent.data.iv_store import IVHistoryStore
from trading_agent.strategy.signals import calculate_iv_rank, regime_and_volatility_gate
from trading_agent.models import OptionContract, OptionChainSnapshot
from trading_agent.risk.risk_gate import evaluate_trade
from trading_agent.data.alpaca_data import AlpacaDataFetcher


# -----------------------------------------------------------------------------
# 1. Zero IV History
# -----------------------------------------------------------------------------
def test_zero_iv_history(tmp_path):
    store = IVHistoryStore(db_path=tmp_path / "iv_zero.db")
    status = store.get_iv_history_status(symbol="SPY", current_atm_iv=0.22, min_required=15)
    
    assert status["history_days"] == 0
    assert status["status"] == "INSUFFICIENT_HISTORY"
    assert status["iv_rank"] is None


# -----------------------------------------------------------------------------
# 2. 1 Historical Day
# -----------------------------------------------------------------------------
def test_one_historical_day(tmp_path):
    store = IVHistoryStore(db_path=tmp_path / "iv_one.db")
    store.record_daily_iv(symbol="SPY", trading_date="2026-08-01", atm_iv=0.20, underlying_price=500.0)
    
    status = store.get_iv_history_status(symbol="SPY", current_atm_iv=0.22, min_required=15, current_date="2026-08-02")
    assert status["history_days"] == 1
    assert status["status"] == "INSUFFICIENT_HISTORY"
    assert status["iv_rank"] is None


# -----------------------------------------------------------------------------
# 3. 14 Historical Days
# -----------------------------------------------------------------------------
def test_fourteen_historical_days(tmp_path):
    store = IVHistoryStore(db_path=tmp_path / "iv_fourteen.db")
    for i in range(1, 15):
        date_str = f"2026-08-{i:02d}"
        store.record_daily_iv(symbol="SPY", trading_date=date_str, atm_iv=0.15 + (i * 0.01), underlying_price=500.0)
    
    status = store.get_iv_history_status(symbol="SPY", current_atm_iv=0.22, min_required=15, current_date="2026-08-15")
    assert status["history_days"] == 14
    assert status["status"] == "INSUFFICIENT_HISTORY"
    assert status["iv_rank"] is None


# -----------------------------------------------------------------------------
# 4. Exactly 15 Historical Days
# -----------------------------------------------------------------------------
def test_exactly_fifteen_historical_days(tmp_path):
    store = IVHistoryStore(db_path=tmp_path / "iv_fifteen.db")
    for i in range(1, 16):
        date_str = f"2026-08-{i:02d}"
        store.record_daily_iv(symbol="SPY", trading_date=date_str, atm_iv=0.10 + (i * 0.01), underlying_price=500.0)
    
    status = store.get_iv_history_status(symbol="SPY", current_atm_iv=0.20, min_required=15, current_date="2026-08-16")
    assert status["history_days"] == 15
    assert status["status"] == "READY"
    assert status["iv_rank"] is not None
    assert 0.0 <= status["iv_rank"] <= 100.0


# -----------------------------------------------------------------------------
# 5. 60 Historical Days
# -----------------------------------------------------------------------------
def test_sixty_historical_days(tmp_path):
    store = IVHistoryStore(db_path=tmp_path / "iv_sixty.db")
    base_date = datetime(2026, 6, 1)
    for i in range(60):
        d_str = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        store.record_daily_iv(symbol="SPY", trading_date=d_str, atm_iv=0.15 + 0.05 * np.sin(i), underlying_price=500.0)
    
    status = store.get_iv_history_status(symbol="SPY", current_atm_iv=0.18, min_required=15, preferred_days=60, current_date="2026-08-10")
    assert status["history_days"] == 60
    assert status["status"] == "READY"
    assert status["iv_rank"] is not None
    assert 0.0 <= status["iv_rank"] <= 100.0


# -----------------------------------------------------------------------------
# 6. Duplicate Scans on Same Trading Day (Upsert Verification)
# -----------------------------------------------------------------------------
def test_duplicate_scans_on_same_day(tmp_path):
    store = IVHistoryStore(db_path=tmp_path / "iv_dup.db")
    date_str = "2026-08-28"

    # Scan 1 at 09:35
    store.record_daily_iv(symbol="SPY", trading_date=date_str, atm_iv=0.20, underlying_price=500.0, timestamp_utc="2026-08-28T13:35:00Z")
    assert store.get_history_count("SPY") == 1

    # Scan 2 at 11:00 (updated IV)
    store.record_daily_iv(symbol="SPY", trading_date=date_str, atm_iv=0.22, underlying_price=502.0, timestamp_utc="2026-08-28T15:00:00Z")
    assert store.get_history_count("SPY") == 1

    # Verify canonical record has latest value
    history = store.get_historical_daily_iv("SPY", lookback_days=10)
    assert len(history) == 1
    assert history[0] == 0.22


# -----------------------------------------------------------------------------
# 7. Current IV Missing
# -----------------------------------------------------------------------------
def test_current_iv_missing():
    history = [0.15 + i * 0.01 for i in range(20)]
    assert calculate_iv_rank(current_atm_iv=None, historical_iv=history, min_required_days=15) is None
    assert calculate_iv_rank(current_atm_iv=0.0, historical_iv=history, min_required_days=15) is None
    assert calculate_iv_rank(current_atm_iv=-0.10, historical_iv=history, min_required_days=15) is None


# -----------------------------------------------------------------------------
# 8. Historical IV Containing NaN
# -----------------------------------------------------------------------------
def test_historical_iv_containing_nan():
    # 20 points with 4 NaNs -> 16 valid points
    history_with_nans = [0.15, np.nan, 0.18, 0.20, np.nan, 0.22, 0.25, 0.19, 0.21, 0.23,
                         np.nan, 0.24, 0.26, 0.27, 0.28, 0.29, 0.30, np.nan, 0.31, 0.32]
    
    # 16 valid points >= 15 min_required -> calculates valid rank
    rank = calculate_iv_rank(current_atm_iv=0.25, historical_iv=history_with_nans, min_required_days=15)
    assert rank is not None
    assert 0.0 <= rank <= 100.0

    # 16 valid points < 18 min_required -> returns None
    rank_strict = calculate_iv_rank(current_atm_iv=0.25, historical_iv=history_with_nans, min_required_days=18)
    assert rank_strict is None


# -----------------------------------------------------------------------------
# 9. Zero IV Range (Max == Min)
# -----------------------------------------------------------------------------
def test_zero_iv_range():
    flat_history = [0.20] * 20
    rank = calculate_iv_rank(current_atm_iv=0.20, historical_iv=flat_history, min_required_days=15)
    assert rank == 50.0


# -----------------------------------------------------------------------------
# 10. Stale Option Quote Rejection
# -----------------------------------------------------------------------------
def test_stale_option_quote_rejection():
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    contract = OptionContract(
        symbol="SPY260918P00500000", underlying_symbol="SPY", strike_price=500.0,
        expiration_date="2026-09-18", contract_type="put", bid=2.0, ask=2.1,
        quote_timestamp=stale_time,
    )
    
    # Evaluate in Risk Gate Rule 11
    from trading_agent.models import IronCondor, CondorLeg, AccountSnapshot
    leg = CondorLeg(contract=contract, side="buy", quantity=1, leg_type="put_long")
    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg, put_short=leg, call_short=leg, call_long=leg,
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18"
    )
    proposal = {"proposal_id": "P_STALE", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    decision = evaluate_trade(proposed_trade=proposal, account=account, config={"MAX_QUOTE_AGE_SECONDS": 900})
    assert decision.approved is False
    assert any("Rule 11" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 11. Valid Option Quote Acceptance
# -----------------------------------------------------------------------------
def test_valid_fresh_option_quote_acceptance():
    fresh_time = datetime.now(timezone.utc).isoformat()
    contract = OptionContract(
        symbol="SPY260918P00500000", underlying_symbol="SPY", strike_price=500.0,
        expiration_date="2026-09-18", contract_type="put", bid=2.0, ask=2.1, open_interest=500,
        delta=-0.15, quote_timestamp=fresh_time,
    )
    from trading_agent.models import IronCondor, CondorLeg, AccountSnapshot
    leg = CondorLeg(contract=contract, side="buy", quantity=1, leg_type="put_long")
    condor = IronCondor(
        underlying_symbol="SPY", put_long=leg, put_short=leg, call_short=leg, call_long=leg,
        net_credit=2.0, max_risk=300.0, max_profit=200.0, wing_width=5.0, expiration_date="2026-09-18"
    )
    proposal = {"proposal_id": "P_FRESH", "symbol": "SPY", "condor": condor, "quantity": 1}
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    decision = evaluate_trade(proposed_trade=proposal, account=account, config={"MAX_QUOTE_AGE_SECONDS": 900})
    # Confirm Rule 11 did not reject
    assert not any("Rule 11" in r for r in decision.rejections)


# -----------------------------------------------------------------------------
# 12. DTE Filtering in Option Chain Request
# -----------------------------------------------------------------------------
def test_dte_filtering_in_option_chain_request():
    fetcher = AlpacaDataFetcher()
    mock_option_client = MagicMock()
    mock_option_client.get_option_chain.return_value = {}
    fetcher._option_data_client = mock_option_client

    fetcher.get_option_chain(symbol="SPY", expiration_gte="2026-09-15", expiration_lte="2026-10-10")

    mock_option_client.get_option_chain.assert_called_once()
    call_args = mock_option_client.get_option_chain.call_args[0][0]
    assert call_args.underlying_symbol == "SPY"
    assert call_args.expiration_date_gte == "2026-09-15"
    assert call_args.expiration_date_lte == "2026-10-10"


# -----------------------------------------------------------------------------
# 13. Chain Optimization Preserves Required Strikes
# -----------------------------------------------------------------------------
def test_chain_optimization_preserves_required_strikes():
    from trading_agent.strategy.condor_builder import IronCondorBuilder

    now_iso = datetime.now(timezone.utc).isoformat()
    exp_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")

    # Construct candidate contracts spanning deltas 0.10 to 0.25 and 5-point wings
    contracts = [
        OptionContract(symbol="SPY_P480", underlying_symbol="SPY", strike_price=480.0, expiration_date=exp_date,
                       contract_type="put", bid=1.10, ask=1.15, delta=-0.10, open_interest=300, quote_timestamp=now_iso),
        OptionContract(symbol="SPY_P485", underlying_symbol="SPY", strike_price=485.0, expiration_date=exp_date,
                       contract_type="put", bid=2.20, ask=2.25, delta=-0.18, open_interest=300, quote_timestamp=now_iso),
        OptionContract(symbol="SPY_C515", underlying_symbol="SPY", strike_price=515.0, expiration_date=exp_date,
                       contract_type="call", bid=2.10, ask=2.15, delta=0.18, open_interest=300, quote_timestamp=now_iso),
        OptionContract(symbol="SPY_C520", underlying_symbol="SPY", strike_price=520.0, expiration_date=exp_date,
                       contract_type="call", bid=1.05, ask=1.10, delta=0.10, open_interest=300, quote_timestamp=now_iso),
    ]
    chain = OptionChainSnapshot(underlying_symbol="SPY", underlying_price=500.0, timestamp=now_iso, contracts=contracts)

    builder = IronCondorBuilder()
    condor = builder.build_condor(option_chain=chain, underlying_price=500.0, target_short_delta=0.18, wing_width=5.0)

    assert condor is not None
    assert condor.put_short.contract.strike_price == 485.0
    assert condor.put_long.contract.strike_price == 480.0
    assert condor.call_short.contract.strike_price == 515.0
    assert condor.call_long.contract.strike_price == 520.0
    assert condor.net_credit > 0
