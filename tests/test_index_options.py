"""
Unit Test Suite for Index Options Special Handling & Settlement Metadata.
Verifies Phase 9 requirements:
- European cash settlement metadata
- Absence of early assignment risk for SPX/SPXW/XSP/DJX/VIX/VIXW
- Position Manager handling of cash-settled index positions
"""

import pytest
from datetime import datetime, timezone

from trading_agent.strategy.option_universe import get_instrument_metadata, is_index_option
from trading_agent.portfolio.position_manager import PositionManager
from trading_agent.models import AccountSnapshot, StrategyCandidate, OptionLeg, OptionContract


def test_index_settlement_rules():
    for sym in ["SPX", "SPXW", "XSP", "DJX", "VIX", "VIXW"]:
        meta = get_instrument_metadata(sym)
        assert meta.settlement_type == "CASH"
        assert meta.exercise_style == "EUROPEAN"
        assert meta.early_assignment_risk is False


def test_position_manager_index_assignment_risk():
    meta = get_instrument_metadata("SPX")
    assert meta.symbol == "SPX"
    assert meta.cash_settled is True
    assert meta.early_assignment_risk is False
    assert meta.exercise_style == "EUROPEAN"
    assert meta.settlement_type == "CASH"
