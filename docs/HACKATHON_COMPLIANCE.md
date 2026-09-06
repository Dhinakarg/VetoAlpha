# Hackathon Compliance Checklist & Architectural Mapping

This document provides a direct, verifiable mapping of the **Adaptive AI Iron Condor Agent** codebase to the three mandatory requirements of the hackathon.

---

## Mandatory Requirement 1: Autonomous Agent

| Autonomous Capability | Code Implementation | Verification / Evidence |
|---|---|---|
| **Scheduled Scanning** | [`trading_agent/main.py`](file:///d:/Algo%20Trading/trading_agent/main.py) | APScheduler cron/interval polling strictly aligned with Alpaca Market Clock (`is_open == True`). |
| **Market Data Acquisition** | [`trading_agent/data/alpaca_data.py`](file:///d:/Algo%20Trading/trading_agent/data/alpaca_data.py) | Dynamic retrieval of bars, quotes, and options chains with quote freshness validation. |
| **Deterministic Regime Identification** | [`trading_agent/strategy/signals.py`](file:///d:/Algo%20Trading/trading_agent/strategy/signals.py) | 14-day Wilder ADX (< 25.0), Bollinger Band Width percentile (< 50%), and 30-day canonical IV Rank calculation. |
| **AI Catalyst Risk Intelligence** | [`trading_agent/strategy/featherless_client.py`](file:///d:/Algo%20Trading/trading_agent/strategy/featherless_client.py) | Featherless open-weights LLM (`deepseek-ai/DeepSeek-V4-Flash-0731`) structured catalyst classification (LOW / MEDIUM / HIGH) with confidence gating ($\ge 0.50$). |
| **Deterministic Risk Gate** | [`trading_agent/risk/risk_gate.py`](file:///d:/Algo%20Trading/trading_agent/risk/risk_gate.py) | Pure 15-rule deterministic validator enforcing max loss ($2,500), position count (3), exposure ($10,000), and circuit breakers. |
| **Order Decision & Execution** | [`trading_agent/execution/alpaca_executor.py`](file:///d:/Algo%20Trading/trading_agent/execution/alpaca_executor.py) | Automated limit order construction with idempotent fingerprint deduplication. |
| **Active Position Monitoring** | [`trading_agent/portfolio/position_manager.py`](file:///d:/Algo%20Trading/trading_agent/portfolio/position_manager.py) | Real-time multi-leg tracking of debit to close, unrealized P&L, DTE decay, and short deltas. |
| **Automated Exit Execution** | [`trading_agent/portfolio/position_manager.py`](file:///d:/Algo%20Trading/trading_agent/portfolio/position_manager.py) | Automated Take Profit ($\ge 50\%$), Stop Loss ($\ge 2.0\times$), Time Exit ($\le 7\text{ DTE}$), and Delta Breach ($\ge 0.30$) rules. |

---

## Mandatory Requirement 2: Alpaca MCP / CLI Integration

| MCP / CLI Requirement | Code Implementation | Verification / Evidence |
|---|---|---|
| **MCP Configuration Files** | [`mcp.json`](file:///d:/Algo%20Trading/mcp.json), [`.cursor/mcp.json`](file:///d:/Algo%20Trading/.cursor/mcp.json) | Standard JSON configuration defining `alpaca-mcp-paper` server with environment variable expansion and `ALPACA_PAPER=true`. |
| **MCP Integration Layer** | [`trading_agent/integrations/alpaca_mcp.py`](file:///d:/Algo%20Trading/trading_agent/integrations/alpaca_mcp.py) | `AlpacaMCPClient` handling capability discovery, tool cataloging, and execution dispatching. |
| **Tool Permission Separation** | [`trading_agent/integrations/alpaca_mcp.py`](file:///d:/Algo%20Trading/trading_agent/integrations/alpaca_mcp.py) | Strict separation between 8 `READ_ONLY` tools (account, quotes, bars, chains, news, clock) and 3 `WRITE` tools (submit order, cancel order, close position). |
| **Deterministic Guard Wrapper** | [`trading_agent/integrations/mcp_guard.py`](file:///d:/Algo%20Trading/trading_agent/integrations/mcp_guard.py) | `MCPGuard` intercepting all write tool requests, ensuring AI models cannot bypass risk gates or submit arbitrary market orders. |
| **Capability Discovery Script** | [`scripts/check_mcp.py`](file:///d:/Algo%20Trading/scripts/check_mcp.py) | CLI tool inspecting paper MCP connection, discovering read/write tools, and confirming zero orders submitted. |
| **MCP Execution Validation** | [`scripts/validate_mcp_execution.py`](file:///d:/Algo%20Trading/scripts/validate_mcp_execution.py) | End-to-end dry-run validation script exercising MCP tool discovery, reads, proposal generation, and guarded write authorization. |
| **Audit Logging & Redaction** | [`trading_agent/infrastructure/logger.py`](file:///d:/Algo%20Trading/trading_agent/infrastructure/logger.py) | Append-only event logging (`logs/agent_log.jsonl`) for all MCP events (`mcp_connected`, `mcp_read_called`, `mcp_write_authorized`, `mcp_write_rejected`) with complete credential scrubbing. |

---

## Mandatory Requirement 3: Options Trading

| Options Capability | Code Implementation | Verification / Evidence |
|---|---|---|
| **Option Chain Filtering** | [`trading_agent/data/alpaca_data.py`](file:///d:/Algo%20Trading/trading_agent/data/alpaca_data.py) | Server-side and client-side optimization filtering contracts to the target 20–45 DTE trading horizon. |
| **4-Leg Iron Condor Construction** | [`trading_agent/strategy/condor_builder.py`](file:///d:/Algo%20Trading/trading_agent/strategy/condor_builder.py) | Algorithm selecting Long Put, Short Put ($\approx 0.15\text{--}0.20\Delta$), Short Call ($\approx 0.15\text{--}0.20\Delta$), and Long Call with exact symmetric $5.00 wing width. |
| **Defined-Risk Metrics** | [`trading_agent/strategy/condor_builder.py`](file:///d:/Algo%20Trading/trading_agent/strategy/condor_builder.py) | Mathematical validation of Net Credit ($C$), Max Profit ($C \times 100$), Max Risk (($W - C) \times 100$), and Lower/Upper Breakevens. |
| **Multi-Leg (MLEG) Order Payloads** | [`trading_agent/execution/alpaca_executor.py`](file:///d:/Algo%20Trading/trading_agent/execution/alpaca_executor.py) | Precise Alpaca MLEG limit order construction with correct position intents (`BUY_TO_OPEN`, `SELL_TO_OPEN`, `SELL_TO_CLOSE`, `BUY_TO_CLOSE`). |
| **Conservative Executable Pricing** | [`trading_agent/strategy/condor_builder.py`](file:///d:/Algo%20Trading/trading_agent/strategy/condor_builder.py) | Calculates net credit using executable bid/ask quotes: $\text{Short Bids} - \text{Long Asks}$. |
| **Position Lifecycle & Exit Management** | [`trading_agent/portfolio/position_manager.py`](file:///d:/Algo%20Trading/trading_agent/portfolio/position_manager.py) | Automated 4-leg position tracking and limit debit close order generation. |
| **P&L Realization** | [`trading_agent/portfolio/position_manager.py`](file:///d:/Algo%20Trading/trading_agent/portfolio/position_manager.py) | Gross and net P&L calculation after accounting for broker commissions and exchange fees. |

---

## Verification Test Summary

```text
======================= 178 passed, 1 warning in 26.85s =======================
```
- **Adversarial & Unit Tests**: 178 tests passing across full codebase.
- **MCP Safety Test Suite**: 15 tests in `tests/test_mcp_guard.py` verifying paper safety, direct AI bypass prevention, circuit breakers, idempotency, and secret scrubbing.
- **Zero Real-Money Risk**: `ALPACA_PAPER=true` strictly asserted at every layer. Live money execution is structurally impossible.
