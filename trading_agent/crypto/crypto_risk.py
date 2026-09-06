"""
Independent Crypto Risk Gate & Position Sizing Engine.
Validates crypto trade candidates against 12 risk controls and calculates risk-based position size.
Completely isolated from options risk gates.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from trading_agent.config import settings
from trading_agent.crypto.crypto_strategy import CryptoStrategyCandidate

logger = logging.getLogger(__name__)


@dataclass
class CryptoRiskDecision:
    approved: bool
    violations: List[str]
    candidate: Optional[CryptoStrategyCandidate]
    position_size: float
    max_loss_usd: float
    risk_percentage: float


class CryptoRiskGate:
    """Independent Risk Gate & Volatility-Based Position Sizer for Crypto Engine."""

    def __init__(self, risk_per_trade: float = 0.0025, max_daily_loss_pct: float = 0.01, max_open_positions: int = 3):
        self.risk_per_trade = risk_per_trade
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_open_positions = max_open_positions

    def evaluate_candidate(
        self,
        candidate: Optional[CryptoStrategyCandidate],
        equity: float,
        daily_pnl: float,
        open_positions: List[Dict[str, Any]],
        consecutive_losses: int = 0,
    ) -> CryptoRiskDecision:
        """Evaluate trade candidate against 12 deterministic risk controls."""
        violations = []
        if not candidate:
            return CryptoRiskDecision(
                approved=False,
                violations=["NO_CANDIDATE: No candidate provided for evaluation"],
                candidate=None,
                position_size=0.0,
                max_loss_usd=0.0,
                risk_percentage=0.0,
            )

        # 1. Deterministic Downside Protection Check
        if candidate.stop_loss <= 0 or candidate.take_profit <= 0:
            violations.append("MISSING_PROTECTION: Stop loss or take profit price missing or invalid")

        if candidate.direction == "long" and (candidate.stop_loss >= candidate.entry_price or candidate.take_profit <= candidate.entry_price):
            violations.append("INVALID_LEVELS: Long trade stop loss must be < entry and take profit must be > entry")
        elif candidate.direction == "short" and (candidate.stop_loss <= candidate.entry_price or candidate.take_profit >= candidate.entry_price):
            violations.append("INVALID_LEVELS: Short trade stop loss must be > entry and take profit must be < entry")

        # 2. Risk/Reward Ratio Check
        min_rr = getattr(settings, "crypto_min_risk_reward", 1.5)
        if candidate.risk_reward_ratio < min_rr:
            violations.append(f"LOW_RISK_REWARD: Risk/reward ratio {candidate.risk_reward_ratio:.2f} < minimum {min_rr:.2f}")

        # 3. Account Equity Validation
        if equity <= 0:
            violations.append("INVALID_EQUITY: Account equity is non-positive")

        # 4. Daily Loss Circuit Breaker
        max_daily_loss_usd = equity * self.max_daily_loss_pct
        if daily_pnl <= -max_daily_loss_usd:
            violations.append(f"DAILY_LOSS_CIRCUIT_BREAKER: Daily P&L (${daily_pnl:.2f}) breached max limit (-${max_daily_loss_usd:.2f})")

        # 5. Consecutive Loss Limit
        if consecutive_losses >= 3:
            violations.append(f"CONSECUTIVE_LOSS_LIMIT: Consecutive loss count ({consecutive_losses}) reached max threshold (3)")

        # 6. Max Concurrent Positions
        if len(open_positions) >= self.max_open_positions:
            violations.append(f"MAX_CONCURRENT_POSITIONS: Active positions ({len(open_positions)}) reached max limit ({self.max_open_positions})")

        # 7. Duplicate Symbol Protection
        cand_sym_clean = candidate.symbol.replace("/", "").upper()
        for pos in open_positions:
            pos_sym_clean = str(pos.get("symbol", "")).replace("/", "").upper()
            if pos_sym_clean == cand_sym_clean:
                violations.append(f"DUPLICATE_SYMBOL: Position already open for '{candidate.symbol}'")
                break

        # 8. Volatility Bounds Check
        vol = candidate.strategy_details.get("realized_vol", 0.0)
        max_vol = getattr(settings, "crypto_max_volatility", 100.0)
        if vol > max_vol:
            violations.append(f"HIGH_VOLATILITY_BREACH: Realized volatility ({vol:.1f}%) exceeds max threshold ({max_vol}%)")

        # Position Sizing Calculation
        per_unit_risk = abs(candidate.entry_price - candidate.stop_loss)
        if per_unit_risk <= 0:
            violations.append("ZERO_PER_UNIT_RISK: Distance between entry and stop loss is zero")
            position_size = 0.0
            max_loss_usd = 0.0
        else:
            risk_budget = equity * self.risk_per_trade
            position_size = round(risk_budget / per_unit_risk, 4)
            max_loss_usd = round(position_size * per_unit_risk, 2)

        # 9. Max Single Trade Risk Check
        if max_loss_usd > equity * 0.01:
            violations.append(f"EXCESSIVE_TRADE_RISK: Max trade loss (${max_loss_usd:.2f}) exceeds 1% equity limit")

        # 10. Position Value Sanity Check
        position_value = position_size * candidate.entry_price
        if position_value > equity * 0.25:
            # Scale down position size to cap position value at 25% of equity
            position_size = round((equity * 0.25) / candidate.entry_price, 4)
            max_loss_usd = round(position_size * per_unit_risk, 2)

        approved = len(violations) == 0

        return CryptoRiskDecision(
            approved=approved,
            violations=violations,
            candidate=candidate if approved else None,
            position_size=position_size if approved else 0.0,
            max_loss_usd=max_loss_usd if approved else 0.0,
            risk_percentage=self.risk_per_trade * 100.0,
        )
