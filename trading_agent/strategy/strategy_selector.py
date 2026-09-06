"""
Deterministic Strategy Selector.
Evaluates multi-strategy eligibility:
  1. Iron Condor
  2. Bull Put Spread
  3. Bear Call Spread
  4. Long Call
  5. Long Put
Generates and scores candidates across multi-dimensional criteria, and returns StrategyDecision.

CRITICAL SAFETY INVARIANT:
The Strategy Selector is strictly a decision engine.
It contains NO broker execution endpoints and NEVER places orders.
"""

import logging
from typing import Dict, List, Optional, Any, Union, Literal, Tuple
from pydantic import BaseModel, Field

from trading_agent.models import (
    OptionChainSnapshot,
    OptionContract,
    IronCondor,
    StrategyCandidate,
    SentimentResult,
    FeatherlessCatalystResponse,
)
from trading_agent.strategy.base import BaseOptionsStrategy
from trading_agent.strategy.iron_condor import IronCondorStrategy
from trading_agent.strategy.bull_put_spread import BullPutSpreadStrategy
from trading_agent.strategy.bear_call_spread import BearCallSpreadStrategy
from trading_agent.strategy.long_call import LongCallStrategy
from trading_agent.strategy.long_put import LongPutStrategy
from trading_agent.strategy.call_debit_spread import CallDebitSpreadStrategy
from trading_agent.strategy.put_debit_spread import PutDebitSpreadStrategy
from trading_agent.strategy.iron_butterfly import IronButterflyStrategy
from trading_agent.strategy.ai_strategy_advisor import AIStrategyAdvisor, StrategyAdvice
from trading_agent.strategy.regime import MarketRegime

logger = logging.getLogger(__name__)


