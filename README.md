# VetoAlpha: Institutional-Grade Autonomous Options Trading Engine 🦅📉📈

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests: 496 Passed](https://img.shields.io/badge/tests-496%20passed%20(100%25)-brightgreen.svg)]()
[![Broker: Alpaca Paper](https://img.shields.io/badge/broker-Alpaca%20Paper%20API-orange.svg)](https://alpaca.markets)
[![Risk: Fail--Closed](https://img.shields.io/badge/risk-15--Rule%20Deterministic%20Gate-red.svg)]()
[![Architecture: MCP](https://img.shields.io/badge/protocol-Model%20Context%20Protocol-purple.svg)]()

**VetoAlpha** is a production-grade, fail-closed autonomous algorithmic trading engine engineered for multi-asset equity, ETF, and index options. Built with a **risk-first philosophy**, VetoAlpha decouples qualitative market intelligence (LLMs) from trade execution, strictly subjecting every order to a deterministic 15-rule risk engine, ACID idempotency database, and multi-leg geometry verifier before submission.

---

## 🏛️ Core Architectural Thesis

> **"Never grant an LLM direct execution authority."**

In VetoAlpha:
1. **The LLM is Strictly Advisory**: Foundation models (DeepSeek-V4, Llama-3.1, GPT-4o, or Gemini via OpenAI-compatible endpoints) ingest market news, SEC filings, and earnings context to classify headline catalyst risk (`LOW`, `MEDIUM`, `HIGH`) and directional bias. The model has **zero authority** to submit orders, size positions, or bypass risk limits.
2. **Deterministic Risk Has Absolute Veto Power**: Every candidate trade must pass through an immutable 15-rule deterministic **RiskGate** that enforces hard mathematical loss caps, liquidity thresholds, wing symmetry, portfolio exposure, and daily circuit breakers.
3. **Fail-Closed Execution**: Any network timeout, data unavailability, low confidence (< 50%), or quote anomaly immediately fails closed to `NO_TRADE`.

---

## 📊 System Architecture & Pipeline

```text
Market Data Ingestion (Alpaca Stock/Option REST & WebSocket)
       │
       ▼
Deterministic Technical Regime Classifier (ADX, Bollinger %tile, SMA-20/50, Realized Vol, IV Rank)
       │
       ▼
AI Catalyst Intelligence Advisory (DeepSeek-V4/OpenAI/Gemini via OpenAI-compatible API)
       │
       ▼
Multi-Strategy Candidate Engine (8 Defined-Risk Strategy Builders)
       │
       ▼
Deterministic 7-Dimensional Scoring Matrix (Ranked 0 - 100)
       │
       ▼
Deterministic 15-Rule RiskGate (Exposure caps, Greeks, Max Loss, Liquidity, 0DTE cutoffs)
       │
       ▼
ACID Idempotency Engine (SQLite Fingerprint DB — 1-Order-Per-Cycle Invariant)
       │
       ▼
Alpaca MLEG Paper Execution (Multi-Leg Limit Orders with OrderStatus Normalization)
       │
       ▼
Active Position Lifecycle Manager (Take-Profit +50%, Stop-Loss -1.5x, DTE <= 5 Time Exits)
```

---

## 🎯 Supported Strategies & Instruments

VetoAlpha evaluates **8 defined-risk options strategies** across a 25-symbol liquid universe:

### 1. Strategy Spectrum
| Strategy Type | Strategy Name | Optimal Market Regime | Implied Volatility (IV) State |
| :--- | :--- | :--- | :--- |
| **Credit Spreads** | Iron Condor | Range-Bound / Neutral | Normal to High IV (> 20%) |
| | Bull Put Spread | Bullish Trend | Normal to High IV (> 20%) |
| | Bear Call Spread | Bearish Trend | Normal to High IV (> 20%) |
| | Iron Butterfly | Pinning / Low Expected Move | Elevated ATM IV |
| **Debit Spreads** | Bull Call Debit Spread | Bullish Momentum | Low to Normal IV (< 40%) |
| | Bear Put Debit Spread | Bearish Breakdown | Low to Normal IV (< 40%) |
| **Long Directional** | Long Call | High Conviction Breakout | Low IV (< 20%) |
| | Long Put | High Conviction Breakdown | Low IV (< 20%) |

### 2. Multi-Asset Instrument Awareness
- **Equity ETFs**: `SPY`, `QQQ`, `IWM`, `DIA`, `VOO`, `IVV` (American-style, Physical settlement).
- **Sector ETFs**: `XLK`, `XLF`, `XLE`, `XLV`, `XLI`, `XLP`, `XLY`, `XLC`, `XLB`, `XLU`.
- **Liquid Mega-Caps**: `AAPL`, `MSFT`, `NVDA`, `AMZN`, `META`, `GOOGL`, `TSLA`, `AMD`, `NFLX`.
- **Cash-Settled Index Options**: `SPX`, `SPXW`, `XSP`, `DJX`, `VIX` (European-style, Cash settlement, **Zero Early Assignment Risk**).
- **Isolated 24/7 Crypto Engine**: Dedicated subsystem evaluating continuous 24/7 market regime discipline across `BTC/USD`, `ETH/USD`, and `SOL/USD`.

---

## 🛡️ The 15 Deterministic RiskGate Rules

Every single options trade proposal must unanimously pass all 15 rules. Failure of even a single rule results in an immediate veto:

| Rule # | Validation Check | Invariant Enforcement |
| :---: | :--- | :--- |
| **1** | Max Loss Per Trade ($) | Strictly capped at configured dollar limit (default: $2,500) |
| **2** | Max Loss Per Trade (%) | Cannot exceed 2.0% of total account equity |
| **3** | Max Concurrent Positions | Capped at 5 simultaneous active multi-leg positions |
| **4** | Total Portfolio Exposure | Cumulative defined risk cannot exceed 15.0% of account equity |
| **5** | Daily Loss Circuit Breaker | Emergency halt trips if daily P&L drops below -$3,000 |
| **6** | Position Deduplication | Prohibits duplicate positions on identical symbol + expiration |
| **7** | Idempotency Fingerprint | SQLite-backed MD5 hash check prevents duplicate order submission |
| **8** | Buying Power Margin | Verifies sufficient cash/option buying power before order routing |
| **9** | Market Hours Verification | Rejects new entries outside regular U.S. market hours (9:30 - 16:00 EST) |
| **10** | Account Health State | Confirms broker account status is `ACTIVE` (not restricted or suspended) |
| **11** | Quote Freshness Guard | Rejects market data with quote age > 900 seconds |
| **12** | Valid Contract Identifiers | Asserts all legs contain valid OCC contract identifiers |
| **13** | Positive Defined Risk | Asserts `max_loss > 0` (rejects naked or unbounded risk) |
| **14** | Leg Structure & Symmetry | Asserts wing widths match exactly and strikes are ordered monotonically |
| **15** | Option Leg Liquidity | Enforces minimum Open Interest ($\ge 50$) and maximum bid-ask spread ($\le 20\%$) |

---

## 🔌 Model Context Protocol (MCP) Integration

VetoAlpha integrates with the **Model Context Protocol (MCP)** via a custom, security-hardened `MCPGuard`:
- **Read-Only Tools** (`alpaca_get_account`, `alpaca_get_positions`, `alpaca_get_quote`, `alpaca_get_bars`, `alpaca_get_option_chain`, `alpaca_get_news`): Direct execution permitted for market context.
- **Guarded Write Tools** (`alpaca_submit_mleg_order`, `alpaca_cancel_order`, `alpaca_close_position`): Strictly gated behind `MCPGuard` verification (enforcing paper environment validation, wing symmetry checks, and RiskGate rules).

---

## 🚀 Quickstart & Usage

### 1. Prerequisites & Environment Setup
Clone the repository and install dependencies:
```bash
git clone https://github.com/Dhinakarg/VetoAlpha.git
cd VetoAlpha
py -3 -m pip install -r requirements.txt
```

Configure your paper trading credentials in `.env` (refer to `.env.example`):
```bash
# Alpaca Paper Trading Credentials
ALPACA_API_KEY=PK********************
ALPACA_SECRET_KEY=****************************************
ALPACA_PAPER=true

# LLM Provider Configuration (Featherless, OpenAI, Gemini, Groq, Ollama)
LLM_API_KEY=your_llm_api_key_here
LLM_BASE_URL=https://api.featherless.ai/v1
LLM_MODEL=deepseek-ai/DeepSeek-V4-Flash-0731
```

### 2. End-to-End System Walkthrough (Zero Live Orders)
Runs a complete, real-time diagnostic walkthrough testing MCP connectivity, live quotes, technical regime detection, LLM catalyst evaluation, RiskGate authorization, and MLEG payload construction:
```bash
py -3 scripts/demo_system.py
```

### 3. Run Autonomous Paper Trading Simulation
Launch the autonomous scanning and execution loop across the liquid universe:
```bash
# Run 10 autonomous cycles in live paper submission mode:
py -3 scripts/simulate_competition.py --universe liquid --cycles 10 --paper

# Run in dry-run mode (constructs and scores candidates without broker submission):
py -3 scripts/simulate_competition.py --universe liquid --cycles 5 --dry-run
```

### 4. Interactive Operations Dashboard
Launch the web-based operational monitoring dashboard:
```bash
py -3 scripts/launch_dashboard.py
# Or directly via streamlit:
streamlit run dashboard/app.py
```

### 5. Multi-Asset Universe Scanner
Scan and rank option opportunities across ETFs, Index Options, and Equities:
```bash
py -3 scripts/scan_options_universe.py
```

---

## 🧪 Comprehensive Verification Suite

VetoAlpha features **496 automated pytest tests** covering adversarial stress cases, mathematical bounds, MCP safety, lifecycle management, and portfolio risk:

```bash
py -3 -m pytest tests/ -v
```

```text
======================= 496 passed, 1 warning in 98.42s =======================
```

### Verification Coverage Areas:
- **Unit & Mathematical Integrity**: Expected move calculations, Black-Scholes ATM IV extraction, and delta-breach boundary checks.
- **Strategy Candidate Construction**: Exact wing symmetry, strike availability, credit/debit validation, and contract multiplier math.
- **RiskGate Invariants**: 15 isolated rule-by-rule breach tests asserting fail-closed rejection.
- **Idempotency & Concurrency**: Process restart persistence, duplicate fingerprint blocking, and file locks.
- **Execution & Reconciler Parity**: MLEG order construction, Alpaca order status normalization (`OrderStatus.PENDING_NEW` -> `new`), and position reconciliation.

---

## 🔒 Security Invariants

1. **Physical Live Trading Lock**: The executor executes `validate_paper_execution_environment()`, raising a fatal `PaperExecutionSafetyError` if live account credentials or endpoints are supplied.
2. **Deterministic Credential Masking**: All logging, telemetry, and terminal outputs pass through `redact_secrets()` to strip API keys, tokens, and bearer credentials.
3. **Fail-Closed Default**: Any network failure, quote staleness, missing delta, or unparsable LLM response triggers an automatic `NO_TRADE` decision.

---

## 📁 Repository Structure

```text
VetoAlpha/
├── dashboard/                 # Streamlit web dashboard
│   └── app.py
├── docs/                      # Technical specifications & audit reports
│   ├── FINAL_PROJECT_SUMMARY.md
│   ├── OPTIONS_UNIVERSE_AUDIT.md
│   └── CRYPTO_VALIDATION_ENGINE.md
├── logs/                      # Audit trails & telemetry (JSONL & SQLite)
├── scripts/                   # CLI execution & audit harnesses
│   ├── demo_system.py         # 24/7 Architecture walkthrough
│   ├── simulate_competition.py# Autonomous paper runner loop
│   ├── scan_options_universe.py# Multi-asset option scanner
│   ├── launch_dashboard.py    # Streamlit dashboard launcher
│   └── final_account_audit.py # Pre-flight broker audit
├── tests/                     # 496 automated regression & adversarial tests
├── trading_agent/             # Core trading engine source code
│   ├── agent/                 # Master orchestrator
│   ├── ai/                    # LLM Catalyst Intelligence Agent
│   ├── data/                  # Alpaca market data & option chain fetcher
│   ├── execution/             # MLEG order executor & position reconciler
│   ├── integrations/          # Alpaca MCP Client & MCPGuard
│   ├── portfolio/             # Position manager & P&L tracking
│   ├── risk/                  # 15-Rule RiskGate, 0DTE Policy, Safety Manager
│   └── strategy/              # 8 defined-risk options strategies & regime classifier
├── .env.example               # Provider-agnostic environment template
├── requirements.txt           # Python package dependencies
└── README.md                  # System documentation
```

---

## ⚠️ Disclaimer
This software is developed strictly for educational, research, and paper-trading evaluation purposes. It is not financial, investment, or trading advice. Algorithmic options trading involves substantial risk of loss. Past simulation results do not guarantee future performance. The authors assume no liability for financial losses incurred through the use of this software.

---

## 📄 License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
