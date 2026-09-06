"""
Multi-Strategy Decision Audit & Explainability Harness.
Evaluates the 10-step multi-strategy autonomous options agent decision pipeline on live/fixture market data.

CRITICAL SAFETY INVARIANTS:
1. Strictly READ-ONLY. Zero order placement.
2. The deterministic safety layer MUST ALWAYS WIN.
3. NO_TRADE must be treated as a first-class decision with explicit deterministic blocking reasons.
"""

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union

# Ensure workspace root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from trading_agent.config import settings
from trading_agent.data.alpaca_data import AlpacaDataFetcher
from trading_agent.strategy.signals import (
    calculate_adx,
    calculate_bollinger_width,
    bollinger_width_percentile,
    extract_atm_iv,
    calculate_iv_rank,
)
from trading_agent.strategy.regime import detect_market_regime, classify_market_regime, MarketRegime
from trading_agent.strategy.strategy_selector import StrategySelector, StrategyDecision
from trading_agent.ai.catalyst_agent import CatalystIntelligenceAgent, CatalystAssessment
from trading_agent.strategy.ai_strategy_advisor import AIStrategyAdvisor, StrategyAdvice
from trading_agent.risk.risk_gate import evaluate_trade, RiskDecision
from trading_agent.execution.alpaca_executor import AlpacaExecutor
from trading_agent.models import AccountSnapshot, OptionChainSnapshot, OptionContract, StrategyCandidate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("audit_decisions")


def redact_secrets(data: Any) -> Any:
    """Recursively scrub sensitive keys or API secrets from dictionary or objects."""
    secret_keys = {"apca_api_key_id", "apca_api_secret_key", "featherless_api_key", "api_key", "secret_key", "authorization"}
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if str(k).lower() in secret_keys:
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_secrets(v)
        return cleaned
    elif isinstance(data, list):
        return [redact_secrets(item) for item in data]
    return data


def calculate_realized_volatility(bars: List[Any], window: int = 20) -> Optional[float]:
    """Calculate 20-day annualized realized volatility from daily closing prices."""
    if not bars or len(bars) < 2:
        return None
    
    closes = []
    for b in bars:
        if isinstance(b, dict):
            c = b.get("close")
        else:
            c = getattr(b, "close", None)
        if c is not None and float(c) > 0:
            closes.append(float(c))

    if len(closes) < 2:
        return None

    recent_closes = closes[-window:] if len(closes) >= window else closes
    returns = []
    for i in range(1, len(recent_closes)):
        if recent_closes[i - 1] > 0:
            r = math.log(recent_closes[i] / recent_closes[i - 1])
            returns.append(r)

    if not returns:
        return None

    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1 if len(returns) > 1 else 1)
    std_dev = math.sqrt(variance)
    annualized_vol = std_dev * math.sqrt(252)
    return round(annualized_vol, 4)


def load_fixture_iv_history(symbol: str, fixture_dir: Path) -> List[float]:
    """Load historical IV points from fixture or return default synthetic history."""
    symbol_fixture = fixture_dir / f"{symbol.lower()}_iv_history.json"
    default_fixture = fixture_dir / "spy_iv_history.json"
    
    target_file = symbol_fixture if symbol_fixture.exists() else default_fixture
    if target_file.exists():
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            iv_points = [float(item["atm_iv"]) for item in data.get("iv_history", []) if item.get("atm_iv")]
            if iv_points:
                return iv_points
        except Exception as e:
            logger.warning(f"Failed to load IV fixture from {target_file}: {e}")

    # Fallback synthetic IV history
    return [0.15, 0.16, 0.14, 0.18, 0.20, 0.17, 0.19, 0.21, 0.16, 0.18, 0.22, 0.15, 0.17, 0.19, 0.20]


