"""
Unit Test Suite for Option Universe Classification & Instrument Metadata.
Verifies Phase 2 requirements:
- ETF Options (SPY, QQQ, IWM, DIA, VOO, IVV)
- Index Options (SPX, SPXW, XSP, DJX)
- Volatility Options (VIX, VIXW)
- Instrument metadata attributes (exercise style, settlement type, cash settlement, contract multiplier)
"""

import pytest
from trading_agent.strategy.option_universe import (
    get_instrument_metadata,
    is_index_option,
    is_volatility_option,
    OPTION_UNIVERSE,
)


def test_equity_etf_metadata():
    spy_meta = get_instrument_metadata("SPY")
    assert spy_meta.symbol == "SPY"
    assert spy_meta.instrument_type == "EQUITY_ETF"
    assert spy_meta.exercise_style == "AMERICAN"
    assert spy_meta.settlement_type == "PHYSICAL"
    assert spy_meta.early_assignment_risk is True
    assert spy_meta.contract_multiplier == 100.0
    assert spy_meta.cash_settled is False


def test_index_options_metadata():
    spx_meta = get_instrument_metadata("SPX")
    assert spx_meta.symbol == "SPX"
    assert spx_meta.instrument_type == "INDEX"
    assert spx_meta.exercise_style == "EUROPEAN"
    assert spx_meta.settlement_type == "CASH"
    assert spx_meta.early_assignment_risk is False
    assert spx_meta.cash_settled is True

    xsp_meta = get_instrument_metadata("XSP")
    assert xsp_meta.exercise_style == "EUROPEAN"
    assert xsp_meta.cash_settled is True


def test_volatility_options_metadata():
    vix_meta = get_instrument_metadata("VIX")
    assert vix_meta.symbol == "VIX"
    assert vix_meta.instrument_type == "VOLATILITY_INDEX"
    assert vix_meta.exercise_style == "EUROPEAN"
    assert vix_meta.settlement_type == "CASH"
    assert vix_meta.is_volatility_index is True
    assert is_volatility_option("VIX") is True
    assert is_volatility_option("VIXW") is True
    assert is_volatility_option("SPY") is False


def test_is_index_option_helper():
    assert is_index_option("SPX") is True
    assert is_index_option("SPXW") is True
    assert is_index_option("XSP") is True
    assert is_index_option("DJX") is True
    assert is_index_option("SPY") is False
    assert is_index_option("QQQ") is False


def test_unknown_symbol_fallback():
    meta = get_instrument_metadata("AAPL")
    assert meta.symbol == "AAPL"
    assert meta.instrument_type == "EQUITY_ETF"
    assert meta.exercise_style == "AMERICAN"
    assert meta.cash_settled is False
