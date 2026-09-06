"""
Broker vs Local Position Reconciliation Engine.
Compares positions reported by the Alpaca broker API against local engine state.
Detects orphan broker positions, missing local positions, and quantity mismatches.
Logs structured alert 'POSITION_RECONCILIATION_FAILURE' on any discrepancy.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

from trading_agent.execution.alpaca_executor import AlpacaExecutor

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationReport:
    reconciled: bool
    broker_position_count: int
    local_position_count: int
    matched_positions: List[str]
    discrepancies: List[Dict[str, Any]]


class PositionReconciler:
    """Automated broker/local position reconciler."""

    def __init__(self, executor: Optional[AlpacaExecutor] = None):
        self.executor = executor or AlpacaExecutor(dry_run=True)

    def reconcile(self, local_positions: List[Dict[str, Any]]) -> ReconciliationReport:
        """Compare Alpaca broker positions with local_positions registry."""
        try:
            broker_positions = self.executor.get_open_positions()
        except Exception as e:
            logger.error(f"Failed to query broker positions for reconciliation: {e}")
            broker_positions = []

        broker_map = {pos.get("symbol", ""): float(pos.get("qty", 0.0)) for pos in broker_positions}
        local_map = {pos.get("symbol", ""): float(pos.get("qty", 0.0)) for pos in local_positions}

        matched = []
        discrepancies = []

        all_symbols = set(broker_map.keys()).union(set(local_map.keys()))

        for sym in all_symbols:
            if not sym:
                continue

            b_qty = broker_map.get(sym, 0.0)
            l_qty = local_map.get(sym, 0.0)

            if b_qty == l_qty:
                matched.append(sym)
            else:
                disc = {
                    "symbol": sym,
                    "broker_qty": b_qty,
                    "local_qty": l_qty,
                    "discrepancy_type": "ORPHAN_BROKER_POSITION" if l_qty == 0 else ("MISSING_BROKER_POSITION" if b_qty == 0 else "QUANTITY_MISMATCH"),
                }
                discrepancies.append(disc)
                logger.error(f"POSITION_RECONCILIATION_FAILURE: Discrepancy detected for {sym}: broker_qty={b_qty}, local_qty={l_qty}")

        is_reconciled = len(discrepancies) == 0

        return ReconciliationReport(
            reconciled=is_reconciled,
            broker_position_count=len(broker_positions),
            local_position_count=len(local_positions),
            matched_positions=matched,
            discrepancies=discrepancies,
        )