def build_synthetic_option_chain(symbol: str, underlying_price: float) -> OptionChainSnapshot:
    """Construct a clean, liquid synthetic option chain for audit testing when live data is offline."""
    ref_dt = datetime.now(timezone.utc)
    exp_date = (ref_dt + timedelta(days=45)).strftime("%Y-%m-%d")
    ts = ref_dt.isoformat()
    contracts = []

    # Strikes around underlying
    base_strike = round(underlying_price / 5.0) * 5.0
    
    # Put legs
    strikes_puts = [
        (base_strike - 20, "put", -0.10, 1.20, 1.30),
        (base_strike - 15, "put", -0.18, 2.50, 2.60),
        (base_strike - 10, "put", -0.28, 4.20, 4.30),
    ]
    # Call legs
    strikes_calls = [
        (base_strike + 10, "call", 0.28, 4.20, 4.30),
        (base_strike + 15, "call", 0.18, 2.50, 2.60),
        (base_strike + 20, "call", 0.10, 1.20, 1.30),
    ]

    for strike, ctype, delta, bid, ask in strikes_puts + strikes_calls:
        sym = f"{symbol}{exp_date.replace('-', '')}{'P' if ctype == 'put' else 'C'}{int(strike*1000):08d}"
        contracts.append(
            OptionContract(
                symbol=sym,
                underlying_symbol=symbol,
                strike_price=float(strike),
                expiration_date=exp_date,
                contract_type=ctype,
                bid=bid,
                ask=ask,
                last_price=round((bid + ask) / 2.0, 2),
                delta=delta,
                implied_volatility=0.20,
                open_interest=1000,
                volume=500,
                quote_timestamp=ts,
            )
        )

    return OptionChainSnapshot(
        underlying_symbol=symbol,
        underlying_price=underlying_price,
        timestamp=ts,
        contracts=contracts,
    )


def detect_disagreement(
    ai_recommendation: Optional[str],
    deterministic_selection: Optional[str],
    eligible_strategies: List[str],
    risk_approved: bool,
) -> Dict[str, Any]:
    """
    Detect disagreement between AI Advisory layer and Deterministic selection/safety layer.
    Deterministic safety layer MUST ALWAYS WIN.
    """
    ai_rec_clean = (ai_recommendation or "").upper().strip()
    det_sel_clean = (deterministic_selection or "NO_TRADE").upper().strip()

    disagreement = {
        "disagreement_detected": False,
        "ai_recommendation": ai_rec_clean or "NONE",
        "deterministic_selection": det_sel_clean,
        "type": "NONE",
        "rationale": "AI and deterministic engine are aligned.",
        "rule_winner": "DETERMINISTIC_SAFETY_LAYER",
    }

    if not ai_rec_clean or ai_rec_clean == "NONE":
        return disagreement

    ai_rec_lower = ai_rec_clean.lower()
    det_sel_lower = det_sel_clean.lower()

    if ai_rec_clean != "NO_TRADE" and ai_rec_lower not in [s.lower() for s in eligible_strategies]:
        disagreement["disagreement_detected"] = True
        disagreement["type"] = "AI_INELIGIBLE_STRATEGY"
        disagreement["rationale"] = (
            f"AI recommended '{ai_rec_clean}', but this strategy is INELIGIBLE under current deterministic "
            f"regime/volatility filters ({eligible_strategies}). Deterministic filter overruled AI recommendation."
        )
    elif ai_rec_clean != "NO_TRADE" and det_sel_clean == "NO_TRADE":
        disagreement["disagreement_detected"] = True
        disagreement["type"] = "AI_PROPOSED_TRADE_DETERMINISTIC_NO_TRADE"
        disagreement["rationale"] = (
            f"AI recommended '{ai_rec_clean}', but deterministic selector/risk gate rejected all candidates "
            f"and selected NO_TRADE. Deterministic safety layer overruled AI."
        )
    elif ai_rec_lower != det_sel_lower and ai_rec_clean != "NO_TRADE" and det_sel_clean != "NO_TRADE":
        disagreement["disagreement_detected"] = True
        disagreement["type"] = "STRATEGY_MISMATCH"
        disagreement["rationale"] = (
            f"AI recommended '{ai_rec_clean}', but deterministic scoring matrix selected '{det_sel_clean}' "
            f"due to quantitative sub-scores. Deterministic selector overruled AI recommendation."
        )
    elif ai_rec_clean != "NO_TRADE" and det_sel_clean != "NO_TRADE" and not risk_approved:
        disagreement["disagreement_detected"] = True
        disagreement["type"] = "RISK_GATE_OVERRIDE"
        disagreement["rationale"] = (
            f"AI and deterministic selector agreed on '{det_sel_clean}', but common 15-rule risk gate "
            f"rejected the candidate. Risk gate overruled trade."
        )

    return disagreement


