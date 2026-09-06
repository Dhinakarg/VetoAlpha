"""
Crypto Portfolio Manager & Position Lifecycle Engine.
Tracks active crypto positions, monitors exit criteria (Take-Profit, Stop-Loss, Time-Exit, Signal-Exit),
and updates portfolio balance state.
Isolated from options portfolio manager.
"""

import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

from trading_agent.crypto.crypto_execution import CryptoOrderPayload
from trading_agent.crypto.crypto_data import CryptoMarketData

logger = logging.getLogger(__name__)


@dataclass
class CryptoPosition:
    position_id: str
    symbol: str
    direction: str  # "long" or "short"
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    entry_timestamp: str
    strategy: str
    regime: str
    entry_fee_usd: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    status: str = "OPEN"  # "OPEN" or "CLOSED"
    exit_price: Optional[float] = None
    exit_timestamp: Optional[str] = None
    exit_reason: Optional[str] = None
    exit_fee_usd: float = 0.0
    realized_pnl: float = 0.0


class CryptoPortfolioManager:
    """Manages active crypto positions and evaluates deterministic exits."""

    def __init__(self, initial_equity: float = 100000.0, fee_pct: float = 0.0005):
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.cash = initial_equity
        self.fee_pct = fee_pct
        self.active_positions: Dict[str, CryptoPosition] = {}
        self.closed_positions: List[CryptoPosition] = []

    def open_position_from_order(self, order: CryptoOrderPayload, regime: str = "UNKNOWN") -> Optional[CryptoPosition]:
        """Create and track an active position from a filled or submitted order."""
        if order.status not in ("FILLED", "DRY_RUN_APPROVED", "SUBMITTED_TO_ALPACA"):
            logger.warning(f"Cannot open position for order {order.order_id} with invalid status '{order.status}'.")
            return None

        pos_id = f"POS-{order.order_id}"
        entry_p = order.filled_avg_price if order.filled_avg_price > 0 else order.limit_price
        qty = order.filled_qty if order.filled_qty > 0 else order.quantity

        pos = CryptoPosition(
            position_id=pos_id,
            symbol=order.symbol,
            direction=order.direction,
            entry_price=entry_p,
            quantity=qty,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            entry_timestamp=order.submission_timestamp,
            strategy=order.fingerprint.split(":")[0] if ":" in order.fingerprint else "UNKNOWN",
            regime=regime,
            entry_fee_usd=order.fee_usd,
            current_price=entry_p,
            unrealized_pnl=0.0,
        )
        self.active_positions[pos_id] = pos
        logger.info(f"Opened active crypto position {pos_id} for {order.symbol} ({order.direction} qty {qty} @ ${entry_p:.2f})")
        return pos

    def update_and_check_exits(self, market_data_map: Dict[str, CryptoMarketData]) -> List[CryptoPosition]:
        """Update mark-to-market prices and evaluate exit conditions for all active positions."""
        closed = []
        for pos_id, pos in list(self.active_positions.items()):
            data = market_data_map.get(pos.symbol)
            if not data or not data.is_valid or data.current_price <= 0:
                continue

            current_p = data.current_price
            pos.current_price = current_p

            # Calculate unrealized P&L
            if pos.direction == "long":
                pos.unrealized_pnl = (current_p - pos.entry_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.entry_price - current_p) * pos.quantity

            # Evaluate Exit Conditions
            exit_reason = None
            if pos.direction == "long":
                if current_p <= pos.stop_loss:
                    exit_reason = "STOP_LOSS"
                elif current_p >= pos.take_profit:
                    exit_reason = "TAKE_PROFIT"
            else:  # short
                if current_p >= pos.stop_loss:
                    exit_reason = "STOP_LOSS"
                elif current_p <= pos.take_profit:
                    exit_reason = "TAKE_PROFIT"

            # Check Time Exit (max 72 hours)
            try:
                entry_dt = datetime.fromisoformat(pos.entry_timestamp)
                if datetime.now(timezone.utc) - entry_dt > timedelta(hours=72):
                    exit_reason = "TIME_EXIT"
            except Exception:
                pass

            if exit_reason:
                closed_pos = self.close_position(pos_id, current_p, exit_reason)
                closed.append(closed_pos)

        return closed

    def close_position(self, position_id: str, exit_price: float, reason: str, exit_timestamp: Optional[str] = None) -> CryptoPosition:
        """Close an active position, calculate realized P&L and fees, and update balance."""
        if position_id not in self.active_positions:
            raise KeyError(f"Position {position_id} not found in active positions")

        pos = self.active_positions.pop(position_id)
        pos.exit_price = exit_price
        pos.exit_timestamp = exit_timestamp or datetime.now(timezone.utc).isoformat()
        pos.exit_reason = reason
        pos.exit_fee_usd = round(pos.quantity * exit_price * self.fee_pct, 4)

        if pos.direction == "long":
            gross_pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.quantity

        pos.realized_pnl = round(gross_pnl - pos.entry_fee_usd - pos.exit_fee_usd, 2)
        pos.status = "CLOSED"

        self.cash += pos.realized_pnl
        self.equity += pos.realized_pnl
        self.closed_positions.append(pos)

        logger.info(
            f"Closed crypto position {pos.position_id} ({pos.symbol} {pos.direction}) "
            f"via {reason} @ ${exit_price:.2f}. Realized P&L: ${pos.realized_pnl:.2f}"
        )
        return pos
