"""
Crypto Paper Execution Simulator & Order Lifecycle Engine.
Simulates realistic order execution with slippage, fees, and order state tracking.
STRICTLY PAPER/SIMULATION ONLY — ZERO LIVE TRADING PATHWAY.
"""

import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from trading_agent.crypto.crypto_strategy import CryptoStrategyCandidate
from trading_agent.crypto.crypto_risk import CryptoRiskDecision

logger = logging.getLogger(__name__)


@dataclass
class CryptoOrderPayload:
    order_id: str
    symbol: str
    direction: str  # "long" or "short"
    order_type: str  # "limit" or "market"
    quantity: float
    limit_price: float
    stop_loss: float
    take_profit: float
    submission_timestamp: str
    execution_mode: str
    fingerprint: str
    side: str = "buy"  # "buy" or "sell"
    status: str = "NEW"
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0
    fee_usd: float = 0.0


class CryptoExecutionEngine:
    """Simulated Execution Engine for Crypto Validation."""

    def __init__(
        self,
        mode: str = "DRY_RUN",
        slippage_pct: float = 0.0002,
        fee_pct: float = 0.0005,
        submit_alpaca: bool = False,
    ):
        self.mode = mode.upper()
        self.slippage_pct = slippage_pct
        self.fee_pct = fee_pct
        self.submit_alpaca = submit_alpaca

        if self.submit_alpaca:
            self._trading_client = None
            try:
                from trading_agent.config import settings
                from alpaca.trading.client import TradingClient

                if settings.alpaca_api_key and settings.alpaca_secret_key:
                    self._trading_client = TradingClient(
                        api_key=settings.alpaca_api_key,
                        secret_key=settings.alpaca_secret_key,
                        paper=True,
                    )
            except Exception as e:
                logger.warning(f"Failed to initialize Alpaca TradingClient for crypto: {e}")

    def build_order_payload(
        self,
        risk_decision: CryptoRiskDecision,
        override_timestamp: Optional[str] = None,
        override_price: Optional[float] = None,
    ) -> Optional[CryptoOrderPayload]:
        """Build structured order payload from approved risk decision."""
        if not risk_decision.approved or not risk_decision.candidate:
            return None

        c = risk_decision.candidate
        order_id = f"CRYPTO-{uuid.uuid4().hex[:8]}"
        fp = f"{c.strategy}:{c.direction}:{c.symbol}:{c.entry_price}:{risk_decision.position_size}"

        base_price = override_price if override_price is not None else c.entry_price
        sub_timestamp = override_timestamp or datetime.now(timezone.utc).isoformat()

        # Adjust price for slippage
        adjusted_entry = base_price * (1.0 + self.slippage_pct) if c.direction == "long" else base_price * (1.0 - self.slippage_pct)
        side = "buy" if c.direction == "long" else "sell"

        return CryptoOrderPayload(
            order_id=order_id,
            symbol=c.symbol,
            direction=c.direction,
            order_type="limit",
            quantity=risk_decision.position_size,
            limit_price=round(adjusted_entry, 2),
            stop_loss=c.stop_loss,
            take_profit=c.take_profit,
            submission_timestamp=sub_timestamp,
            execution_mode=self.mode,
            fingerprint=fp,
            side=side,
            status="NEW",
        )

    def build_exit_order_payload(
        self,
        position_id: str,
        symbol: str,
        direction: str,
        quantity: float,
        exit_price: float,
        exit_reason: str,
    ) -> CryptoOrderPayload:
        """Build structured order payload for closing an existing position."""
        side = "sell" if direction == "long" else "buy"
        order_id = f"CRYPTO-EXIT-{uuid.uuid4().hex[:8]}"
        fp = f"EXIT:{position_id}:{exit_reason}"

        return CryptoOrderPayload(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            order_type="market",
            quantity=quantity,
            limit_price=exit_price,
            stop_loss=0.0,
            take_profit=0.0,
            submission_timestamp=datetime.now(timezone.utc).isoformat(),
            execution_mode=self.mode,
            fingerprint=fp,
            side=side,
            status="NEW",
        )

    def execute_order(self, payload: CryptoOrderPayload) -> CryptoOrderPayload:
        """Simulate order execution through lifecycle states and optionally submit paper order to Alpaca."""
        if self.mode == "DRY_RUN":
            payload.status = "DRY_RUN_APPROVED"
            logger.info(f"[CRYPTO DRY RUN] Approved order payload for {payload.symbol} {payload.direction} qty {payload.quantity}")
            return payload

        # Optional submit to Alpaca Paper Trading Account
        if self.submit_alpaca and getattr(self, "_trading_client", None):
            try:
                from alpaca.trading.requests import MarketOrderRequest
                from alpaca.trading.enums import OrderSide, TimeInForce

                # Exits close held paper positions -> OrderSide.SELL; Entries open positions -> OrderSide.BUY
                if payload.fingerprint.startswith("EXIT:"):
                    alpaca_side = OrderSide.SELL
                else:
                    alpaca_side = OrderSide.BUY

                sym_upper = payload.symbol.upper()
                if "ETH" in sym_upper:
                    order_qty = max(0.01, round(payload.quantity, 4))
                elif "SOL" in sym_upper:
                    order_qty = max(0.2, round(payload.quantity, 4))
                else:
                    order_qty = max(0.001, round(payload.quantity, 4))

                req = MarketOrderRequest(
                    symbol=payload.symbol,
                    qty=order_qty,
                    side=alpaca_side,
                    time_in_force=TimeInForce.GTC,
                )
                alpaca_order = self._trading_client.submit_order(req)
                payload.order_id = f"ALPACA-{alpaca_order.id}"
                payload.status = "SUBMITTED_TO_ALPACA"
                logger.info(f"[ALPACA PAPER CRYPTO ORDER SUBMITTED] Order ID: {alpaca_order.id} | Symbol: {payload.symbol} | Side: {alpaca_side.value} | Qty: {order_qty}")
            except Exception as e:
                logger.error(f"Alpaca paper crypto order submission error: {e}")

        # PAPER_SIMULATION mode internal fill execution
        if payload.status != "SUBMITTED_TO_ALPACA":
            payload.status = "FILLED"
        payload.filled_qty = payload.quantity
        payload.filled_avg_price = payload.limit_price
        payload.fee_usd = round(payload.quantity * payload.filled_avg_price * self.fee_pct, 4)

        logger.info(
            f"[CRYPTO PAPER SIMULATION] Order {payload.order_id}: {payload.symbol} {payload.direction} "
            f"qty {payload.quantity} @ ${payload.filled_avg_price:.2f} (Fee: ${payload.fee_usd:.4f})"
        )
        return payload
