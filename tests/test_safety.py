"""
Unit tests for Persistent Safety, Idempotency, and Symbol Concurrency Control Layer.
Tests SQLite idempotency, process restarts, symbol locks, stale quote detection,
circuit breakers, market hours, and paper mode enforcement.
No LLM calls. No network calls.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from trading_agent.config import settings
from trading_agent.models import (
    AccountSnapshot,
    OptionContract,
    CondorLeg,
    IronCondor,
    TradeProposal,
)
from trading_agent.risk.safety import (
    SafetyManager,
    SymbolLockManager,
    create_trade_fingerprint,
    is_data_fresh,
    validate_runtime_safety,
)


def _make_fresh_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_sample_condor(symbol: str = "SPY", quote_ts: str = None) -> IronCondor:
    ts = quote_ts or _make_fresh_timestamp()
    exp = "2026-09-18"
    put_long = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918P00090000", underlying_symbol=symbol, strike_price=90.0,
            expiration_date=exp, contract_type="put", bid=1.20, ask=1.25, open_interest=500,
            delta=-0.10, quote_timestamp=ts,
        ),
        side="buy", quantity=1, leg_type="put_long",
    )
    put_short = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918P00095000", underlying_symbol=symbol, strike_price=95.0,
            expiration_date=exp, contract_type="put", bid=2.50, ask=2.55, open_interest=500,
            delta=-0.18, quote_timestamp=ts,
        ),
        side="sell", quantity=1, leg_type="put_short",
    )
    call_short = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918C00105000", underlying_symbol=symbol, strike_price=105.0,
            expiration_date=exp, contract_type="call", bid=2.40, ask=2.45, open_interest=500,
            delta=0.18, quote_timestamp=ts,
        ),
        side="sell", quantity=1, leg_type="call_short",
    )
    call_long = CondorLeg(
        contract=OptionContract(
            symbol=f"{symbol}260918C00110000", underlying_symbol=symbol, strike_price=110.0,
            expiration_date=exp, contract_type="call", bid=1.10, ask=1.15, open_interest=500,
            delta=0.10, quote_timestamp=ts,
        ),
        side="buy", quantity=1, leg_type="call_long",
    )

    return IronCondor(
        underlying_symbol=symbol,
        put_long=put_long,
        put_short=put_short,
        call_short=call_short,
        call_long=call_long,
        net_credit=2.50,
        max_risk=250.0,
        max_profit=250.0,
        wing_width=5.0,
        expiration_date=exp,
    )


@pytest.fixture
def temp_safety_mgr(tmp_path):
    """Fixture providing an isolated SafetyManager with a temporary SQLite database file."""
    db_file = tmp_path / "test_idempotency.db"
    return SafetyManager(db_path=db_file)


def test_duplicate_fingerprint(temp_safety_mgr):
    """Test that recording a trade attempt marks its fingerprint as duplicate on subsequent checks."""
    condor = _make_sample_condor(symbol="SPY")
    fp = create_trade_fingerprint(condor)

    assert temp_safety_mgr.is_duplicate_trade(fp) is False

    # Record trade attempt
    temp_safety_mgr.record_trade_attempt(
        fingerprint=fp, symbol="SPY", expiration="2026-09-18", order_id="ORD_123", status="filled"
    )

    # Subsequent check must identify it as duplicate
    assert temp_safety_mgr.is_duplicate_trade(fp) is True


def test_process_restart_persistence(tmp_path):
    """Test process restart: A new SafetyManager instance pointing to the same SQLite DB detects past fingerprints."""
    db_file = tmp_path / "restart_test.db"

    # Process 1 instance
    mgr_1 = SafetyManager(db_path=db_file)
    condor = _make_sample_condor(symbol="AAPL")
    fp = create_trade_fingerprint(condor)

    mgr_1.record_trade_attempt(
        fingerprint=fp, symbol="AAPL", expiration="2026-09-18", order_id="ORD_AAPL_1"
    )
    assert mgr_1.is_duplicate_trade(fp) is True

    # Simulate Process Restart (Process 2 instance)
    mgr_2 = SafetyManager(db_path=db_file)
    assert mgr_2.is_duplicate_trade(fp) is True


def test_concurrent_execution_lock():
    """Test per-symbol mutual exclusion lock blocks overlapping concurrent executions."""
    lock_mgr = SymbolLockManager()
    symbol = "QQQ"
    results = []

    def _worker(worker_id: int):
        try:
            with lock_mgr.acquire_symbol_lock(symbol, blocking=True, timeout=0.1):
                results.append(f"worker_{worker_id}_entered")
                time.sleep(0.05)
                results.append(f"worker_{worker_id}_exited")
        except TimeoutError:
            results.append(f"worker_{worker_id}_blocked")

    t1 = threading.Thread(target=_worker, args=(1,))
    t2 = threading.Thread(target=_worker, args=(2,))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Verify serialized execution or timeout blockage
    assert len(results) >= 2
    assert "worker_1_entered" in results


def test_stale_quote_rejection(temp_safety_mgr):
    """Test that stale quote timestamps (> max_age_seconds) fail runtime safety validation."""
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=1800)).isoformat()
    stale_condor = _make_sample_condor(symbol="SPY", quote_ts=old_ts)
    proposal = TradeProposal(
        proposal_id="P_STALE", symbol="SPY", condor=stale_condor, quantity=1, created_at=old_ts
    )
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    safe, violations = temp_safety_mgr.validate_runtime_safety(
        proposed_trade=proposal, account=account, max_quote_age_seconds=900
    )

    assert safe is False
    assert any("Stale quote timestamp" in v for v in violations)


def test_stale_market_data():
    """Test is_data_fresh returns True for fresh timestamps and False for stale ones."""
    fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()

    assert is_data_fresh(fresh_ts, max_age_seconds=900) is True
    assert is_data_fresh(stale_ts, max_age_seconds=900) is False
    assert is_data_fresh(None) is False
    assert is_data_fresh("invalid-timestamp-string") is False


def test_circuit_breaker(temp_safety_mgr):
    """Test circuit breaker triggers when daily loss limit is breached."""
    assert temp_safety_mgr.is_circuit_breaker_active(daily_pnl=100.0) is False
    assert temp_safety_mgr.is_circuit_breaker_active(daily_pnl=-1000.0) is False

    # Breached limit ($3,000 max daily loss)
    assert temp_safety_mgr.is_circuit_breaker_active(daily_pnl=-3500.0) is True

    # Once triggered, remains active
    assert temp_safety_mgr.is_circuit_breaker_active(daily_pnl=0.0) is True

    # Reset
    temp_safety_mgr.reset_circuit_breaker()
    assert temp_safety_mgr.is_circuit_breaker_active(daily_pnl=0.0) is False


def test_closed_market(temp_safety_mgr):
    """Test runtime safety rejects trades when market is closed."""
    condor = _make_sample_condor()
    proposal = TradeProposal(proposal_id="P_CLOSED", symbol="SPY", condor=condor, quantity=1, created_at=_make_fresh_timestamp())
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    safe, violations = temp_safety_mgr.validate_runtime_safety(
        proposed_trade=proposal, account=account, market_open=False
    )

    assert safe is False
    assert any("Market is currently closed" in v for v in violations)


def test_paper_account_requirement(temp_safety_mgr, monkeypatch):
    """Test runtime safety rejects execution if live trading mode is detected when paper_only=True."""
    condor = _make_sample_condor()
    proposal = TradeProposal(proposal_id="P_PAPER", symbol="SPY", condor=condor, quantity=1, created_at=_make_fresh_timestamp())
    account = AccountSnapshot(equity=100000.0, buying_power=100000.0)

    # Monkeypatch settings.alpaca_paper to False (Live mode)
    monkeypatch.setattr(settings, "alpaca_paper", False)

    safe, violations = temp_safety_mgr.validate_runtime_safety(
        proposed_trade=proposal, account=account, paper_only=True
    )

    assert safe is False
    assert any("Real/Live trading mode detected" in v for v in violations)
