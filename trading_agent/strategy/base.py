"""
Base Options Strategy Interface and Protocol.
Defines the standard contract that every quantitative options strategy must implement.

CRITICAL ARCHITECTURAL SAFETY INVARIANT:
The strategy layer is strictly a candidate trade generator.
Strategies MUST NOT place orders directly, access broker execution endpoints,
or bypass deterministic risk gates.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Union, Tuple
from trading_agent.models import OptionChainSnapshot, OptionContract


class BaseOptionsStrategy(ABC):
    """Abstract Base Class for Options Trading Strategies."""

    @abstractmethod
    def strategy_name(self) -> str:
        """Return the unique identifier for this options strategy (e.g. 'iron_condor')."""
        pass

    @abstractmethod
    def supports_market_regime(self, regime_context: Dict[str, Any]) -> bool:
        """Evaluate if the strategy is suited for current market regime conditions.
        
        Args:
            regime_context: Dictionary containing technical regime metrics
                            (e.g., ADX, Bollinger width percentile, IV Rank, range-bound flag).
        Returns:
            True if current regime meets strategy criteria, False otherwise.
        """
        pass

    @abstractmethod
    def generate_candidate(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[Any]:
        """Construct a strategy candidate trade from the options chain.
        
        Returns:
            Structured strategy candidate object (e.g. IronCondor) or None if no valid setup exists.
        """
        pass

    @abstractmethod
    def validate_candidate(self, candidate: Any) -> Tuple[bool, List[str]]:
        """Validate structure, leg geometry, Greeks, and liquidity of a candidate trade.
        
        Returns:
            Tuple of (is_valid: bool, rejection_reasons: List[str]).
        """
        pass

    @abstractmethod
    def calculate_max_profit(self, candidate: Any) -> float:
        """Calculate maximum profit in dollars per contract."""
        pass

    @abstractmethod
    def calculate_max_loss(self, candidate: Any) -> float:
        """Calculate maximum defined risk/loss in dollars per contract."""
        pass

    @abstractmethod
    def calculate_breakevens(self, candidate: Any) -> Tuple[float, ...]:
        """Calculate breakeven underlying prices at expiration."""
        pass

    @abstractmethod
    def score_candidate(
        self,
        candidate: Any,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Score candidate attractiveness (0.0 to 100.0) based on risk/reward and volatility edge."""
        pass
