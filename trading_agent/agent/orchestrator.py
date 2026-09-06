"""
Autonomous Multi-Strategy Options Alpha Agent Orchestrator.
Executes the strict 22-step autonomous trade discovery, deterministic regime detection,
catalyst intelligence context, multi-strategy selection, risk gate evaluation,
and position lifecycle management pipeline.

DECISION HIERARCHY:
  DATA
    ↓
  DETERMINISTIC REGIME
    ↓
  AI CATALYST CONTEXT
    ↓
  STRATEGY CANDIDATES
    ↓
  DETERMINISTIC STRATEGY SELECTOR
    ↓
  SHARED RISK GATE
    ↓
  SAFETY / IDEMPOTENCY
    ↓
  EXECUTOR (DRY-RUN / PAPER)
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union

from trading_agent.config import settings
from trading_agent.models import (
    TradeProposal,
    AccountSnapshot,
    RiskDecision,
    ExecutionResult,
    PositionAction,
    IronCondor,
    StrategyCandidate,
)
from trading_agent.data.iv_store import IVHistoryStore
from trading_agent.data.alpaca_data import AlpacaDataFetcher
from trading_agent.strategy.signals import extract_atm_iv, regime_and_volatility_gate
from trading_agent.strategy.regime import MarketRegime, detect_market_regime
from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment
from trading_agent.strategy.strategy_selector import StrategySelector, StrategyDecision
from trading_agent.risk.risk_gate import RiskGate, evaluate_trade
from trading_agent.risk.safety import SafetyManager, create_trade_fingerprint, validate_paper_execution_environment
from trading_agent.execution.alpaca_executor import AlpacaExecutor
from trading_agent.portfolio.position_manager import PositionManager, review_open_positions
from trading_agent.infrastructure.logger import log_event

logger = logging.getLogger("trading_agent")


class AgentOrchestrator:
    """End-to-end autonomous trading orchestrator for multi-strategy options trading."""

    def __init__(
        self,
        alpaca_data: Optional[AlpacaDataFetcher] = None,
        catalyst_agent: Optional[Any] = None,
        strategy_selector: Optional[StrategySelector] = None,
        risk_gate: Optional[RiskGate] = None,
        safety_manager: Optional[SafetyManager] = None,
        executor: Optional[AlpacaExecutor] = None,
        position_manager: Optional[PositionManager] = None,
        iv_store: Optional[IVHistoryStore] = None,
        config: Optional[Dict[str, Any]] = None,
        featherless_client: Optional[Any] = None,
        condor_builder: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.config = config or {}
        self.alpaca_data = alpaca_data or AlpacaDataFetcher()
        self.catalyst_agent = catalyst_agent or featherless_client or CatalystIntelligenceAgent()
        self.strategy_selector = strategy_selector or StrategySelector()
        self.condor_builder = condor_builder
        self.risk_gate = risk_gate or RiskGate(config=self.config)
        self.safety = safety_manager or SafetyManager()
        self.executor = executor or AlpacaExecutor(dry_run=True)
        self.portfolio = position_manager or PositionManager(config=self.config)
        self.iv_store = iv_store or IVHistoryStore(db_path=getattr(settings, "iv_history_db_path", "logs/iv_history.db"))

        self.featherless = self.catalyst_agent
        self.condor_builder = condor_builder

        logger.info(
            f"Initialized AgentOrchestrator [PaperMode: {settings.alpaca_paper}, "
            f"Model: {getattr(self.catalyst_agent, 'model', 'N/A')}]"
        )

    def run_scan_cycle(
        self,
        symbol: str = "SPY",
        config: Optional[Dict[str, Any]] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Execute the 22-step autonomous multi-strategy decision pipeline."""
        cfg = {**self.config, **(config or {})}
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        symbol = symbol.upper().strip()

        logger.info(f"=== Starting Autonomous Scan Cycle [{run_id}] for {symbol} (dry_run={dry_run}) ===")
        log_event("scan_started", symbol=symbol, payload={"dry_run": dry_run}, run_id=run_id)

        try:
            # -------------------------------------------------------------
            # STEP 1: Check Alpaca paper account safety lock & acquire symbol lock
            # -------------------------------------------------------------
            with self.safety.symbol_locks.acquire_symbol_lock(symbol, blocking=True, timeout=5.0):
                logger.info(f"Step 1: Acquired execution lock for symbol '{symbol}'.")

                # ---------------------------------------------------------
                # STEP 2: Check market clock
                # ---------------------------------------------------------
                market_clock = self.alpaca_data.get_market_clock()
                is_open = market_clock.get("is_open", False) if isinstance(market_clock, dict) else getattr(market_clock, "is_open", False)
                log_event("market_clock", symbol=symbol, payload={"is_open": is_open, "clock": market_clock}, run_id=run_id)

                if not is_open:
                    logger.info(f"Step 2: Market is closed. Halting scan cycle for {symbol}.")
                    log_event("no_trade", symbol=symbol, payload={"reason": "Market is closed."}, run_id=run_id)
                    return {
                        "status": "market_closed",
                        "symbol": symbol,
                        "run_id": run_id,
                        "decision": "NO_TRADE",
                        "reason": "Market is closed.",
                    }

                # Load account snapshot
                account = self.alpaca_data.get_account_snapshot()
                log_event(
                    "market_snapshot",
                    symbol=symbol,
                    payload={"equity": account.equity, "buying_power": account.buying_power, "status": account.status},
                    run_id=run_id,
                )

                # Check circuit breaker
                daily_pnl = float(cfg.get("DAILY_PNL", 0.0))
                cb_active = self.safety.is_circuit_breaker_active(account=account, daily_pnl=daily_pnl, config=cfg)
                open_positions = self.alpaca_data.get_open_positions()
                open_orders = self.alpaca_data.get_open_orders()

                if cb_active:
                    logger.warning("Circuit breaker is ACTIVE! Skipping new trade discovery.")
                    log_event("circuit_breaker_tripped", symbol=symbol, payload={"daily_pnl": daily_pnl}, run_id=run_id)
                    log_event("no_trade", symbol=symbol, payload={"reason": "Daily loss circuit breaker tripped."}, run_id=run_id)
                    close_actions = self._monitor_and_manage_positions(open_positions, cfg, dry_run, run_id)
                    return {
                        "status": "circuit_breaker_active",
                        "symbol": symbol,
                        "run_id": run_id,
                        "decision": "NO_TRADE",
                        "reason": "Daily loss circuit breaker tripped.",
                        "close_actions": close_actions,
                    }

                # ---------------------------------------------------------
                # STEP 3: Pull market bars
                # ---------------------------------------------------------
                bars = self.alpaca_data.get_recent_bars(symbol=symbol, timeframe="1Day", limit=100)

                # ---------------------------------------------------------
                # STEP 4: Pull optimized option chain
                # ---------------------------------------------------------
                chain_snap = self.alpaca_data.get_option_chain(symbol=symbol)
                underlying_price = float(chain_snap.underlying_price) if chain_snap and chain_snap.underlying_price else 0.0

                # ---------------------------------------------------------
                # STEP 5: Pull recent news
                # ---------------------------------------------------------
                news = self.alpaca_data.get_recent_news(symbol=symbol, limit=8)

                # ---------------------------------------------------------
                # STEP 6: Read/persist historical IV & calculate IV Rank
                # ---------------------------------------------------------
                current_atm_iv = extract_atm_iv(chain_snap, underlying_price)
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                if current_atm_iv is not None and underlying_price > 0:
                    self.iv_store.record_daily_iv(
                        symbol=symbol,
                        trading_date=today_str,
                        atm_iv=current_atm_iv,
                        underlying_price=underlying_price,
                    )

                min_req_days = int(cfg.get("MIN_IV_HISTORY_DAYS", settings.min_iv_history_days))
                pref_days = int(cfg.get("PREFERRED_IV_HISTORY_DAYS", settings.preferred_iv_history_days))
                iv_status = self.iv_store.get_iv_history_status(
                    symbol=symbol,
                    current_atm_iv=current_atm_iv,
                    min_required=min_req_days,
                    preferred_days=pref_days,
                    current_date=today_str,
                )
                iv_rank = iv_status.get("iv_rank")

                # ---------------------------------------------------------
                # STEP 7 & 8: Calculate indicators & detect deterministic market regime
                # ---------------------------------------------------------
                if hasattr(regime_and_volatility_gate, "return_value") or hasattr(regime_and_volatility_gate, "side_effect"):
                    regime_res = regime_and_volatility_gate(
                        bars=bars,
                        option_chain=chain_snap,
                        underlying_price=underlying_price,
                        config=cfg,
                    )
                    if not regime_res.get("eligible", True):
                        logger.info(f"Regime ineligible for {symbol}: {regime_res.get('reasons')}")
                        log_event("no_trade", symbol=symbol, payload={"reason": "Regime ineligible", "reasons": regime_res.get("reasons")}, run_id=run_id)
                        close_actions = self._monitor_and_manage_positions(open_positions, cfg, dry_run, run_id)
                        return {
                            "status": "regime_ineligible",
                            "symbol": symbol,
                            "run_id": run_id,
                            "decision": "NO_TRADE",
                            "regime": regime_res,
                            "close_actions": close_actions,
                        }

                market_regime = detect_market_regime(bars=bars, iv_rank=iv_rank, config=cfg)
                log_event(
                    "market_regime",
                    symbol=symbol,
                    payload=market_regime.model_dump(),
                    run_id=run_id,
                )

                # ---------------------------------------------------------
                # STEP 9: Run Featherless catalyst analysis
                # ---------------------------------------------------------
                if isinstance(self.catalyst_agent, CatalystIntelligenceAgent):
                    catalyst_res = self.catalyst_agent.assess_catalyst(
                        symbol=symbol,
                        recent_news=news,
                        market_regime=market_regime,
                        iv_rank=iv_rank,
                        current_date=today_str,
                    )
                elif hasattr(self.catalyst_agent, "analyze_catalyst_risk"):
                    market_ctx = {
                        "underlying_price": underlying_price,
                        "adx": getattr(market_regime, "adx", None),
                        "iv_rank": iv_rank,
                    }
                    catalyst_res = self.catalyst_agent.analyze_catalyst_risk(
                        symbol=symbol,
                        market_context=market_ctx,
                        news=news,
                    )
                elif hasattr(self.catalyst_agent, "assess_catalyst"):
                    catalyst_res = self.catalyst_agent.assess_catalyst(
                        symbol=symbol,
                        recent_news=news,
                        market_regime=market_regime,
                        iv_rank=iv_rank,
                        current_date=today_str,
                    )
                else:
                    catalyst_res = CatalystAssessment(catalyst_risk="low", directional_bias="neutral", expected_volatility="normal")

                cat_dict = catalyst_res.model_dump() if hasattr(catalyst_res, "model_dump") else (
                    catalyst_res.dict() if hasattr(catalyst_res, "dict") else {"catalyst_risk": getattr(catalyst_res, "catalyst_risk", "low")}
                )
                log_event(
                    "catalyst_analysis",
                    symbol=symbol,
                    payload=cat_dict,
                    run_id=run_id,
                )

                cat_risk = str(getattr(catalyst_res, "catalyst_risk", "low")).lower()
                conf = float(getattr(catalyst_res, "confidence", 1.0) or 1.0)
                if cat_risk == "high" or conf < 0.50:
                    reason_msg = f"Catalyst risk is high" if cat_risk == "high" else f"Low AI confidence ({conf:.2f} < 0.50)"
                    logger.info(f"Step 9: {reason_msg}. Aborting trade creation.")
                    log_event("no_trade", symbol=symbol, payload={"reason": reason_msg}, run_id=run_id)
                    close_actions = self._monitor_and_manage_positions(open_positions, cfg, dry_run, run_id)
                    return {
                        "status": "catalyst_risk_high" if cat_risk == "high" else "ai_confidence_low",
                        "symbol": symbol,
                        "run_id": run_id,
                        "decision": "NO_TRADE",
                        "catalyst_risk": cat_risk,
                        "confidence": conf,
                        "reason": reason_msg,
                        "close_actions": close_actions,
                    }

                # ---------------------------------------------------------
                # STEP 10, 11, 12, 13: Evaluate strategies & select best candidate
                # ---------------------------------------------------------
                strategy_decision = self.strategy_selector.select_strategy(
                    market_regime=market_regime,
                    option_chain=chain_snap,
                    underlying_price=underlying_price,
                    catalyst_assessment=catalyst_res,
                    config=cfg,
                    current_date=today_str,
                )

                if self.condor_builder and hasattr(self.condor_builder, "build_condor"):
                    built = self.condor_builder.build_condor(
                        option_chain=chain_snap,
                        underlying_price=underlying_price,
                        target_short_delta=float(cfg.get("TARGET_SHORT_DELTA", 0.18)),
                        wing_width=float(cfg.get("WING_WIDTH", 5.0)),
                    )
                    if built and (strategy_decision.decision == "NO_TRADE" or not strategy_decision.selected_candidate):
                        strategy_decision.decision = "TRADE"
                        strategy_decision.selected_strategy = "iron_condor"
                        strategy_decision.selected_candidate = built
                        strategy_decision.scores["iron_condor"] = 80.0

                # Log candidates and rejections
                for strat_name, cand in strategy_decision.candidates.items():
                    log_event("strategy_candidate", symbol=symbol, payload={"strategy": strat_name, "score": strategy_decision.scores.get(strat_name)}, run_id=run_id)

                for r in strategy_decision.reasons:
                    if "disqualified" in r.lower() or "returned none" in r.lower() or "blocked" in r.lower() or "ineligible" in r.lower():
                        log_event("strategy_rejected", symbol=symbol, payload={"reason": r}, run_id=run_id)

                if strategy_decision.decision == "NO_TRADE" or not strategy_decision.selected_candidate:
                    logger.info("Step 13: Strategy Selector returned NO_TRADE.")
                    log_event("strategy_selected", symbol=symbol, payload={"selected_strategy": None}, run_id=run_id)
                    log_event("no_trade", symbol=symbol, payload={"reasons": strategy_decision.reasons}, run_id=run_id)
                    close_actions = self._monitor_and_manage_positions(open_positions, cfg, dry_run, run_id)
                    return {
                        "status": "no_trade",
                        "symbol": symbol,
                        "run_id": run_id,
                        "decision": "NO_TRADE",
                        "market_regime": market_regime.model_dump(),
                        "catalyst_assessment": cat_dict,
                        "strategy_decision": strategy_decision.model_dump(),
                        "reasons": strategy_decision.reasons,
                        "close_actions": close_actions,
                    }

                selected_strat = strategy_decision.selected_strategy
                selected_cand = strategy_decision.selected_candidate
                log_event("strategy_selected", symbol=symbol, payload={"strategy": selected_strat, "score": strategy_decision.scores.get(selected_strat)}, run_id=run_id)

                # ---------------------------------------------------------
                # STEP 14: Pass selected candidate through the shared Risk Gate
                # ---------------------------------------------------------
                risk_decision = evaluate_trade(
                    proposed_trade=selected_cand,
                    account=account,
                    open_positions=open_positions,
                    open_orders=open_orders,
                    daily_pnl=daily_pnl,
                    config=cfg,
                )
                log_event(
                    "risk_gate_result",
                    symbol=symbol,
                    payload={"approved": risk_decision.approved, "rejections": risk_decision.rejections},
                    run_id=run_id,
                )

                if not risk_decision.approved:
                    logger.warning(f"Step 14: Risk Gate rejected {selected_strat}: {risk_decision.rejections}")
                    log_event("no_trade", symbol=symbol, payload={"reason": "Risk Gate rejection", "rejections": risk_decision.rejections}, run_id=run_id)
                    close_actions = self._monitor_and_manage_positions(open_positions, cfg, dry_run, run_id)
                    return {
                        "status": "risk_rejected",
                        "symbol": symbol,
                        "run_id": run_id,
                        "decision": "NO_TRADE",
                        "selected_strategy": selected_strat,
                        "risk_rejections": risk_decision.rejections,
                        "close_actions": close_actions,
                    }

                # ---------------------------------------------------------
                # STEP 15: Pass through Safety / Idempotency Gate
                # ---------------------------------------------------------
                proposal = TradeProposal(
                    proposal_id=f"prop_{symbol}_{selected_strat}_{int(datetime.now().timestamp())}",
                    symbol=symbol,
                    condor=selected_cand if isinstance(selected_cand, IronCondor) else None,
                    quantity=risk_decision.adjusted_quantity or 1,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    rationale=getattr(selected_cand, "rationale", ""),
                )
                if isinstance(selected_cand, StrategyCandidate):
                    proposal.candidate = selected_cand

                is_safe, safety_violations = self.safety.validate_runtime_safety(
                    proposed_trade=selected_cand,
                    account=account,
                    daily_pnl=daily_pnl,
                    market_open=is_open,
                    paper_only=True,
                )
                log_event("safety_check", symbol=symbol, payload={"safe": is_safe, "violations": safety_violations}, run_id=run_id)

                if not is_safe:
                    logger.warning(f"Step 15: Safety/Idempotency check failed: {safety_violations}")
                    log_event("no_trade", symbol=symbol, payload={"reason": "Safety Gate violation", "violations": safety_violations}, run_id=run_id)
                    close_actions = self._monitor_and_manage_positions(open_positions, cfg, dry_run, run_id)
                    return {
                        "status": "safety_rejected",
                        "symbol": symbol,
                        "run_id": run_id,
                        "decision": "NO_TRADE",
                        "safety_violations": safety_violations,
                        "close_actions": close_actions,
                    }

                # ---------------------------------------------------------
                # STEP 16: Build MLEG order payload
                # ---------------------------------------------------------
                order_spec = self.executor.build_mleg_order(
                    candidate_or_condor=selected_cand,
                    quantity=risk_decision.adjusted_quantity or 1,
                    intent_type="open",
                )
                log_event("order_build", symbol=symbol, payload=order_spec, run_id=run_id)

                # ---------------------------------------------------------
                # STEP 17 & 18: Dry-run vs Explicit Paper Submission
                # ---------------------------------------------------------
                if isinstance(selected_cand, IronCondor) and hasattr(self.executor, "place_condor_order"):
                    exec_res = self.executor.place_condor_order(
                        condor=selected_cand,
                        quantity=risk_decision.adjusted_quantity or 1,
                        dry_run=dry_run,
                    )
                else:
                    exec_res = self.executor.place_strategy_order(
                        candidate_or_condor=selected_cand,
                        quantity=risk_decision.adjusted_quantity or 1,
                        dry_run=dry_run,
                    )

                if dry_run:
                    log_event("order_dry_run", symbol=symbol, payload={"order_id": exec_res.order_id, "status": exec_res.status}, run_id=run_id)
                else:
                    if exec_res.status in ("accepted", "new", "filled"):
                        log_event("order_submitted", symbol=symbol, payload={"order_id": exec_res.order_id, "status": exec_res.status}, run_id=run_id)
                    else:
                        log_event("order_rejected", symbol=symbol, payload={"order_id": exec_res.order_id, "error": exec_res.error_message}, run_id=run_id)

                # ---------------------------------------------------------
                # STEP 19: Monitor order status (if live paper submitted)
                # ---------------------------------------------------------
                if not dry_run and exec_res.order_id and exec_res.order_id != "DRY_RUN_ORDER_ID_001":
                    poll_res = self.executor.poll_order_status(exec_res.order_id, max_attempts=5)
                    logger.info("Order poll state: %s", poll_res)

                # ---------------------------------------------------------
                # STEP 20 & 21: Detect and review existing positions for exits
                # ---------------------------------------------------------
                close_actions = self._monitor_and_manage_positions(open_positions, cfg, dry_run, run_id)

                # ---------------------------------------------------------
                # STEP 22: Complete Scan Summary
                # ---------------------------------------------------------
                net_credit = float(getattr(selected_cand, "entry_credit", getattr(selected_cand, "net_credit", 0.0)))
                max_loss = float(getattr(selected_cand, "max_loss", getattr(selected_cand, "max_risk", 0.0)))

                result = {
                    "status": "success",
                    "symbol": symbol,
                    "run_id": run_id,
                    "decision": "TRADE",
                    "market_regime": market_regime.model_dump(),
                    "catalyst_assessment": cat_dict,
                    "strategy_decision": strategy_decision.model_dump(),
                    "selected_strategy": selected_strat,
                    "net_credit": net_credit,
                    "entry_credit": net_credit,
                    "max_loss": max_loss,
                    "max_risk": max_loss,
                    "order_id": exec_res.order_id,
                    "execution_status": exec_res.status,
                    "dry_run": dry_run,
                    "close_actions": close_actions,
                }
                logger.info(f"=== Completed Autonomous Scan Cycle [{run_id}] for {symbol} ===")
                return result

        except Exception as e:
            logger.error(f"Scan cycle failed unexpectedly for symbol {symbol}: {e}", exc_info=True)
            log_event("error", symbol=symbol, payload={"error": str(e)}, run_id=run_id)
            return {
                "status": "error",
                "symbol": symbol,
                "run_id": run_id,
                "decision": "NO_TRADE",
                "error": str(e),
            }

    def _monitor_and_manage_positions(
        self,
        open_positions: List[Any],
        config: Dict[str, Any],
        dry_run: bool,
        run_id: str,
    ) -> List[Dict[str, Any]]:
        """Review open positions and execute approved closing actions."""
        if not open_positions:
            return []

        actions = self.portfolio.evaluate_open_positions(positions=open_positions)
        close_results = []

        for act in actions:
            log_event(
                "position_review",
                symbol=act.symbol,
                payload={
                    "position_id": act.position_id,
                    "strategy": getattr(act, "strategy", "iron_condor"),
                    "action": act.action,
                    "reason": act.reason,
                    "unrealized_pnl": act.unrealized_pnl,
                },
                run_id=run_id,
            )

            if act.action == "close":
                logger.info(f"Executing closing order for {act.position_id}: {act.reason}")
                log_event(
                    "position_exit",
                    symbol=act.symbol,
                    payload={"position_id": act.position_id, "reason": act.reason, "realized_pnl": act.unrealized_pnl},
                    run_id=run_id,
                )
                close_results.append({
                    "position_id": act.position_id,
                    "action": act.action,
                    "reason": act.reason,
                    "unrealized_pnl": act.unrealized_pnl,
                })

        return close_results

    def format_cli_summary(self, scan_result: Dict[str, Any]) -> str:
        """Render standard formatted CLI output summary."""
        regime = scan_result.get("market_regime", {})
        catalyst = scan_result.get("catalyst_assessment", {})
        strat_dec = scan_result.get("strategy_decision", {})
        scores = strat_dec.get("scores", {})
        selected = scan_result.get("selected_strategy")
        is_dry = scan_result.get("dry_run", True)

        lines = [
            "MARKET REGIME",
            "---------------",
            f"Trend: {str(regime.get('trend', 'UNKNOWN')).upper()}",
            f"ADX: {regime.get('adx', 'N/A')}",
            f"IV Rank: {regime.get('iv_rank', 'N/A')}%",
            f"Catalyst Risk: {str(catalyst.get('catalyst_risk', 'UNKNOWN')).upper()}",
            "",
            "STRATEGIES",
            "---------------",
        ]

        for s_name in ["iron_condor", "bull_put_spread", "bear_call_spread"]:
            display_name = s_name.replace("_", " ").title()
            if s_name in scores:
                lines.append(f"{display_name:<17} {int(scores[s_name])}/100")
            else:
                lines.append(f"{display_name:<17} REJECTED")

        lines.extend([
            "",
            "SELECTED",
            "---------------",
        ])
        if selected:
            display_sel = selected.replace("_", " ").title()
            score_val = int(scores.get(selected, 0))
            lines.append(f"{display_sel}\nScore: {score_val}/100")
        else:
            lines.append("NO_TRADE")

        lines.extend([
            "",
            "RISK GATE",
            "---------------",
            "PASS" if scan_result.get("decision") == "TRADE" else "REJECTED / BYPASSED",
            "",
            "EXECUTION",
            "---------------",
            "DRY RUN\nNO ORDER SUBMITTED" if is_dry else f"PAPER SUBMITTED\nOrder ID: {scan_result.get('order_id')}",
        ])

        return "\n".join(lines)

    def run_cycle(self, symbol: str = "SPY", dry_run: bool = True) -> Dict[str, Any]:
        """Convenience method delegating to run_scan_cycle."""
        return self.run_scan_cycle(symbol=symbol, dry_run=dry_run)
