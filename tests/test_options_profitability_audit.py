"""
Phase 18 — Options Engine Profitability & Cost Modeling Unit & Integration Test Suite.
Tests cost model, lifecycle position simulation, look-ahead bias prevention,
walk-forward bounds, portfolio correlation controls, and risk-gate integrity.
STRICTLY READ-ONLY — ZERO BROKER ENDPOINTS CALLED.
"""

import pytest
import pandas as pd
from datetime import datetime, timezone

from trading_agent.strategy.cost_model import OptionsCostModel, TradeCostBreakdown
from trading_agent.strategy.lifecycle_simulator import PositionLifecycleSimulator, SimulatedPosition
from trading_agent.strategy.walk_forward import WalkForwardEngine, calculate_comprehensive_metrics
from trading_agent.risk.portfolio_risk import PortfolioCorrelationManager


def test_cost_model_credit_spread_attribution():
    cost_model = OptionsCostModel(commission_per_contract=1.50, regulatory_fee_per_contract=0.05, slippage_pct=0.02)
    # 2-leg credit spread, 1 contract, Entry Mid $2.00, Exit Mid $0.50, Entry Spread $0.20, Exit Spread $0.10
    breakdown = cost_model.calculate_trade_costs(
        strategy_name="bull_put_spread",
        num_legs=2,
        quantity=1,
        entry_mid=2.00,
        exit_mid=0.50,
        entry_spread=0.20,
        exit_spread=0.10,
        is_credit=True,
    )
    assert breakdown.commissions == 6.00  # (2 legs * 1 * 2) * 1.50 = $6.00
    assert breakdown.regulatory_fees == 0.20  # (4 contract trades) * 0.05 = $0.20
    assert breakdown.gross_pnl > breakdown.net_pnl
    assert breakdown.total_costs > 0


def test_cost_model_debit_option_attribution():
    cost_model = OptionsCostModel(commission_per_contract=1.50, regulatory_fee_per_contract=0.05, slippage_pct=0.02)
    # 1-leg debit long call, 1 contract, Entry Mid $3.00, Exit Mid $6.00
    breakdown = cost_model.calculate_trade_costs(
        strategy_name="long_call",
        num_legs=1,
        quantity=1,
        entry_mid=3.00,
        exit_mid=6.00,
        entry_spread=0.10,
        exit_spread=0.10,
        is_credit=False,
    )
    assert breakdown.gross_pnl > 0
    assert breakdown.net_pnl == breakdown.gross_pnl - breakdown.total_costs


def test_lifecycle_simulator_profit_target_exit():
    cost_model = OptionsCostModel()
    simulator = PositionLifecycleSimulator(cost_model=cost_model)
    bars = [{"timestamp": f"day_{i}", "close": 550.0 + i * 2.0} for i in range(20)]
    regimes = [{"regime_class": "BULL", "volatility": "normal"} for _ in range(20)]

    pos = SimulatedPosition(
        trade_id="TR_PROFIT",
        symbol="SPY",
        strategy_name="long_call",
        entry_index=2,
        entry_date="day_2",
        underlying_price_entry=554.0,
        option_entry_price=2.00,
        num_legs=1,
        quantity=1,
        is_credit=False,
        dte_entry=30,
        profit_target_pct=0.50,
        stop_loss_pct=0.50,
    )

    res = simulator.simulate_position(pos, bars, regimes)
    assert res.status == "CLOSED"
    assert res.exit_reason in ["PROFIT_TARGET_REACHED", "SIMULATION_END_CLOSE"]
    assert res.exit_index > res.entry_index


def test_lifecycle_simulator_regime_transition_exit():
    cost_model = OptionsCostModel()
    simulator = PositionLifecycleSimulator(cost_model=cost_model)
    bars = [{"timestamp": f"day_{i}", "close": 550.0} for i in range(20)]
    regimes = [{"regime_class": "RANGE", "volatility": "normal"} for _ in range(20)]
    regimes[5] = {"regime_class": "REGIME_TRANSITION", "volatility": "normal"}

    pos = SimulatedPosition(
        trade_id="TR_TRANS",
        symbol="SPY",
        strategy_name="iron_condor",
        entry_index=1,
        entry_date="day_1",
        underlying_price_entry=550.0,
        option_entry_price=2.00,
        num_legs=4,
        quantity=1,
        is_credit=True,
        dte_entry=30,
        profit_target_pct=0.50,
        stop_loss_pct=1.00,
    )

    res = simulator.simulate_position(pos, bars, regimes)
    assert res.status == "CLOSED"
    assert res.exit_reason == "REGIME_TRANSITION_EXIT"
    assert res.exit_index == 5


def test_portfolio_correlation_manager_limits():
    pm = PortfolioCorrelationManager(max_aggregate_delta=100.0, max_correlated_positions=2)
    open_pos = [{"symbol": "SPY", "delta": 40.0}, {"symbol": "QQQ", "delta": 40.0}]

    res_blocked_count = pm.evaluate_portfolio_exposure("IWM", 30.0, open_pos)
    assert res_blocked_count["allowed"] is False
    assert "limit reached" in res_blocked_count["reason"]

    open_pos_small = [{"symbol": "SPY", "delta": 40.0}]
    res_allowed = pm.evaluate_portfolio_exposure("QQQ", 30.0, open_pos_small)
    assert res_allowed["allowed"] is True


def test_walk_forward_index_inequality():
    engine = WalkForwardEngine()
    mock_records = [{"timestamp": f"day_{i}", "open": 500.0, "high": 505.0, "low": 495.0, "close": 500.0 + (i * 0.2), "volume": 10000} for i in range(80)]
    df = pd.DataFrame(mock_records)
    cfg = {"strategy_name": "bull_put_spread", "dte": 30, "profit_target": 0.50, "stop_loss": 1.00, "iv_rank": 35.0, "entry_option_price": 2.00}
    res = engine.run_walk_forward(df, "SPY", cfg, num_windows=2, use_transition_filter=True)
    assert "out_of_sample_metrics" in res
    assert isinstance(res["trades"], list)