def run_decision_audit_for_symbol(
    symbol: str,
    alpaca_data: AlpacaDataFetcher,
    selector: StrategySelector,
    catalyst_agent: CatalystIntelligenceAgent,
    ai_advisor: AIStrategyAdvisor,
    config: Dict[str, Any],
    jsonl_path: Optional[Path] = None,
    fixture_dir: Path = Path("tests/fixtures"),
    offline: bool = False,
) -> Dict[str, Any]:
    """Run full 10-step decision audit pipeline for a given symbol."""
    timestamp_str = datetime.now(timezone.utc).isoformat()
    audit_reasons: List[str] = []

    # 1. Alpaca Account Snapshot
    try:
        account = alpaca_data.get_account_snapshot()
    except Exception as e:
        logger.warning(f"Could not fetch Alpaca account snapshot for {symbol}: {e}")
        account = AccountSnapshot(
            account_id="AUDIT_PAPER_MOCK",
            equity=100000.0,
            cash=100000.0,
            buying_power=100000.0,
            status="ACTIVE",
        )

    # 2. Market Data Pull
    try:
        quote = alpaca_data.get_underlying_quote(symbol)
        underlying_price = float(quote.get("last") or quote.get("ask") or quote.get("bid") or 500.0)
    except Exception as e:
        logger.warning(f"Could not fetch quote for {symbol}: {e}")
        underlying_price = 500.0

    try:
        bars = alpaca_data.get_recent_bars(symbol=symbol, timeframe="1Day", limit=100)
    except Exception as e:
        logger.warning(f"Could not fetch price bars for {symbol}: {e}")
        bars = []

    try:
        chain = alpaca_data.get_option_chain(symbol=symbol)
    except Exception as e:
        logger.warning(f"Could not fetch option chain for {symbol}: {e}")
        chain = None

    if not chain or not getattr(chain, "contracts", None):
        logger.info(f"Using synthetic option chain for {symbol} audit.")
        chain = build_synthetic_option_chain(symbol, underlying_price)

    try:
        news = alpaca_data.get_recent_news(symbol=symbol, limit=5)
    except Exception as e:
        news = []

    # 3. Realized Volatility & Indicators
    realized_vol = calculate_realized_volatility(bars, window=20)
    adx_val = calculate_adx(bars)
    bb_pct = bollinger_width_percentile(bars)

    # 4. IV Metrics & IV Rank
    atm_iv = extract_atm_iv(chain, underlying_price)
    iv_history = load_fixture_iv_history(symbol, fixture_dir)
    iv_rank = calculate_iv_rank(atm_iv, iv_history)

    # 5. Market Regime
    regime = detect_market_regime(bars, iv_rank, config=config)
    regime_class = classify_market_regime(bars, iv_rank, config=config)

    # 6. AI Catalyst Assessment & AI Advisory
    catalyst_assessment = catalyst_agent.assess_catalyst(
        symbol=symbol,
        recent_news=news,
        market_regime=regime.regime_class,
        iv_rank=iv_rank,
    )
    ai_advice = ai_advisor.advise_strategy(
        symbol=symbol,
        market_regime=regime.regime_class,
        iv_rank=iv_rank,
        catalyst_risk=getattr(catalyst_assessment, "catalyst_risk", "low") if catalyst_assessment else "low",
    )

    # 7 & 8. Deterministic Strategy Selector
    decision: StrategyDecision = selector.select_strategy(
        market_regime=regime,
        option_chain=chain,
        underlying_price=underlying_price,
        catalyst_assessment=catalyst_assessment,
        config=config,
    )

    # 9. Risk Gate Validation
    risk_decision: Optional[RiskDecision] = None
    final_trade_decision = decision.decision
    final_selected_strategy = decision.selected_strategy

    if decision.decision == "TRADE" and decision.selected_candidate is not None:
        risk_decision = evaluate_trade(
            proposed_trade=decision.selected_candidate,
            account=account,
            open_positions=[],
            open_orders=[],
            daily_pnl=0.0,
            config=config,
        )
        if not risk_decision.approved:
            final_trade_decision = "NO_TRADE"
            final_selected_strategy = None
            audit_reasons.append(f"Risk gate rejected selected trade '{decision.selected_strategy}': {risk_decision.reason}")
            audit_reasons.extend(risk_decision.rejections)

    # Compile explicit NO_TRADE reasons if applicable
    if final_trade_decision == "NO_TRADE":
        if decision.reasons:
            audit_reasons.extend(decision.reasons)
        if regime.reasons:
            audit_reasons.extend(regime.reasons)

    # 10. AI vs. Deterministic Disagreement Analysis
    disagreement = detect_disagreement(
        ai_recommendation=ai_advice.preferred_strategy if ai_advice else None,
        deterministic_selection=final_selected_strategy or decision.selected_strategy,
        eligible_strategies=regime.eligible_strategies or [],
        risk_approved=risk_decision.approved if risk_decision else True,
    )

    # Construct complete audit trace dictionary
    trace_record = {
        "timestamp": timestamp_str,
        "symbol": symbol,
        "underlying_price": underlying_price,
        "market_regime": {
            "regime_class": regime.regime_class,
            "trend": regime.trend,
            "volatility": regime.volatility,
            "confidence": regime.confidence,
            "adx": regime.adx,
            "bb_width_percentile": regime.bb_width_percentile,
            "realized_volatility_20d": realized_vol,
            "atm_iv": atm_iv,
            "iv_rank": iv_rank,
            "eligible_strategies": regime.eligible_strategies,
        },
        "catalyst_analysis": {
            "catalyst_risk": catalyst_assessment.catalyst_risk,
            "directional_bias": catalyst_assessment.directional_bias,
            "confidence": catalyst_assessment.confidence,
            "flagged_events": catalyst_assessment.flagged_events,
            "rationale": catalyst_assessment.rationale,
        },
        "ai_advisory": {
            "preferred_strategy": ai_advice.preferred_strategy if ai_advice else None,
            "confidence": ai_advice.confidence if ai_advice else 0.0,
            "rationale": ai_advice.rationale if ai_advice else "",
            "risk_flags": ai_advice.risk_flags if ai_advice else [],
        },
        "disagreement": disagreement,
        "strategy_candidates": {},
        "final_decision": {
            "decision": final_trade_decision,
            "selected_strategy": final_selected_strategy,
            "raw_selector_decision": decision.decision,
            "raw_selected_strategy": decision.selected_strategy,
            "scores": decision.scores,
            "reasons": audit_reasons,
        },
        "risk_gate": {
            "approved": risk_decision.approved if risk_decision else (final_trade_decision == "TRADE"),
            "rejections": risk_decision.rejections if risk_decision else [],
            "warnings": risk_decision.warnings if risk_decision else [],
            "reason": risk_decision.reason if risk_decision else "",
        },
        "execution": {
            "mode": "READ_ONLY",
            "orders_submitted": 0,
        },
    }

    # Extract candidate summary metrics for candidates evaluated
    for strat_name, cand in decision.candidates.items():
        score = decision.scores.get(strat_name, 0.0)
        cand_dict = {
            "eligible": True,
            "score": score,
        }
        if hasattr(cand, "net_credit"):
            cand_dict["credit"] = getattr(cand, "net_credit", 0.0)
        if hasattr(cand, "net_debit"):
            cand_dict["debit"] = getattr(cand, "net_debit", 0.0)
        if hasattr(cand, "max_profit"):
            cand_dict["max_profit"] = getattr(cand, "max_profit", 0.0)
        if hasattr(cand, "max_loss"):
            cand_dict["max_loss"] = getattr(cand, "max_loss", 0.0)
        if hasattr(cand, "max_risk"):
            cand_dict["max_loss"] = getattr(cand, "max_risk", 0.0)
        if hasattr(cand, "expiration_date"):
            exp_s = getattr(cand, "expiration_date", "")
            try:
                exp_dt = datetime.strptime(exp_s, "%Y-%m-%d")
                cand_dict["dte"] = (exp_dt - datetime.now()).days
            except Exception:
                cand_dict["dte"] = 45
        trace_record["strategy_candidates"][strat_name] = cand_dict

    # Append to JSONL log file
    if jsonl_path:
        try:
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            redacted_record = redact_secrets(trace_record)
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(redacted_record) + "\n")
        except Exception as e:
            logger.error(f"Failed to append decision audit log to {jsonl_path}: {e}")

    return trace_record


