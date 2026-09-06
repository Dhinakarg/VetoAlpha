"""
Autonomous Crypto Paper Competition & Validation Harness.
Executes repeated multi-strategy crypto scan cycles with full risk-gate evaluation,
simulation execution, position lifecycle management, restart recovery, and telemetry logging.
ISOLATED FROM OPTIONS TELEMETRY AND EXECUTION PATHWAYS.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from trading_agent.config import settings
from trading_agent.crypto.crypto_data import CryptoDataFetcher, CryptoMarketData
from trading_agent.crypto.crypto_regime import classify_crypto_regime, CryptoRegime
from trading_agent.crypto.crypto_strategy import CryptoStrategyCandidate
from trading_agent.crypto.crypto_selector import CryptoStrategySelector, CryptoStrategyDecision
from trading_agent.crypto.crypto_risk import CryptoRiskGate, CryptoRiskDecision
from trading_agent.crypto.crypto_execution import CryptoExecutionEngine, CryptoOrderPayload
from trading_agent.crypto.crypto_portfolio import CryptoPortfolioManager, CryptoPosition
from trading_agent.crypto.crypto_pnl import CryptoPnLEngine, CryptoPerformanceReport

logger = logging.getLogger("simulate_crypto")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class CryptoSimulationRunner:
    """Autonomous Crypto Simulation Harness."""

    def __init__(
        self,
        symbols: List[str],
        cycles: int = 10,
        paper: bool = False,
        resume: bool = False,
        data_dir: Optional[Path] = None,
        submit_alpaca: bool = False,
    ):
        self.symbols = symbols
        self.cycles = cycles
        self.paper = paper
        self.resume = resume
        self.data_dir = data_dir or (ROOT_DIR / "logs")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.submit_alpaca = submit_alpaca

        self.execution_mode = "PAPER_SIMULATION" if paper else "DRY_RUN"
        self.state_file = self.data_dir / "crypto_state.json"
        self.telemetry_file = self.data_dir / "crypto_run.jsonl"
        self.audit_file = self.data_dir / "crypto_decision_audit.jsonl"

        self.data_fetcher = CryptoDataFetcher()
        self.selector = CryptoStrategySelector(min_score=getattr(settings, "crypto_min_signal_score", 65.0))
        self.risk_gate = CryptoRiskGate(
            risk_per_trade=getattr(settings, "crypto_risk_per_trade", 0.0025),
            max_daily_loss_pct=getattr(settings, "crypto_max_daily_loss", 0.01),
            max_open_positions=getattr(settings, "crypto_max_open_positions", 3),
        )
        self.executor = CryptoExecutionEngine(mode=self.execution_mode, submit_alpaca=self.submit_alpaca)
        self.portfolio = CryptoPortfolioManager(initial_equity=100000.0)

        self.fingerprints_seen: Set[str] = set()
        self.current_cycle = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.circuit_breaker_tripped = False
        self.circuit_breaker_reason = ""
        self.stats = {"ai_calls": 0, "ai_skipped": 0, "ai_failures": 0, "ai_cache_hits": 0}

        if self.resume and self.state_file.exists():
            self._load_state()

    def run_simulation(self):
        """Execute autonomous crypto simulation cycles."""
        logger.info(f"Starting Crypto Validation Engine: Mode={self.execution_mode}, Symbols={self.symbols}, Cycles={self.cycles}")

        start_c = 1 if self.current_cycle >= self.cycles else self.current_cycle + 1
        end_c = self.cycles if self.current_cycle >= self.cycles else self.cycles

        for cycle in range(start_c, end_c + 1):
            self.current_cycle = cycle
            logger.info(f"--- Starting Crypto Simulation Cycle {cycle}/{self.cycles} ---")

            # 1. Fetch live market data map
            market_data_map: Dict[str, CryptoMarketData] = {}
            for sym in self.symbols:
                market_data_map[sym] = self.data_fetcher.fetch_market_data(sym)

            # 2. Check position exits
            closed = self.portfolio.update_and_check_exits(market_data_map)
            for c_pos in closed:
                self.daily_pnl += c_pos.realized_pnl
                if c_pos.realized_pnl < 0:
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0

                # Execute closing order via executor (submits SELL order to Alpaca if submit_alpaca is enabled)
                exit_payload = self.executor.build_exit_order_payload(
                    position_id=c_pos.position_id,
                    symbol=c_pos.symbol,
                    direction=c_pos.direction,
                    quantity=c_pos.quantity,
                    exit_price=c_pos.exit_price,
                    exit_reason=c_pos.exit_reason,
                )
                self.executor.execute_order(exit_payload)

            # 3. Check Circuit Breaker
            if self._check_circuit_breaker():
                logger.warning(f"Crypto Circuit Breaker Active: {self.circuit_breaker_reason}. Skipping new entries.")
                self._save_state()
                continue

            # 4. Evaluate candidates for each symbol
            cycle_orders_submitted = 0
            for sym in self.symbols:
                if cycle_orders_submitted >= self.risk_gate.max_open_positions:
                    logger.info(f"Cycle order limit ({self.risk_gate.max_open_positions}) reached. Skipping remaining symbols.")
                    break

                data = market_data_map[sym]
                regime_res = classify_crypto_regime(data)

                # AI Quota Skipping Check
                ai_assessment = None
                skip_ai = False
                skip_reason = ""

                if self.circuit_breaker_tripped:
                    skip_ai = True
                    skip_reason = "Circuit breaker active"
                elif regime_res.regime == CryptoRegime.UNKNOWN:
                    skip_ai = True
                    skip_reason = "UNKNOWN regime"
                elif not getattr(settings, "crypto_ai_enabled", True):
                    skip_ai = True
                    skip_reason = "Crypto AI disabled in config"

                if skip_ai:
                    self.stats["ai_skipped"] += 1
                    logger.info(f"ai_call_skipped_reason for {sym}: {skip_reason}")
                else:
                    try:
                        self.stats["ai_calls"] += 1
                        ai_assessment = self.selector.ai_agent.analyze_catalyst(
                            symbol=sym,
                            recent_news=[],
                            market_regime=regime_res.regime.value,
                            iv_rank=50.0,
                        )
                    except Exception as e:
                        self.stats["ai_failures"] += 1
                        logger.warning(f"Featherless AI assessment failed for {sym}: {e}")

                # Gather open positions from local state + live broker API
                open_pos_list = [{"symbol": p.symbol} for p in self.portfolio.active_positions.values()]
                if self.submit_alpaca and getattr(self.executor, "_trading_client", None):
                    try:
                        b_positions = self.executor._trading_client.get_all_positions()
                        for bp in b_positions:
                            open_pos_list.append({"symbol": bp.symbol})
                    except Exception as e:
                        logger.error(f"CRYPTO_POSITION_RECONCILIATION_WARNING: Failed to query Alpaca broker positions: {e}")

                # Check if symbol already has an open position (Enforce HOLD State)
                clean_sym = sym.replace("/", "").upper()
                already_open = any(str(p.get("symbol", "")).replace("/", "").upper() == clean_sym for p in open_pos_list)

                if already_open:
                    logger.info(f"CRYPTO_HOLD for {sym}: Position already active in broker/local state. Skipping new entry.")
                    telemetry_rec = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event_type": "CRYPTO_HOLD",
                        "cycle": cycle,
                        "symbol": sym,
                        "price": data.current_price,
                        "regime": regime_res.regime.value,
                        "decision": "HOLD",
                        "strategy": "NO_TRADE",
                        "risk_approved": False,
                        "risk_violations": [f"DUPLICATE_SYMBOL: Position already open for '{sym}'"],
                        "ai_call_skipped": skip_ai,
                        "ai_skip_reason": skip_reason,
                        "execution_mode": self.execution_mode,
                        "engine": "CRYPTO_VALIDATION_ENGINE",
                    }
                    self._append_telemetry(telemetry_rec)
                    continue

                # Select strategy candidate
                decision = self.selector.evaluate(data, regime_res, ai_assessment)

                # Evaluate Risk Gate
                risk_decision = self.risk_gate.evaluate_candidate(
                    candidate=decision.selected_candidate,
                    equity=self.portfolio.equity,
                    daily_pnl=self.daily_pnl,
                    open_positions=open_pos_list,
                    consecutive_losses=self.consecutive_losses,
                )

                # Check Idempotency Fingerprint
                fingerprint = ""
                if risk_decision.approved and risk_decision.candidate:
                    c = risk_decision.candidate
                    fingerprint = f"{c.strategy}:{c.direction}:{c.symbol}:{c.entry_price:.2f}"
                    if fingerprint in self.fingerprints_seen:
                        logger.warning(f"DUPLICATE_BLOCKED for {sym}: Fingerprint '{fingerprint}' already processed.")
                        risk_decision.approved = False
                        risk_decision.violations.append("DUPLICATE_BLOCKED: Trade fingerprint already executed.")

                # Build & Execute Order Payload
                if risk_decision.approved and risk_decision.candidate:
                    payload = self.executor.build_order_payload(risk_decision)
                    if payload:
                        executed_payload = self.executor.execute_order(payload)
                        if executed_payload.status in ("FILLED", "DRY_RUN_APPROVED", "SUBMITTED_TO_ALPACA"):
                            self.fingerprints_seen.add(fingerprint)
                            cycle_orders_submitted += 1
                            if executed_payload.status in ("FILLED", "SUBMITTED_TO_ALPACA"):
                                self.portfolio.open_position_from_order(executed_payload, regime=regime_res.regime.value)

                # Log Telemetry Record
                event_type = "CRYPTO_ENTRY_SUBMITTED" if risk_decision.approved else "CRYPTO_DUPLICATE_ENTRY_BLOCKED" if "DUPLICATE_BLOCKED" in str(risk_decision.violations) else "CRYPTO_HOLD"
                telemetry_rec = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_type": event_type,
                    "cycle": cycle,
                    "symbol": sym,
                    "price": data.current_price,
                    "regime": regime_res.regime.value,
                    "decision": decision.action,
                    "strategy": decision.selected_candidate.strategy if decision.selected_candidate else "NO_TRADE",
                    "risk_approved": risk_decision.approved,
                    "risk_violations": risk_decision.violations,
                    "ai_call_skipped": skip_ai,
                    "ai_skip_reason": skip_reason,
                    "execution_mode": self.execution_mode,
                    "engine": "CRYPTO_VALIDATION_ENGINE",
                }
                self._append_telemetry(telemetry_rec)

            self._save_state()

        logger.info("Autonomous Crypto Simulation Completed Successfully.")

    def _check_circuit_breaker(self) -> bool:
        """Evaluate crypto circuit breaker conditions."""
        max_loss = self.portfolio.initial_equity * getattr(settings, "crypto_max_daily_loss", 0.01)
        if self.daily_pnl <= -max_loss:
            self.circuit_breaker_tripped = True
            self.circuit_breaker_reason = f"Daily loss limit (-${max_loss:.2f}) breached"
            return True
        return False

    def _save_state(self):
        """Save crypto state checkpoint."""
        state = {
            "current_cycle": self.current_cycle,
            "equity": self.portfolio.equity,
            "cash": self.portfolio.cash,
            "daily_pnl": self.daily_pnl,
            "consecutive_losses": self.consecutive_losses,
            "circuit_breaker_tripped": self.circuit_breaker_tripped,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "fingerprints_seen": list(self.fingerprints_seen),
            "stats": self.stats,
            "engine": "CRYPTO_VALIDATION_ENGINE",
        }
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save crypto state checkpoint: {e}")

    def _load_state(self):
        """Load crypto state checkpoint."""
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.current_cycle = state.get("current_cycle", 0)
            self.portfolio.equity = state.get("equity", 100000.0)
            self.portfolio.cash = state.get("cash", 100000.0)
            self.daily_pnl = state.get("daily_pnl", 0.0)
            self.consecutive_losses = state.get("consecutive_losses", 0)
            self.circuit_breaker_tripped = state.get("circuit_breaker_tripped", False)
            self.circuit_breaker_reason = state.get("circuit_breaker_reason", "")
            self.fingerprints_seen = set(state.get("fingerprints_seen", []))
            self.stats = state.get("stats", self.stats)
            logger.info(f"Resumed crypto state at cycle {self.current_cycle}")
        except Exception as e:
            logger.error(f"Failed to load crypto state checkpoint: {e}")

    def _append_telemetry(self, record: Dict[str, Any]):
        """Append record to logs/crypto_run.jsonl."""
        try:
            with open(self.telemetry_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Failed to write crypto telemetry: {e}")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Crypto Validation Simulation Runner")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USD", "ETH/USD", "SOL/USD"])
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--paper", action="store_true", default=False)
    parser.add_argument("--submit-alpaca-crypto", action="store_true", default=False, help="Submit paper crypto orders directly to Alpaca Account")
    parser.add_argument("--resume", action="store_true", default=False)

    args = parser.parse_args()
    runner = CryptoSimulationRunner(
        symbols=args.symbols,
        cycles=args.cycles,
        paper=args.paper or args.submit_alpaca_crypto,
        resume=args.resume,
        submit_alpaca=args.submit_alpaca_crypto,
    )
    runner.run_simulation()


if __name__ == "__main__":
    main()
