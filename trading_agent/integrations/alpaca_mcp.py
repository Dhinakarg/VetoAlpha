"""
Alpaca Model Context Protocol (MCP) Integration Layer.
Exposes standard MCP tool definitions, discovers server capabilities,
and classifies tools into READ_ONLY and WRITE operations.
Strictly enforced: Paper Trading environment, zero hardcoded secrets,
and all WRITE operations must pass through deterministic MCPGuard.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from trading_agent.config import settings
from trading_agent.infrastructure.logger import AgentEventLogger
from trading_agent.risk.safety import validate_paper_execution_environment, PaperExecutionSafetyError

logger = logging.getLogger(__name__)

# Classification of Alpaca MCP Tools
READ_ONLY_TOOLS = {
    "alpaca_get_account": {
        "description": "Fetch paper trading account details (equity, buying power, status)",
        "category": "account",
        "permission": "READ_ONLY",
    },
    "alpaca_get_positions": {
        "description": "Fetch current open positions and active contracts",
        "category": "portfolio",
        "permission": "READ_ONLY",
    },
    "alpaca_get_clock": {
        "description": "Fetch current market clock state (open/closed, next open/close)",
        "category": "market_data",
        "permission": "READ_ONLY",
    },
    "alpaca_get_quote": {
        "description": "Fetch latest underlying stock bid/ask/last quote",
        "category": "market_data",
        "permission": "READ_ONLY",
    },
    "alpaca_get_bars": {
        "description": "Fetch historical price bars for technical indicators (ADX, Bollinger)",
        "category": "market_data",
        "permission": "READ_ONLY",
    },
    "alpaca_get_option_chain": {
        "description": "Fetch live option chain snapshot filtered by target DTE (20-45 DTE)",
        "category": "options_data",
        "permission": "READ_ONLY",
    },
    "alpaca_get_orders": {
        "description": "Query order status and history by order ID or status filter",
        "category": "orders",
        "permission": "READ_ONLY",
    },
    "alpaca_get_news": {
        "description": "Fetch latest market and company news for catalyst intelligence",
        "category": "news",
        "permission": "READ_ONLY",
    },
}

WRITE_TOOLS = {
    "alpaca_submit_mleg_order": {
        "description": "Submit a defined-risk 4-leg Iron Condor multi-leg limit order",
        "category": "execution",
        "permission": "WRITE",
        "requires_guard": True,
    },
    "alpaca_cancel_order": {
        "description": "Cancel an open order by order ID",
        "category": "execution",
        "permission": "WRITE",
        "requires_guard": True,
    },
    "alpaca_close_position": {
        "description": "Submit a 4-leg closing multi-leg limit order",
        "category": "execution",
        "permission": "WRITE",
        "requires_guard": True,
    },
}

ALL_MCP_TOOLS = {**READ_ONLY_TOOLS, **WRITE_TOOLS}


class AlpacaMCPClient:
    """Standard Alpaca Model Context Protocol (MCP) Client Interface.
    
    Provides auditable tool discovery, read-only data access, and
    deterministic guard routing for write operations.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.agent_logger = AgentEventLogger()
        self._connected = False
        self._initialize_connection()

    def _initialize_connection(self):
        """Validate environment and establish paper MCP connection."""
        try:
            validate_paper_execution_environment()
            self._connected = True
            self.agent_logger.log_event("mcp_connected", payload={
                "environment": "PAPER",
                "mcp_dry_run": self.dry_run,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })
        except PaperExecutionSafetyError as e:
            self._connected = False
            self.agent_logger.log_event("mcp_safety_blocked", payload={
                "reason": str(e),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })
            raise

    def discover_tools(self) -> Dict[str, Any]:
        """Discover and list all available Alpaca MCP tools categorized by permission."""
        read_tools = [name for name, meta in ALL_MCP_TOOLS.items() if meta["permission"] == "READ_ONLY"]
        write_tools = [name for name, meta in ALL_MCP_TOOLS.items() if meta["permission"] == "WRITE"]

        payload = {
            "mcp_server": "alpaca-mcp-paper",
            "environment": "PAPER",
            "read_tools_count": len(read_tools),
            "write_tools_count": len(write_tools),
            "read_tools": read_tools,
            "write_tools": write_tools,
            "tool_metadata": ALL_MCP_TOOLS,
        }

        self.agent_logger.log_event("mcp_tool_discovered", payload={
            "read_tools": read_tools,
            "write_tools": write_tools,
        })
        return payload

    def execute_read_tool(self, tool_name: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a verified READ_ONLY Alpaca MCP tool."""
        if tool_name not in READ_ONLY_TOOLS:
            err = f"Security Violation: '{tool_name}' is not a permitted READ_ONLY tool."
            self.agent_logger.log_event("mcp_safety_blocked", payload={"tool": tool_name, "error": err})
            raise PermissionError(err)

        params = parameters or {}
        symbol = params.get("symbol", "SPY")

        self.agent_logger.log_event("mcp_read_called", symbol=symbol, payload={
            "tool": tool_name,
            "params": params,
        })

        # Route to data layer
        from trading_agent.data.alpaca_data import AlpacaDataFetcher
        fetcher = AlpacaDataFetcher()

        if tool_name == "alpaca_get_account":
            snap = fetcher.get_account_snapshot()
            return {"account_id": snap.account_id, "equity": snap.equity, "buying_power": snap.buying_power, "status": snap.status}
        elif tool_name == "alpaca_get_clock":
            return fetcher.get_market_clock()
        elif tool_name == "alpaca_get_quote":
            return fetcher.get_underlying_quote(symbol)
        elif tool_name == "alpaca_get_bars":
            return {"symbol": symbol, "bars": fetcher.get_recent_bars(symbol, limit=params.get("limit", 100))}
        elif tool_name == "alpaca_get_option_chain":
            chain = fetcher.get_option_chain(symbol)
            return {"symbol": symbol, "contracts_count": len(chain.contracts) if chain else 0}
        elif tool_name == "alpaca_get_positions":
            return {"positions": fetcher.get_open_positions()}
        elif tool_name == "alpaca_get_news":
            return {"news": fetcher.get_recent_news(symbol, limit=params.get("limit", 10))}
        else:
            return {"status": "ok", "tool": tool_name}

    def execute_guarded_write_tool(
        self,
        tool_name: str,
        proposal: Dict[str, Any],
        account: Any,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Execute an Alpaca MCP WRITE tool strictly through the deterministic MCPGuard."""
        if tool_name not in WRITE_TOOLS:
            err = f"Security Violation: '{tool_name}' is not recognized as a WRITE tool."
            self.agent_logger.log_event("mcp_safety_blocked", payload={"tool": tool_name, "error": err})
            raise PermissionError(err)

        is_dry = dry_run if dry_run is not None else self.dry_run

        from trading_agent.integrations.mcp_guard import MCPGuard
        guard = MCPGuard(dry_run=is_dry)
        return guard.authorize_and_execute_mcp_write(tool_name=tool_name, proposal=proposal, account=account)
