"""
Option Universe Classification & Instrument Metadata Model.
Categorizes underlying symbols into instrument types (Equity ETF, Index, Index Weekly, Volatility Index, Volatility Index Weekly)
and provides instrument-specific exercise style, settlement type, assignment risk, contract multiplier, and trading rules.
"""

import logging
from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

InstrumentType = Literal[
    "EQUITY_ETF",
    "INDEX",
    "INDEX_WEEKLY",
    "VOLATILITY_INDEX",
    "VOLATILITY_INDEX_WEEKLY",
]

ExerciseStyle = Literal["AMERICAN", "EUROPEAN"]
SettlementType = Literal["PHYSICAL", "CASH"]


class InstrumentMetadata(BaseModel):
    """Metadata describing operational and risk properties of an options instrument."""

    symbol: str
    instrument_type: InstrumentType
    exercise_style: ExerciseStyle = "AMERICAN"
    settlement_type: SettlementType = "PHYSICAL"
    early_assignment_risk: bool = True
    contract_multiplier: float = 100.0
    cash_settled: bool = False
    is_volatility_index: bool = False
    has_weekly_expirations: bool = False
    description: str = ""


OPTION_UNIVERSE: Dict[str, InstrumentMetadata] = {
    # Equity ETFs
    "SPY": InstrumentMetadata(
        symbol="SPY",
        instrument_type="EQUITY_ETF",
        exercise_style="AMERICAN",
        settlement_type="PHYSICAL",
        early_assignment_risk=True,
        contract_multiplier=100.0,
        cash_settled=False,
        is_volatility_index=False,
        has_weekly_expirations=True,
        description="SPDR S&P 500 ETF Trust",
    ),
    "QQQ": InstrumentMetadata(
        symbol="QQQ",
        instrument_type="EQUITY_ETF",
        exercise_style="AMERICAN",
        settlement_type="PHYSICAL",
        early_assignment_risk=True,
        contract_multiplier=100.0,
        cash_settled=False,
        is_volatility_index=False,
        has_weekly_expirations=True,
        description="Invesco QQQ Trust (Nasdaq-100)",
    ),
    "IWM": InstrumentMetadata(
        symbol="IWM",
        instrument_type="EQUITY_ETF",
        exercise_style="AMERICAN",
        settlement_type="PHYSICAL",
        early_assignment_risk=True,
        contract_multiplier=100.0,
        cash_settled=False,
        is_volatility_index=False,
        has_weekly_expirations=True,
        description="iShares Russell 2000 ETF",
    ),
    "DIA": InstrumentMetadata(
        symbol="DIA",
        instrument_type="EQUITY_ETF",
        exercise_style="AMERICAN",
        settlement_type="PHYSICAL",
        early_assignment_risk=True,
        contract_multiplier=100.0,
        cash_settled=False,
        is_volatility_index=False,
        has_weekly_expirations=True,
        description="SPDR Dow Jones Industrial Average ETF",
    ),
    "VOO": InstrumentMetadata(
        symbol="VOO",
        instrument_type="EQUITY_ETF",
        exercise_style="AMERICAN",
        settlement_type="PHYSICAL",
        early_assignment_risk=True,
        contract_multiplier=100.0,
        cash_settled=False,
        is_volatility_index=False,
        has_weekly_expirations=False,
        description="Vanguard S&P 500 ETF",
    ),
    "IVV": InstrumentMetadata(
        symbol="IVV",
        instrument_type="EQUITY_ETF",
        exercise_style="AMERICAN",
        settlement_type="PHYSICAL",
        early_assignment_risk=True,
        contract_multiplier=100.0,
        cash_settled=False,
        is_volatility_index=False,
        has_weekly_expirations=False,
        description="iShares Core S&P 500 ETF",
    ),
    # Broad Index Options
    "SPX": InstrumentMetadata(
        symbol="SPX",
        instrument_type="INDEX",
        exercise_style="EUROPEAN",
        settlement_type="CASH",
        early_assignment_risk=False,
        contract_multiplier=100.0,
        cash_settled=True,
        is_volatility_index=False,
        has_weekly_expirations=True,
        description="S&P 500 Index (AM-Settled)",
    ),
    "SPXW": InstrumentMetadata(
        symbol="SPXW",
        instrument_type="INDEX_WEEKLY",
        exercise_style="EUROPEAN",
        settlement_type="CASH",
        early_assignment_risk=False,
        contract_multiplier=100.0,
        cash_settled=True,
        is_volatility_index=False,
        has_weekly_expirations=True,
        description="S&P 500 Index Weekly (PM-Settled)",
    ),
    "XSP": InstrumentMetadata(
        symbol="XSP",
        instrument_type="INDEX",
        exercise_style="EUROPEAN",
        settlement_type="CASH",
        early_assignment_risk=False,
        contract_multiplier=100.0,
        cash_settled=True,
        is_volatility_index=False,
        has_weekly_expirations=True,
        description="Mini-SPX Index (1/10th S&P 500)",
    ),
    "DJX": InstrumentMetadata(
        symbol="DJX",
        instrument_type="INDEX",
        exercise_style="EUROPEAN",
        settlement_type="CASH",
        early_assignment_risk=False,
        contract_multiplier=100.0,
        cash_settled=True,
        is_volatility_index=False,
        has_weekly_expirations=False,
        description="Dow Jones Industrial Average Index (1/100th)",
    ),
    # Volatility Index Options
    "VIX": InstrumentMetadata(
        symbol="VIX",
        instrument_type="VOLATILITY_INDEX",
        exercise_style="EUROPEAN",
        settlement_type="CASH",
        early_assignment_risk=False,
        contract_multiplier=100.0,
        cash_settled=True,
        is_volatility_index=True,
        has_weekly_expirations=True,
        description="Cboe Volatility Index Options",
    ),
    "VIXW": InstrumentMetadata(
        symbol="VIXW",
        instrument_type="VOLATILITY_INDEX_WEEKLY",
        exercise_style="EUROPEAN",
        settlement_type="CASH",
        early_assignment_risk=False,
        contract_multiplier=100.0,
        cash_settled=True,
        is_volatility_index=True,
        has_weekly_expirations=True,
        description="Cboe Volatility Index Weekly Options",
    ),
}


def get_instrument_metadata(symbol: str) -> InstrumentMetadata:
    """Retrieve metadata for given option underlying symbol. Returns default Equity ETF if unknown."""
    sym_upper = symbol.strip().upper()
    if sym_upper in OPTION_UNIVERSE:
        return OPTION_UNIVERSE[sym_upper]

    # Default fallback for unlisted equity symbols
    return InstrumentMetadata(
        symbol=sym_upper,
        instrument_type="EQUITY_ETF",
        exercise_style="AMERICAN",
        settlement_type="PHYSICAL",
        early_assignment_risk=True,
        contract_multiplier=100.0,
        cash_settled=False,
        is_volatility_index=False,
        has_weekly_expirations=False,
        description=f"Equity Underlying {sym_upper}",
    )


def is_index_option(symbol: str) -> bool:
    """Return True if symbol is a cash-settled index option (SPX, SPXW, XSP, DJX, VIX, VIXW)."""
    meta = get_instrument_metadata(symbol)
    return meta.cash_settled or meta.instrument_type in ["INDEX", "INDEX_WEEKLY", "VOLATILITY_INDEX", "VOLATILITY_INDEX_WEEKLY"]


def is_volatility_option(symbol: str) -> bool:
    """Return True if symbol is a volatility index option (VIX, VIXW)."""
    meta = get_instrument_metadata(symbol)
    return meta.is_volatility_index
