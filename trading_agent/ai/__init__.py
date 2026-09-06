"""
AI subpackage for contextual catalyst intelligence and market analysis.
Strictly isolated from order execution and broker endpoints.
"""

from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment

__all__ = ["CatalystIntelligenceAgent", "CatalystAssessment"]
