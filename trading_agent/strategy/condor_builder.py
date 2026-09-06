"""
Deterministic Iron Condor option candidate selection engine.
Maintains 100% backwards compatibility by subclassing IronCondorStrategy.
Contains NO LLM calls.
"""

from typing import Dict, List, Optional, Any, Union, Tuple
from trading_agent.models import (
    OptionChainSnapshot,
    OptionContract,
    CondorLeg,
    IronCondor,
)
from trading_agent.strategy.iron_condor import IronCondorStrategy, _parse_date


class IronCondorBuilder(IronCondorStrategy):
    """Deterministic selection engine for 4-leg Iron Condor options strategies.
    
    Subclasses IronCondorStrategy to preserve backwards compatibility.
    """

    def build_condor(
        self,
        option_chain: Union[OptionChainSnapshot, List[OptionContract]],
        underlying_price: float,
        current_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        target_short_delta: Optional[float] = None,
        wing_width: Optional[float] = None,
        target_dte_min: Optional[int] = None,
        target_dte_max: Optional[int] = None,
        **kwargs: Any,
    ) -> Optional[IronCondor]:
        """Construct a deterministic IronCondor from option chain or return None if any rule fails."""
        return self.generate_candidate(
            option_chain=option_chain,
            underlying_price=underlying_price,
            current_date=current_date,
            config=config,
            target_short_delta=target_short_delta,
            wing_width=wing_width,
            target_dte_min=target_dte_min,
            target_dte_max=target_dte_max,
            **kwargs,
        )
