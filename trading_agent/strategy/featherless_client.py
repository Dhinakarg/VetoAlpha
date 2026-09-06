"""
Featherless AI Client for catalyst-risk assessment.
Enforces strict AI reasoning layer boundaries, structured JSON validation, in-memory caching,
daily budget limits, transient retry logic, and fail-safe defaults.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pydantic import ValidationError

from trading_agent.config import settings
from trading_agent.models import FeatherlessCatalystResponse, SentimentResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a market catalyst-risk analyst, not an order execution system.\n"
    "Analyze only the supplied information.\n"
    "Never invent news or events.\n"
    "Never provide an order.\n"
    "Never override deterministic risk controls.\n"
    "Your only task is to classify whether selling an iron condor is exposed to a significant catalyst."
)


class FeatherlessError(Exception):
    """Base exception for Featherless AI client errors."""
    pass


class FeatherlessAPIError(FeatherlessError):
    """Raised when the Featherless API returns an error response."""
    pass


class FeatherlessValidationError(FeatherlessError):
    """Raised when the response fails Pydantic schema validation or JSON parsing."""
    pass


class FeatherlessBudgetExceededError(FeatherlessError):
    """Raised when daily LLM call quota is exceeded."""
    pass


class FeatherlessClient:
    """Client for evaluating market catalyst risk using Featherless AI OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_llm_calls_per_day: Optional[int] = None,
        timeout: float = 10.0,
    ):
        self.api_key = api_key or settings.featherless_api_key
        self.base_url = base_url or settings.featherless_base_url
        self.model = model or settings.featherless_model
        self.max_llm_calls_per_day = (
            max_llm_calls_per_day
            if max_llm_calls_per_day is not None
            else settings.max_llm_calls_per_day
        )
        self.timeout = timeout

        # In-memory cache: key -> SentimentResult
        self._cache: Dict[str, SentimentResult] = {}
        # Daily call counter: trading_date -> int
        self._daily_calls: Dict[str, int] = {}

    def _get_trading_date(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def analyze_catalyst_risk(
        self,
        symbol: str,
        market_context: dict,
        news: list[dict],
        upcoming_events: dict,
        trading_date: Optional[str] = None,
        force_refresh: bool = False,
    ) -> SentimentResult:
        """Analyze market catalyst risk for target symbol and return validated SentimentResult."""
        current_date = trading_date or self._get_trading_date()
        cache_key = f"{symbol}_{current_date}"
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. In-memory cache check
        if not force_refresh and cache_key in self._cache:
            logger.info(f"Featherless cache hit for {cache_key}")
            return self._cache[cache_key]

        # 2. Daily LLM budget check
        calls_today = self._daily_calls.get(current_date, 0)
        if calls_today >= self.max_llm_calls_per_day:
            logger.warning(
                f"Daily LLM call budget ({self.max_llm_calls_per_day}) exceeded for date {current_date}. Failing safe to high risk."
            )
            fail_safe_res = SentimentResult(
                score=-0.8,
                confidence=1.0,
                rationale="Daily LLM call budget exceeded. Fail-safe triggered to high catalyst risk.",
                model_used=self.model,
                source_tags=["budget_exceeded", symbol],
                timestamp=now_iso,
                catalyst_risk="high",
                flagged_events=["daily_llm_budget_exceeded"],
            )
            return fail_safe_res

        # If API key is missing, fail safe
        if not self.api_key:
            logger.warning(f"No FEATHERLESS_API_KEY configured. Failing safe to high catalyst risk for {symbol}.")
            fail_safe_res = SentimentResult(
                score=-0.8,
                confidence=1.0,
                rationale="Featherless API key not configured. Fail-safe triggered.",
                model_used=self.model,
                source_tags=["missing_api_key", symbol],
                timestamp=now_iso,
                catalyst_risk="high",
                flagged_events=["missing_api_key"],
            )
            return fail_safe_res

        # 3. Prepare payload & prompt
        prompt_data = {
            "symbol": symbol,
            "market_context": market_context,
            "news": news,
            "upcoming_events": upcoming_events,
            "required_json_format": {
                "catalyst_risk": "low | medium | high",
                "confidence": 0.0,
                "rationale": "explanation string",
                "flagged_events": ["event string"],
            },
        }

        user_content = (
            f"Analyze the following market catalyst information for {symbol} and classify the risk of selling an Iron Condor:\n\n"
            f"{json.dumps(prompt_data, indent=2)}\n\n"
            "Return STRICT valid JSON matching the required schema."
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # 4. Invoke API with retry logic (max 2 attempts on transient errors)
        max_attempts = 2
        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:

                from openai import OpenAI

                client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    timeout=self.timeout,
                )

                # Increment daily call counter upon actual attempt
                self._daily_calls[current_date] = self._daily_calls.get(current_date, 0) + 1

                logger.info(
                    f"Calling Featherless API for {symbol} (Attempt {attempt}/{max_attempts}, Model: {self.model})"
                )

                completion = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=300,
                )

                raw_content = completion.choices[0].message.content or ""
                
                # 5. Parse & Validate Pydantic schema
                try:
                    parsed_response = FeatherlessCatalystResponse.model_validate_json(raw_content)
                except (json.JSONDecodeError, ValidationError) as ve:
                    logger.error(f"Featherless response JSON validation failed for {symbol}: {ve}")
                    raise FeatherlessValidationError(f"Invalid response schema: {ve}") from ve

                # 6. Low Confidence Check (< 0.50 -> catalyst_risk="high")
                catalyst_risk = parsed_response.catalyst_risk
                if parsed_response.confidence < 0.50:
                    logger.warning(
                        f"Featherless confidence ({parsed_response.confidence:.2f}) below 0.50 for {symbol}. Forcing catalyst_risk='high'."
                    )
                    catalyst_risk = "high"

                # Map catalyst_risk to sentiment score
                score_map = {"low": 0.5, "medium": 0.0, "high": -0.8}
                score = score_map.get(catalyst_risk, -0.8)

                result = SentimentResult(
                    score=score,
                    confidence=parsed_response.confidence,
                    rationale=parsed_response.rationale,
                    model_used=self.model,
                    source_tags=["featherless-ai", symbol],
                    timestamp=now_iso,
                    catalyst_risk=catalyst_risk,
                    flagged_events=parsed_response.flagged_events,
                )

                # Cache success result
                self._cache[cache_key] = result
                return result

            except FeatherlessValidationError as e:
                # Validation errors are non-transient, do not retry
                last_error = e
                break

            except Exception as e:
                # Transient network / API error
                last_error = e
                logger.warning(f"Featherless API attempt {attempt} failed for {symbol}: {type(e).__name__}")
                if attempt < max_attempts:
                    time.sleep(0.5 * attempt)  # Exponential backoff

        # 7. Fail-safe fallback on any API error or validation failure
        err_msg = str(last_error) if last_error else "Unknown error"
        logger.error(f"Featherless AI analysis failed for {symbol}. Failing safe to high catalyst risk. Error: {err_msg}")

        fail_safe_result = SentimentResult(
            score=-0.8,
            confidence=0.0,
            rationale=f"Analysis failed ({type(last_error).__name__}). Fail-safe triggered to high catalyst risk.",
            model_used=self.model,
            source_tags=["fail_safe_fallback", symbol],
            timestamp=now_iso,
            catalyst_risk="high",
            flagged_events=["api_or_validation_failure"],
        )
        return fail_safe_result
