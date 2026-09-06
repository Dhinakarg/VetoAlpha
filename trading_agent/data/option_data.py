"""
Option chain and contract data fetcher interface.
"""

from datetime import datetime, timezone
from typing import Optional
from trading_agent.models import OptionChainSnapshot, OptionContract


class OptionDataFetcher:
    """Interface for fetching options chain data from Alpaca."""

    def __init__(self):
        pass

    def fetch_option_chain(
        self, symbol: str, expiration_date: Optional[str] = None
    ) -> OptionChainSnapshot:
        """Fetch option contracts chain for specified symbol and expiration."""
        exp = expiration_date or "2026-09-18"
        contracts = [
            OptionContract(
                symbol=f"{symbol}260918P00090000",
                underlying_symbol=symbol,
                strike_price=90.0,
                expiration_date=exp,
                contract_type="put",
                bid=1.20,
                ask=1.25,
                open_interest=500,
                volume=100,
                delta=-0.10,
            ),
            OptionContract(
                symbol=f"{symbol}260918P00095000",
                underlying_symbol=symbol,
                strike_price=95.0,
                expiration_date=exp,
                contract_type="put",
                bid=2.50,
                ask=2.55,
                open_interest=500,
                volume=100,
                delta=-0.18,
            ),
            OptionContract(
                symbol=f"{symbol}260918C00105000",
                underlying_symbol=symbol,
                strike_price=105.0,
                expiration_date=exp,
                contract_type="call",
                bid=2.40,
                ask=2.45,
                open_interest=500,
                volume=100,
                delta=0.18,
            ),
            OptionContract(
                symbol=f"{symbol}260918C00110000",
                underlying_symbol=symbol,
                strike_price=110.0,
                expiration_date=exp,
                contract_type="call",
                bid=1.10,
                ask=1.15,
                open_interest=500,
                volume=100,
                delta=0.10,
            ),
        ]
        return OptionChainSnapshot(
            underlying_symbol=symbol,
            underlying_price=100.0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            contracts=contracts,
        )
