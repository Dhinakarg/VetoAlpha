"""
Infrastructure subpackage handling structured audit logging, storage, and secret redaction.
"""

from trading_agent.infrastructure.logger import (
    AgentEventLogger,
    log_event,
    SUPPORTED_EVENT_TYPES,
    redact_secrets,
    agent_event_logger,
)

__all__ = [
    "AgentEventLogger",
    "log_event",
    "SUPPORTED_EVENT_TYPES",
    "redact_secrets",
    "agent_event_logger",
]
