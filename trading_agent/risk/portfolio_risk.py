"""
Portfolio-Level Correlation & Multi-Asset Exposure Manager.
Prevents correlated drawdowns across SPY, QQQ, and IWM by enforcing
aggregate portfolio delta limits and capping maximum simultaneous correlated positions.
"""

from typing import Dict, List, Any


DEFAULT_CORRELATION_GROUPS = {
    "US_INDEX": ["SPY", "QQQ", "IWM", "DIA", "VOO", "IVV", "SPX", "SPXW", "XSP", "DJX"],
    "US_LARGE_CAP": ["SPY", "SPX", "SPXW", "XSP", "VOO", "IVV"],
    "TECH": ["QQQ", "XLK", "NVDA", "AMD", "MSFT", "AAPL", "META", "GOOGL"],
    "SMALL_CAP": ["IWM"],
    "DOW": ["DIA", "DJX"],
    "VOLATILITY": ["VIX", "VIXW"],
    "ENERGY": ["XLE", "XOM", "CVX"],
    "FINANCIAL": ["XLF", "JPM", "BAC"],
    "BROAD_MARKET": ["SPY", "VOO", "IVV"],
}


class PortfolioCorrelationManager:
    """Multi-asset portfolio risk and correlation controller."""

    def __init__(
        self,
        max_aggregate_delta: float = 150.0,
        max_correlated_positions: int = 2,
        max_positions_per_group: int = 2,
        correlated_symbols: List[str] = None,
        correlation_groups: Dict[str, List[str]] = None,
    ):
        self.max_aggregate_delta = max_aggregate_delta
        self.max_correlated_positions = max_correlated_positions
        self.max_positions_per_group = max_positions_per_group
        self.correlated_symbols = correlated_symbols or ["SPY", "QQQ", "IWM"]
        self.correlation_groups = correlation_groups or DEFAULT_CORRELATION_GROUPS

    def evaluate_portfolio_exposure(
        self,
        proposed_symbol: str,
        proposed_delta: float,
        open_positions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evaluate whether adding proposed_symbol trade violates correlation exposure limits."""
        # 1. Check legacy index basket correlation count limit
        if proposed_symbol in self.correlated_symbols:
            correlated_count = sum(1 for p in open_positions if p.get("symbol") in self.correlated_symbols)
            if correlated_count >= self.max_correlated_positions:
                return {
                    "allowed": False,
                    "reason": f"Correlated exposure limit reached: Maximum {self.max_correlated_positions} correlated positions allowed across primary basket.",
                }

        # 2. Check multi-group correlation limits
        for grp_name, grp_syms in self.correlation_groups.items():
            if proposed_symbol in grp_syms:
                grp_count = sum(1 for p in open_positions if p.get("symbol") in grp_syms)
                if grp_count >= self.max_positions_per_group:
                    return {
                        "allowed": False,
                        "reason": f"Correlation group '{grp_name}' limit reached: Maximum {self.max_positions_per_group} positions allowed per correlation group.",
                    }

        # 3. Check total aggregate portfolio delta across all positions
        total_delta = abs(proposed_delta)
        for pos in open_positions:
            total_delta += abs(float(pos.get("delta", 20.0)))

        if total_delta > self.max_aggregate_delta:
            return {
                "allowed": False,
                "reason": f"Aggregate portfolio delta limit exceeded: Proposed total delta ({total_delta:.1f}) exceeds budget ({self.max_aggregate_delta:.1f}).",
            }

        return {"allowed": True, "reason": "Portfolio correlation checks passed."}
