# Autonomous Crypto Validation Engine Report

## Executive Summary
This document provides technical documentation and validation results for the **Autonomous Crypto Validation Engine** (`CRYPTO_VALIDATION_MODE`). The crypto engine operates 24/7 as an isolated research, backtesting, and validation environment to answer the core architectural question:

> *"Does the autonomous decision-making, regime detection, risk management, execution discipline, and performance attribution architecture work when applied continuously to a 24/7 market?"*

---

## 1. Subsystem Architecture & System Isolation

```text
                               SHARED INFRASTRUCTURE
                                         │
                    ┌────────────────────┴────────────────────┐
                    │                                         │
             OPTIONS ENGINE                             CRYPTO ENGINE
         (PRIMARY OPTIONS ENGINE)                     (VALIDATION ONLY)
                    │                                         │
            Alpaca Options API                        24/7 Crypto Data
                    │                                         │
          5 Options Strategies                      5 Crypto Strategies
                    │                                         │
            Options Risk Gate                         Crypto Risk Gate
                    │                                         │
             Options Portfolio                        Crypto Portfolio
                    │                                         │
             Paper Trading DB                         Isolated Telemetry
                    │                                         │
             OPTIONS ENGINE                            VALIDATION ONLY
```

### Strict Isolation Guarantees
- **Execution Isolation**: Crypto order payloads cannot reach options execution methods or Alpaca MLEG option endpoints.
- **Telemetry Isolation**: Logs are routed to separate files (`logs/crypto_run.jsonl`, `logs/crypto_decision_audit.jsonl`, `logs/crypto_state.json`, `logs/crypto_pnl.jsonl`).
- **Risk Isolation**: Crypto risk limits and circuit breakers do not cross-contaminate options circuit breakers or options risk gates.

---

## 2. Crypto Strategy Definitions

The engine implements 5 defined-risk crypto strategies:
1. **`TREND_FOLLOWING_LONG`**: Activated in `STRONG_BULL` / `BULL` regimes when ADX >= 22 and RSI > 55.
2. **`TREND_FOLLOWING_SHORT`**: Activated in `STRONG_BEAR` / `BEAR` regimes when ADX >= 22 and RSI < 45.
3. **`MEAN_REVERSION_LONG`**: Activated in `RANGE` regimes when RSI < 45.
4. **`MEAN_REVERSION_SHORT`**: Activated in `RANGE` regimes when RSI > 55.
5. **`MOMENTUM_BREAKOUT`**: Activated in `STRONG_BULL` / `STRONG_BEAR` regimes with volume expansion ratio > 1.2.

Every candidate returns a standardized `CryptoStrategyCandidate` with deterministic stop loss and take profit targets. Candidates lacking downside protection are rejected.

---

## 3. Deterministic Risk & Position Sizing Methodology

Position sizing is volatility and risk-based:
$$\text{Risk Budget} = \text{Equity} \times \text{Risk Per Trade (default 0.25\%)}$$
$$\text{Position Size} = \frac{\text{Risk Budget}}{|\text{Entry Price} - \text{Stop Loss Price}|}$$

### 12-Point Crypto Risk Gate
1. Deterministic downside protection (Stop Loss & Take Profit mandatory)
2. Minimum Risk/Reward Ratio (default >= 1.5)
3. Positive account equity validation
4. Daily Loss Circuit Breaker (default 1.0% max loss)
5. Consecutive Loss Limit (default 3)
6. Max Concurrent Open Positions (default 3)
7. Duplicate symbol protection
8. Volatility bounds check
9. Max single-trade risk cap (1.0% equity cap)
10. Position value cap (max 25% of equity)
11. Quote freshness & data validity check
12. Fingerprint idempotency check (`CRYPTO_DUPLICATE_BLOCKED`)

---

## 4. Execution Methodology

The crypto execution engine operates in two non-live modes:
- **`DRY_RUN`**: Simulates candidate generation, risk checks, and payload construction without order fills.
- **`PAPER_SIMULATION`**: Simulates fills with realistic 0.02% slippage and 0.05% maker/taker transaction fee model.

**Zero Live Trading Pathway**: There is no live execution code path in the crypto subsystem.

---

## 5. Performance Attribution & Buy & Hold Benchmark

The analytics engine (`CryptoPnLEngine`) computes:
- Win Rate %, Profit Factor, Expectancy ($)
- Max Drawdown %, Annualized Sharpe Ratio
- **Buy & Hold Benchmark Return %**: Benchmark asset (e.g. BTC/USD) return over the evaluation window
- **Strategy Alpha %**: Strategy Return % - Buy & Hold Return %
- **Strategy & Regime Attribution**: Performance broken down by strategy family and market regime.

---

## 6. Historical Backtest & Walk-Forward Methodology

The historical backtester (`scripts/backtest_crypto.py`) evaluates strategy candidates across historical candle windows:
- **Zero Look-Ahead Bias**: Technical indicators evaluate strictly past candles available at decision time.
- **Out-of-Sample Split**: 70% Train / 30% Out-of-Sample evaluation split.
- **Classification**: Explicitly labeled `HISTORICAL SIMULATION`.

---

## 7. AI Advisory Governance

Featherless AI is integrated strictly as an **advisory layer**:
- Generates catalyst risk, directional bias, and advisory preferences.
- If Featherless AI disagrees with deterministic selection, the engine logs `AI_DETERMINISTIC_DISAGREEMENT` and enforces the deterministic decision.
- Featherless calls are automatically skipped when deterministic state implies `NO_TRADE` (saving API quota).

---

## 8. Test Verification & Results

- **Crypto Engine Test Suite**: 9 unit & scenario tests passed (`tests/test_crypto_engine.py`).
- **Isolation Test Suite**: 6 architectural isolation tests passed (`tests/test_crypto_isolation.py`).
- **Total Test Suite**: **356 passed out of 356 tests** across the entire workspace.

---

## 9. Interpretation Guidelines: Code Correctness vs. Strategy Profitability

> **IMPORTANT DISTINCTION**:
> Passing 100% of automated unit, integration, and isolation tests proves that the **software architecture, safety controls, risk gates, and execution engine function correctly**.
> It does **NOT** guarantee that any specific crypto trading strategy is profitable.
> The purpose of this engine is to objectively measure and report performance (including negative alpha or underperformance) without overfitting or parameter tuning.
