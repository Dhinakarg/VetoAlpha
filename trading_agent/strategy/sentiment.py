"""
Featherless LLM sentiment analyzer wrapper around FeatherlessClient.
"""

from typing import Optional, List, Dict
from trading_agent.models import SentimentResult
from trading_agent.strategy.featherless_client import FeatherlessClient


class FeatherlessSentimentAnalyzer:
    """Market sentiment analyzer wrapping FeatherlessClient."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        client: Optional[FeatherlessClient] = None,
    ):
        self.client = client or FeatherlessClient(
            api_key=api_key, base_url=base_url, model=model
        )

    def analyze_market_news(
        self,
        symbol: str,
        news_items: Optional[List[Dict]] = None,
        market_context: Optional[Dict] = None,
        upcoming_events: Optional[Dict] = None,
    ) -> SentimentResult:
        """Analyze market news and catalyst risk for target symbol."""
        news = news_items or []
        ctx = market_context or {"underlying_price": 100.0, "vix": 18.5}
        events = upcoming_events or {"earnings_date": None, "fomc_meeting": False}

        return self.client.analyze_catalyst_risk(
            symbol=symbol,
            market_context=ctx,
            news=news,
            upcoming_events=events,
        )
