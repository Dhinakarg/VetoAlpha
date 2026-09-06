"""
Unit tests for FeatherlessClient.
All OpenAI API calls are 100% mocked. No real network calls are made.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from trading_agent.strategy.featherless_client import (
    FeatherlessClient,
    SYSTEM_PROMPT,
)
from trading_agent.models import SentimentResult


@pytest.fixture
def client():
    """Fixture providing FeatherlessClient with dummy API key and low call limit for testing."""
    return FeatherlessClient(
        api_key="test_dummy_key",
        base_url="https://api.featherless.ai/v1",
        model="meta-llama/Meta-Llama-3.1-8B-Instruct",
        max_llm_calls_per_day=3,
        timeout=5.0,
    )


def test_1_valid_response(client):
    """Test 1: Valid structured JSON response returns expected SentimentResult with catalyst_risk='low'."""
    mock_payload = {
        "catalyst_risk": "low",
        "confidence": 0.90,
        "rationale": "No major catalysts or earnings events in the next 14 days.",
        "flagged_events": [],
    }

    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content=json.dumps(mock_payload)))
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_client_inst = MagicMock()
        mock_openai.return_value = mock_client_inst
        mock_client_inst.chat.completions.create.return_value = mock_completion

        res = client.analyze_catalyst_risk(
            symbol="AAPL",
            market_context={"price": 180.0},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        assert res.catalyst_risk == "low"
        assert res.confidence == 0.90
        assert "No major catalysts" in res.rationale
        assert res.score == 0.5
        mock_client_inst.chat.completions.create.assert_called_once()

        # Check system prompt passed
        args, kwargs = mock_client_inst.chat.completions.create.call_args
        messages = kwargs.get("messages", [])
        assert messages[0]["content"] == SYSTEM_PROMPT


def test_2_malformed_json(client):
    """Test 2: Malformed non-JSON string from API triggers fail-safe catalyst_risk='high'."""
    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(content="This is plain text, not valid JSON!")
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_client_inst = MagicMock()
        mock_openai.return_value = mock_client_inst
        mock_client_inst.chat.completions.create.return_value = mock_completion

        res = client.analyze_catalyst_risk(
            symbol="TSLA",
            market_context={},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        assert res.catalyst_risk == "high"
        assert res.confidence == 0.0
        assert "Fail-safe triggered" in res.rationale


def test_3_schema_validation_failure(client):
    """Test 3: Response missing required fields or having invalid enum fails safe to catalyst_risk='high'."""
    invalid_schema_payload = {
        "catalyst_risk": "extreme_invalid_enum",  # Invalid enum value
        "confidence": 0.8,
        # missing rationale field
    }

    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content=json.dumps(invalid_schema_payload)))
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_client_inst = MagicMock()
        mock_openai.return_value = mock_client_inst
        mock_client_inst.chat.completions.create.return_value = mock_completion

        res = client.analyze_catalyst_risk(
            symbol="NVDA",
            market_context={},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        assert res.catalyst_risk == "high"
        assert res.confidence == 0.0
        assert "Fail-safe triggered" in res.rationale


def test_4_low_confidence(client):
    """Test 4: Response with confidence < 0.50 forces catalyst_risk to 'high'."""
    mock_payload = {
        "catalyst_risk": "low",
        "confidence": 0.40,  # Below 0.50 threshold
        "rationale": "Uncertain about upcoming rumor.",
        "flagged_events": ["rumor_unverified"],
    }

    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content=json.dumps(mock_payload)))
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_client_inst = MagicMock()
        mock_openai.return_value = mock_client_inst
        mock_client_inst.chat.completions.create.return_value = mock_completion

        res = client.analyze_catalyst_risk(
            symbol="AMD",
            market_context={},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        assert res.catalyst_risk == "high"  # Forced from 'low' to 'high'
        assert res.confidence == 0.40
        assert res.flagged_events == ["rumor_unverified"]


def test_5_api_failure(client):
    """Test 5: Transient API failure retries maximum 2 attempts, then fails safe to catalyst_risk='high'."""
    with patch("openai.OpenAI") as mock_openai:
        mock_client_inst = MagicMock()
        mock_openai.return_value = mock_client_inst
        # Simulate 500 error on every attempt
        mock_client_inst.chat.completions.create.side_effect = Exception("500 Internal Server Error")

        res = client.analyze_catalyst_risk(
            symbol="MSFT",
            market_context={},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        assert res.catalyst_risk == "high"
        assert res.confidence == 0.0
        # Check retry count: exactly 2 attempts
        assert mock_client_inst.chat.completions.create.call_count == 2


def test_6_cache_hit(client):
    """Test 6: Repeated calls for the same symbol & trading_date hit in-memory cache without calling API."""
    mock_payload = {
        "catalyst_risk": "low",
        "confidence": 0.95,
        "rationale": "Clear calendar.",
        "flagged_events": [],
    }

    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content=json.dumps(mock_payload)))
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_client_inst = MagicMock()
        mock_openai.return_value = mock_client_inst
        mock_client_inst.chat.completions.create.return_value = mock_completion

        # First call -> hits API
        res1 = client.analyze_catalyst_risk(
            symbol="GOOGL",
            market_context={},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        # Second call -> cache hit
        res2 = client.analyze_catalyst_risk(
            symbol="GOOGL",
            market_context={},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        assert res1.catalyst_risk == "low"
        assert res2.catalyst_risk == "low"
        # OpenAI client created and called EXACTLY ONCE
        assert mock_client_inst.chat.completions.create.call_count == 1


def test_7_daily_budget_exceeded(client):
    """Test 7: Exceeding max_llm_calls_per_day triggers fail-safe without invoking API."""
    mock_payload = {
        "catalyst_risk": "low",
        "confidence": 0.95,
        "rationale": "Clear calendar.",
        "flagged_events": [],
    }

    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content=json.dumps(mock_payload)))
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_client_inst = MagicMock()
        mock_openai.return_value = mock_client_inst
        mock_client_inst.chat.completions.create.return_value = mock_completion

        # Limit is 3 calls (from fixture). Perform 3 unique calls for different symbols on the same date.
        for i in range(3):
            client.analyze_catalyst_risk(
                symbol=f"SYM_{i}",
                market_context={},
                news=[],
                upcoming_events={},
                trading_date="2026-08-28",
            )

        assert mock_client_inst.chat.completions.create.call_count == 3

        # 4th call exceeds daily budget
        res_budget_exceeded = client.analyze_catalyst_risk(
            symbol="SYM_EXCEEDED",
            market_context={},
            news=[],
            upcoming_events={},
            trading_date="2026-08-28",
        )

        assert res_budget_exceeded.catalyst_risk == "high"
        assert res_budget_exceeded.flagged_events == ["daily_llm_budget_exceeded"]
        # API call count remains 3 (no API call on 4th attempt)
        assert mock_client_inst.chat.completions.create.call_count == 3