def format_human_readable_report(trace: Dict[str, Any]) -> str:
    """Format machine trace into clear, human-readable terminal audit report."""
    symbol = trace["symbol"]
    timestamp = trace["timestamp"]
    price = trace["underlying_price"]
    mr = trace["market_regime"]
    cat = trace["catalyst_analysis"]
    ai = trace["ai_advisory"]
    dis = trace["disagreement"]
    fin = trace["final_decision"]
    rg = trace["risk_gate"]
    exe = trace["execution"]
    cands = trace.get("strategy_candidates", {})

    lines = []
    lines.append("=" * 60)
    lines.append("MULTI-STRATEGY DECISION AUDIT")
    lines.append("=" * 60)
    lines.append(f"Symbol: {symbol}")
    lines.append(f"Timestamp: {timestamp}")
    lines.append(f"Underlying Price: ${price:.2f}")
    lines.append("")
    lines.append("## MARKET REGIME")
    lines.append(f"Regime: {mr['regime_class']}")
    lines.append(f"Trend: {mr['trend']}")
    lines.append(f"Volatility State: {mr['volatility']}")
    lines.append(f"Confidence: {mr['confidence']:.2f}")
    lines.append(f"ADX: {mr['adx'] if mr['adx'] is not None else 'N/A'}")
    lines.append(f"BB Width Percentile: {mr['bb_width_percentile'] if mr['bb_width_percentile'] is not None else 'N/A'}")
    lines.append(f"Realized Volatility (20d): {mr['realized_volatility_20d'] * 100:.2f}%" if mr['realized_volatility_20d'] else "Realized Volatility: N/A")
    lines.append(f"ATM IV: {mr['atm_iv'] * 100:.2f}%" if mr['atm_iv'] else "ATM IV: N/A")
    lines.append(f"IV Rank: {mr['iv_rank']:.1f}%" if mr['iv_rank'] is not None else "IV Rank: N/A")
    lines.append(f"Eligible Strategies: {', '.join(mr['eligible_strategies']) if mr['eligible_strategies'] else 'None'}")
    lines.append("")
    lines.append("## CATALYST ANALYSIS")
    lines.append(f"Catalyst Risk: {cat['catalyst_risk'].upper()}")
    lines.append(f"Directional Bias: {cat['directional_bias']}")
    lines.append(f"AI Confidence: {cat['confidence']:.2f}")
    lines.append(f"Flagged Events: {', '.join(cat['flagged_events']) if cat['flagged_events'] else 'None'}")
    lines.append(f"Rationale: {cat['rationale']}")
    lines.append("")
    lines.append("## STRATEGY CANDIDATES")

    strategy_names = [
        ("IRON CONDOR", "iron_condor"),
        ("BULL PUT SPREAD", "bull_put_spread"),
        ("BEAR CALL SPREAD", "bear_call_spread"),
        ("LONG CALL", "long_call"),
        ("LONG PUT", "long_put"),
    ]

    for label, key in strategy_names:
        lines.append(f"\n{label}")
        if key in cands:
            c_info = cands[key]
            lines.append(f"Eligible: YES")
            lines.append(f"Score: {c_info['score']:.1f}/100")
            lines.append(f"Max Profit: ${c_info.get('max_profit', 0.0):.2f}")
            lines.append(f"Max Loss: ${c_info.get('max_loss', 0.0):.2f}")
            if "credit" in c_info:
                lines.append(f"Net Credit: ${c_info['credit']:.2f}")
            if "debit" in c_info:
                lines.append(f"Net Debit: ${c_info['debit']:.2f}")
        else:
            lines.append("Eligible: NO (or candidate construction failed)")
            lines.append("Score: 0.0")

    lines.append("")
    lines.append("## AI ADVISORY")
    lines.append(f"Preferred Strategy: {ai['preferred_strategy'] or 'NONE'}")
    lines.append(f"Confidence: {ai['confidence']:.2f}")
    lines.append(f"Rationale: {ai['rationale']}")
    lines.append(f"Risk Flags: {', '.join(ai['risk_flags']) if ai['risk_flags'] else 'None'}")
    lines.append("")

    if dis["disagreement_detected"]:
        lines.append("## DISAGREEMENT ANALYSIS")
        lines.append(f"Disagreement Type: {dis['type']}")
        lines.append(f"AI Recommendation: {dis['ai_recommendation']}")
        lines.append(f"Deterministic Choice: {dis['deterministic_selection']}")
        lines.append(f"Explanation: {dis['rationale']}")
        lines.append(f"Resolution: {dis['rule_winner']} wins")
        lines.append("")

    lines.append("## FINAL DETERMINISTIC DECISION")
    lines.append(f"Selected Strategy: {fin['selected_strategy'] or 'NO_TRADE'}")
    lines.append(f"Decision: {fin['decision']}")
    lines.append("Reasons / Blocking Factors:")
    for r in fin.get("reasons", []):
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("## RISK GATE")
    lines.append(f"Approved: {'PASS' if rg['approved'] else 'REJECT'}")
    if rg["rejections"]:
        lines.append("Rejections:")
        for rej in rg["rejections"]:
            lines.append(f"  - {rej}")
    lines.append("")
    lines.append("## EXECUTION")
    lines.append(f"Mode: {exe['mode']}")
    lines.append(f"Orders Submitted: {exe['orders_submitted']}")
    lines.append("=" * 60)
    lines.append("")

    return "\n".join(lines)


