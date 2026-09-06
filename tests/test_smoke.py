"""
Smoke test suite importing every module in trading_agent and validating typed Pydantic models.
"""

import pytest


def test_import_all_modules():
    """Verify that every module in the package can be imported without errors."""
    import trading_agent
    import trading_agent.config
    import trading_agent.models
    import trading_agent.data
    import trading_agent.data.alpaca_data
    import trading_agent.data.market_clock
    import trading_agent.data.option_data
    import trading_agent.strategy
    import trading_agent.strategy.signals
    import trading_agent.strategy.sentiment
    import trading_agent.strategy.condor_builder
    import trading_agent.risk
    import trading_agent.risk.risk_gate
    import trading_agent.risk.safety
    import trading_agent.execution
    import trading_agent.execution.alpaca_executor
    import trading_agent.portfolio
    import trading_agent.portfolio.position_manager
    import trading_agent.portfolio.pnl
    import trading_agent.infrastructure
    import trading_agent.infrastructure.logger
    import trading_agent.infrastructure.storage
    import trading_agent.agent
    import trading_agent.agent.orchestrator
    import trading_agent.main

    assert trading_agent.__version__ == "0.1.0"


def test_pydantic_models_instantiation():
    """Validate that all 11 required Pydantic models instantiate correctly with typed fields."""
    from trading_agent.models import (
        AccountSnapshot,
        OptionContract,
        OptionChainSnapshot,
        MarketSnapshot,
        SentimentResult,
        CondorLeg,
        IronCondor,
        TradeProposal,
        RiskDecision,
        ExecutionResult,
        PositionAction,
    )

    # 1. AccountSnapshot
    account = AccountSnapshot(
        account_id="ACC_123",
        cash=100000.0,
        buying_power=100000.0,
        equity=100000.0,
        portfolio_value=100000.0,
        status="ACTIVE",
    )
    assert account.cash == 100000.0

    # 2. OptionContract (Put & Call)
    put_contract = OptionContract(
        symbol="SPY260918P00090000",
        underlying_symbol="SPY",
        strike_price=90.0,
        expiration_date="2026-09-18",
        contract_type="put",
        bid=1.20,
        ask=1.25,
        delta=-0.15,
    )
    call_contract = OptionContract(
        symbol="SPY260918C00110000",
        underlying_symbol="SPY",
        strike_price=110.0,
        expiration_date="2026-09-18",
        contract_type="call",
        bid=1.10,
        ask=1.15,
        delta=0.15,
    )
    assert put_contract.contract_type == "put"
    assert call_contract.contract_type == "call"

    # 3. OptionChainSnapshot
    chain = OptionChainSnapshot(
        underlying_symbol="SPY",
        underlying_price=100.0,
        timestamp="2026-08-28T22:00:00Z",
        contracts=[put_contract, call_contract],
    )
    assert len(chain.contracts) == 2

    # 4. MarketSnapshot
    market = MarketSnapshot(
        symbol="SPY",
        underlying_price=100.0,
        vix=18.5,
        timestamp="2026-08-28T22:00:00Z",
        option_chain=chain,
    )
    assert market.vix == 18.5

    # 5. SentimentResult
    sentiment = SentimentResult(
        score=0.25,
        confidence=0.9,
        rationale="Slightly bullish macro environment.",
        model_used="meta-llama/Meta-Llama-3.1-8B-Instruct",
        source_tags=["news", "financials"],
        timestamp="2026-08-28T22:00:00Z",
    )
    assert sentiment.score == 0.25

    # 6. CondorLeg
    leg_put_long = CondorLeg(contract=put_contract, side="buy", quantity=1, leg_type="put_long")
    leg_put_short = CondorLeg(contract=put_contract, side="sell", quantity=1, leg_type="put_short")
    leg_call_short = CondorLeg(contract=call_contract, side="sell", quantity=1, leg_type="call_short")
    leg_call_long = CondorLeg(contract=call_contract, side="buy", quantity=1, leg_type="call_long")
    assert leg_put_long.side == "buy"

    # 7. IronCondor
    condor = IronCondor(
        underlying_symbol="SPY",
        put_long=leg_put_long,
        put_short=leg_put_short,
        call_short=leg_call_short,
        call_long=leg_call_long,
        net_credit=1.50,
        max_risk=350.0,
        max_profit=150.0,
        wing_width=5.0,
        expiration_date="2026-09-18",
    )
    assert condor.net_credit == 1.50

    # 8. TradeProposal
    proposal = TradeProposal(
        proposal_id="PROP_001",
        symbol="SPY",
        condor=condor,
        sentiment=sentiment,
        quantity=1,
        created_at="2026-08-28T22:00:00Z",
        rationale="High IV rank and neutral sentiment.",
    )
    assert proposal.proposal_id == "PROP_001"

    # 9. RiskDecision
    risk_dec = RiskDecision(
        proposal_id="PROP_001",
        approved=True,
        adjusted_quantity=1,
        risk_score=0.1,
        rule_violations=[],
        max_allowed_loss=2500.0,
        reason="Passed all risk metrics.",
    )
    assert risk_dec.approved is True

    # 10. ExecutionResult
    exec_res = ExecutionResult(
        execution_id="EXEC_001",
        proposal_id="PROP_001",
        order_id="ORD_999",
        status="filled",
        filled_credit=1.50,
        filled_at="2026-08-28T22:00:00Z",
    )
    assert exec_res.status == "filled"

    # 11. PositionAction
    pos_action = PositionAction(
        action_type="open",
        symbol="SPY",
        position_id="POS_001",
        reason="Opening new Iron Condor position",
        target_condor=condor,
        timestamp="2026-08-28T22:00:00Z",
    )
    assert pos_action.action_type == "open"


def test_orchestrator_dry_run_cycle():
    """Test full cycle execution in Orchestrator."""
    from trading_agent.agent import AgentOrchestrator

    orchestrator = AgentOrchestrator()
    res = orchestrator.run_cycle(symbol="SPY")
    assert res["status"] in ("success", "no_trade", "market_closed", "regime_ineligible", "catalyst_risk_high", "no_condor_found", "rejected", "risk_rejected")
    assert res["symbol"] == "SPY"
