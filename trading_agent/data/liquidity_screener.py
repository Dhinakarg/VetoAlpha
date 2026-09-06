"""
Lightweight Symbol-Level Options Liquidity Screener.
Screens optionable underlyings before fetching heavy multi-leg option chains.
Filters on underlying price, daily volume, quote freshness, and ATM spread %.
STRICTLY READ-ONLY — ZERO CODE MUTATION.
"""

import logging
from typing import Dict, Any, Tuple
from trading_agent.config import settings

logger = logging.getLogger(__name__)


def screen_symbol_liquidity(
    symbol: str,
    underlying_price: float,
    daily_volume: int = 1000000,
    atm_spread_pct: float = 0.05,
    quote_age_seconds: float = 30.0,
) -> Tuple[bool, str]:
    """Lightweight symbol-level liquidity screen.
    
    Returns (passed: bool, reason: str).
    """
    # 1. Price Check
    if underlying_price < settings.liquidity_min_price:
        return False, f"UNDERLYING_PRICE_TOO_LOW (Price ${underlying_price:.2f} < ${settings.liquidity_min_price:.2f})"

    # 2. Volume Check
    if daily_volume < settings.liquidity_min_volume:
        return False, f"VOLUME_TOO_LOW (Volume {daily_volume:,} < {settings.liquidity_min_volume:,})"

    # 3. Quote Freshness Check
    if quote_age_seconds > settings.max_option_quote_age_seconds:
        return False, f"QUOTE_STALE (Age {quote_age_seconds:.1f}s > {settings.max_option_quote_age_seconds}s)"

    # 4. ATM Spread Check
    if atm_spread_pct > settings.liquidity_max_spread_pct:
        return False, f"OPTION_SPREAD_TOO_WIDE (Spread {atm_spread_pct*100:.1f}% > {settings.liquidity_max_spread_pct*100:.1f}%)"

    return True, "PASS"
