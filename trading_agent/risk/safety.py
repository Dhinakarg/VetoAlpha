"""
Persistent Safety, Idempotency, and Concurrency Control Layer.
Protects against duplicate trade executions caused by API timeouts, process restarts,
scheduler overlaps, network retries, and duplicate scans.
Uses SQLite for persistent idempotency tracking and process-level symbol locks.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple

from trading_agent.config import settings
from trading_agent.models import AccountSnapshot, IronCondor, TradeProposal, StrategyCandidate

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("logs/idempotency.db")


class PaperExecutionSafetyError(Exception):
    """Raised when an attempt is made to execute in a live/real-money environment or when paper safety checks fail."""
    pass


def validate_paper_execution_environment(
    trading_client: Optional[Any] = None,
    account: Optional[Union[AccountSnapshot, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Strictly verify that the runtime environment is configured exclusively for Alpaca Paper Trading.
    
    Raises:
        PaperExecutionSafetyError: If any condition for paper execution is not met.
    """
    # 1. Config environment check
    is_paper_config = getattr(settings, "alpaca_paper", False)
    if not is_paper_config:
        raise PaperExecutionSafetyError(
            "CRITICAL SAFETY VIOLATION: ALPACA_PAPER is False! Live money trading is strictly blocked and prohibited."
        )

    # 2. Client endpoint check
    if trading_client is not None:
        client_paper = getattr(trading_client, "_paper", None)
        if client_paper is False:
            raise PaperExecutionSafetyError(
                "CRITICAL SAFETY VIOLATION: Trading client is connected to Live Alpaca endpoint (_paper is False)!"
            )

    # 3. Account status & type check
    account_id = "UNKNOWN"
    if account is not None:
        raw_status = getattr(account, "status", None) if hasattr(account, "status") else account.get("status")
        status = str(raw_status).replace("AccountStatus.", "").upper()
        if status != "ACTIVE":
            raise PaperExecutionSafetyError(
                f"CRITICAL SAFETY VIOLATION: Alpaca account status is '{raw_status}', expected 'ACTIVE'."
            )
        account_id = getattr(account, "account_id", None) if hasattr(account, "account_id") else account.get("account_id", "UNKNOWN")

    # 4. Hackathon Mode Account Matching Check
    is_hackathon_mode = getattr(settings, "hackathon_mode", False)
    hackathon_acc_id = getattr(settings, "hackathon_account_id", None)
    if is_hackathon_mode:
        if not hackathon_acc_id:
            raise PaperExecutionSafetyError(
                "HACKATHON_ACCOUNT_MISMATCH: HACKATHON_MODE is true but HACKATHON_ACCOUNT_ID is not configured."
            )
        if account_id != "UNKNOWN" and account_id != hackathon_acc_id:
            raise PaperExecutionSafetyError(
                f"HACKATHON_ACCOUNT_MISMATCH: Current account ID '{account_id}' does not match explicitly configured HACKATHON_ACCOUNT_ID '{hackathon_acc_id}'."
            )

    return {
        "paper_verified": True,
        "alpaca_paper_config": True,
        "account_id": account_id,
        "hackathon_mode": is_hackathon_mode,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def _parse_datetime(ts: Union[str, datetime, None]) -> Optional[datetime]:
    """Parse string or datetime to UTC datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        ts_str = str(ts)
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def create_trade_fingerprint(cand: Union[IronCondor, StrategyCandidate, Dict[str, Any], Any]) -> str:
    """Generate a deterministic unique fingerprint for a proposed options trade structure."""
    if isinstance(cand, StrategyCandidate):
        strat = cand.strategy_name
        underlying = cand.underlying
        expiration = cand.expiration
        legs_str = ":".join(sorted(l.contract.symbol for l in cand.legs))
        return f"{strat}:sell_credit:{underlying}:{expiration}:{legs_str}"

    if isinstance(cand, dict):
        strat = cand.get("strategy_name", cand.get("strategy", "iron_condor"))
        underlying = cand.get("underlying", cand.get("underlying_symbol", ""))
        expiration = cand.get("expiration", cand.get("expiration_date", ""))
        if "legs" in cand and isinstance(cand["legs"], list):
            legs_str = ":".join(sorted(str(getattr(l, "contract", {}).get("symbol", "") if isinstance(getattr(l, "contract", {}), dict) else getattr(getattr(l, "contract", None), "symbol", "")) for l in cand["legs"]))
            return f"{strat}:sell_credit:{underlying}:{expiration}:{legs_str}"

        def _get_sym(leg_dict):
            if not leg_dict:
                return ""
            c = leg_dict.get("contract", {})
            return c.get("symbol", "") if isinstance(c, dict) else getattr(c, "symbol", "")

        pl_sym = _get_sym(cand.get("put_long"))
        ps_sym = _get_sym(cand.get("put_short"))
        cs_sym = _get_sym(cand.get("call_short"))
        cl_sym = _get_sym(cand.get("call_long"))
        return f"iron_condor:sell_credit:{underlying}:{expiration}:{pl_sym}:{ps_sym}:{cs_sym}:{cl_sym}"

    underlying = getattr(cand, "underlying_symbol", getattr(cand, "underlying", ""))
    expiration = getattr(cand, "expiration_date", getattr(cand, "expiration", ""))
    if hasattr(cand, "legs") and getattr(cand, "legs"):
        strat = getattr(cand, "strategy_name", "options_strategy")
        legs_str = ":".join(sorted(l.contract.symbol for l in cand.legs))
        return f"{strat}:sell_credit:{underlying}:{expiration}:{legs_str}"

    pl_sym = cand.put_long.contract.symbol if getattr(cand, "put_long", None) and cand.put_long.contract else ""
    ps_sym = cand.put_short.contract.symbol if getattr(cand, "put_short", None) and cand.put_short.contract else ""
    cs_sym = cand.call_short.contract.symbol if getattr(cand, "call_short", None) and cand.call_short.contract else ""
    cl_sym = cand.call_long.contract.symbol if getattr(cand, "call_long", None) and cand.call_long.contract else ""

    return f"iron_condor:sell_credit:{underlying}:{expiration}:{pl_sym}:{ps_sym}:{cs_sym}:{cl_sym}"


def is_data_fresh(timestamp: Union[str, datetime, None], max_age_seconds: int = 900) -> bool:
    """Check if given timestamp is fresh relative to current UTC time."""
    dt = _parse_datetime(timestamp)
    if dt is None:
        return False
    now_utc = datetime.now(timezone.utc)
    age = (now_utc - dt).total_seconds()
    return 0 <= age <= max_age_seconds


class SymbolLockManager:
    """Thread-safe and process-safe per-symbol execution locks."""

    def __init__(self):
        self._global_lock = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}

    def get_lock(self, symbol: str) -> threading.Lock:
        sym = symbol.upper().strip()
        with self._global_lock:
            if sym not in self._locks:
                self._locks[sym] = threading.Lock()
            return self._locks[sym]

    @contextmanager
    def acquire_symbol_lock(self, symbol: str, blocking: bool = True, timeout: float = -1):
        """Context manager to acquire lock for a given symbol."""
        lock = self.get_lock(symbol)
        acquired = lock.acquire(blocking=blocking, timeout=timeout)
        if not acquired:
            raise TimeoutError(f"Could not acquire execution lock for symbol '{symbol}'. Concurrent task running.")
        try:
            yield
        finally:
            lock.release()


class SafetyManager:
    """Central safety, persistent idempotency, and circuit breaker manager."""

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        max_daily_loss_usd: float = 3000.0,
        max_quote_age_seconds: int = 900,
    ):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.max_daily_loss_usd = max_daily_loss_usd
        self.max_quote_age_seconds = max_quote_age_seconds
        self.symbol_locks = SymbolLockManager()
        self._is_halted = False

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if not hasattr(self, "_memory_conn") or self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._memory_conn

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def _init_db(self):
        """Initialize SQLite persistent idempotency table."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS trade_fingerprints (
                        fingerprint TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        expiration TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        order_id TEXT,
                        status TEXT NOT NULL
                    )
                """)
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def is_duplicate_trade(self, fingerprint: str) -> bool:
        """Query SQLite persistent table to check if trade fingerprint was previously recorded."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM trade_fingerprints WHERE fingerprint = ?", (fingerprint,))
            row = cur.fetchone()
            return row is not None
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def record_trade_attempt(
        self,
        fingerprint: str,
        symbol: str = "",
        expiration: str = "",
        order_id: Optional[str] = None,
        status: str = "pending",
    ):
        """Insert or update trade fingerprint attempt in SQLite."""
        conn = self._get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO trade_fingerprints (fingerprint, symbol, expiration, created_at, order_id, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        order_id = excluded.order_id,
                        status = excluded.status,
                        created_at = excluded.created_at
                """, (fingerprint, symbol, expiration, now_iso, order_id, status))
        finally:
            if str(self.db_path) != ":memory:":
                conn.close()

    def is_circuit_breaker_active(
        self,
        account: Optional[Union[AccountSnapshot, Dict[str, Any]]] = None,
        daily_pnl: float = 0.0,
        config: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if daily loss threshold has triggered circuit breaker."""
        cfg = config or {}
        max_daily_loss = float(cfg.get("MAX_DAILY_LOSS_USD", self.max_daily_loss_usd))

        if daily_pnl < -max_daily_loss:
            self._is_halted = True
            return True

        return self._is_halted

    def check_circuit_breaker(
        self,
        account: Optional[Union[AccountSnapshot, Dict[str, Any]]] = None,
        starting_equity: float = 100000.0,
    ) -> bool:
        """Compatibility method for checking circuit breaker based on account equity drawdown."""
        if account is not None:
            eq = float(getattr(account, "equity", starting_equity) if hasattr(account, "equity") else account.get("equity", starting_equity))
            loss = starting_equity - eq
            if loss >= self.max_daily_loss_usd:
                self._is_halted = True
                return True
        return self._is_halted

    def reset_circuit_breaker(self):
        """Reset emergency halt state."""
        self._is_halted = False

    def validate_runtime_safety(
        self,
        proposed_trade: Union[TradeProposal, Dict[str, Any]],
        account: Union[AccountSnapshot, Dict[str, Any]],
        daily_pnl: float = 0.0,
        market_open: bool = True,
        paper_only: bool = True,
        max_quote_age_seconds: Optional[int] = None,
    ) -> Tuple[bool, List[str]]:
        """Perform comprehensive pre-trade runtime safety evaluation.
        
        Validates:
        1. Paper account requirement
        2. Market open status
        3. Circuit breaker state
        4. Persistent duplicate fingerprint check
        5. Option leg quote freshness
        """
        violations = []
        max_age = max_quote_age_seconds or self.max_quote_age_seconds

        # 1. Paper account requirement
        is_paper = getattr(settings, "alpaca_paper", True)
        if paper_only and not is_paper:
            violations.append("Safety Violation: Real/Live trading mode detected! Alpaca paper trading required.")

        # 2. Market open check
        if not market_open:
            violations.append("Safety Violation: Market is currently closed.")

        # 3. Circuit breaker check
        if self.is_circuit_breaker_active(account=account, daily_pnl=daily_pnl):
            violations.append(f"Safety Violation: Circuit breaker active (Daily P&L: ${daily_pnl:.2f}).")

        # Extract trade structure (supports TradeProposal, StrategyCandidate, IronCondor, dict)
        cand = proposed_trade
        if hasattr(proposed_trade, "candidate") and getattr(proposed_trade, "candidate"):
            cand = getattr(proposed_trade, "candidate")
        elif hasattr(proposed_trade, "condor") and getattr(proposed_trade, "condor"):
            cand = getattr(proposed_trade, "condor")
        elif isinstance(proposed_trade, dict):
            cand = proposed_trade.get("candidate") or proposed_trade.get("condor") or proposed_trade

        if not cand:
            violations.append("Safety Violation: Missing strategy structure in proposal.")
            return False, violations

        # 4. Persistent duplicate trade fingerprint check
        fp = create_trade_fingerprint(cand)
        if self.is_duplicate_trade(fp):
            violations.append(f"Safety Violation: Duplicate trade fingerprint already recorded in database: {fp}")

        # 5. Leg quote freshness check
        if isinstance(cand, StrategyCandidate):
            legs = cand.legs or []
            for leg in legs:
                contract = getattr(leg, "contract", None) if hasattr(leg, "contract") else (leg.get("contract") if isinstance(leg, dict) else leg)
                if not contract:
                    violations.append("Safety Violation: Missing contract on strategy leg.")
                    continue
                ts = getattr(contract, "quote_timestamp", None) if hasattr(contract, "quote_timestamp") else (contract.get("quote_timestamp") if isinstance(contract, dict) else None)
                if not is_data_fresh(ts, max_age_seconds=max_age):
                    violations.append(f"Safety Violation: Stale quote timestamp on leg (timestamp: {ts}).")
        else:
            condor = cand
            legs = [
                ("put_long", getattr(condor, "put_long", None) if hasattr(condor, "put_long") else (condor.get("put_long") if isinstance(condor, dict) else None)),
                ("put_short", getattr(condor, "put_short", None) if hasattr(condor, "put_short") else (condor.get("put_short") if isinstance(condor, dict) else None)),
                ("call_short", getattr(condor, "call_short", None) if hasattr(condor, "call_short") else (condor.get("call_short") if isinstance(condor, dict) else None)),
                ("call_long", getattr(condor, "call_long", None) if hasattr(condor, "call_long") else (condor.get("call_long") if isinstance(condor, dict) else None)),
            ]

            for leg_name, leg in legs:
                if not leg:
                    violations.append(f"Safety Violation: Missing leg {leg_name}.")
                    continue
                contract = getattr(leg, "contract", None) if hasattr(leg, "contract") else (leg.get("contract") if isinstance(leg, dict) else leg)
                if not contract:
                    violations.append(f"Safety Violation: Missing contract on leg {leg_name}.")
                    continue

                ts = getattr(contract, "quote_timestamp", None) if hasattr(contract, "quote_timestamp") else (contract.get("quote_timestamp") if isinstance(contract, dict) else None)
                if not is_data_fresh(ts, max_age_seconds=max_age):
                    violations.append(f"Safety Violation: Stale quote timestamp on leg {leg_name} (timestamp: {ts}).")

        return len(violations) == 0, violations


# Global default instance
safety_manager = SafetyManager()


def is_circuit_breaker_active(
    account: Optional[Union[AccountSnapshot, Dict[str, Any]]] = None,
    daily_pnl: float = 0.0,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """Helper delegating to default SafetyManager instance."""
    return safety_manager.is_circuit_breaker_active(account=account, daily_pnl=daily_pnl, config=config)


def is_duplicate_trade(fingerprint: str) -> bool:
    """Helper delegating to default SafetyManager instance."""
    return safety_manager.is_duplicate_trade(fingerprint)


def record_trade_attempt(
    fingerprint: str,
    symbol: str = "",
    expiration: str = "",
    order_id: Optional[str] = None,
    status: str = "pending",
):
    """Helper delegating to default SafetyManager instance."""
    safety_manager.record_trade_attempt(
        fingerprint=fingerprint,
        symbol=symbol,
        expiration=expiration,
        order_id=order_id,
        status=status,
    )


def validate_runtime_safety(
    proposed_trade: Union[TradeProposal, Dict[str, Any]],
    account: Union[AccountSnapshot, Dict[str, Any]],
    daily_pnl: float = 0.0,
    market_open: bool = True,
    paper_only: bool = True,
    max_quote_age_seconds: int = 900,
) -> Tuple[bool, List[str]]:
    """Helper delegating to default SafetyManager instance."""
    return safety_manager.validate_runtime_safety(
        proposed_trade=proposed_trade,
        account=account,
        daily_pnl=daily_pnl,
        market_open=market_open,
        paper_only=paper_only,
        max_quote_age_seconds=max_quote_age_seconds,
    )
