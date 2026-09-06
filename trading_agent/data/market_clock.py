"""
Market clock and session hours helper.
"""

from datetime import datetime, timezone


class MarketClock:
    """Helper to check trading session status and market open state."""

    def __init__(self):
        pass

    def is_market_open(self) -> bool:
        """Check if regular market hours are open (simplified mock check)."""
        now = datetime.now(timezone.utc)
        # Check weekday (Monday=0 to Friday=4)
        return now.weekday() < 5

    def get_time_to_close_minutes(self) -> int:
        """Minutes remaining until regular market close."""
        return 390
