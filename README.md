# VetoAlpha: Autonomous Multi-Strategy Options Trading Agent 🦅📉📈

VetoAlpha is an autonomous AI options trading engine designed to discover, evaluate, risk-gate, and execute 8 defined-risk options strategies (Call Debit Spread, Put Debit Spread, Iron Butterfly, Iron Condor, Bull Put Spread, Bear Call Spread, Long Call, Long Put) on Alpaca Paper Trading accounts using **LLM Catalyst Intelligence** and **Alpaca Model Context Protocol (MCP)** integration.

---

## 📋 Hackathon Compliance Status Matrix

| Requirement | Status | Evidence |
|---|---|---|
| **Autonomous AI Agent** | **PASS** | Autonomous orchestrator (`trading_agent/main.py` & `scripts/simulate_competition.py`) with 10-step scan cycle |
| **Alpaca Infrastructure** | **PASS** | Alpaca Trading API, MLEG limit orders, account & market data integration |
| **Alpaca MCP Integration** | **PASS** | `AlpacaMCPClient` ([`trading_agent/integrations/alpaca_mcp.py`](file:///d:/Algo%20Trading/trading_agent/integrations/alpaca_mcp.py)) & `MCPGuard` ([`trading_agent/integrations/mcp_guard.py`](file:///d:/Algo%20Trading/trading_agent/integrations/mcp_guard.py)) |
| **Options Trading** | **PASS** | 5 defined-risk option strategies (Iron Condor, Bull Put, Bear Call, Long Call, Long Put) |
| **Paper Trading Environment** | **PASS** | Hardcoded paper-only safety locks (`ALPACA_PAPER=True`), live-money endpoints physically blocked |
| **Fresh $100k Account** | **VERIFIED / NOT_VERIFIED** | Audited via `scripts/final_account_audit.py`; explicit `HACKATHON_ACCOUNT_MISMATCH` protection |
| **Risk Management** | **PASS** | Strategy-agnostic 15-rule risk gate, global circuit breaker, quote freshness & idempotency DB |
| **Testing** | **PASS** | 332+ passing unit, adversarial, and pipeline pytest tests |
| **Auditability** | **PASS** | Machine-readable decision traces (`logs/decision_audit.jsonl`) & telemetry (`logs/competition_run.jsonl`) |

---

## 🏛️ Architecture & Decision Pipeline

The agent enforces a strict separation between **AI Catalyst Intelligence** and **Deterministic Execution Authorization**. The LLM provides sentiment intelligence but has **zero authority** to directly submit orders or bypass risk limits.

```text
Market Data Pull
       │
       ▼
Deterministic Technical Regime Classifier (ADX, Bollinger Percentile, SMA, Realized Vol, IV Rank)
       │
       ▼
Featherless AI Advisory (Catalyst Risk, Directional Bias, Advisory Preference — Advisory Only)
       │
       ▼
Multi-Strategy Candidate Building (Iron Condor, Bull Put, Bear Call, Long Call, Long Put)
       │
       ▼
Deterministic Scoring Matrix (7-Dimensional Normalized Scoring 0-100)
       │
       ▼
Shared 15-Rule Risk Gate (Exposure limits, Greeks check, Max Risk limits)
       │
       ▼
Portfolio Idempotency & Conflict Check (SQLite Idempotency DB, 1-order-per-symbol-per-cycle)
       │
       ▼
Alpaca MCP Guard & MLEG Order Submission (Paper Trading API Only)
       │
       ▼
Position Lifecycle & Exit Management (Take-Profit, Stop-Loss, DTE Time-Exit, Delta Breach)
```

---

## 🔌 Alpaca MCP Integration

The project integrates Alpaca's Model Context Protocol (MCP) server for auditable tool discovery, read-only market data acquisition, and guarded write execution.

### Tool Permission Model

All MCP tools are partitioned into two strict security tiers:

| Tier | Tools | Description | Security Level |
|---|---|---|---|
| **READ_ONLY** | `alpaca_get_account`, `alpaca_get_positions`, `alpaca_get_clock`, `alpaca_get_quote`, `alpaca_get_bars`, `alpaca_get_option_chain`, `alpaca_get_orders`, `alpaca_get_news` | Read-only market and portfolio context acquisition | Safe — Direct execution permitted |
| **WRITE** | `alpaca_submit_mleg_order`, `alpaca_cancel_order`, `alpaca_close_position` | Order submission, cancellation, and position closing | Restricted — Requires `MCPGuard` authorization |

---

## 🛠️ Verification & Audit Scripts

Run the verification suite:

```bash
# 1. Hackathon Compliance Matrix Audit
py -3 scripts/hackathon_compliance_audit.py

# 2. Final Account Safety Audit
py -3 scripts/final_account_audit.py

# 3. Featherless AI Usage & Quota Summary
py -3 scripts/ai_usage_summary.py

# 4. Multi-Strategy Validation Harness
py -3 scripts/validate_strategy.py --symbol SPY

# 5. Read-Only Decision Audit & Explainability Harness
py -3 scripts/audit_decisions.py --symbols SPY QQQ IWM

# 6. Autonomous Paper Competition Simulation Harness
py -3 scripts/simulate_competition.py --symbols SPY QQQ IWM --cycles 5 --dry-run

# 7. Competition Readiness Summary
py -3 scripts/summarize_competition.py

# 8. Dashboard Snapshot Generator
py -3 scripts/competition_snapshot.py
```

---

## 🧪 Automated Testing

Run the full automated test suite (332+ tests passing):

```bash
py -3 -m pytest tests/
```

---

## ⚠️ Security Invariant
This agent is hardcoded to operate **exclusively on Alpaca Paper Trading accounts**. Real-money live trading is structurally blocked at the configuration, client, and guard layers.

### Deterministic MCP Guard (`MCPGuard`)

Every write operation requested through Alpaca MCP must pass through `trading_agent/integrations/mcp_guard.py`:
1. **Paper Verification**: Asserts `ALPACA_PAPER=True` and rejects any live account credentials.
2. **Direct AI Bypass Prevention**: Unconditionally blocks prompts/payloads containing `bypass_risk: true` or market orders.
3. **MLEG Geometry Validation**: Confirms $\text{Long Put} < \text{Short Put} < \text{Short Call} < \text{Long Call}$ with exact symmetric wing width.
4. **15-Rule Risk Gate**: Enforces max portfolio loss, max concurrent positions, daily loss circuit breakers, and delta limits.
5. **Idempotency Protection**: SQLite-backed trade fingerprinting prevents duplicate submissions.
6. **MCP Dry-Run Default**: `MCP_DRY_RUN=true` intercepts write requests and logs execution payloads without submitting live broker orders.

---

## 🚀 Quickstart & Hackathon Demonstration Commands

### 1. Judge-Facing Comprehensive Demo (Zero Live Orders)
Runs a single, end-to-end demonstration walking through MCP connectivity, live market data, strategy regime, Featherless AI risk scoring, risk gate authorization, and test verification:
```bash
python scripts/demo_hackathon.py
```

### 2. Alpaca MCP Capability Discovery
Inspects MCP server connectivity, lists all discovered tools by category, and confirms paper account status:
```bash
python scripts/check_mcp.py
```

### 3. MCP Guard & Execution Validation Harness
Demonstrates end-to-end MCP read/write authorization and dry-run interception:
```bash
python scripts/validate_mcp_execution.py
```

### 4. Interactive Streamlit Dashboard
Launch the read-only operational dashboard to inspect live account metrics, active positions, regime indicators, and audit logs:
```bash
streamlit run dashboard/app.py
```

---

## 🧪 Automated Testing

The repository contains **178 comprehensive automated tests** covering adversarial edge cases, MCP safety, Featherless intelligence, and options lifecycle:

```bash
pytest tests/ -v
```

```text
======================= 178 passed, 1 warning in 26.85s =======================
```

---

## 📋 Hackathon Compliance

For detailed compliance mapping against the 3 mandatory hackathon requirements (Autonomous Agent, Alpaca MCP, Options Trading), see [HACKATHON_COMPLIANCE.md](docs/HACKATHON_COMPLIANCE.md).

---

## ⚠️ Security Invariant
This agent is hardcoded to operate **exclusively on Alpaca Paper Trading accounts**. Real-money live trading is structurally blocked at the configuration, client, and guard layers.
