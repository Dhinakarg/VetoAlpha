"""
Strategy subpackage for market signals, Featherless LLM sentiment, and Iron Condor construction.
"""

from trading_agent.strategy.signals import (
    SignalGenerator,
    calculate_adx,
    is_range_bound,
    calculate_bollinger_width,
    bollinger_width_percentile,
    extract_atm_iv,
    calculate_iv_rank,
    regime_and_volatility_gate,
)
from trading_agent.strategy.sentiment import FeatherlessSentimentAnalyzer
from trading_agent.strategy.base import BaseOptionsStrategy
from trading_agent.strategy.iron_condor import IronCondorStrategy
from trading_agent.strategy.condor_builder import IronCondorBuilder
from trading_agent.strategy.bull_put_spread import BullPutSpreadStrategy, build_bull_put_spread
from trading_agent.strategy.bear_call_spread import BearCallSpreadStrategy, build_bear_call_spread
from trading_agent.strategy.long_call import LongCallStrategy, build_long_call
from trading_agent.strategy.long_put import LongPutStrategy, build_long_put
from trading_agent.strategy.ai_strategy_advisor import AIStrategyAdvisor, StrategyAdvice
from trading_agent.strategy.regime import MarketRegime, detect_market_regime, classify_market_regime
from trading_agent.strategy.strategy_selector import StrategySelector, StrategyDecision
from trading_agent.strategy.featherless_client import (
    FeatherlessClient,
    FeatherlessError,
    FeatherlessAPIError,
    FeatherlessValidationError,
    FeatherlessBudgetExceededError,
)

__all__ = [
    "BaseOptionsStrategy",
    "IronCondorStrategy",
    "BullPutSpreadStrategy",
    "build_bull_put_spread",
    "BearCallSpreadStrategy",
    "build_bear_call_spread",
    "LongCallStrategy",
    "build_long_call",
    "LongPutStrategy",
    "build_long_put",
    "AIStrategyAdvisor",
    "StrategyAdvice",
    "MarketRegime",
    "detect_market_regime",
    "classify_market_regime",
    "StrategySelector",
    "StrategyDecision",
    "SignalGenerator",
    "calculate_adx",
    "is_range_bound",
    "calculate_bollinger_width",
    "bollinger_width_percentile",
    "extract_atm_iv",
    "calculate_iv_rank",
    "regime_and_volatility_gate",
    "FeatherlessSentimentAnalyzer",
    "IronCondorBuilder",
    "FeatherlessClient",
    "FeatherlessError",
    "FeatherlessAPIError",
    "FeatherlessValidationError",
    "FeatherlessBudgetExceededError",
]
