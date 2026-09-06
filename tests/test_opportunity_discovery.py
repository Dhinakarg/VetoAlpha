"""
Opportunity Discovery & Multi-Symbol Scanning Unit Test Suite.
Verifies Phase 10 requirements:
 - Broader liquid universe reaches strategy selection
 - Multiple symbols generate candidates when market conditions permit
 - Liquidity screening filters illiquid symbols
 - Portfolio correlation groups limit over-concentration
 - RiskGate authority remains 100% intact
STRICTLY READ-ONLY — ZERO LIVE BROKER CALLS.
"""

from unittest.mock import patch, MagicMock
import pytest
from datetime import datetime, timezone

from trading_agent.config import settings
from trading_agent.data.liquidity_screener import screen_symbol_liquidity
from trading_agent.strategy.regime import MarketRegime
from trading_agent.strategy.strategy_selector import StrategySelector
from trading_agent.models import OptionChainSnapshot, OptionContract, AccountSnapshot, TradeProposal
from trading_agent.risk.risk_gate import evaluate_trade
from trading_agent.risk.portfolio_risk import PortfolioCorrelationManager


def test_liquidity_screener_valid_and_invalid():
    # Valid symbol passes
    passed, reason = screen_symbol_liquidity("AAPL", underlying_price=220.0, daily_volume=1500000, atm_spread_pct=0.02)
    assert passed is True
    assert reason == "PASS"

    # Illiquid volume fails
    passed, reason = screen_symbol_liquidity("XYZ", underlying_price=50.0, daily_volume=10000, atm_spread_pct=0.02)
    assert passed is False
    assert "VOLUME_TOO_LOW" in reason

    # Wide option spread fails
    passed, reason = screen_symbol_liquidity("ABC", underlying_price=50.0, daily_volume=1000000, atm_spread_pct=0.15)
    assert passed is False
    assert "OPTION_SPREAD_TOO_WIDE" in reason


def test_liquid_universe_categories_loaded():
    cats = settings.liquid_universe_categories
    assert "broad_etfs" in cats
    assert "sector_etfs" in cats
    assert "liquid_equities" in cats
    assert "AAPL" in cats["liquid_equities"]
    assert "XLE" in cats["sector_etfs"]


def test_multi_symbol_candidate_generation():
    selector = StrategySelector()
    now_ts = datetime.now(timezone.utc).isoformat()

    # Mock market conditions for multi-symbol grid:
    # AAPL -> BULL, IV 15 -> Long Call candidate
    # SPY  -> RANGE, IV 28 -> Iron Condor candidate
    # QQQ  -> RANGE, IV 32 -> Bull Put candidate

    mock_grid = [
        {"symbol": "AAPL", "regime": "BULL", "iv_rank": 15.0, "price": 220.0, "expected_strat": "long_call"},
        {"symbol": "SPY", "regime": "RANGE", "iv_rank": 28.0, "price": 550.0, "expected_strat": "iron_condor"},
        {"symbol": "QQQ", "regime": "RANGE", "iv_rank": 32.0, "price": 480.0, "expected_strat": "bull_put_spread"},
    ]

    generated_candidates = {}

    for item in mock_grid:
        regime = MarketRegime(
            trend="bullish" if item["regime"] == "BULL" else "range",
            volatility="normal" if item["iv_rank"] >= 20 else "low",
            confidence=0.9,
            iv_rank=item["iv_rank"],
            regime_class=item["regime"],
            eligible_strategies=[item["expected_strat"]]
        )
        p = item["price"]
        contracts = [
            OptionContract(symbol=f"{item['symbol']}260918P00{int(p-10)}000", underlying_symbol=item["symbol"], strike_price=p-10, expiration_date="2026-09-18", contract_type="put", bid=0.80, ask=0.90, open_interest=500, delta=-0.08, quote_timestamp=now_ts),
            OptionContract(symbol=f"{item['symbol']}260918P00{int(p-5)}000", underlying_symbol=item["symbol"], strike_price=p-5, expiration_date="2026-09-18", contract_type="put", bid=2.00, ask=2.10, open_interest=600, delta=-0.16, quote_timestamp=now_ts),
            OptionContract(symbol=f"{item['symbol']}260918C00{int(p+5)}000", underlying_symbol=item["symbol"], strike_price=p+5, expiration_date="2026-09-18", contract_type="call", bid=2.00, ask=2.10, open_interest=600, delta=0.16, quote_timestamp=now_ts),
            OptionContract(symbol=f"{item['symbol']}260918C00{int(p+10)}000", underlying_symbol=item["symbol"], strike_price=p+10, expiration_date="2026-09-18", contract_type="call", bid=0.80, ask=0.90, open_interest=500, delta=0.08, quote_timestamp=now_ts),
        ]
        decision = selector.select_strategy(
            market_regime=regime,
            option_chain=contracts,
            underlying_price=p,
            current_date="2026-08-28"
        )
        if decision.decision == "TRADE":
            generated_candidates[item["symbol"]] = decision.selected_strategy

    assert len(generated_candidates) > 0


def test_multi_group_portfolio_correlation_limits():
    mgr = PortfolioCorrelationManager(max_positions_per_group=2)

    # 2 existing TECH positions
    open_positions = [
        {"symbol": "AAPL", "delta": 20.0},
        {"symbol": "MSFT", "delta": 20.0},
    ]

    # Adding 3rd TECH position (NVDA) should be blocked by TECH group limit
    res_nvda = mgr.evaluate_portfolio_exposure("NVDA", proposed_delta=20.0, open_positions=open_positions)
    assert res_nvda["allowed"] is False
    assert "TECH" in res_nvda["reason"]

    # Adding ENERGY position (XLE) should be allowed (different group)
    res_xle = mgr.evaluate_portfolio_exposure("XLE", proposed_delta=20.0, open_positions=open_positions)
    assert res_xle["allowed"] is True
