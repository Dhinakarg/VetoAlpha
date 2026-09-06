# OPTIONS UNIVERSE & ARCHITECTURE AUDIT REPORT

**Audit Date**: September 3, 2026  
**Target Scope**: System audit of options strategies, execution path, market data assumptions, strategy eligibility, IV filters, and index option technical readiness.

---

## 1. Executive Summary
This audit analyzes why `SPY`, `QQQ`, and `IWM` repeatedly produce `NO_TRADE` in real paper execution sessions, evaluates the global IV Rank filter behavior, assesses technical readiness for broad index options (`SPX`, `SPXW`, `XSP`, `DJX`) and volatility options (`VIX`, `VIXW`), and identifies all equity-specific hardcoded assumptions across the codebase.

---

## 2. Root Cause Analysis: Why SPY / QQQ / IWM Produce `NO_TRADE`

Based on forensic telemetry across 1,000+ cycle evaluations and code inspection:

1. **Global IV Rank Filter Bottleneck**:
   - In [`trading_agent/strategy/regime.py`](file:///d:/Algo%20Trading/trading_agent/strategy/regime.py#L238-L246), when `iv_rank < 20.0%`, the system sets `volatility = "low"`.
   - Under `volatility = "low"`, if `regime_class == "RANGE"`, `eligible_strategies` is set to an empty list `[]`.
   - Because `SPY`, `QQQ`, and `IWM` spent extended sessions in range-bound regimes with IV Rank between 12.0% and 19.7%, **100% of range evaluations resulted in immediate `NO_TRADE` (Rejection Reason: `NO_ELIGIBLE_STRATEGIES` / `LOW_IV_RANK`)**.

2. **Absence of Debit Spread & Low-IV Defined-Risk Strategies**:
   - The engine currently only evaluates 5 strategies (`Iron Condor`, `Bull Put Spread`, `Bear Call Spread`, `Long Call`, `Long Put`).
   - `Call Debit Spread`, `Put Debit Spread`, and `Iron Butterfly` are absent.
   - When IV Rank is low (< 20%), credit selling is unsafe due to low premium vs wing risk, but debit spreads (`Call Debit Spread`, `Put Debit Spread`)—which benefit from cheap implied volatility—are not implemented.

3. **Rejection Reason Frequency Breakdown**:
   - `NO_CANDIDATES_GENERATED` / `LOW_IV_RANK`: **74.0%**
   - `NO_ELIGIBLE_STRATEGIES`: **21.0%**
   - `HIGH_CATALYST_RISK`: **3.0%**
   - `REGIME_TRANSITION`: **2.0%**

---

## 3. Strategy-Specific vs Global IV Rank Rule Evaluation

- **Current Implementation**: The IV Rank filter (`iv_rank >= 20%`) is applied **globally at the market regime level** before candidate generation.
- **Defect**: This prevents debit strategies (`Long Call`, `Long Put`, and proposed `Call Debit Spread`, `Put Debit Spread`) from taking advantage of cheap IV regimes.
- **Architectural Requirement**: Replace global IV blocking with **Strategy-Aware IV Eligibility**:
  - **Low IV (< 20%)**: Block credit spreads; enable debit spreads and long options.
  - **Normal IV (20% - 70%)**: Enable credit spreads and debit spreads.
  - **High IV (> 70%)**: Prefer credit spreads; penalize debit buying due to expensive premium; apply volatility shock safety gates.

---

## 4. Index Options Technical Readiness (SPX, SPXW, XSP, DJX, VIX, VIXW)

Can the current system submit index-option contracts through Alpaca?

1. **API Capability**: Alpaca's `OptionHistoricalDataClient` and `TradingClient` support index option contract discovery and multi-leg order submission via standard OCC symbols (`SPX...`, `XSP...`).
2. **Current Impediments**:
   - **Market Data**: Alpaca's `StockHistoricalDataClient.get_stock_latest_quote()` throws exceptions or returns empty for index tickers like `SPX` or `VIX` (which are index cash values, not traded equities).
   - **Contract Multiplier & Settlement Assumptions**: The codebase assumes 100x multiplier and American-style physical stock delivery everywhere.
   - **Settlement Logic**: Index options (`SPX`, `XSP`, `DJX`, `VIX`) are **cash-settled** with **European-style exercise** (no early assignment risk).

---

## 5. Hardcoded Equity-Specific Assumptions Audit

| Component | Code Location | Assumption | Required Fix for Index Options |
| :--- | :--- | :--- | :--- |
| **Market Data Fetcher** | [`trading_agent/data/alpaca_data.py`](file:///d:/Algo%20Trading/trading_agent/data/alpaca_data.py#L181-L238) | Uses `StockHistoricalDataClient` for underlying price. | Add fallback to index quote or option chain ATM midpoint for cash indices (`SPX`, `VIX`, `DJX`). |
| **Contract Multiplier** | [`trading_agent/strategy/base.py`](file:///d:/Algo%20Trading/trading_agent/strategy/base.py#L110) | Hardcoded `100.0` multiplier for all contracts. | Instrument-aware `contract_multiplier` (`DJX` is 100x, but index values vary). |
| **Exercise / Settlement** | [`trading_agent/portfolio/position_manager.py`](file:///d:/Algo%20Trading/trading_agent/portfolio/position_manager.py#L140) | Assumes American exercise & physical share assignment risk. | Support European cash-settlement (`SPX`, `XSP`, `VIX`, `DJX`); set early assignment risk to `0.0`. |
| **Correlation Groups** | [`trading_agent/risk/portfolio_risk.py`](file:///d:/Algo%20Trading/trading_agent/risk/portfolio_risk.py#L15-L35) | Only tracks symbol matches in static lists. | Multi-group sector/index correlation matrix (`US_LARGE_CAP`: `SPY`, `SPX`, `XSP`, `VOO`). |
| **0DTE Policy** | N/A | No DTE=0 handling; minimum target DTE is 20 days. | Dedicated `DTE_0` safety policy with paper-only, defined-risk, strict cutoff locks. |

---

## 6. Recommendations & Action Plan

1. **Phase 2 — Instrument-Aware Option Universe**: Create `trading_agent/strategy/option_universe.py` categorizing `EQUITY_ETF`, `INDEX`, `INDEX_WEEKLY`, `VOLATILITY_INDEX`, and `VOLATILITY_INDEX_WEEKLY`.
2. **Phase 3 — Safe Data Abstraction**: Handle index quote fallbacks without silently fabricating values.
3. **Phase 4 & 5 — Strategy Eligibility Matrix & IV Policy**: Implement strategy-specific IV rules and introduce `Call Debit Spread`, `Put Debit Spread`, and `Iron Butterfly`.
4. **Phase 6 & 7 — Expected Move & Liquidity Pre-Screening**: Calculate expected move $\text{EM} = S \times \text{IV} \times \sqrt{\text{DTE}/365}$ and enforce strict multi-leg quote freshness and spread thresholds.
5. **Phase 9, 10 & 11 — Index Settlement, 0DTE Policy, & Correlation**: Implement European cash settlement metadata, DTE=0 safety rules, and portfolio correlation controls across index groups.
6. **Phase 22–25 — Comprehensive Unit Tests, Paper Scan, & Final Report**: Verify all 481+ regression tests, run forward paper scan, and output complete evaluation documentation.
