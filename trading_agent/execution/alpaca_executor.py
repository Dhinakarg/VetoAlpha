"""
Multi-leg Iron Condor options order execution engine using alpaca-py SDK.
Handles opening and closing 4-leg orders with strict position intents, limit pricing,
dry-run safety, and order lifecycle tracking.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union

from trading_agent.config import settings
from trading_agent.models import (
    IronCondor,
    TradeProposal,
    RiskDecision,
    ExecutionResult,
    StrategyCandidate,
    AccountSnapshot,
)
from trading_agent.risk.safety import validate_paper_execution_environment, PaperExecutionSafetyError

logger = logging.getLogger(__name__)


class AlpacaExecutor:
    """Multi-leg option order execution wrapper for Alpaca Trading API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: Optional[bool] = None,
        dry_run: bool = True,
    ):
        self.api_key = api_key or settings.alpaca_api_key
        self.secret_key = secret_key or settings.alpaca_secret_key
        self.paper = paper if paper is not None else settings.alpaca_paper
        self.dry_run = dry_run

        # Print Mandatory Live Paper Execution Safety Banner
        self.print_paper_safety_check_banner()

        self._trading_client = None
        if self.api_key and self.secret_key:
            self._init_client()

    def print_paper_safety_check_banner(self) -> Dict[str, Any]:
        """Print and return mandatory Paper Execution Safety Check status."""
        status = {
            "account_mode": "PAPER ONLY" if self.paper else "LIVE (PROHIBITED)",
            "paper": bool(self.paper),
            "live_trading": False,
            "new_entries": "ENABLED (PAPER ONLY)",
            "exits": "ENABLED",
            "risk_gate": "ACTIVE",
            "circuit_breaker": "ACTIVE",
        }
        banner = f"""
============================================================
PAPER EXECUTION SAFETY CHECK
------------------------------------------------------------
Account Mode:    {status['account_mode']}
Paper:           {status['paper']}
Live Trading:    {status['live_trading']} (LOCKED)
New Entries:     {status['new_entries']}
Exits:           {status['exits']}
Risk Gate:       {status['risk_gate']}
Circuit Breaker: {status['circuit_breaker']}
============================================================
"""
        logger.info(banner)
        if not self.paper:
            raise PaperExecutionSafetyError("LIVE TRADING ATTEMPT DETECTED: System locked to PAPER mode ONLY.")
        return status

    def _init_client(self):
        try:
            from alpaca.trading.client import TradingClient

            self._trading_client = TradingClient(
                api_key=self.api_key, secret_key=self.secret_key, paper=self.paper
            )
        except Exception as e:
            logger.error(f"Failed to initialize Alpaca TradingClient in executor: {e}")

    def place_condor_order(
        self,
        condor: IronCondor,
        quantity: int = 1,
        dry_run: Optional[bool] = None,
    ) -> ExecutionResult:
        """Place opening 4-leg Iron Condor multi-leg limit order.
        
        Legs & Position Intents:
        - Long Put:   BUY  (BUY_TO_OPEN)
        - Short Put:  SELL (SELL_TO_OPEN)
        - Short Call: SELL (SELL_TO_OPEN)
        - Long Call:  BUY  (BUY_TO_OPEN)
        """
        is_dry = dry_run if dry_run is not None else self.dry_run
        timestamp = datetime.now(timezone.utc).isoformat()

        if condor is None:
            return ExecutionResult(
                execution_id=f"exec_err_{int(datetime.now().timestamp())}",
                proposal_id="invalid_condor",
                status="rejected",
                error_message="Malformed order: condor payload is None.",
            )

        proposal_id = f"open_{condor.underlying_symbol}_{condor.expiration_date}"

        # 1. Validate that all four legs exist
        if not (getattr(condor, "put_long", None) and getattr(condor, "put_short", None) and getattr(condor, "call_short", None) and getattr(condor, "call_long", None)):
            return ExecutionResult(
                execution_id=f"exec_err_{int(datetime.now().timestamp())}",
                proposal_id=proposal_id,
                status="rejected",
                error_message="Incomplete Iron Condor: All four option legs must be present.",
            )

        try:
            from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
            from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce

            # 2. Construct 4 OptionLegRequests with exact position intents
            legs = [
                OptionLegRequest(
                    symbol=condor.put_long.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.BUY,
                    position_intent=PositionIntent.BUY_TO_OPEN,
                ),
                OptionLegRequest(
                    symbol=condor.put_short.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.SELL,
                    position_intent=PositionIntent.SELL_TO_OPEN,
                ),
                OptionLegRequest(
                    symbol=condor.call_short.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.SELL,
                    position_intent=PositionIntent.SELL_TO_OPEN,
                ),
                OptionLegRequest(
                    symbol=condor.call_long.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.BUY,
                    position_intent=PositionIntent.BUY_TO_OPEN,
                ),
            ]

            # 3. Construct multi-leg LimitOrderRequest (net credit limit price)
            limit_price = float(condor.net_credit)
            order_req = LimitOrderRequest(
                order_class=OrderClass.MLEG,
                qty=quantity,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
                legs=legs,
            )

            # 4. Handle DRY-RUN mode
            if is_dry or not self._trading_client:
                logger.info(
                    f"[DRY RUN] Built 4-leg Iron Condor order for {condor.underlying_symbol}: "
                    f"Qty={quantity}, Limit Credit=${limit_price:.2f}, Legs={[l.symbol for l in legs]}"
                )
                return ExecutionResult(
                    execution_id=f"dry_exec_{int(datetime.now().timestamp())}",
                    proposal_id=proposal_id,
                    order_id="DRY_RUN_ORDER_ID_001",
                    status="new",
                    filled_credit=limit_price,
                    filled_at=timestamp,
                    legs_execution=[
                        {"leg": "put_long", "symbol": condor.put_long.contract.symbol, "side": "buy", "intent": "buy_to_open", "filled": False},
                        {"leg": "put_short", "symbol": condor.put_short.contract.symbol, "side": "sell", "intent": "sell_to_open", "filled": False},
                        {"leg": "call_short", "symbol": condor.call_short.contract.symbol, "side": "sell", "intent": "sell_to_open", "filled": False},
                        {"leg": "call_long", "symbol": condor.call_long.contract.symbol, "side": "buy", "intent": "buy_to_open", "filled": False},
                    ],
                )

            # 5. Live Paper Submission
            validate_paper_execution_environment(self._trading_client)
            logger.info(f"Submitting 4-leg Iron Condor order to Alpaca (Paper: {self.paper})...")
            submitted_order = self._trading_client.submit_order(order_req)

            status_str = str(submitted_order.status).lower()
            status_map = {
                "accepted": "accepted",
                "new": "new",
                "partially_filled": "partially_filled",
                "filled": "filled",
                "canceled": "canceled",
                "rejected": "rejected",
                "expired": "expired",
                "pending_new": "new",
            }
            mapped_status = status_map.get(status_str, "new")

            return ExecutionResult(
                execution_id=f"exec_{submitted_order.id}",
                proposal_id=proposal_id,
                order_id=str(submitted_order.id),
                status=mapped_status,
                filled_credit=limit_price if mapped_status == "filled" else 0.0,
                filled_at=submitted_order.filled_at.isoformat() if getattr(submitted_order, "filled_at", None) else None,
                legs_execution=[
                    {"symbol": l.symbol, "side": str(l.side), "intent": str(l.position_intent)}
                    for l in legs
                ],
            )

        except Exception as e:
            logger.error(f"Alpaca multi-leg order submission failed: {e}")
            return ExecutionResult(
                execution_id=f"exec_err_{int(datetime.now().timestamp())}",
                proposal_id=proposal_id,
                status="rejected",
                error_message=f"Order submission error: {str(e)}",
            )

    def close_condor_order(
        self,
        condor: IronCondor,
        quantity: int = 1,
        close_limit_debit: Optional[float] = None,
        dry_run: Optional[bool] = None,
    ) -> ExecutionResult:
        """Place closing 4-leg Iron Condor multi-leg limit order.
        
        Legs & Position Intents (Reversed):
        - Long Put:   SELL (SELL_TO_CLOSE)
        - Short Put:  BUY  (BUY_TO_CLOSE)
        - Short Call: BUY  (BUY_TO_CLOSE)
        - Long Call:  SELL (SELL_TO_CLOSE)
        """
        is_dry = dry_run if dry_run is not None else self.dry_run
        timestamp = datetime.now(timezone.utc).isoformat()

        if condor is None:
            return ExecutionResult(
                execution_id=f"close_err_{int(datetime.now().timestamp())}",
                proposal_id="invalid_condor",
                status="rejected",
                error_message="Malformed order: condor payload is None.",
            )

        proposal_id = f"close_{condor.underlying_symbol}_{condor.expiration_date}"

        # 1. Validate that all four legs exist
        if not (getattr(condor, "put_long", None) and getattr(condor, "put_short", None) and getattr(condor, "call_short", None) and getattr(condor, "call_long", None)):
            return ExecutionResult(
                execution_id=f"close_err_{int(datetime.now().timestamp())}",
                proposal_id=proposal_id,
                status="rejected",
                error_message="Incomplete Iron Condor: All four option legs must be present for closing.",
            )

        try:
            from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
            from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce

            # Construct 4 closing legs with reversed position intents
            legs = [
                OptionLegRequest(
                    symbol=condor.put_long.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.SELL,
                    position_intent=PositionIntent.SELL_TO_CLOSE,
                ),
                OptionLegRequest(
                    symbol=condor.put_short.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.BUY,
                    position_intent=PositionIntent.BUY_TO_CLOSE,
                ),
                OptionLegRequest(
                    symbol=condor.call_short.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.BUY,
                    position_intent=PositionIntent.BUY_TO_CLOSE,
                ),
                OptionLegRequest(
                    symbol=condor.call_long.contract.symbol,
                    ratio_qty=1,
                    side=OrderSide.SELL,
                    position_intent=PositionIntent.SELL_TO_CLOSE,
                ),
            ]

            # Determine close limit debit price
            debit_price = float(close_limit_debit if close_limit_debit is not None else round(condor.net_credit * 0.5, 2))

            order_req = LimitOrderRequest(
                order_class=OrderClass.MLEG,
                qty=quantity,
                time_in_force=TimeInForce.DAY,
                limit_price=debit_price,
                legs=legs,
            )

            if is_dry or not self._trading_client:
                logger.info(
                    f"[DRY RUN] Built 4-leg Closing order for {condor.underlying_symbol}: "
                    f"Qty={quantity}, Close Debit=${debit_price:.2f}"
                )
                return ExecutionResult(
                    execution_id=f"dry_close_{int(datetime.now().timestamp())}",
                    proposal_id=proposal_id,
                    order_id="DRY_RUN_CLOSE_ORDER_ID_001",
                    status="new",
                    filled_credit=0.0,
                    filled_at=timestamp,
                    legs_execution=[
                        {"leg": "put_long", "symbol": condor.put_long.contract.symbol, "side": "sell", "intent": "sell_to_close"},
                        {"leg": "put_short", "symbol": condor.put_short.contract.symbol, "side": "buy", "intent": "buy_to_close"},
                        {"leg": "call_short", "symbol": condor.call_short.contract.symbol, "side": "buy", "intent": "buy_to_close"},
                        {"leg": "call_long", "symbol": condor.call_long.contract.symbol, "side": "sell", "intent": "sell_to_close"},
                    ],
                )

            validate_paper_execution_environment(self._trading_client)
            logger.info(f"Submitting 4-leg Closing order to Alpaca (Paper: {self.paper})...")
            submitted_order = self._trading_client.submit_order(order_req)
            return ExecutionResult(
                execution_id=f"exec_{submitted_order.id}",
                proposal_id=proposal_id,
                order_id=str(submitted_order.id),
                status=str(submitted_order.status).lower(),
                filled_credit=0.0,
            )

        except Exception as e:
            logger.error(f"Alpaca closing order submission failed: {e}")
            return ExecutionResult(
                execution_id=f"close_err_{int(datetime.now().timestamp())}",
                proposal_id=proposal_id,
                status="rejected",
                error_message=f"Close order submission error: {str(e)}",
            )

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Fetch current order details and status from Alpaca API."""
        if not self._trading_client:
            return {"order_id": order_id, "status": "simulated", "filled_qty": 0}

        try:
            order = self._trading_client.get_order_by_id(order_id)
            return {
                "order_id": str(order.id),
                "symbol": str(getattr(order, "symbol", "")),
                "status": str(order.status).lower(),
                "qty": float(getattr(order, "qty", 0) or 0),
                "filled_qty": float(getattr(order, "filled_qty", 0) or 0),
                "filled_avg_price": float(getattr(order, "filled_avg_price", 0.0) or 0.0),
                "created_at": order.created_at.isoformat() if getattr(order, "created_at", None) else "",
            }
        except Exception as e:
            logger.error(f"Failed to fetch status for order {order_id}: {e}")
            return {"order_id": order_id, "status": "error", "error": str(e)}

    def poll_order_status(
        self,
        order_id: str,
        max_attempts: int = 10,
        delay_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        """Poll order status with bounded attempts and return latest state."""
        import time
        latest = {"order_id": order_id, "status": "unknown"}
        for attempt in range(1, max_attempts + 1):
            latest = self.get_order_status(order_id)
            status = latest.get("status", "").lower()
            if status in ("filled", "canceled", "rejected", "expired"):
                return latest
            if attempt < max_attempts:
                time.sleep(delay_seconds)
        return latest

    def build_mleg_order(
        self,
        candidate_or_condor: Union[StrategyCandidate, IronCondor, Any],
        quantity: int = 1,
        intent_type: str = "open",
    ) -> Dict[str, Any]:
        """Build structured MLEG order specification."""
        if isinstance(candidate_or_condor, StrategyCandidate):
            strat = candidate_or_condor.strategy_name
            symbol = candidate_or_condor.underlying
            limit_price = float(candidate_or_condor.entry_credit)
            legs_spec = []
            for leg in candidate_or_condor.legs:
                side = leg.side.upper()
                if intent_type == "open":
                    intent = "BUY_TO_OPEN" if side == "BUY" else "SELL_TO_OPEN"
                else:
                    intent = "SELL_TO_CLOSE" if side == "BUY" else "BUY_TO_CLOSE"
                    side = "SELL" if side == "BUY" else "BUY"
                legs_spec.append({
                    "symbol": leg.contract.symbol,
                    "ratio_qty": leg.quantity,
                    "side": side,
                    "position_intent": intent,
                    "strike_price": leg.contract.strike_price,
                    "contract_type": leg.contract.contract_type,
                })
            return {
                "order_class": "mleg",
                "strategy": strat,
                "symbol": symbol,
                "qty": quantity,
                "limit_price": limit_price,
                "time_in_force": "day",
                "legs": legs_spec,
            }
        elif isinstance(candidate_or_condor, IronCondor):
            condor = candidate_or_condor
            limit_price = float(condor.net_credit)
            if intent_type == "open":
                legs_spec = [
                    {"symbol": condor.put_long.contract.symbol, "ratio_qty": 1, "side": "BUY", "position_intent": "BUY_TO_OPEN"},
                    {"symbol": condor.put_short.contract.symbol, "ratio_qty": 1, "side": "SELL", "position_intent": "SELL_TO_OPEN"},
                    {"symbol": condor.call_short.contract.symbol, "ratio_qty": 1, "side": "SELL", "position_intent": "SELL_TO_OPEN"},
                    {"symbol": condor.call_long.contract.symbol, "ratio_qty": 1, "side": "BUY", "position_intent": "BUY_TO_OPEN"},
                ]
            else:
                legs_spec = [
                    {"symbol": condor.put_long.contract.symbol, "ratio_qty": 1, "side": "SELL", "position_intent": "SELL_TO_CLOSE"},
                    {"symbol": condor.put_short.contract.symbol, "ratio_qty": 1, "side": "BUY", "position_intent": "BUY_TO_CLOSE"},
                    {"symbol": condor.call_short.contract.symbol, "ratio_qty": 1, "side": "BUY", "position_intent": "BUY_TO_CLOSE"},
                    {"symbol": condor.call_long.contract.symbol, "ratio_qty": 1, "side": "SELL", "position_intent": "SELL_TO_CLOSE"},
                ]
            return {
                "order_class": "mleg",
                "strategy": "iron_condor",
                "symbol": condor.underlying_symbol,
                "qty": quantity,
                "limit_price": limit_price,
                "time_in_force": "day",
                "legs": legs_spec,
            }
        return {"error": "Unknown order type"}

    def submit_mleg_order(
        self,
        candidate_or_condor: Any,
        quantity: int = 1,
        dry_run: Optional[bool] = None,
    ) -> ExecutionResult:
        """Submit opening multi-leg order for any supported options strategy to Alpaca Paper API."""
        return self.place_strategy_order(candidate_or_condor=candidate_or_condor, quantity=quantity, dry_run=dry_run)

    def place_strategy_order(
        self,
        candidate_or_condor: Union[StrategyCandidate, IronCondor, Any],
        quantity: int = 1,
        dry_run: Optional[bool] = None,
    ) -> ExecutionResult:
        """Place opening multi-leg order for any supported strategy."""
        if isinstance(candidate_or_condor, IronCondor):
            return self.place_condor_order(condor=candidate_or_condor, quantity=quantity, dry_run=dry_run)

        is_dry = dry_run if dry_run is not None else self.dry_run
        timestamp = datetime.now(timezone.utc).isoformat()

        if not isinstance(candidate_or_condor, StrategyCandidate) or not candidate_or_condor.legs:
            return ExecutionResult(
                execution_id=f"exec_err_{int(datetime.now().timestamp())}",
                proposal_id="invalid_candidate",
                status="rejected",
                error_message="Malformed order: strategy candidate is None or has no legs.",
            )

        strat = candidate_or_condor.strategy_name
        symbol = candidate_or_condor.underlying
        proposal_id = f"open_{symbol}_{candidate_or_condor.expiration}_{strat}"
        limit_price = float(candidate_or_condor.entry_credit if candidate_or_condor.entry_credit > 0 else candidate_or_condor.entry_debit)

        try:
            from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
            from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce

            legs = []
            legs_exec = []
            for leg in candidate_or_condor.legs:
                side_enum = OrderSide.BUY if leg.side.lower() == "buy" else OrderSide.SELL
                intent_enum = PositionIntent.BUY_TO_OPEN if leg.side.lower() == "buy" else PositionIntent.SELL_TO_OPEN
                legs.append(
                    OptionLegRequest(
                        symbol=leg.contract.symbol,
                        ratio_qty=leg.quantity,
                        side=side_enum,
                        position_intent=intent_enum,
                    )
                )
                legs_exec.append({
                    "leg": leg.leg_type,
                    "symbol": leg.contract.symbol,
                    "side": leg.side.lower(),
                    "intent": "buy_to_open" if leg.side.lower() == "buy" else "sell_to_open",
                    "filled": False,
                })

            order_req = LimitOrderRequest(
                order_class=OrderClass.MLEG,
                qty=quantity,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
                legs=legs,
            )

            if is_dry or not self._trading_client:
                logger.info(
                    f"[DRY RUN] Built {len(legs)}-leg {strat} order for {symbol}: "
                    f"Qty={quantity}, Limit Credit=${limit_price:.2f}, Legs={[l.symbol for l in legs]}"
                )
                return ExecutionResult(
                    execution_id=f"dry_exec_{int(datetime.now().timestamp())}",
                    proposal_id=proposal_id,
                    order_id="DRY_RUN_ORDER_ID_001",
                    status="new",
                    filled_credit=limit_price,
                    filled_at=timestamp,
                    legs_execution=legs_exec,
                )

            validate_paper_execution_environment(self._trading_client)
            logger.info(f"Submitting {len(legs)}-leg {strat} order to Alpaca (Paper: {self.paper})...")
            submitted_order = self._trading_client.submit_order(order_req)
            raw_status = str(submitted_order.status).lower().replace("orderstatus.", "")
            status_map = {
                "accepted": "accepted",
                "new": "new",
                "pending_new": "new",
                "pending": "pending",
                "partially_filled": "partially_filled",
                "filled": "filled",
                "canceled": "canceled",
                "rejected": "rejected",
                "expired": "expired",
                "failed": "failed",
            }
            mapped_status = status_map.get(raw_status, "accepted")

            return ExecutionResult(
                execution_id=f"exec_{submitted_order.id}",
                proposal_id=proposal_id,
                order_id=str(submitted_order.id),
                status=mapped_status,
                filled_credit=limit_price if mapped_status == "filled" else 0.0,
                filled_at=submitted_order.filled_at.isoformat() if getattr(submitted_order, "filled_at", None) else None,
                legs_execution=[
                    {"symbol": l.symbol, "side": str(l.side), "intent": str(l.position_intent)}
                    for l in legs
                ],
            )

        except Exception as e:
            logger.error(f"Alpaca {strat} order submission failed: {e}")
            return ExecutionResult(
                execution_id=f"exec_err_{int(datetime.now().timestamp())}",
                proposal_id=proposal_id,
                status="rejected",
                error_message=f"Order submission error: {str(e)}",
            )

    def execute_proposal(
        self,
        proposal: Union[TradeProposal, StrategyCandidate, IronCondor, Any],
        risk_decision: RiskDecision,
        dry_run: Optional[bool] = None,
    ) -> ExecutionResult:
        """Execute a trade proposal or strategy candidate if approved by the Risk Gate."""
        if not risk_decision.approved:
            prop_id = getattr(proposal, "proposal_id", "prop_0")
            logger.warning(
                f"Trade proposal {prop_id} was rejected by Risk Gate: {risk_decision.reason}"
            )
            return ExecutionResult(
                execution_id=f"exec_rejected_{prop_id}",
                proposal_id=prop_id,
                status="rejected",
                error_message=f"Risk Gate rejected proposal: {risk_decision.reason}",
            )

        qty = risk_decision.adjusted_quantity or getattr(proposal, "quantity", 1)
        if isinstance(proposal, TradeProposal):
            cand = getattr(proposal, "candidate", None) or getattr(proposal, "condor", None)
            return self.place_strategy_order(cand or proposal, quantity=qty, dry_run=dry_run)
        return self.place_strategy_order(proposal, quantity=qty, dry_run=dry_run)

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions from Alpaca broker (or dry-run mock)."""
        if self.dry_run or not self._trading_client:
            logger.info("[DRY-RUN] Get open positions (returning empty list)")
            return []
        try:
            positions = self._trading_client.get_all_positions()
            return [
                {
                    "symbol": str(p.symbol),
                    "qty": float(p.qty),
                    "market_value": float(p.market_value or 0.0),
                    "cost_basis": float(p.cost_basis or 0.0),
                    "unrealized_pl": float(p.unrealized_pl or 0.0),
                    "side": str(p.side),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Failed to fetch open positions from Alpaca: {e}")
            return []

    def get_account(self) -> AccountSnapshot:
        """Get account snapshot from Alpaca broker (or dry-run mock)."""
        from trading_agent.models import AccountSnapshot
        if self.dry_run or not self._trading_client:
            logger.info("[DRY-RUN] Get account info (returning mock snapshot)")
            return AccountSnapshot(
                account_id="DRY_RUN_PAPER",
                equity=100000.0,
                cash=100000.0,
                buying_power=400000.0,
                status="ACTIVE",
            )
        try:
            acct = self._trading_client.get_account()
            return AccountSnapshot(
                account_id=str(acct.id),
                equity=float(acct.equity),
                cash=float(acct.cash),
                buying_power=float(acct.buying_power),
                status=str(acct.status),
            )
        except Exception as e:
            logger.error(f"Failed to fetch account info from Alpaca: {e}")
            return AccountSnapshot(
                account_id="ERROR_FALLBACK",
                equity=100000.0,
                cash=100000.0,
                buying_power=400000.0,
                status="ACTIVE",
            )

    def close_position(self, symbol: str) -> ExecutionResult:
        """Close an open position for symbol."""
        timestamp = datetime.now(timezone.utc).isoformat()
        if self.dry_run or not self._trading_client:
            logger.info(f"[DRY-RUN] Close position: {symbol}")
            return ExecutionResult(
                execution_id=f"dry_close_{int(datetime.now().timestamp())}",
                proposal_id=f"close_{symbol}",
                status="accepted",
                filled_at=timestamp,
            )
        try:
            res = self._trading_client.close_position(symbol)
            return ExecutionResult(
                execution_id=f"exec_close_{res.id}",
                proposal_id=f"close_{symbol}",
                status=str(res.status).lower(),
                filled_at=timestamp,
            )
        except Exception as e:
            logger.error(f"Failed to close position for {symbol}: {e}")
            return ExecutionResult(
                execution_id=f"exec_err_{int(datetime.now().timestamp())}",
                proposal_id=f"close_{symbol}",
                status="rejected",
                error_message=str(e),
            )
