"""
Autonomous Adaptive AI Iron Condor Agent CLI Entrypoint and APScheduler Engine.
Runs continuous or single-cycle market scans strictly during active Alpaca market hours.
"""

import argparse
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_agent.config import settings
from trading_agent.agent.orchestrator import AgentOrchestrator
from trading_agent.data.alpaca_data import AlpacaDataFetcher
from trading_agent.infrastructure.logger import log_event

logger = logging.getLogger("trading_agent")


def setup_logging():
    """Configure console logging formatting."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def mask_account_id(account_id: Optional[str]) -> str:
    """Mask account ID for secure display in console and logs."""
    if not account_id:
        return "UNKNOWN_ACCOUNT"
    acc = str(account_id).strip()
    if len(acc) <= 4:
        return "****"
    return f"{acc[:2]}****{acc[-2:]}"


def print_startup_banner(
    mode_str: str,
    account_id: str,
    watchlist: List[str],
    interval_minutes: int,
    model_name: str,
    max_risk_usd: float,
    daily_loss_limit_usd: float,
):
    """Print clean startup dashboard with zero leaked secrets."""
    masked_acc = mask_account_id(account_id)
    watchlist_str = ", ".join(watchlist)

    banner = f"""
====================================
ADAPTIVE AI IRON CONDOR AGENT
====================================

Mode:
{mode_str}

Alpaca Account:
{masked_acc}

Watchlist:
{watchlist_str}

Scan Interval:
{interval_minutes} minutes

Featherless Model:
{model_name}

Risk Per Trade:
${max_risk_usd:,.2f}

Daily Loss Limit:
${daily_loss_limit_usd:,.2f}

====================================
"""
    print(banner)


def validate_safety_mode(dry_run: bool):
    """Enforce strict paper trading constraint. Halt immediately if real money is detected."""
    is_paper = getattr(settings, "alpaca_paper", True)
    if not is_paper:
        err_msg = (
            "CRITICAL SAFETY VIOLATION: Real-money live trading mode detected (ALPACA_PAPER=False)! "
            "This application strictly enforces paper trading. Halting execution."
        )
        logger.critical(err_msg)
        sys.exit(1)


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command line flags."""
    parser = argparse.ArgumentParser(
        description="Autonomous Adaptive AI Iron Condor Agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single scan cycle across the watchlist and exit immediately.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Run in dry-run mode (builds & validates orders without submitting to Alpaca).",
    )
    parser.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Allow order submission to the configured Alpaca Paper environment.",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="SPY,QQQ",
        help="Comma-separated ticker symbols to scan.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Scan interval in minutes when running continuous scheduler.",
    )

    return parser.parse_args(args)


def run_market_scan_job(
    orchestrator: AgentOrchestrator,
    alpaca_data: AlpacaDataFetcher,
    watchlist: List[str],
    dry_run: bool,
):
    """Scheduled job checking market clock and executing scan cycles for active symbols."""
    logger.info("Checking Alpaca market clock for scheduled scan tick...")
    try:
        clock = alpaca_data.get_market_clock()
        is_open = clock.get("is_open", False) if isinstance(clock, dict) else getattr(clock, "is_open", False)

        if not is_open:
            next_open = clock.get("next_open", "unknown") if isinstance(clock, dict) else getattr(clock, "next_open", "unknown")
            logger.info(f"Market is currently closed (Next Open: {next_open}). Skipping scan tick.")
            log_event("market_clock", symbol=None, payload={"is_open": False, "next_open": str(next_open)})
            return

        logger.info(f"Market is OPEN. Executing scan cycle across watchlist: {watchlist}")
        for symbol in watchlist:
            symbol = symbol.strip().upper()
            if symbol:
                res = orchestrator.run_scan_cycle(symbol=symbol, dry_run=dry_run)
                if hasattr(orchestrator, "format_cli_summary"):
                    print("\n" + orchestrator.format_cli_summary(res) + "\n")

    except Exception as e:
        logger.error(f"Error during scheduled market scan job: {e}", exc_info=True)


def main():
    setup_logging()
    args = parse_args()

    # Validate paper mode constraint
    validate_safety_mode(dry_run=args.dry_run)

    # Parse watchlist
    watchlist = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not watchlist:
        watchlist = ["SPY", "QQQ"]

    # Operating mode label
    mode_label = "Paper / Dry Run" if args.dry_run else "Paper / Live Execution"

    # Initialize components
    alpaca_data = AlpacaDataFetcher()
    orchestrator = AgentOrchestrator()

    # Fetch account for startup banner
    try:
        acc = alpaca_data.get_account_snapshot()
        acc_id = acc.account_id or "PAPER_ACCOUNT"
    except Exception:
        acc_id = "PAPER_ACCOUNT"

    # Print startup banner
    print_startup_banner(
        mode_str=mode_label,
        account_id=acc_id,
        watchlist=watchlist,
        interval_minutes=args.interval,
        model_name=settings.featherless_model,
        max_risk_usd=2500.0,
        daily_loss_limit_usd=3000.0,
    )

    log_event(
        "scan_started",
        symbol=None,
        payload={"mode": mode_label, "watchlist": watchlist, "once": args.once, "interval": args.interval},
    )

    # Single-run execution
    if args.once:
        logger.info("Executing single scan cycle (--once)...")
        run_market_scan_job(
            orchestrator=orchestrator,
            alpaca_data=alpaca_data,
            watchlist=watchlist,
            dry_run=args.dry_run,
        )
        logger.info("Single scan cycle completed. Exiting.")
        return

    # Continuous APScheduler loop
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler

        scheduler = BlockingScheduler(job_defaults={"max_instances": 1, "coalesce": True})
        scheduler.add_job(
            run_market_scan_job,
            trigger="interval",
            minutes=args.interval,
            next_run_time=datetime.now(timezone.utc),
            args=[orchestrator, alpaca_data, watchlist, args.dry_run],
            id="iron_condor_scanner",
        )

        def shutdown_handler(signum, frame):
            logger.info("Received termination signal. Shutting down scheduler gracefully...")
            scheduler.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        logger.info(f"Starting continuous APScheduler (Interval: {args.interval}m, MaxInstances: 1)...")
        scheduler.start()

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested. Exiting agent.")


if __name__ == "__main__":
    main()