def verify_repeatability(symbol: str, count: int = 3) -> bool:
    """Verify that multiple deterministic pipeline runs on identical inputs yield identical outputs."""
    logger.info(f"Running repeatability verification for {symbol} ({count} iterations)...")
    alpaca_data = AlpacaDataFetcher()
    selector = StrategySelector()
    catalyst_agent = CatalystIntelligenceAgent()
    ai_advisor = AIStrategyAdvisor()
    config = settings.model_dump() if hasattr(settings, "model_dump") else {}

    results = []
    for i in range(count):
        trace = run_decision_audit_for_symbol(
            symbol=symbol,
            alpaca_data=alpaca_data,
            selector=selector,
            catalyst_agent=catalyst_agent,
            ai_advisor=ai_advisor,
            config=config,
            jsonl_path=None,
        )
        mr = trace["market_regime"]
        fin = trace["final_decision"]
        key_summary = (
            mr["regime_class"],
            mr["trend"],
            mr["volatility"],
            tuple(sorted(mr["eligible_strategies"])) if mr["eligible_strategies"] else (),
            fin["decision"],
            fin["selected_strategy"],
            tuple(sorted(fin["scores"].items())),
        )
        results.append(key_summary)

    first = results[0]
    all_equal = all(r == first for r in results)
    if all_equal:
        logger.info(f"✅ REPEATABILITY VERIFIED ({count}/{count} identical deterministic outcomes)")
    else:
        logger.error("❌ REPEATABILITY CHECK FAILED: Outcomes differed across iterations!")

    return all_equal


