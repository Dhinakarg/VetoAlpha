"""
Crypto Strategy Selector & Deterministic Scoring Engine.
Ranks candidate crypto strategies using a normalized 0-100 scoring matrix.
Integrates AI advisory layer strictly as an advisory input.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from trading_agent.config import settings
from trading_agent.crypto.crypto_data import CryptoMarketData
from trading_agent.crypto.crypto_regime import CryptoRegimeResult, CryptoRegime
from trading_agent.crypto.crypto_strategy import CryptoStrategyCandidate, CryptoStrategyBuilder
from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment

logger = logging.getLogger(__name__)


@dataclass
class CryptoStrategyDecision:
    symbol: str
    action: str  # "trade" or "no_trade"
    selected_candidate: Optional[CryptoStrategyCandidate]
    candidates_evaluated: List[CryptoStrategyCandidate]
    score: float
    reasons: List[str]
    ai_advisory: Dict[str, Any]
    ai_agreed: bool


class CryptoStrategySelector:
    """Deterministic Strategy Selector for Crypto Validation Engine."""

    def __init__(self, min_score: float = 65.0):
        self.min_score = min_score
        self.ai_agent = CatalystIntelligenceAgent()

    def evaluate(
        self,
        data: CryptoMarketData,
        regime_res: CryptoRegimeResult,
        ai_assessment: Optional[CatalystAssessment] = None,
    ) -> CryptoStrategyDecision:
        """Evaluate market data, generate candidates, score deterministically, and select best candidate."""
        reasons = []
        if not data.is_valid or regime_res.regime == CryptoRegime.UNKNOWN:
            reasons.append(f"Invalid market data or UNKNOWN regime for {data.symbol}")
            return CryptoStrategyDecision(
                symbol=data.symbol,
                action="no_trade",
                selected_candidate=None,
                candidates_evaluated=[],
                score=0.0,
                reasons=reasons,
                ai_advisory={},
                ai_agreed=True,
            )

        # 1. Generate Candidates
        candidates = CryptoStrategyBuilder.generate_candidates(data, regime_res)
        if not candidates:
            reasons.append(f"No strategy candidates generated for {data.symbol} under regime {regime_res.regime.value}")
            return CryptoStrategyDecision(
                symbol=data.symbol,
                action="no_trade",
                selected_candidate=None,
                candidates_evaluated=[],
                score=0.0,
                reasons=reasons,
                ai_advisory={},
                ai_agreed=True,
            )

        # 2. Score Candidates (0-100 normalized matrix)
        scored_candidates = []
        for c in candidates:
            score = self._score_candidate(c, data, regime_res)
            c.raw_score = score
            scored_candidates.append(c)

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x.raw_score, reverse=True)
        best = scored_candidates[0]

        # 3. AI Advisory Check (Advisory Only)
        ai_info = {}
        ai_agreed = True
        if ai_assessment:
            ai_info = {
                "risk": ai_assessment.catalyst_risk,
                "bias": ai_assessment.directional_bias,
                "confidence": ai_assessment.confidence,
                "rationale": ai_assessment.rationale,
            }
            # Log disagreement if AI bias opposes candidate direction
            if (ai_assessment.directional_bias == "bearish" and best.direction == "long") or \
               (ai_assessment.directional_bias == "bullish" and best.direction == "short"):
                ai_agreed = False
                logger.info(f"AI_DETERMINISTIC_DISAGREEMENT for {data.symbol}: AI bias is '{ai_assessment.directional_bias}', candidate is '{best.direction}'. Deterministic decision maintained.")

        # 4. Final Selection Gate
        if best.raw_score < self.min_score:
            reasons.append(f"Top candidate '{best.strategy}' score ({best.raw_score:.1f}) below minimum threshold ({self.min_score})")
            return CryptoStrategyDecision(
                symbol=data.symbol,
                action="no_trade",
                selected_candidate=best,
                candidates_evaluated=scored_candidates,
                score=best.raw_score,
                reasons=reasons,
                ai_advisory=ai_info,
                ai_agreed=ai_agreed,
            )

        reasons.append(f"Selected strategy '{best.strategy}' with score {best.raw_score:.1f}/100")
        return CryptoStrategyDecision(
            symbol=data.symbol,
            action="trade",
            selected_candidate=best,
            candidates_evaluated=scored_candidates,
            score=best.raw_score,
            reasons=reasons,
            ai_advisory=ai_info,
            ai_agreed=ai_agreed,
        )

    def _score_candidate(self, candidate: CryptoStrategyCandidate, data: CryptoMarketData, regime_res: CryptoRegimeResult) -> float:
        """Deterministic 0-100 scoring matrix."""
        # 1. Trend Alignment (0-25)
        trend_score = 0.0
        if (candidate.direction == "long" and regime_res.regime in (CryptoRegime.STRONG_BULL, CryptoRegime.BULL)) or \
           (candidate.direction == "short" and regime_res.regime in (CryptoRegime.STRONG_BEAR, CryptoRegime.BEAR)):
            trend_score = 25.0
        elif regime_res.regime == CryptoRegime.RANGE:
            trend_score = 18.0

        # 2. Momentum (0-20)
        adx = data.adx_14 or 20.0
        adx_score = min(20.0, max(5.0, (adx / 40.0) * 20.0))

        # 3. Volatility Quality (0-15)
        vol_score = 15.0 if data.realized_volatility and 25.0 <= data.realized_volatility <= 75.0 else 8.0

        # 4. Liquidity & Volume (0-15)
        vol_ratio = data.volume_ratio or 1.0
        liq_score = min(15.0, max(5.0, vol_ratio * 10.0))

        # 5. Risk / Reward Ratio (0-15)
        rr = candidate.risk_reward_ratio
        rr_score = min(15.0, max(5.0, (rr / 2.5) * 15.0))

        # 6. Signal Quality / Confidence (0-10)
        conf_score = candidate.confidence * 10.0

        total = trend_score + adx_score + vol_score + liq_score + rr_score + conf_score
        return round(min(100.0, total), 2)
