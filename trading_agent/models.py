from __future__ import annotations

"""
Typed Pydantic models for Adaptive AI Iron Condor Agent.
Includes snapshots, option chains, strategy structures, risk decisions, and execution tracking.
"""

from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field


class AccountSnapshot(BaseModel):
    """Snapshot of Alpaca paper trading account state."""

    account_id: str = ""
    cash: float = 100000.0
    buying_power: float = 100000.0
    equity: float = 100000.0
    portfolio_value: float = 100000.0
    status: str = "ACTIVE"
    currency: str = "USD"


class OptionContract(BaseModel):
    """Specification of an individual option contract."""

    symbol: str
    underlying_symbol: str
    strike_price: float
    expiration_date: str
    contract_type: Literal["call", "put"]
    bid: float = 0.0
    ask: float = 0.0
    last_price: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    quote_timestamp: Optional[str] = None


class OptionChainSnapshot(BaseModel):
    """Snapshot of an option chain for a given underlying symbol."""

    underlying_symbol: str
    underlying_price: float
    timestamp: str
    contracts: List[OptionContract] = Field(default_factory=list)


class MarketSnapshot(BaseModel):
    """Market context snapshot including underlying price, VIX, and option chain."""

    symbol: str
    underlying_price: float
    vix: float = 0.0
    timestamp: str
    option_chain: Optional[OptionChainSnapshot] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volume_24h: Optional[int] = None


class FeatherlessCatalystResponse(BaseModel):
    """Structured response schema returned by Featherless AI catalyst analysis."""

    catalyst_risk: Literal["low", "medium", "high"]
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score between 0.0 and 1.0")
    rationale: str
    flagged_events: List[str] = Field(default_factory=list)


class SentimentResult(BaseModel):
    """Output from Featherless LLM market sentiment analysis."""

    score: float = Field(0.0, ge=-1.0, le=1.0, description="Sentiment score between -1.0 (bearish) and 1.0 (bullish)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence score between 0.0 and 1.0")
    rationale: str
    model_used: str
    source_tags: List[str] = Field(default_factory=list)
    timestamp: str
    catalyst_risk: Literal["low", "medium", "high"] = "high"
    flagged_events: List[str] = Field(default_factory=list)


class CondorLeg(BaseModel):
    """An individual leg of an Iron Condor position."""

    contract: OptionContract
    side: Literal["buy", "sell"]
    quantity: int = 1
    leg_type: Literal["put_long", "put_short", "call_short", "call_long"]


class IronCondor(BaseModel):
    """Structure representing a 4-leg Iron Condor strategy."""

    underlying_symbol: str
    put_long: CondorLeg
    put_short: CondorLeg
    call_short: CondorLeg
    call_long: CondorLeg
    net_credit: float = 0.0
    max_risk: float = 0.0
    max_profit: float = 0.0
    wing_width: float = 0.0
    expiration_date: str
    lower_breakeven: Optional[float] = None
    upper_breakeven: Optional[float] = None


class TradeProposal(BaseModel):
    """A generated proposal for executing an Iron Condor trade."""

    proposal_id: str
    symbol: str
    condor: Optional[IronCondor] = None
    candidate: Optional[StrategyCandidate] = None
    sentiment: Optional[SentimentResult] = None
    quantity: int = 1
    created_at: str
    rationale: str = ""


class RiskDecision(BaseModel):
    """Result of evaluating a TradeProposal through the Risk Gate."""

    proposal_id: str = ""
    approved: bool
    rejections: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    risk_metrics: Dict[str, Any] = Field(default_factory=dict)
    adjusted_quantity: int = 0
    risk_score: float = 0.0
    rule_violations: List[str] = Field(default_factory=list)
    max_allowed_loss: float = 0.0
    reason: str = ""


class ExecutionResult(BaseModel):
    """Result of order execution attempt via Alpaca API."""

    execution_id: str
    proposal_id: str
    order_id: Optional[str] = None
    status: Literal[
        "accepted", "new", "partially_filled", "filled", "canceled", "rejected", "expired", "pending", "failed"
    ]
    filled_credit: float = 0.0
    filled_at: Optional[str] = None
    legs_execution: List[dict] = Field(default_factory=list)
    error_message: Optional[str] = None


class PositionAction(BaseModel):
    """Action to take on an active position (open, close, roll, adjust, hold)."""

    action_type: Literal["open", "close", "roll", "adjust", "hold"] = "hold"
    action: Optional[Literal["open", "close", "roll", "adjust", "hold"]] = None
    symbol: str
    position_id: Optional[str] = None
    expiration: Optional[str] = None
    reason: str = ""
    entry_credit: Optional[float] = 0.0
    current_debit_to_close: Optional[float] = 0.0
    unrealized_pnl: Optional[float] = 0.0
    dte: Optional[int] = None
    short_put_delta: Optional[float] = None
    short_call_delta: Optional[float] = None
    target_condor: Optional[IronCondor] = None
    strategy: Optional[str] = "iron_condor"
    metrics: Optional[Dict[str, Any]] = None
    timestamp: str = ""


class StrategyLeg(BaseModel):
    """Standardized representation of an individual strategy leg."""

    contract: OptionContract
    side: Literal["buy", "sell", "BUY", "SELL"]
    quantity: int = 1
    leg_type: str = ""
    intent: Optional[str] = None
    position_intent: Optional[str] = None


# Alias for backward and multi-strategy compatibility
OptionLeg = StrategyLeg


class StrategyCandidate(BaseModel):
    """Standardized multi-strategy candidate trade object."""

    strategy_name: str
    underlying: str
    underlying_symbol: Optional[str] = None
    expiration: str
    expiration_date: Optional[str] = None
    legs: List[StrategyLeg] = Field(default_factory=list)
    direction: Optional[Literal["bullish", "bearish", "neutral"]] = "neutral"
    entry_credit: float = 0.0
    entry_debit: float = 0.0
    net_credit: Optional[float] = None
    net_credit_or_debit: Optional[float] = None
    max_profit: float = 0.0
    max_loss: float = 0.0
    max_risk: Optional[float] = None
    breakeven: Optional[float] = None
    breakevens: List[float] = Field(default_factory=list)
    wing_width: Optional[float] = None
    dte: int = 0
    short_delta: Optional[float] = None
    probability_estimate_if_available: Optional[float] = None
    market_regime: Optional[str] = None
    sentiment_risk: Optional[str] = None
    confidence: Optional[float] = None
    required_buying_power: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    liquidity_score: Optional[float] = None
    candidate_score: Optional[float] = None
    liquidity_metrics: Dict[str, Any] = Field(default_factory=dict)
    quote_timestamps: Dict[str, str] = Field(default_factory=dict)
    rationale: str = ""
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
