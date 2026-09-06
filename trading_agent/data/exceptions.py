"""
Structured exceptions for Alpaca market data layer.
"""


class AlpacaDataError(Exception):
    """Base exception for Alpaca data fetcher errors."""
    pass


class AlpacaAuthError(AlpacaDataError):
    """Raised when authentication fails (HTTP 401/403). Non-retryable."""
    pass


class AlpacaRateLimitError(AlpacaDataError):
    """Raised when rate limit is exceeded (HTTP 429). Retryable."""
    pass


class AlpacaNetworkError(AlpacaDataError):
    """Raised when network/server errors occur (HTTP 5xx / Connection error). Retryable."""
    pass