class StrategyDecision(BaseModel):
    """Deterministic Multi-Strategy Evaluation and Selection Decision."""

    selected_strategy: Optional[str] = None
    selected_candidate: Optional[Any] = None
    candidates: Dict[str, Any] = Field(default_factory=dict)
    scores: Dict[str, float] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)
    decision: Literal["TRADE", "NO_TRADE"]
    ai_recommendation: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class StrategySelector:
    """Deterministic selector evaluating and ranking options strategy candidates."""

    def __init__(
        self,
        min_selection_score: float = 50.0,
        iron_condor_strategy: Optional[IronCondorStrategy] = None,
        bull_put_strategy: Optional[BullPutSpreadStrategy] = None,
        bear_call_strategy: Optional[BearCallSpreadStrategy] = None,
        long_call_strategy: Optional[LongCallStrategy] = None,
        long_put_strategy: Optional[LongPutStrategy] = None,
        call_debit_strategy: Optional[CallDebitSpreadStrategy] = None,
        put_debit_strategy: Optional[PutDebitSpreadStrategy] = None,
        iron_butterfly_strategy: Optional[IronButterflyStrategy] = None,
        ai_advisor: Optional[AIStrategyAdvisor] = None,
    ):
        self.min_selection_score = min_selection_score
        self.iron_condor = iron_condor_strategy or IronCondorStrategy()
        self.bull_put = bull_put_strategy or BullPutSpreadStrategy()
        self.bear_call = bear_call_strategy or BearCallSpreadStrategy()
        self.long_call = long_call_strategy or LongCallStrategy()
        self.long_put = long_put_strategy or LongPutStrategy()
        self.call_debit = call_debit_strategy or CallDebitSpreadStrategy()
        self.put_debit = put_debit_strategy or PutDebitSpreadStrategy()
        self.iron_butterfly = iron_butterfly_strategy or IronButterflyStrategy()
        self.ai_advisor = ai_advisor or AIStrategyAdvisor()

    def _extract_catalyst_risk(self, catalyst_assessment: Optional[Any]) -> Tuple[str, float]:
        """Extract catalyst risk tier ('low', 'medium', 'high') and confidence."""
        if catalyst_assessment is None:
            return "low", 1.0

        if hasattr(catalyst_assessment, "catalyst_risk"):
            risk = getattr(catalyst_assessment, "catalyst_risk", "low")
            conf = getattr(catalyst_assessment, "confidence", 1.0)
            return str(risk).lower(), float(conf or 1.0)
        elif isinstance(catalyst_assessment, dict):
            risk = catalyst_assessment.get("catalyst_risk", catalyst_assessment.get("risk", "low"))
            conf = catalyst_assessment.get("confidence", 1.0)
            return str(risk).lower(), float(conf or 1.0)

        return "low", 1.0

    def _calculate_candidate_score(
        self,
        strategy_name: str,
        candidate: Any,
        market_regime: MarketRegime,
        underlying_price: float,
        catalyst_risk: str,
        catalyst_confidence: float,
        ai_preferred: Optional[str] = None,
    ) -> Tuple[float, Dict[str, float]]:
        """Score candidate along normalized dimensions (0–100)."""
        regime_class = getattr(market_regime, "regime_class", "RANGE")
        trend = market_regime.trend

        # 1. Regime Fit Score (0-100)
        if regime_class == "STRONG_BULL":
            regime_fit = 100.0 if strategy_name in ("long_call", "call_debit_spread") else (80.0 if strategy_name == "bull_put_spread" else 10.0)
        elif regime_class == "BULL":
            regime_fit = 100.0 if strategy_name in ("bull_put_spread", "call_debit_spread") else (80.0 if strategy_name == "long_call" else 10.0)
        elif regime_class == "STRONG_BEAR":
            regime_fit = 100.0 if strategy_name in ("long_put", "put_debit_spread") else (80.0 if strategy_name == "bear_call_spread" else 10.0)
        elif regime_class == "BEAR":
            regime_fit = 100.0 if strategy_name in ("bear_call_spread", "put_debit_spread") else (80.0 if strategy_name == "long_put" else 10.0)
        elif regime_class == "RANGE" or trend == "range":
            regime_fit = 100.0 if strategy_name in ("iron_condor", "iron_butterfly") else 70.0
        else:
            regime_fit = 0.0

        # 2. Volatility Fit Score (0-100)
        iv_rank = market_regime.iv_rank or 50.0
        if strategy_name in ("long_call", "long_put", "call_debit_spread", "put_debit_spread"):
            # Debit strategies prefer lower IV to buy cheaper premium
            vol_fit = max(0.0, 100.0 - iv_rank)
        else:
            # Credit strategies prefer elevated IV
            vol_fit = min(100.0, (iv_rank / 70.0) * 100.0)

        # 3. Liquidity Score (0-100)
        liquidity = 90.0

        # 4. Reward-to-Risk Score (0-100)
        if isinstance(candidate, IronCondor):
            credit = candidate.net_credit
            risk = candidate.max_risk or (candidate.wing_width * 100.0 - credit * 100.0)
            ratio = (credit * 100.0) / risk if risk > 0 else 0.0
            rr_score = min(100.0, (ratio / 0.40) * 100.0)
            short_strike = candidate.put_short.contract.strike_price
        elif isinstance(candidate, StrategyCandidate):
            if candidate.entry_debit > 0:
                # Debit strategy: risk is debit, target profit is ~2.5x
                rr_score = 75.0
                short_strike = candidate.legs[0].contract.strike_price if candidate.legs else underlying_price
            else:
                credit = candidate.entry_credit
                risk = candidate.max_loss or (candidate.wing_width * 100.0 - credit * 100.0) if candidate.wing_width else 100.0
                ratio = (credit * 100.0) / risk if risk > 0 else 0.0
                rr_score = min(100.0, (ratio / 0.35) * 100.0)
                short_strike = candidate.legs[0].contract.strike_price if candidate.legs else underlying_price
        else:
            rr_score = 50.0
            short_strike = underlying_price

        # 5. Distance Score (0-100)
        dist_pct = abs(underlying_price - short_strike) / underlying_price if underlying_price > 0 else 0.0
        distance_score = min(100.0, (dist_pct / 0.05) * 100.0)

        # 6. Catalyst Compatibility (0-100)
        if catalyst_risk == "low":
            catalyst_score = 100.0
        elif catalyst_risk == "medium":
            catalyst_score = 60.0
        else:
            catalyst_score = 0.0

        # 7. AI Advisory Context Boost
        ai_boost = 0.0
        if ai_preferred and ai_preferred.lower() == strategy_name.lower():
            ai_boost = 15.0

        # Weighted score (weights sum to 1.0)
        total_score = (
            (regime_fit * 0.30)
            + (vol_fit * 0.20)
            + (liquidity * 0.15)
            + (rr_score * 0.15)
            + (distance_score * 0.10)
            + (catalyst_score * 0.10)
            + ai_boost
        )
        total_score = min(100.0, max(0.0, total_score))

        sub_scores = {
            "regime_fit": round(regime_fit, 1),
            "volatility_fit": round(vol_fit, 1),
            "liquidity": round(liquidity, 1),
            "reward_risk": round(rr_score, 1),
            "distance": round(distance_score, 1),
            "catalyst_compat": round(catalyst_score, 1),
            "ai_boost": round(ai_boost, 1),
        }
        return round(total_score, 1), sub_scores

    def select_strategy(
        self,
        market_regime: MarketRegime,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        catalyst_assessment: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
        current_date: Optional[str] = None,
        **kwargs: Any,
    ) -> StrategyDecision:
        """Evaluate strategy candidates across all 5 strategies and deterministically select top trade."""
        cfg = dict(config or {})
        min_score = float(cfg.get("MIN_STRATEGY_SCORE", self.min_selection_score))
        reasons: List[str] = []

        catalyst_risk, catalyst_conf = self._extract_catalyst_risk(catalyst_assessment)
        flagged_events = getattr(catalyst_assessment, "flagged_events", []) if catalyst_assessment else []
        error_type = getattr(catalyst_assessment, "error_type", None) if catalyst_assessment else None
        is_ai_unavailable = "AI_SERVICE_UNAVAILABLE" in flagged_events or error_type is not None

        # 1. Real High Catalyst Risk Filter (Real news / earnings flagged)
        if catalyst_risk == "high" and not is_ai_unavailable:
            reasons.append(f"High catalyst risk ({catalyst_risk.upper()}) detected from news/events; trading blocked for safety.")
            why_no_trade = {
                "symbol": str(getattr(market_regime, "symbol", "UNKNOWN")),
                "decision": "NO_TRADE",
                "primary_reason": "HIGH_CATALYST_RISK",
                "catalyst_risk": catalyst_risk,
                "flagged_events": flagged_events,
                "ai_status": "HIGH_RISK_FLAGGED",
                "final_decision": "NO_TRADE",
            }
            return StrategyDecision(
                selected_strategy=None,
                selected_candidate=None,
                candidates={},
                scores={},
                reasons=reasons,
                decision="NO_TRADE",
                diagnostics={"catalyst_risk": catalyst_risk, "why_no_trade": why_no_trade},
            )

        # 2. Check for UNKNOWN regime
        if getattr(market_regime, "regime_class", "") == "UNKNOWN":
            reasons.append("Market regime UNKNOWN / insufficient data; NO TRADE.")
            why_no_trade = {
                "symbol": str(getattr(market_regime, "symbol", "UNKNOWN")),
                "decision": "NO_TRADE",
                "primary_reason": "REGIME_UNKNOWN",
                "trend_regime": "UNKNOWN",
                "ai_status": "SKIPPED_REGIME_UNKNOWN",
                "final_decision": "NO_TRADE",
            }
            return StrategyDecision(
                selected_strategy=None,
                selected_candidate=None,
                candidates={},
                scores={},
                reasons=reasons,
                decision="NO_TRADE",
                diagnostics={"regime": "UNKNOWN", "why_no_trade": why_no_trade},
            )

        candidates: Dict[str, Any] = {}
        scores: Dict[str, float] = {}
        sub_scores_map: Dict[str, Dict[str, float]] = {}

        # Check if eligible_strategies is explicitly defined and empty
        if hasattr(market_regime, "eligible_strategies") and market_regime.eligible_strategies is not None:
            if len(market_regime.eligible_strategies) == 0:
                primary_reason_code = "REGIME_TRANSITION" if getattr(market_regime, "regime_class", "") == "REGIME_TRANSITION" else "NO_ELIGIBLE_STRATEGIES"
                reasons.append(f"Low volatility state / regime filter ({primary_reason_code}): No strategy families are eligible under current regime/volatility conditions; NO TRADE.")
                why_no_trade = {
                    "symbol": str(getattr(market_regime, "symbol", "UNKNOWN")),
                    "decision": "NO_TRADE",
                    "primary_reason": primary_reason_code,
                    "regime_class": getattr(market_regime, "regime_class", "UNKNOWN"),
                    "volatility_state": getattr(market_regime, "volatility", "low"),
                    "iv_rank": getattr(market_regime, "iv_rank", None),
                    "ai_status": "SKIPPED_NO_ELIGIBLE_STRATEGIES",
                    "final_decision": "NO_TRADE",
                }
                return StrategyDecision(
                    selected_strategy=None,
                    selected_candidate=None,
                    candidates={},
                    scores={},
                    reasons=reasons,
                    decision="NO_TRADE",
                    diagnostics={"eligible_strategies": [], "why_no_trade": why_no_trade},
                )
            eligible_set = set(market_regime.eligible_strategies)
        else:
            eligible_set = {"iron_condor", "bull_put_spread", "bear_call_spread", "long_call", "long_put"}

        trend = market_regime.trend

        # Strategy 1: Iron Condor
        if "iron_condor" in eligible_set:
            condor = self.iron_condor.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if condor:
                candidates["iron_condor"] = condor
                score, sub = self._calculate_candidate_score(
                    "iron_condor", condor, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["iron_condor"] = score
                sub_scores_map["iron_condor"] = sub
            else:
                reasons.append("Iron Condor candidate construction returned None (strike/delta/liquidity mismatch).")

        # Strategy 2: Bull Put Spread
        if "bull_put_spread" in eligible_set:
            bps = self.bull_put.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if bps:
                candidates["bull_put_spread"] = bps
                score, sub = self._calculate_candidate_score(
                    "bull_put_spread", bps, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["bull_put_spread"] = score
                sub_scores_map["bull_put_spread"] = sub
            else:
                reasons.append("Bull Put Spread candidate construction returned None (strike/delta/liquidity mismatch).")

        # Strategy 3: Bear Call Spread
        if "bear_call_spread" in eligible_set:
            bcs = self.bear_call.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if bcs:
                candidates["bear_call_spread"] = bcs
                score, sub = self._calculate_candidate_score(
                    "bear_call_spread", bcs, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["bear_call_spread"] = score
                sub_scores_map["bear_call_spread"] = sub
            else:
                reasons.append("Bear Call Spread candidate construction returned None (strike/delta/liquidity mismatch).")

        # Strategy 4: Long Call
        if "long_call" in eligible_set or trend == "bullish":
            lc = self.long_call.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if lc:
                candidates["long_call"] = lc
                score, sub = self._calculate_candidate_score(
                    "long_call", lc, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["long_call"] = score
                sub_scores_map["long_call"] = sub

        # Strategy 5: Long Put
        if "long_put" in eligible_set or trend == "bearish":
            lp = self.long_put.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if lp:
                candidates["long_put"] = lp
                score, sub = self._calculate_candidate_score(
                    "long_put", lp, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["long_put"] = score
                sub_scores_map["long_put"] = sub

        # Strategy 6: Call Debit Spread
        if "call_debit_spread" in eligible_set:
            cds = self.call_debit.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if cds:
                candidates["call_debit_spread"] = cds
                score, sub = self._calculate_candidate_score(
                    "call_debit_spread", cds, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["call_debit_spread"] = score
                sub_scores_map["call_debit_spread"] = sub

        # Strategy 7: Put Debit Spread
        if "put_debit_spread" in eligible_set:
            pds = self.put_debit.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if pds:
                candidates["put_debit_spread"] = pds
                score, sub = self._calculate_candidate_score(
                    "put_debit_spread", pds, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["put_debit_spread"] = score
                sub_scores_map["put_debit_spread"] = sub

        # Strategy 8: Iron Butterfly
        if "iron_butterfly" in eligible_set:
            ib = self.iron_butterfly.generate_candidate(
                option_chain=option_chain,
                underlying_price=underlying_price,
                current_date=current_date,
                config=cfg,
                **kwargs,
            )
            if ib:
                candidates["iron_butterfly"] = ib
                score, sub = self._calculate_candidate_score(
                    "iron_butterfly", ib, market_regime, underlying_price, catalyst_risk, catalyst_conf
                )
                scores["iron_butterfly"] = score
                sub_scores_map["iron_butterfly"] = sub

        # Check if any valid candidates were generated
        if not candidates:
            reasons.append("No viable strategy candidates met structural, liquidity, or Greek criteria.")
            why_no_trade = {
                "symbol": str(getattr(market_regime, "symbol", "UNKNOWN")),
                "decision": "NO_TRADE",
                "primary_reason": "NO_CANDIDATES_GENERATED",
                "reasons": reasons,
                "final_decision": "NO_TRADE",
            }
            return StrategyDecision(
                selected_strategy=None,
                selected_candidate=None,
                candidates={},
                scores={},
                reasons=reasons,
                decision="NO_TRADE",
                diagnostics={"sub_scores": sub_scores_map, "why_no_trade": why_no_trade},
            )

        # Deterministic Ranking
        sorted_candidates = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_strategy, best_score = sorted_candidates[0]

        # Case B: AI Fallback Evaluation when AI is unavailable
        fallback_applied = False
        if is_ai_unavailable:
            if best_score >= 65.0:
                fallback_applied = True
                reasons.append(f"SAFE AI FALLBACK ACTIVE: AI service unavailable [{error_type}], but deterministic candidate '{best_strategy}' score ({best_score:.1f}) meets fallback threshold (>=65.0). Applied 0.5x conservative risk cap.")
            else:
                reasons.append(f"AI service unavailable [{error_type}] and top candidate '{best_strategy}' score ({best_score:.1f}) fell below fallback threshold (65.0); NO TRADE.")
                why_no_trade = {
                    "symbol": str(getattr(market_regime, "symbol", "UNKNOWN")),
                    "decision": "NO_TRADE",
                    "primary_reason": "AI_UNAVAILABLE_DETERMINISTIC_SCORE_INSUFFICIENT",
                    "error_type": error_type,
                    "best_strategy": best_strategy,
                    "best_score": best_score,
                    "required_score": 65.0,
                    "final_decision": "NO_TRADE",
                }
                return StrategyDecision(
                    selected_strategy=None,
                    selected_candidate=None,
                    candidates=candidates,
                    scores=scores,
                    reasons=reasons,
                    decision="NO_TRADE",
                    diagnostics={"sub_scores": sub_scores_map, "ranking": sorted_candidates, "why_no_trade": why_no_trade},
                )

        if best_score < min_score:
            reasons.append(
                f"Top ranked strategy '{best_strategy}' score ({best_score:.1f}) fell below minimum required score ({min_score:.1f})."
            )
            why_no_trade = {
                "symbol": str(getattr(market_regime, "symbol", "UNKNOWN")),
                "decision": "NO_TRADE",
                "primary_reason": "SCORE_BELOW_MINIMUM",
                "best_strategy": best_strategy,
                "best_score": best_score,
                "min_score": min_score,
                "final_decision": "NO_TRADE",
            }
            return StrategyDecision(
                selected_strategy=None,
                selected_candidate=None,
                candidates=candidates,
                scores=scores,
                reasons=reasons,
                decision="NO_TRADE",
                diagnostics={"sub_scores": sub_scores_map, "ranking": sorted_candidates, "why_no_trade": why_no_trade},
            )

        # Opportunity Quality Tiering
        if best_score >= 75.0:
            quality_tier = "EXCELLENT"
        elif best_score >= 60.0:
            quality_tier = "GOOD"
        elif best_score >= 50.0:
            quality_tier = "MARGINAL"
        else:
            quality_tier = "NO_TRADE"

        if quality_tier in ("MARGINAL", "NO_TRADE"):
            reasons.append(
                f"Top ranked strategy '{best_strategy}' score ({best_score:.1f}) classified as '{quality_tier}'; MARGINAL/NO_TRADE candidates blocked from RiskGate."
            )
            why_no_trade = {
                "symbol": str(getattr(market_regime, "symbol", "UNKNOWN")),
                "decision": "NO_TRADE",
                "primary_reason": f"QUALITY_TIER_{quality_tier}",
                "best_strategy": best_strategy,
                "best_score": best_score,
                "quality_tier": quality_tier,
                "min_score": min_score,
                "final_decision": "NO_TRADE",
            }
            return StrategyDecision(
                selected_strategy=None,
                selected_candidate=None,
                candidates=candidates,
                scores=scores,
                reasons=reasons,
                decision="NO_TRADE",
                diagnostics={"sub_scores": sub_scores_map, "ranking": sorted_candidates, "why_no_trade": why_no_trade, "quality_tier": quality_tier},
            )

        selected_cand = candidates[best_strategy]
        if isinstance(selected_cand, StrategyCandidate):
            selected_cand.candidate_score = best_score
            if fallback_applied:
                selected_cand.diagnostics["ai_fallback_applied"] = True
                selected_cand.diagnostics["position_size_multiplier"] = 0.5

        reasons.append(
            f"Strategy '{best_strategy}' selected deterministically with top score {best_score:.1f}/100."
        )

        return StrategyDecision(
            selected_strategy=best_strategy,
            selected_candidate=selected_cand,
            candidates=candidates,
            scores=scores,
            reasons=reasons,
            decision="TRADE",
            diagnostics={
                "sub_scores": sub_scores_map,
                "ranking": sorted_candidates,
                "ai_fallback_applied": fallback_applied,
                "position_size_multiplier": 0.5 if fallback_applied else 1.0,
            },
        )
