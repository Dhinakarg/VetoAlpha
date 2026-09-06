"""
Deterministic Crypto Market Regime Classifier.
Classifies crypto asset market conditions into discrete regimes based strictly on technical metrics.
LLM output has ZERO authority over regime classification.
"""

import logging
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional

from trading_agent.crypto.crypto_data import CryptoMarketData

logger = logging.getLogger(__name__)


class CryptoRegime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    RANGE = "RANGE"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass
class CryptoRegimeResult:
    symbol: str
    regime: CryptoRegime
    confidence: float
    adx: Optional[float]
    rsi: Optional[float]
    trend_bias: str
    volatility_state: str
    rationale: str


def classify_crypto_regime(data: CryptoMarketData) -> CryptoRegimeResult:
    """Deterministically classify crypto market regime."""
    if not data.is_valid or data.current_price <= 0 or not data.adx_14 or not data.rsi_14:
        return CryptoRegimeResult(
            symbol=data.symbol,
            regime=CryptoRegime.UNKNOWN,
            confidence=0.0,
            adx=data.adx_14,
            rsi=data.rsi_14,
            trend_bias="neutral",
            volatility_state="unknown",
            rationale=f"Missing or invalid market data: {data.error_reason or 'incomplete indicators'}",
        )

    price = data.current_price
    sma_20 = data.sma_20 or price
    sma_50 = data.sma_50 or price
    adx = data.adx_14
    rsi = data.rsi_14
    realized_vol = data.realized_volatility or 0.0

    # Volatility Check (>80% annualized realized vol is HIGH_VOLATILITY)
    vol_state = "high" if realized_vol > 80.0 else ("low" if realized_vol < 25.0 else "normal")
    if realized_vol > 100.0:
        return CryptoRegimeResult(
            symbol=data.symbol,
            regime=CryptoRegime.HIGH_VOLATILITY,
            confidence=0.90,
            adx=adx,
            rsi=rsi,
            trend_bias="neutral",
            volatility_state=vol_state,
            rationale=f"Extreme realized volatility detected ({realized_vol:.1f}%). High volatility regime activated.",
        )

    # Trend Direction Check
    is_above_sma20 = price > sma_20
    is_above_sma50 = price > sma_50

    if is_above_sma20 and is_above_sma50 and rsi > 55.0:
        trend = "bullish"
    elif not is_above_sma20 and not is_above_sma50 and rsi < 45.0:
        trend = "bearish"
    else:
        trend = "neutral"

    # Regime Determination based on ADX & Trend
    if adx >= 35.0 and trend == "bullish" and rsi >= 60.0:
        regime = CryptoRegime.STRONG_BULL
        confidence = 0.92
        rationale = f"Strong bullish momentum: ADX ({adx:.1f}) >= 35, RSI ({rsi:.1f}) >= 60, price above SMAs."
    elif adx >= 22.0 and trend == "bullish":
        regime = CryptoRegime.BULL
        confidence = 0.85
        rationale = f"Bullish trend: ADX ({adx:.1f}) >= 22, RSI ({rsi:.1f}) > 55, price above SMAs."
    elif adx >= 35.0 and trend == "bearish" and rsi <= 40.0:
        regime = CryptoRegime.STRONG_BEAR
        confidence = 0.92
        rationale = f"Strong bearish breakdown: ADX ({adx:.1f}) >= 35, RSI ({rsi:.1f}) <= 40, price below SMAs."
    elif adx >= 22.0 and trend == "bearish":
        regime = CryptoRegime.BEAR
        confidence = 0.85
        rationale = f"Bearish trend: ADX ({adx:.1f}) >= 22, RSI ({rsi:.1f}) < 45, price below SMAs."
    else:
        regime = CryptoRegime.RANGE
        confidence = 0.80
        rationale = f"Range-bound market: ADX ({adx:.1f}) < 22, RSI ({rsi:.1f}) neutral."

    return CryptoRegimeResult(
        symbol=data.symbol,
        regime=regime,
        confidence=confidence,
        adx=adx,
        rsi=rsi,
        trend_bias=trend,
        volatility_state=vol_state,
        rationale=rationale,
    )
