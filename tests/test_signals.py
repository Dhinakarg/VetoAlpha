"""
Synthetic unit tests for deterministic strategy signals, indicators, and volatility gate.
No LLM calls. No network calls.
"""

import numpy as np
import pandas as pd
import pytest

from trading_agent.strategy import (
    calculate_adx,
    is_range_bound,
    calculate_bollinger_width,
    bollinger_width_percentile,
    extract_atm_iv,
    calculate_iv_rank,
    regime_and_volatility_gate,
)
from trading_agent.models import OptionContract, OptionChainSnapshot


def _generate_synthetic_bars(trend: float = 0.0, noise: float = 0.1, length: int = 60) -> pd.DataFrame:
    """Generate synthetic OHLC bars for testing."""
    np.random.seed(42)
    prices = []

    for i in range(length):
        if trend == 0.0:
            # Range-bound sine wave oscillation around 100
            p = 100.0 + 0.5 * np.sin(i / 2.0) + np.random.normal(0, noise)
        else:
            # Strong trend
            p = 100.0 + (i * trend) + np.random.normal(0, noise)
        prices.append(p)

    prices_arr = np.array(prices)
    highs = prices_arr + 0.3
    lows = prices_arr - 0.3
    opens = prices_arr - 0.05
    closes = prices_arr

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.random.randint(1000, 5000, length),
    })


def test_adx_range_bound_vs_trending():
    """Test that calculate_adx yields low ADX for range-bound data and high ADX for trending data."""
    range_bars = _generate_synthetic_bars(trend=0.0, noise=0.2, length=60)
    trend_bars = _generate_synthetic_bars(trend=1.5, noise=0.1, length=60)

    adx_range = calculate_adx(range_bars, period=14)
    adx_trend = calculate_adx(trend_bars, period=14)

    assert adx_range is not None
    assert adx_trend is not None
    assert adx_range < adx_trend
    assert adx_range < 25.0


def test_is_range_bound():
    """Test is_range_bound threshold evaluation."""
    range_bars = _generate_synthetic_bars(trend=0.0, noise=0.2, length=60)
    trend_bars = _generate_synthetic_bars(trend=1.5, noise=0.1, length=60)

    assert is_range_bound(range_bars, adx_threshold=25.0) is True
    assert is_range_bound(trend_bars, adx_threshold=25.0) is False

    # Short bars edge case (< period * 2)
    short_bars = range_bars.head(10)
    assert is_range_bound(short_bars) is False


def test_bollinger_width_and_percentile():
    """Test Bollinger Bandwidth and percentile calculations."""
    bars = _generate_synthetic_bars(trend=0.0, noise=1.0, length=100)

    width = calculate_bollinger_width(bars, period=20)
    assert width is not None
    assert width > 0.0

    percentile = bollinger_width_percentile(bars, lookback=90, period=20)
    assert percentile is not None
    assert 0.0 <= percentile <= 100.0


def test_extract_atm_iv():
    """Test extracting near-the-money implied volatility from option chain contracts."""
    underlying_price = 100.0
    contracts = [
        OptionContract(
            symbol="SPY90P", underlying_symbol="SPY", strike_price=90.0, expiration_date="2026-09-18",
            contract_type="put", implied_volatility=0.35
        ),
        OptionContract(
            symbol="SPY100C", underlying_symbol="SPY", strike_price=100.0, expiration_date="2026-09-18",
            contract_type="call", implied_volatility=0.20
        ),
        OptionContract(
            symbol="SPY100P", underlying_symbol="SPY", strike_price=100.0, expiration_date="2026-09-18",
            contract_type="put", implied_volatility=0.22
        ),
        OptionContract(
            symbol="SPY110C", underlying_symbol="SPY", strike_price=110.0, expiration_date="2026-09-18",
            contract_type="call", implied_volatility=0.18
        ),
    ]

    chain = OptionChainSnapshot(
        underlying_symbol="SPY", underlying_price=underlying_price, timestamp="2026-08-28T00:00:00Z", contracts=contracts
    )

    atm_iv = extract_atm_iv(chain, underlying_price)
    assert atm_iv is not None
    # Mean of 100 strike call (0.20) and 100 strike put (0.22) = 0.21
    assert pytest.approx(atm_iv, abs=1e-3) == 0.21

    # Edge case: missing / None IV
    missing_iv_contracts = [
        OptionContract(
            symbol="SPY100C", underlying_symbol="SPY", strike_price=100.0, expiration_date="2026-09-18",
            contract_type="call", implied_volatility=None
        )
    ]
    assert extract_atm_iv(missing_iv_contracts, underlying_price) is None