def main():
    parser = argparse.ArgumentParser(description="Multi-Strategy Decision Audit & Explainability Harness")
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "IWM"], help="Symbols to audit")
    parser.add_argument("--jsonl", type=str, default="logs/decision_audit.jsonl", help="Path to JSONL audit log")
    parser.add_argument("--repeat", type=int, default=1, help="Number of repeatability check runs")
    parser.add_argument("--fixture-dir", type=str, default="tests/fixtures", help="Directory for test fixtures")
    parser.add_argument("--offline", action="store_true", help="Run in offline synthetic fixture mode")

    args = parser.parse_args()
    jsonl_path = Path(args.jsonl)
    fixture_dir = Path(args.fixture_dir)

    alpaca_data = AlpacaDataFetcher()
    selector = StrategySelector()
    catalyst_agent = CatalystIntelligenceAgent()
    ai_advisor = AIStrategyAdvisor()
    config = settings.model_dump() if hasattr(settings, "model_dump") else {}

    print("\nStarting Multi-Strategy Autonomous Decision Audit...\n")

    for symbol in args.symbols:
        trace = run_decision_audit_for_symbol(
            symbol=symbol,
            alpaca_data=alpaca_data,
            selector=selector,
            catalyst_agent=catalyst_agent,
            ai_advisor=ai_advisor,
            config=config,
            jsonl_path=jsonl_path,
            fixture_dir=fixture_dir,
            offline=args.offline,
        )
        report = format_human_readable_report(trace)
        print(report)

        if args.repeat > 1:
            verify_repeatability(symbol=symbol, count=args.repeat)

    print(f"\nAudit complete. Machine-readable audit trace saved to: {jsonl_path.resolve()}\n")


if __name__ == "__main__":
    main()
