"""
Crypto Validation Strategy Candidates & Builders.
Defines 5 standardized crypto strategies with deterministic downside protection.
Isolated from options strategy candidates.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from trading_agent.crypto.crypto_data import CryptoMarketData
from trading_agent.crypto.crypto_regime import CryptoRegime, CryptoRegimeResult


@dataclass
class CryptoStrategyCandidate:
    strategy: str
    symbol: str
    direction: str  # "long" or "short"
    entry_price: float
    stop_loss: float
    take_profit: float
    expected_risk: float
    expected_reward: float
    risk_reward_ratio: float
    confidence: float
    regime: str
    signal_timestamp: str
    raw_score: float = 0.0
    strategy_details: Dict[str, Any] = field(default_factory=dict)


class CryptoStrategyBuilder:
    """Generates standardized CryptoStrategyCandidate objects based on technical market data & regime."""

    @staticmethod
    def generate_candidates(data: CryptoMarketData, regime_res: CryptoRegimeResult) -> List[CryptoStrategyCandidate]:
        """Generate eligible candidate strategies for a crypto asset based on its regime."""
        if not data.is_valid or regime_res.regime == CryptoRegime.UNKNOWN:
            return []

        candidates = []
        p = data.current_price
        atr = data.atr_14 or (p * 0.02)
        regime = regime_res.regime
        ts = datetime.now(timezone.utc).isoformat()

        # Strategy 1: Trend Following Long (STRONG_BULL / BULL)
        if regime in (CryptoRegime.STRONG_BULL, CryptoRegime.BULL):
            stop = round(p - (atr * 2.0), 2)
            tp = round(p + (atr * 4.0), 2)
            risk = round(p - stop, 2)
            reward = round(tp - p, 2)
            rr = round(reward / risk, 2) if risk > 0 else 0.0
            if rr >= 1.5:
                candidates.append(CryptoStrategyCandidate(
                    strategy="TREND_FOLLOWING_LONG",
                    symbol=data.symbol,
                    direction="long",
                    entry_price=p,
                    stop_loss=stop,
                    take_profit=tp,
                    expected_risk=risk,
                    expected_reward=reward,
                    risk_reward_ratio=rr,
                    confidence=0.85,
                    regime=regime.value,
                    signal_timestamp=ts,
                    strategy_details={"atr": atr, "adx": data.adx_14, "rsi": data.rsi_14},
                ))

        # Strategy 2: Trend Following Short (STRONG_BEAR / BEAR)
        if regime in (CryptoRegime.STRONG_BEAR, CryptoRegime.BEAR):
            stop = round(p + (atr * 2.0), 2)
            tp = round(p - (atr * 4.0), 2)
            risk = round(stop - p, 2)
            reward = round(p - tp, 2)
            rr = round(reward / risk, 2) if risk > 0 else 0.0
            if rr >= 1.5:
                candidates.append(CryptoStrategyCandidate(
                    strategy="TREND_FOLLOWING_SHORT",
                    symbol=data.symbol,
                    direction="short",
                    entry_price=p,
                    stop_loss=stop,
                    take_profit=tp,
                    expected_risk=risk,
                    expected_reward=reward,
                    risk_reward_ratio=rr,
                    confidence=0.85,
                    regime=regime.value,
                    signal_timestamp=ts,
                    strategy_details={"atr": atr, "adx": data.adx_14, "rsi": data.rsi_14},
                ))

        # Strategy 3: Mean Reversion Long (RANGE)
        if regime == CryptoRegime.RANGE and data.rsi_14 and data.rsi_14 < 45.0:
            stop = round(p - (atr * 1.5), 2)
            tp = round(data.sma_20 or (p + atr * 2.0), 2)
            risk = round(p - stop, 2)
            reward = round(tp - p, 2)
            rr = round(reward / risk, 2) if risk > 0 else 0.0
            if rr >= 1.3:
                candidates.append(CryptoStrategyCandidate(
                    strategy="MEAN_REVERSION_LONG",
                    symbol=data.symbol,
                    direction="long",
                    entry_price=p,
                    stop_loss=stop,
                    take_profit=tp,
                    expected_risk=risk,
                    expected_reward=reward,
                    risk_reward_ratio=rr,
                    confidence=0.75,
                    regime=regime.value,
                    signal_timestamp=ts,
                    strategy_details={"atr": atr, "rsi": data.rsi_14, "sma_20": data.sma_20},
                ))

        # Strategy 4: Mean Reversion Short (RANGE)
        if regime == CryptoRegime.RANGE and data.rsi_14 and data.rsi_14 > 55.0:
            stop = round(p + (atr * 1.5), 2)
            tp = round(data.sma_20 or (p - atr * 2.0), 2)
            risk = round(stop - p, 2)
            reward = round(p - tp, 2)
            rr = round(reward / risk, 2) if risk > 0 else 0.0
            if rr >= 1.3:
                candidates.append(CryptoStrategyCandidate(
                    strategy="MEAN_REVERSION_SHORT",
                    symbol=data.symbol,
                    direction="short",
                    entry_price=p,
                    stop_loss=stop,
                    take_profit=tp,
                    expected_risk=risk,
                    expected_reward=reward,
                    risk_reward_ratio=rr,
                    confidence=0.75,
                    regime=regime.value,
                    signal_timestamp=ts,
                    strategy_details={"atr": atr, "rsi": data.rsi_14, "sma_20": data.sma_20},
                ))

        # Strategy 5: Momentum Breakout (STRONG_BULL / STRONG_BEAR)
        if regime in (CryptoRegime.STRONG_BULL, CryptoRegime.STRONG_BEAR) and data.volume_ratio and data.volume_ratio > 1.2:
            is_bull = regime == CryptoRegime.STRONG_BULL
            direction = "long" if is_bull else "short"
            stop = round(p - (atr * 2.5), 2) if is_bull else round(p + (atr * 2.5), 2)
            tp = round(p + (atr * 5.0), 2) if is_bull else round(p - (atr * 5.0), 2)
            risk = abs(p - stop)
            reward = abs(tp - p)
            rr = round(reward / risk, 2) if risk > 0 else 0.0
            if rr >= 1.5:
                candidates.append(CryptoStrategyCandidate(
                    strategy="MOMENTUM_BREAKOUT",
                    symbol=data.symbol,
                    direction=direction,
                    entry_price=p,
                    stop_loss=stop,
                    take_profit=tp,
                    expected_risk=risk,
                    expected_reward=reward,
                    risk_reward_ratio=rr,
                    confidence=0.88,
                    regime=regime.value,
                    signal_timestamp=ts,
                    strategy_details={"volume_ratio": data.volume_ratio, "atr": atr},
                ))

        return candidates