def test_calculate_iv_rank():
    """Test IV Rank calculation under normal and zero-range conditions."""
    history = [0.15 + (i * 0.02) for i in range(15)]  # 0.15 to 0.43 (mid = 0.29)

    # Current = 0.29 (middle of 0.15 to 0.43 -> 50% IV rank)
    rank = calculate_iv_rank(0.29, history, min_required_days=15)
    assert rank == 50.0

    # Current = 0.43 (max -> 100% IV rank)
    rank_max = calculate_iv_rank(0.43, history, min_required_days=15)
    assert rank_max == 100.0

    # Zero historical range (max == min)
    zero_range_history = [0.20] * 15
    rank_zero_range = calculate_iv_rank(0.20, zero_range_history, min_required_days=15)
    assert rank_zero_range == 50.0  # Safe zero division fallback

    # Insufficient history points
    assert calculate_iv_rank(0.20, [0.20] * 5, min_required_days=15) is None


def test_regime_and_volatility_gate_eligible():
    """Test regime_and_volatility_gate returns eligible=True when all metrics pass."""
    range_bars = _generate_synthetic_bars(trend=0.0, noise=0.2, length=60)
    underlying_price = 100.0

    contracts = [
        OptionContract(
            symbol="SPY100C", underlying_symbol="SPY", strike_price=100.0, expiration_date="2026-09-18",
            contract_type="call", implied_volatility=0.25
        ),
        OptionContract(
            symbol="SPY100P", underlying_symbol="SPY", strike_price=100.0, expiration_date="2026-09-18",
            contract_type="put", implied_volatility=0.25
        ),
    ]

    iv_history = [0.15, 0.18, 0.20, 0.22, 0.25] * 5  # 25 points, min=0.15, max=0.25, current=0.25 -> IV rank=100%

    config = {"adx_threshold": 25.0, "min_iv_rank": 30.0, "min_historical_iv_points": 15}

    res = regime_and_volatility_gate(
        bars=range_bars,
        option_chain=contracts,
        underlying_price=underlying_price,
        iv_history=iv_history,
        config=config,
    )

    assert res["eligible"] is True
    assert res["adx"] is not None
    assert res["adx"] < 25.0
    assert res["current_atm_iv"] == 0.25
    assert res["iv_rank"] == 100.0
    assert len(res["reasons"]) == 0


def test_regime_and_volatility_gate_rejections():
    """Test regime_and_volatility_gate correctly captures rejection reasons."""
    trend_bars = _generate_synthetic_bars(trend=1.5, noise=0.1, length=60)
    underlying_price = 100.0

    contracts = [
        OptionContract(
            symbol="SPY100C", underlying_symbol="SPY", strike_price=100.0, expiration_date="2026-09-18",
            contract_type="call", implied_volatility=0.16
        ),
    ]

    # Current IV 0.16, min=0.15, max=0.35 -> IV rank = (0.16-0.15)/0.20 * 100 = 5% (below min_iv_rank 30%)
    iv_history = [0.15, 0.20, 0.25, 0.30, 0.35] * 4

    config = {"adx_threshold": 25.0, "min_iv_rank": 30.0, "min_historical_iv_points": 15}

    res = regime_and_volatility_gate(
        bars=trend_bars,
        option_chain=contracts,
        underlying_price=underlying_price,
        iv_history=iv_history,
        config=config,
    )

    assert res["eligible"] is False
    assert len(res["reasons"]) >= 2
    reasons_text = " ".join(res["reasons"]).lower()
    assert "trending" in reasons_text
    assert "iv rank" in reasons_text
