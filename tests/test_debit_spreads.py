"""
Unit Test Suite for Defined-Risk Debit Spreads & Iron Butterfly Strategies.
Verifies Phase 8 requirements:
- Call Debit Spread candidate generation, debit pricing, max loss/profit
- Put Debit Spread candidate generation, debit pricing, max loss/profit
- Iron Butterfly candidate generation, 4-leg credit pricing, max loss/profit
- Low IV eligibility for debit spreads
"""

import pytest
from datetime import datetime, timezone

from trading_agent.models import OptionChainSnapshot, OptionContract
from trading_agent.strategy.call_debit_spread import CallDebitSpreadStrategy
from trading_agent.strategy.put_debit_spread import PutDebitSpreadStrategy
from trading_agent.strategy.iron_butterfly import IronButterflyStrategy
from trading_agent.strategy.regime import MarketRegime
from trading_agent.strategy.strategy_selector import StrategySelector


@pytest.fixture
def spy_option_chain() -> OptionChainSnapshot:
    now_ts = datetime.now(timezone.utc).isoformat()
    return OptionChainSnapshot(
        underlying_symbol="SPY",
        underlying_price=550.0,
        timestamp=now_ts,
        contracts=[
            OptionContract(symbol="SPY260918P00540000", underlying_symbol="SPY", strike_price=540.0, expiration_date="2026-09-18", contract_type="put", bid=1.0, ask=1.10, delta=-0.10, quote_timestamp=now_ts),
            OptionContract(symbol="SPY260918P00545000", underlying_symbol="SPY", strike_price=545.0, expiration_date="2026-09-18", contract_type="put", bid=2.5, ask=2.60, delta=-0.25, quote_timestamp=now_ts),
            OptionContract(symbol="SPY260918P00550000", underlying_symbol="SPY", strike_price=550.0, expiration_date="2026-09-18", contract_type="put", bid=5.0, ask=5.20, delta=-0.50, quote_timestamp=now_ts),
            OptionContract(symbol="SPY260918C00550000", underlying_symbol="SPY", strike_price=550.0, expiration_date="2026-09-18", contract_type="call", bid=5.0, ask=5.20, delta=0.50, quote_timestamp=now_ts),
            OptionContract(symbol="SPY260918C00555000", underlying_symbol="SPY", strike_price=555.0, expiration_date="2026-09-18", contract_type="call", bid=2.5, ask=2.60, delta=0.25, quote_timestamp=now_ts),
            OptionContract(symbol="SPY260918C00560000", underlying_symbol="SPY", strike_price=560.0, expiration_date="2026-09-18", contract_type="call", bid=1.0, ask=1.10, delta=0.10, quote_timestamp=now_ts),
        ]
    )


def test_call_debit_spread_generation(spy_option_chain):
    strat = CallDebitSpreadStrategy()
    cand = strat.generate_candidate(spy_option_chain, underlying_price=550.0, current_date="2026-08-28")
    assert cand is not None
    assert cand.strategy_name == "call_debit_spread"
    assert cand.entry_debit > 0
    assert cand.max_profit > 0
    assert cand.max_loss > 0


def test_put_debit_spread_generation(spy_option_chain):
    strat = PutDebitSpreadStrategy()
    cand = strat.generate_candidate(spy_option_chain, underlying_price=550.0, current_date="2026-08-28")
    assert cand is not None
    assert cand.strategy_name == "put_debit_spread"
    assert cand.entry_debit > 0
    assert cand.max_profit > 0
    assert cand.max_loss > 0


def test_iron_butterfly_generation(spy_option_chain):
    strat = IronButterflyStrategy()
    cand = strat.generate_candidate(spy_option_chain, underlying_price=550.0, current_date="2026-08-28")
    assert cand is not None
    assert cand.strategy_name == "iron_butterfly"
    assert cand.entry_credit > 0
    assert cand.max_loss > 0
    assert len(cand.legs) == 4


def test_low_iv_debit_spread_eligibility(spy_option_chain):
    selector = StrategySelector()
    regime = MarketRegime(
        trend="bullish",
        volatility="low",
        confidence=0.9,
        regime_class="BULL",
        iv_rank=15.0,
        eligible_strategies=["call_debit_spread", "long_call"],
    )
    decision = selector.select_strategy(market_regime=regime, option_chain=spy_option_chain, underlying_price=550.0, current_date="2026-08-28")
    assert decision.decision == "TRADE"
    assert decision.selected_strategy in ("call_debit_spread", "long_call")
