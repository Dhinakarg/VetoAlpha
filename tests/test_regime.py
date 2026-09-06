"""
Deterministic Test Suite for Market Regime Engine.
"""

from typing import List, Dict, Any
import numpy as np
import pandas as pd
import pytest

from trading_agent.strategy.regime import MarketRegime, detect_market_regime


def _create_synthetic_bars(prices: List[float]) -> pd.DataFrame:
    """Helper to generate a realistic OHLCV DataFrame from close prices."""
    records = []
    for i, p in enumerate(prices):
        records.append({
            "open": p - 0.2,
            "high": p + 0.8,
            "low": p - 0.8,
            "close": p,
            "volume": 10000 + i * 100,
        })
    return pd.DataFrame(records)


@pytest.fixture
def bullish_bars() -> pd.DataFrame:
    # 60 bars of steadily rising prices from $500 to $580
    prices = [500.0 + (i * 1.35) for i in range(60)]
    return _create_synthetic_bars(prices)


@pytest.fixture
def bearish_bars() -> pd.DataFrame:
    # 60 bars of steadily falling prices from $580 to $500
    prices = [580.0 - (i * 1.35) for i in range(60)]
    return _create_synthetic_bars(prices)


@pytest.fixture
def range_bars() -> pd.DataFrame:
    # 60 bars of oscillating prices in range $545 - $555
    prices = [550.0 + 3.0 * np.sin(i * 0.4) for i in range(60)]
    return _create_synthetic_bars(prices)


# -----------------------------------------------------------------------------
# 1. Bullish Trending Market
# -----------------------------------------------------------------------------
def test_bullish_trending_market(bullish_bars):
    regime = detect_market_regime(bars=bullish_bars, iv_rank=45.0)

    assert regime.trend == "bullish"
    assert regime.volatility == "normal"
    assert regime.confidence >= 0.70
    assert "bull_put_spread" in regime.eligible_strategies
    assert "iron_condor" not in regime.eligible_strategies
    assert regime.adx is not None and regime.adx >= 25.0
    assert regime.current_price > regime.sma_20


# -----------------------------------------------------------------------------
# 2. Bearish Trending Market
# -----------------------------------------------------------------------------
def test_bearish_trending_market(bearish_bars):
    regime = detect_market_regime(bars=bearish_bars, iv_rank=55.0)

    assert regime.trend == "bearish"
    assert regime.volatility == "normal"
    assert regime.confidence >= 0.70
    assert "bear_call_spread" in regime.eligible_strategies
    assert "iron_condor" not in regime.eligible_strategies
    assert regime.adx is not None and regime.adx >= 25.0
    assert regime.current_price < regime.sma_20


# -----------------------------------------------------------------------------
# 3. Range-Bound Market
# -----------------------------------------------------------------------------
def test_range_bound_market(range_bars):
    regime = detect_market_regime(bars=range_bars, iv_rank=40.0)

    assert regime.trend == "range"
    assert regime.volatility == "normal"
    assert regime.confidence >= 0.70
    assert "iron_condor" in regime.eligible_strategies
    assert "bull_put_spread" in regime.eligible_strategies
    assert "bear_call_spread" in regime.eligible_strategies
    assert regime.adx is not None and regime.adx < 25.0


# -----------------------------------------------------------------------------
# 4. Low IV State (< 20.0%)
# -----------------------------------------------------------------------------
def test_low_iv_state(range_bars):
    regime = detect_market_regime(bars=range_bars, iv_rank=12.5)

    assert regime.volatility == "low"
    # Credit selling must be ineligible in low IV
    assert not any(s in regime.eligible_strategies for s in ["iron_condor", "bull_put_spread", "bear_call_spread"])
    assert any("Low volatility state" in r for r in regime.reasons)


# -----------------------------------------------------------------------------
# 5. High IV State (> 70.0%)
# -----------------------------------------------------------------------------
def test_high_iv_state(range_bars):
    regime = detect_market_regime(bars=range_bars, iv_rank=82.0)

    assert regime.volatility == "high"
    assert "iron_condor" in regime.eligible_strategies
    assert any("High volatility state" in r for r in regime.reasons)


# -----------------------------------------------------------------------------
# 6. Missing IV History (iv_rank is None)
# -----------------------------------------------------------------------------
def test_missing_iv_history(range_bars):
    regime = detect_market_regime(bars=range_bars, iv_rank=None)

    assert regime.volatility == "low"
    assert not any(s in regime.eligible_strategies for s in ["iron_condor", "bull_put_spread", "bear_call_spread"])
    assert any("Missing historical IV Rank" in r for r in regime.reasons)


# -----------------------------------------------------------------------------
# 7. Insufficient Bars (< 20 bars)
# -----------------------------------------------------------------------------
def test_insufficient_bars():
    short_df = _create_synthetic_bars([550.0 + i for i in range(10)])
    regime = detect_market_regime(bars=short_df, iv_rank=45.0)

    assert regime.confidence == 0.0
    assert len(regime.eligible_strategies) == 0
    assert any("Insufficient price bars" in r for r in regime.reasons)


# -----------------------------------------------------------------------------
# 8. Ambiguous Regime (Trending ADX but conflicting moving averages)
# -----------------------------------------------------------------------------
def test_ambiguous_regime():
    # Long decline followed by sudden sharp pop (Price > SMA20 but SMA20 < SMA50)
    prices = [580.0 - i * 1.5 for i in range(50)] + [510.0 + i * 2.5 for i in range(10)]
    df = _create_synthetic_bars(prices)
    regime = detect_market_regime(bars=df, iv_rank=40.0)

    # Defaults to range with documented reasons
    assert regime.trend == "range"
    assert any("Ambiguous directional bias" in r or "Range-bound regime" in r for r in regime.reasons)


# -----------------------------------------------------------------------------
# 9. Determinism: No LLM / Network Dependencies
# -----------------------------------------------------------------------------
def test_no_llm_calls_in_regime_module():
    import trading_agent.strategy.regime as regime_mod
    # Verify no Featherless or LLM clients imported
    assert not hasattr(regime_mod, "FeatherlessClient")
    assert not hasattr(regime_mod, "OpenAI")
