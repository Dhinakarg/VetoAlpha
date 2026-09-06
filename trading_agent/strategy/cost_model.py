"""
Realistic Options Execution & Cost Modeling Engine.
Calculates bid/ask spreads, entry/exit slippage, commissions, regulatory fees,
and net P&L attribution per trade.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class TradeCostBreakdown:
    gross_pnl: float
    commissions: float
    regulatory_fees: float
    entry_slippage: float
    exit_slippage: float
    total_costs: float
    net_pnl: float
    fill_price_entry: float
    fill_price_exit: float
    bid_entry: float
    ask_entry: float
    bid_exit: float
    ask_exit: float


class OptionsCostModel:
    """Realistic transaction friction & slippage calculator for options trading."""

    def __init__(
        self,
        commission_per_contract: float = 1.50,
        regulatory_fee_per_contract: float = 0.05,
        slippage_pct: float = 0.02,  # 2% of bid-ask spread
    ):
        self.commission_per_contract = commission_per_contract
        self.regulatory_fee_per_contract = regulatory_fee_per_contract
        self.slippage_pct = slippage_pct

    def calculate_trade_costs(
        self,
        strategy_name: str,
        num_legs: int,
        quantity: int,
        entry_mid: float,
        exit_mid: float,
        entry_spread: float,
        exit_spread: float,
        is_credit: bool = True,
    ) -> TradeCostBreakdown:
        """Calculate exact net P&L accounting for spread, slippage, and fees.
        
        Credit strategies (Iron Condor, Spreads):
          - Entry: Net credit received at Mid - (Spread/2 + Slippage)
          - Exit: Net debit paid at Mid + (Spread/2 + Slippage)
        
        Debit strategies (Long Call, Long Put):
          - Entry: Net debit paid at Ask (Mid + Spread/2 + Slippage)
          - Exit: Net credit received at Bid (Mid - Spread/2 - Slippage)
        """
        # Calculate realistic fill prices
        half_entry_spread = entry_spread / 2.0
        half_exit_spread = exit_spread / 2.0

        entry_slip = half_entry_spread * self.slippage_pct
        exit_slip = half_exit_spread * self.slippage_pct

        if is_credit:
            # Credit strategy: Sell to open, Buy to close
            bid_entry = entry_mid - half_entry_spread
            ask_entry = entry_mid + half_entry_spread
            fill_price_entry = max(0.01, entry_mid - half_entry_spread - entry_slip)  # Conservative net credit

            bid_exit = exit_mid - half_exit_spread
            ask_exit = exit_mid + half_exit_spread
            fill_price_exit = exit_mid + half_exit_spread + exit_slip  # Conservative closing debit

            gross_pnl = (fill_price_entry - fill_price_exit) * 100.0 * quantity
        else:
            # Debit strategy: Buy to open, Sell to close
            bid_entry = entry_mid - half_entry_spread
            ask_entry = entry_mid + half_entry_spread
            fill_price_entry = entry_mid + half_entry_spread + entry_slip  # Pay ask + slip

            bid_exit = exit_mid - half_exit_spread
            ask_exit = exit_mid + half_exit_spread
            fill_price_exit = max(0.0, exit_mid - half_exit_spread - exit_slip)  # Sell at bid - slip

            gross_pnl = (fill_price_exit - fill_price_entry) * 100.0 * quantity

        # Calculate fees across all contract legs for entry and exit
        total_contract_trades = num_legs * quantity * 2  # Entry + Exit
        commissions = total_contract_trades * self.commission_per_contract
        regulatory_fees = total_contract_trades * self.regulatory_fee_per_contract

        total_entry_slippage_usd = entry_slip * 100.0 * num_legs * quantity
        total_exit_slippage_usd = exit_slip * 100.0 * num_legs * quantity

        total_costs = commissions + regulatory_fees + total_entry_slippage_usd + total_exit_slippage_usd
        net_pnl = gross_pnl - total_costs

        return TradeCostBreakdown(
            gross_pnl=round(gross_pnl, 2),
            commissions=round(commissions, 2),
            regulatory_fees=round(regulatory_fees, 2),
            entry_slippage=round(total_entry_slippage_usd, 2),
            exit_slippage=round(total_exit_slippage_usd, 2),
            total_costs=round(total_costs, 2),
            net_pnl=round(net_pnl, 2),
            fill_price_entry=round(fill_price_entry, 2),
            fill_price_exit=round(fill_price_exit, 2),
            bid_entry=round(bid_entry, 2),
            ask_entry=round(ask_entry, 2),
            bid_exit=round(bid_exit, 2),
            ask_exit=round(ask_exit, 2),
        )
