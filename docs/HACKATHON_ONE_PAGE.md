# Autonomous Multi-Strategy Options Agent — Hackathon Submission

## Strategy
The agent operates an autonomous multi-strategy options engine supporting five defined-risk strategy families:
1. **Iron Condor**: Range-bound, elevated IV, neutral bias
2. **Bull Put Spread**: Bullish trend, elevated IV, bullish bias
3. **Bear Call Spread**: Bearish trend, elevated IV, bearish bias
4. **Long Call**: Strong bullish momentum / directional breakout
5. **Long Put**: Strong bearish momentum / directional breakdown

Market regime (trend, ADX, Bollinger width percentile, realized volatility, and IV rank) deterministically classifies market conditions and filters eligible strategy families before candidate construction.

## AI Logic
The agent incorporates **Featherless AI** (DeepSeek-V4 / Llama-3.1 via OpenAI-compatible endpoints) strictly as a **Contextual Catalyst & Strategy Intelligence Advisor**:
- **What AI Does**: Parses unstructured market news, upcoming event catalysts, and macro context to estimate catalyst risk (`low`, `medium`, `high`), directional bias, and advisory strategy preferences.
- **What AI Does NOT Control**: The LLM has ZERO authority to place trades, submit orders, select option strikes, size positions, override risk gates, trip circuit breakers, or bypass paper trading safety locks.

## Deterministic Decision Layer
1. **Technical Regime Classifier**: Computes ADX(14), 20-day SMA, 50-day SMA, Bollinger width percentile, and 20-day historical realized volatility.
2. **IV Rank Engine**: Extracts ATM IV and calculates IV rank against persistent historical IV DB.
3. **Candidate Builder & Scoring Matrix**: Builds valid multi-leg candidates matching delta, DTE (20–45 days), and wing width parameters; scores candidates across 7 normalized dimensions (0–100).
4. **Shared 15-Rule Risk Gate**: Every candidate must pass through the identical strategy-agnostic risk gate.

## Risk Management
- **Max Risk Per Trade**: Capped at configured maximum dollar loss and percentage of account equity.
- **Portfolio Exposure**: Enforces maximum concurrent open positions and portfolio risk allocation limits.
- **Daily Circuit Breaker**: Emergency circuit breaker trips if daily loss exceeds limit, halting new entries while permitting risk-reducing exits.
- **Idempotency Protection**: Computes trade structure fingerprints (`strategy:direction:underlying:expiration:legs`) and checks SQLite idempotency DB to reject duplicate submissions (`DUPLICATE_BLOCKED`).
- **Quote Freshness**: Rejects candidate generation if option quote timestamps exceed max age.

## Alpaca Infrastructure
- **Paper Trading**: Configured exclusively for Alpaca Paper Trading API endpoints (`ALPACA_PAPER=True`). Live money trading is physically blocked.
- **MLEG Options Execution**: Builds defined-risk multi-leg limit orders with explicit position intents (`BUY_TO_OPEN`, `SELL_TO_OPEN`, `BUY_TO_CLOSE`, `SELL_TO_CLOSE`).
- **Alpaca MCP Integration**: Exposes standard Model Context Protocol interface ([`trading_agent/integrations/alpaca_mcp.py`](file:///d:/Algo%20Trading/trading_agent/integrations/alpaca_mcp.py)) categorizing read-only data access (`alpaca_get_account`, `alpaca_get_positions`, `alpaca_get_option_chain`) and write tools (`alpaca_submit_mleg_order`, `alpaca_close_position`) governed strictly by [`MCPGuard`](file:///d:/Algo%20Trading/trading_agent/integrations/mcp_guard.py).

## Autonomous Loop
```
Market Data Pull
    ↓
Deterministic Technical Regime
    ↓
Featherless AI Advisory (Advisory Only)
    ↓
Multi-Strategy Candidate Building
    ↓
Deterministic Scoring Matrix
    ↓
Shared 15-Rule Risk Gate
    ↓
Portfolio Idempotency & Conflict Check
    ↓
Alpaca MLEG Order Submission
    ↓
Position Lifecycle & Exit Management
    ↓
P&L Attribution & Telemetry Logging
```

## Safety Invariant
**The deterministic safety layer ALWAYS has final authority.** If Featherless AI recommends a trade that violates technical regime eligibility, fails candidate liquidity filters, or trips any of the 15 risk gate rules, the agent fails closed to `NO_TRADE`.

## Evaluation & Compliance Verification
- **Test Suite**: 332/332 passing unit, adversarial, and pipeline pytest tests.
- **Paper Execution Verified**: Pre-flight safety suite independently verifies paper API endpoint, active account status, and credential security.
- **Decision Auditability**: Machine-readable JSONL audit traces logged to `logs/decision_audit.jsonl` and competition telemetry logged to `logs/competition_run.jsonl`.
