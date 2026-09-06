"""
Persistent SQLite store for historical daily ATM Implied Volatility (IV) observations.
Enforces at most ONE canonical ATM IV observation per symbol per trading date for IV Rank.
Intraday scans update the current day's observation rather than creating multiple points.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

logger = logging.getLogger(__name__)


class IVHistoryStore:
    """Persistent SQLite store for daily ATM Implied Volatility series."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            db_path = Path("logs") / "iv_history.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a sqlite3 connection with standard settings."""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initialize database schema with compound primary key (symbol, trading_date)."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_iv_history (
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    atm_iv REAL NOT NULL,
                    underlying_price REAL NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    PRIMARY KEY (symbol, trading_date)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_iv_symbol_date
                ON daily_iv_history(symbol, trading_date)
                """
            )
            conn.commit()

    def record_daily_iv(
        self,
        symbol: str,
        trading_date: str,
        atm_iv: float,
        underlying_price: float,
        timestamp_utc: Optional[str] = None,
    ) -> bool:
        """Record or update the canonical ATM IV observation for a symbol and trading date.
        
        If an observation already exists for this symbol and date, it is updated in place.
        """
        symbol = symbol.strip().upper()
        trading_date = str(trading_date)[:10]

        if atm_iv is None or atm_iv <= 0:
            logger.warning(f"Invalid ATM IV ({atm_iv}) for {symbol} on {trading_date}. Skipping record.")
            return False

        if underlying_price is None or underlying_price <= 0:
            logger.warning(f"Invalid underlying price ({underlying_price}) for {symbol}. Skipping record.")
            return False

        if not timestamp_utc:
            timestamp_utc = datetime.now(timezone.utc).isoformat()

        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO daily_iv_history (symbol, trading_date, atm_iv, underlying_price, timestamp_utc)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, trading_date) DO UPDATE SET
                        atm_iv = excluded.atm_iv,
                        underlying_price = excluded.underlying_price,
                        timestamp_utc = excluded.timestamp_utc
                    """,
                    (symbol, trading_date, float(atm_iv), float(underlying_price), timestamp_utc),
                )
                conn.commit()
                logger.debug(f"Recorded canonical IV for {symbol} on {trading_date}: IV={atm_iv:.4f}, Px={underlying_price:.2f}")
                return True
        except Exception as e:
            logger.error(f"Failed to record daily IV for {symbol} on {trading_date}: {e}")
            return False

    def get_historical_daily_iv(
        self,
        symbol: str,
        lookback_days: int = 252,
        exclude_date: Optional[str] = None,
    ) -> List[float]:
        """Fetch chronological list of historical daily ATM IV values for a symbol.
        
        Args:
            symbol: Ticker symbol (e.g. SPY)
            lookback_days: Maximum number of distinct trading days to return
            exclude_date: Optional trading date (e.g. today) to exclude from historical series
            
        Returns:
            List of float IV values ordered from oldest to newest.
        """
        symbol = symbol.strip().upper()
        query = "SELECT atm_iv FROM daily_iv_history WHERE symbol = ?"
        params: List[Any] = [symbol]

        if exclude_date:
            query += " AND trading_date != ?"
            params.append(str(exclude_date)[:10])

        query += " ORDER BY trading_date ASC"

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                iv_list = [float(row["atm_iv"]) for row in rows]
                if lookback_days and len(iv_list) > lookback_days:
                    iv_list = iv_list[-lookback_days:]
                return iv_list
        except Exception as e:
            logger.error(f"Failed to fetch historical IV for {symbol}: {e}")
            return []

    def get_history_count(self, symbol: str, exclude_date: Optional[str] = None) -> int:
        """Get count of distinct historical trading days stored for a symbol."""
        symbol = symbol.strip().upper()
        query = "SELECT COUNT(*) as cnt FROM daily_iv_history WHERE symbol = ?"
        params: List[Any] = [symbol]

        if exclude_date:
            query += " AND trading_date != ?"
            params.append(str(exclude_date)[:10])

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(query, params)
                row = cursor.fetchone()
                return int(row["cnt"]) if row else 0
        except Exception as e:
            logger.error(f"Failed to count IV history days for {symbol}: {e}")
            return 0

    def get_iv_history_status(
        self,
        symbol: str,
        current_atm_iv: Optional[float] = None,
        min_required: int = 15,
        preferred_days: int = 60,
        exclude_current_day: bool = True,
        current_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate and return structured cold-start status and metrics for IV history."""
        symbol = symbol.strip().upper()
        today_str = current_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        exclude = today_str if exclude_current_day else None

        history = self.get_historical_daily_iv(symbol=symbol, lookback_days=preferred_days, exclude_date=exclude)
        history_days = len(history)

        status = "READY" if history_days >= min_required else "INSUFFICIENT_HISTORY"

        from trading_agent.strategy.signals import calculate_iv_rank
        iv_rank = calculate_iv_rank(current_atm_iv=current_atm_iv, historical_iv=history, min_required_days=min_required)

        return {
            "symbol": symbol,
            "history_days": history_days,
            "minimum_required": min_required,
            "preferred_days": preferred_days,
            "status": status,
            "current_atm_iv": current_atm_iv,
            "iv_rank": iv_rank,
        }
