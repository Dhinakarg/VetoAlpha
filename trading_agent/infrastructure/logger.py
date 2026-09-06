"""
Append-only JSON Lines event logging infrastructure for autonomous trading audit trails.
Logs every lifecycle event to logs/agent_log.jsonl with strict secret redaction.
"""

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Set

from trading_agent.config import settings

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("logs/agent_log.jsonl")

SUPPORTED_EVENT_TYPES: Set[str] = {
    "scan_started",
    "market_clock",
    "market_snapshot",
    "regime_check",
    "sentiment_check",
    "condor_proposed",
    "risk_gate_result",
    "safety_check",
    "order_dry_run",
    "order_submitted",
    "order_partially_filled",
    "order_filled",
    "order_rejected",
    "position_reviewed",
    "position_closed",
    "circuit_breaker_tripped",
    "error",
    # Validation harness events
    "execution_validation_started",
    "paper_safety_check",
    "order_payload_built",
    "paper_order_submitted",
    "order_status_changed",
    "paper_position_detected",
    "exit_signal_generated",
    "paper_close_submitted",
    "paper_trade_completed",
    "execution_validation_failed",
}

SENSITIVE_KEYS: Set[str] = {
    "api_key",
    "secret_key",
    "featherless_api_key",
    "alpaca_api_key",
    "alpaca_secret_key",
    "authorization",
    "bearer",
    "password",
    "token",
    "secret",
}


def _get_known_secrets() -> List[str]:
    """Retrieve known configuration secrets to redact from logs."""
    secrets = []
    for val in [
        getattr(settings, "alpaca_api_key", None),
        getattr(settings, "alpaca_secret_key", None),
        getattr(settings, "featherless_api_key", None),
    ]:
        if val and len(str(val).strip()) > 3:
            secrets.append(str(val).strip())
    return secrets


def redact_secrets(data: Any, known_secrets: Optional[List[str]] = None) -> Any:
    """Recursively scrub sensitive keys and token values from data structures."""
    secrets = known_secrets if known_secrets is not None else _get_known_secrets()

    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(sens in k_lower for sens in SENSITIVE_KEYS):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_secrets(v, secrets)
        return cleaned

    elif isinstance(data, list):
        return [redact_secrets(item, secrets) for item in data]

    elif isinstance(data, str):
        val = data
        for sec in secrets:
            if sec in val:
                val = val.replace(sec, "[REDACTED]")
        # Check bearer header patterns
        val = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{8,}", "Bearer [REDACTED]", val)
        return val

    return data


class AgentEventLogger:
    """Thread-safe append-only JSON Lines event logger for the trading agent."""

    def __init__(self, log_path: Optional[Union[str, Path]] = None, default_run_id: Optional[str] = None):
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG_PATH
        self.current_run_id = default_run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self._lock = threading.Lock()
        self._ensure_log_dir()

    def _ensure_log_dir(self):
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory for agent log: {e}")

    def log_event(
        self,
        event_type: str,
        symbol: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an append-only structured event to logs/agent_log.jsonl with secret redaction."""
        now_utc = datetime.now(timezone.utc).isoformat()
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        active_run_id = run_id or self.current_run_id
        safe_payload = redact_secrets(payload or {})

        event_record = {
            "event_id": event_id,
            "run_id": active_run_id,
            "timestamp_utc": now_utc,
            "event_type": event_type,
            "symbol": symbol.upper() if symbol else None,
            "payload": safe_payload,
        }

        line = json.dumps(event_record, default=str) + "\n"

        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as e:
                logger.error(f"Failed writing event to {self.log_path}: {e}")

        return event_record


# Global default event logger instance
agent_event_logger = AgentEventLogger()


def log_event(
    event_type: str,
    symbol: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper delegating to default AgentEventLogger instance."""
    return agent_event_logger.log_event(
        event_type=event_type,
        symbol=symbol,
        payload=payload,
        run_id=run_id,
    )
