"""
Strategy selector module alias and interface.
Re-exports StrategySelector and StrategyDecision.
"""

from trading_agent.strategy.strategy_selector import StrategySelector, StrategyDecision

__all__ = ["StrategySelector", "StrategyDecision"]
