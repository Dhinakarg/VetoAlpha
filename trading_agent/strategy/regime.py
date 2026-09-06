"""
Deterministic Market Regime Engine.
Decides market trend, volatility state, regime classification, and eligible strategy families.
Contains NO LLM calls, NO network calls, and strictly fails closed.
"""

import logging
from typing import Dict, List, Optional, Any, Union, Literal
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from trading_agent.strategy.signals import (
    calculate_adx,
    calculate_bollinger_width,
    bollinger_width_percentile,
    _to_dataframe,
)

logger = logging.getLogger(__name__)


class MarketRegime(BaseModel):
    """Deterministic Market Regime Classification."""

    trend: Literal["bullish", "bearish", "range"]
    volatility: Literal["low", "normal", "high"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    regime_class: Optional[Literal["STRONG_BULL", "BULL", "RANGE", "BEAR", "STRONG_BEAR", "UNKNOWN", "REGIME_TRANSITION"]] = "RANGE"
    reasons: List[str] = Field(default_factory=list)
    adx: Optional[float] = None
    bb_width_percentile: Optional[float] = None
    iv_rank: Optional[float] = None
    current_price: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    eligible_strategies: List[str] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


def classify_market_regime(
    bars: Union[pd.DataFrame, List[Dict[str, Any]], List[Any]],
    iv_rank: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Deterministic market regime classifier returning one of:
    STRONG_BULL, BULL, RANGE, BEAR, STRONG_BEAR, UNKNOWN.
    """
    df = _to_dataframe(bars)
    if len(df) < 20 or not {"high", "low", "close"}.issubset(df.columns):
        return "UNKNOWN"

    cfg = dict(config or {})
    range_adx_max = float(cfg.get("RANGE_ADX_MAX", 25.0))
    strong_adx_min = float(cfg.get("STRONG_TREND_ADX_MIN", 35.0))

    adx = calculate_adx(df)
    if adx is None:
        return "UNKNOWN"

    close_series = df["close"].astype(float)
    current_price = float(close_series.iloc[-1])
    sma_20 = float(close_series.rolling(20).mean().iloc[-1])
    sma_50 = float(close_series.rolling(50).mean().iloc[-1]) if len(df) >= 50 else sma_20

    if adx < range_adx_max:
        return "RANGE"

    price_above_sma20 = current_price > sma_20
    price_above_sma50 = current_price > sma_50

    if price_above_sma20 and price_above_sma50:
        if adx >= strong_adx_min and sma_20 > sma_50:
            return "STRONG_BULL"
        return "BULL"
    elif (not price_above_sma20) and (not price_above_sma50):
        if adx >= strong_adx_min and sma_20 < sma_50:
            return "STRONG_BEAR"
        return "BEAR"
    else:
        return "RANGE"


def detect_market_regime(
    bars: Union[pd.DataFrame, List[Dict[str, Any]], List[Any]],
    iv_rank: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
) -> MarketRegime:
    """Detect deterministic market regime from price bars and IV rank."""
    cfg = dict(config or {})
    range_adx_max = float(cfg.get("RANGE_ADX_MAX", 25.0))
    trend_adx_min = float(cfg.get("TREND_ADX_MIN", 25.0))
    strong_adx_min = float(cfg.get("STRONG_TREND_ADX_MIN", 35.0))
    low_iv_rank = float(cfg.get("LOW_IV_RANK", 20.0))
    high_iv_rank = float(cfg.get("HIGH_IV_RANK", 70.0))

    df = _to_dataframe(bars)
    reasons: List[str] = []

    # 1. Fail Closed on Insufficient Price Bars
    if len(df) < 20 or not {"high", "low", "close"}.issubset(df.columns):
        reasons.append(f"Insufficient price bars (count: {len(df)}, required: >= 20).")
        vol_state: Literal["low", "normal", "high"] = "low"
        if iv_rank is not None:
            if iv_rank > high_iv_rank:
                vol_state = "high"
            elif iv_rank >= low_iv_rank:
                vol_state = "normal"

        return MarketRegime(
            trend="range",
            volatility=vol_state,
            confidence=0.0,
            regime_class="UNKNOWN",
            reasons=reasons,
            iv_rank=iv_rank,
            eligible_strategies=[],
            diagnostics={"error": "insufficient_bars", "bar_count": len(df)},
        )

    close_series = df["close"].astype(float)
    current_price = float(close_series.iloc[-1])
    sma_20 = float(close_series.rolling(20).mean().iloc[-1])
    sma_50 = float(close_series.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None

    # Calculate 5-bar SMA20 slope (% change over 5 bars)
    sma20_series = close_series.rolling(20).mean()
    sma20_slope = ((sma20_series.iloc[-1] - sma20_series.iloc[-6]) / sma20_series.iloc[-6]) * 100.0 if len(sma20_series) >= 6 and sma20_series.iloc[-6] > 0 else 0.0

    adx = calculate_adx(df)
    bb_pct = bollinger_width_percentile(df)

    # 2. Determine Trend & Regime Class
    trend: Literal["bullish", "bearish", "range"] = "range"
    regime_class: Literal["STRONG_BULL", "BULL", "RANGE", "BEAR", "STRONG_BEAR", "UNKNOWN", "REGIME_TRANSITION"] = "RANGE"
    trend_confidence = 0.5

    if adx is None:
        reasons.append("ADX calculation returned None (insufficient bars for Wilder smoothing); defaulted to range.")
        trend = "range"
        regime_class = "UNKNOWN"
        trend_confidence = 0.3
    elif adx < range_adx_max:
        # Check if Bollinger Bands are expanding rapidly (breakout transition warning)
        if bb_pct is not None and bb_pct >= 85.0:
            trend = "range"
            regime_class = "REGIME_TRANSITION"
            reasons.append(f"Regime transition detected: Low ADX ({adx:.2f}) but Bollinger Band expansion ({bb_pct:.1f}%) indicates imminent breakout.")
            trend_confidence = 0.40
        else:
            trend = "range"
            regime_class = "RANGE"
            reasons.append(f"Range-bound regime: 14-day ADX ({adx:.2f}) is below trend threshold ({range_adx_max:.1f}).")
            trend_confidence = 0.85
    else:
        price_above_sma20 = current_price > sma_20
        price_above_sma50 = (current_price > sma_50) if sma_50 is not None else price_above_sma20

        if price_above_sma20 and price_above_sma50:
            if sma20_slope < -0.1:
                trend = "range"
                regime_class = "REGIME_TRANSITION"
                reasons.append(f"Regime transition warning: Price > SMA20/50 but SMA20 slope is negative ({sma20_slope:.2f}%).")
                trend_confidence = 0.40
            elif adx >= strong_adx_min and sma_50 is not None and sma_20 > sma_50:
                trend = "bullish"
                regime_class = "STRONG_BULL"
                reasons.append(f"Strong Bullish trend: ADX ({adx:.2f}) >= {strong_adx_min:.1f}, Price > SMA20 > SMA50.")
                trend_confidence = 0.90
            else:
                trend = "bullish"
                regime_class = "BULL"
                reasons.append(f"Bullish trend: 14-day ADX ({adx:.2f}) >= {trend_adx_min:.1f} and price (${current_price:.2f}) > SMA20 (${sma_20:.2f}).")
                trend_confidence = 0.90
        elif (not price_above_sma20) and (not price_above_sma50):
            if sma20_slope > 0.1:
                trend = "range"
                regime_class = "REGIME_TRANSITION"
                reasons.append(f"Regime transition warning: Price < SMA20/50 but SMA20 slope is positive ({sma20_slope:.2f}%).")
                trend_confidence = 0.40
            elif adx >= strong_adx_min and sma_50 is not None and sma_20 < sma_50:
                trend = "bearish"
                regime_class = "STRONG_BEAR"
                reasons.append(f"Strong Bearish trend: ADX ({adx:.2f}) >= {strong_adx_min:.1f}, Price < SMA20 < SMA50.")
                trend_confidence = 0.90
            else:
                trend = "bearish"
                regime_class = "BEAR"
                reasons.append(f"Bearish trend: 14-day ADX ({adx:.2f}) >= {trend_adx_min:.1f} and price (${current_price:.2f}) < SMA20 (${sma_20:.2f}).")
                trend_confidence = 0.90
        else:
            trend = "range"
            regime_class = "REGIME_TRANSITION"
            reasons.append(f"Ambiguous directional bias: ADX ({adx:.2f}) >= {trend_adx_min:.1f} but SMA20/SMA50 signals diverge; classified as transition.")
            trend_confidence = 0.40

    # 3. Determine Volatility State
    volatility: Literal["low", "normal", "high"] = "low"
    vol_confidence = 0.5

    if iv_rank is None:
        volatility = "low"
        reasons.append("Missing historical IV Rank observation; failing closed to low volatility.")
        vol_confidence = 0.2
    elif iv_rank < low_iv_rank:
        volatility = "low"
        reasons.append(f"Low volatility state: IV Rank ({iv_rank:.1f}%) < {low_iv_rank:.1f}%.")
        vol_confidence = 0.90
    elif iv_rank > high_iv_rank:
        volatility = "high"
        reasons.append(f"High volatility state: IV Rank ({iv_rank:.1f}%) > {high_iv_rank:.1f}%.")
        vol_confidence = 0.90
    else:
        volatility = "normal"
        reasons.append(f"Normal volatility state: IV Rank ({iv_rank:.1f}%) is within [{low_iv_rank:.1f}%, {high_iv_rank:.1f}%].")
        vol_confidence = 0.85

    # 4. Determine Strategy Family Eligibility based on Strategy-Aware Selection Matrix
    eligible_strategies: List[str] = []
    if regime_class == "REGIME_TRANSITION":
        eligible_strategies = []
        reasons.append("Regime transition detected; trading blocked to protect against false breakouts/reversals.")
    elif volatility in ["normal", "high"]:
        if regime_class == "STRONG_BULL":
            eligible_strategies = ["long_call", "bull_put_spread", "call_debit_spread"]
        elif regime_class == "BULL":
            eligible_strategies = ["bull_put_spread", "call_debit_spread", "long_call"]
        elif regime_class == "RANGE":
            eligible_strategies = ["iron_condor", "iron_butterfly", "bull_put_spread", "bear_call_spread"]
        elif regime_class == "BEAR":
            eligible_strategies = ["bear_call_spread", "put_debit_spread"]
        elif regime_class == "STRONG_BEAR":
            eligible_strategies = ["bear_call_spread", "put_debit_spread", "long_put"]
        else:
            eligible_strategies = []
            reasons.append("Regime UNKNOWN or insufficient data; NO TRADE.")
    else:
        # Low volatility state -> credit spreads blocked, but debit spreads and defined-risk long options are eligible
        if regime_class in ["STRONG_BULL", "BULL"]:
            eligible_strategies = ["call_debit_spread", "long_call"]
        elif regime_class in ["STRONG_BEAR", "BEAR"]:
            eligible_strategies = ["put_debit_spread", "long_put"]
        elif regime_class == "RANGE":
            eligible_strategies = ["call_debit_spread", "put_debit_spread", "iron_butterfly"]
        else:
            eligible_strategies = []
            reasons.append("Low volatility state: credit spreads blocked; debit strategies eligible.")

    overall_confidence = round((trend_confidence * 0.6) + (vol_confidence * 0.4), 2)

    return MarketRegime(
        trend=trend,
        volatility=volatility,
        confidence=min(1.0, max(0.0, overall_confidence)),
        regime_class=regime_class,
        reasons=reasons,
        adx=round(adx, 2) if adx is not None else None,
        bb_width_percentile=round(bb_pct, 1) if bb_pct is not None else None,
        iv_rank=round(iv_rank, 1) if iv_rank is not None else None,
        current_price=round(current_price, 2),
        sma_20=round(sma_20, 2),
        sma_50=round(sma_50, 2) if sma_50 is not None else None,
        eligible_strategies=eligible_strategies,
        diagnostics={
            "regime_class": regime_class,
            "price_vs_sma20_diff": round(current_price - sma_20, 2),
            "sma20_vs_sma50_diff": round(sma_20 - sma_50, 2) if sma_50 is not None else None,
            "bb_percentile": bb_pct,
        },
    )
