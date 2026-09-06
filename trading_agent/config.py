"""
Configuration management for Adaptive AI Iron Condor Agent.
Loads settings from environment variables and .env file securely.
"""

import os
from typing import Optional
from dotenv import load_dotenv

# Load .env file if available
load_dotenv()


class Settings:
    """Application settings class reading strictly from environment variables."""

    def __init__(self):
        self.alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
        self.alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
        self.alpaca_paper: bool = os.getenv("ALPACA_PAPER", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        # Generic OpenAI-compatible LLM Configuration (Featherless, OpenAI, Gemini, Groq, Ollama)
        self.llm_api_key: str = os.getenv("LLM_API_KEY") or os.getenv("FEATHERLESS_API_KEY", "")
        self.llm_base_url: str = os.getenv("LLM_BASE_URL") or os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
        self.llm_model: str = os.getenv("LLM_MODEL") or os.getenv("FEATHERLESS_MODEL", "deepseek-ai/DeepSeek-V4-Flash-0731")

        # Backward compatibility aliases
        self.featherless_api_key: str = self.llm_api_key
        self.featherless_base_url: str = self.llm_base_url
        self.featherless_model: str = self.llm_model
        self.starting_capital: float = float(
            os.getenv("STARTING_CAPITAL", "100000.0")
        )
        self.max_llm_calls_per_day: int = int(
            os.getenv("MAX_LLM_CALLS_PER_DAY", "50")
        )
        self.min_iv_history_days: int = int(
            os.getenv("MIN_IV_HISTORY_DAYS", "15")
        )
        self.preferred_iv_history_days: int = int(
            os.getenv("PREFERRED_IV_HISTORY_DAYS", "60")
        )
        self.target_dte_min: int = int(
            os.getenv("TARGET_DTE_MIN", "20")
        )
        self.target_dte_max: int = int(
            os.getenv("TARGET_DTE_MAX", "45")
        )
        self.max_option_quote_age_seconds: int = int(
            os.getenv("MAX_OPTION_QUOTE_AGE_SECONDS", "900")
        )
        self.iv_history_db_path: str = os.getenv(
            "IV_HISTORY_DB_PATH", "logs/iv_history.db"
        )
        self.hackathon_mode: bool = os.getenv("HACKATHON_MODE", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self.hackathon_account_id: Optional[str] = os.getenv("HACKATHON_ACCOUNT_ID", None)

        # Options Universe & Invalidation Config
        self.iv_refresh_delta: float = float(os.getenv("IV_REFRESH_DELTA", "15.0"))
        self.liquidity_min_price: float = float(os.getenv("LIQUIDITY_MIN_PRICE", "10.0"))
        self.liquidity_min_volume: int = int(os.getenv("LIQUIDITY_MIN_VOLUME", "500000"))
        self.liquidity_max_spread_pct: float = float(os.getenv("LIQUIDITY_MAX_SPREAD_PCT", "0.10"))

        self.liquid_universe_categories: dict = {
            "broad_etfs": ["SPY", "QQQ", "IWM", "DIA", "VOO", "IVV"],
            "sector_etfs": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLC", "XLB", "XLU"],
            "liquid_equities": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "NFLX"],
        }

        # Dedicated Crypto Validation Settings
        self.crypto_validation_enabled: bool = os.getenv("CRYPTO_VALIDATION_ENABLED", "true").lower() in ("true", "1", "yes")
        self.crypto_symbols: list = [s.strip() for s in os.getenv("CRYPTO_SYMBOLS", "BTC/USD,ETH/USD,SOL/USD").split(",") if s.strip()]
        self.crypto_execution_mode: str = os.getenv("CRYPTO_EXECUTION_MODE", "DRY_RUN")
        self.crypto_timeframe: str = os.getenv("CRYPTO_TIMEFRAME", "15Min")
        self.crypto_risk_per_trade: float = float(os.getenv("CRYPTO_RISK_PER_TRADE", "0.0025"))
        self.crypto_max_daily_loss: float = float(os.getenv("CRYPTO_MAX_DAILY_LOSS", "0.01"))
        self.crypto_max_open_positions: int = int(os.getenv("CRYPTO_MAX_OPEN_POSITIONS", "3"))
        self.crypto_min_risk_reward: float = float(os.getenv("CRYPTO_MIN_RISK_REWARD", "1.5"))
        self.crypto_min_signal_score: float = float(os.getenv("CRYPTO_MIN_SIGNAL_SCORE", "65.0"))
        self.crypto_ai_enabled: bool = os.getenv("CRYPTO_AI_ENABLED", "true").lower() in ("true", "1", "yes")

    @property
    def alpaca_base_url(self) -> str:
        if self.alpaca_paper:
            return "https://paper-api.alpaca.markets"
        return "https://api.alpaca.markets"

    def model_dump(self) -> dict:
        """Return dictionary representation of configuration settings."""
        return {
            "alpaca_api_key": self.alpaca_api_key,
            "alpaca_secret_key": self.alpaca_secret_key,
            "alpaca_paper": self.alpaca_paper,
            "featherless_api_key": self.featherless_api_key,
            "featherless_base_url": self.featherless_base_url,
            "featherless_model": self.featherless_model,
            "starting_capital": self.starting_capital,
            "max_llm_calls_per_day": self.max_llm_calls_per_day,
            "min_iv_history_days": self.min_iv_history_days,
            "preferred_iv_history_days": self.preferred_iv_history_days,
            "target_dte_min": self.target_dte_min,
            "target_dte_max": self.target_dte_max,
            "max_option_quote_age_seconds": self.max_option_quote_age_seconds,
            "iv_history_db_path": self.iv_history_db_path,
            "hackathon_mode": self.hackathon_mode,
            "hackathon_account_id": self.hackathon_account_id,
            "crypto_validation_enabled": self.crypto_validation_enabled,
            "crypto_symbols": self.crypto_symbols,
            "crypto_execution_mode": self.crypto_execution_mode,
            "crypto_timeframe": self.crypto_timeframe,
            "crypto_risk_per_trade": self.crypto_risk_per_trade,
            "crypto_max_daily_loss": self.crypto_max_daily_loss,
            "crypto_max_open_positions": self.crypto_max_open_positions,
            "crypto_min_risk_reward": self.crypto_min_risk_reward,
            "crypto_min_signal_score": self.crypto_min_signal_score,
            "crypto_ai_enabled": self.crypto_ai_enabled,
        }


# Singleton instance
settings = Settings()
