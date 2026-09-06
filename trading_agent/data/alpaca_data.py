"""
Alpaca market-data layer using alpaca-py SDK.
Separates read operations from order execution.
Includes structured exceptions, retries for transient failures, and option chain normalization.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Callable

from trading_agent.config import settings
from trading_agent.models import AccountSnapshot, OptionChainSnapshot, OptionContract, MarketSnapshot
from trading_agent.data.exceptions import (
    AlpacaDataError,
    AlpacaAuthError,
    AlpacaRateLimitError,
    AlpacaNetworkError,
)

logger = logging.getLogger(__name__)


def _with_retry(max_attempts: int = 3, backoff_factor: float = 0.5):
    """Decorator to retry transient rate limits and network errors, re-raising auth errors immediately."""

    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except AlpacaAuthError:
                    # Authentication failure is non-retryable
                    raise
                except AlpacaDataError as e:
                    last_err = e
                    if isinstance(e, (AlpacaRateLimitError, AlpacaNetworkError)):
                        logger.warning(f"{func.__name__} attempt {attempt} failed ({type(e).__name__}): {e}")
                        if attempt < max_attempts:
                            time.sleep(backoff_factor * (2 ** (attempt - 1)))
                    else:
                        raise
                except Exception as e:
                    err_str = str(e).lower()
                    if "401" in err_str or "403" in err_str or "unauthorized" in err_str or "forbidden" in err_str:
                        raise AlpacaAuthError(f"Authentication failed: {e}") from e
                    elif "429" in err_str or "rate limit" in err_str:
                        last_err = AlpacaRateLimitError(f"Rate limit exceeded: {e}")
                    else:
                        last_err = AlpacaNetworkError(f"Network failure: {e}")

                    logger.warning(f"{func.__name__} attempt {attempt} failed ({type(last_err).__name__}): {e}")
                    if attempt < max_attempts:
                        time.sleep(backoff_factor * (2 ** (attempt - 1)))

            raise last_err or AlpacaDataError("All retry attempts failed")

        return wrapper

    return decorator


class AlpacaDataFetcher:
    """Read-only market data and account state fetcher using alpaca-py."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: Optional[bool] = None,
    ):
        self.api_key = api_key or settings.alpaca_api_key
        self.secret_key = secret_key or settings.alpaca_secret_key
        self.paper = paper if paper is not None else settings.alpaca_paper

        self._trading_client = None
        self._stock_data_client = None
        self._option_data_client = None
        self._news_client = None

        if self.api_key and self.secret_key:
            self._init_clients()

    def _init_clients(self):
        """Initialize alpaca-py SDK client instances."""
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.historical.news import NewsClient

            self._trading_client = TradingClient(
                api_key=self.api_key, secret_key=self.secret_key, paper=self.paper
            )
            self._stock_data_client = StockHistoricalDataClient(
                api_key=self.api_key, secret_key=self.secret_key
            )
            self._option_data_client = OptionHistoricalDataClient(
                api_key=self.api_key, secret_key=self.secret_key
            )
            self._news_client = NewsClient(
                api_key=self.api_key, secret_key=self.secret_key
            )
        except Exception as e:
            logger.error(f"Failed to initialize Alpaca clients: {e}")

    def _handle_exception(self, e: Exception, msg: str):
        """Helper to map low-level exceptions to structured Alpaca exceptions."""
        err_msg = str(e).lower()
        if "401" in err_msg or "403" in err_msg or "unauthorized" in err_msg or "forbidden" in err_msg:
            raise AlpacaAuthError(f"{msg}: {e}") from e
        elif "429" in err_msg or "rate limit" in err_msg:
            raise AlpacaRateLimitError(f"{msg}: {e}") from e
        elif any(code in err_msg for code in ["500", "502", "503", "504", "connection"]):
            raise AlpacaNetworkError(f"{msg}: {e}") from e
        raise AlpacaDataError(f"{msg}: {e}") from e

    @_with_retry()
    def get_account_snapshot(self) -> AccountSnapshot:
        """Fetch current account balance and status."""
        if not self._trading_client:
            return AccountSnapshot(
                account_id="PAPER_SIMULATOR",
                cash=settings.starting_capital,
                buying_power=settings.starting_capital,
                equity=settings.starting_capital,
                portfolio_value=settings.starting_capital,
                status="ACTIVE",
            )

        try:
            acct = self._trading_client.get_account()
            return AccountSnapshot(
                account_id=str(acct.id),
                cash=float(acct.cash),
                buying_power=float(acct.buying_power),
                equity=float(acct.equity),
                portfolio_value=float(acct.portfolio_value),
                status=str(acct.status),
                currency=str(acct.currency),
            )
        except Exception as e:
            self._handle_exception(e, "Failed to fetch account snapshot")

    def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        """Fetch market snapshot including quote and underlying price."""
        quote = self.get_underlying_quote(symbol)
        now_iso = datetime.now(timezone.utc).isoformat()
        return MarketSnapshot(
            symbol=symbol,
            underlying_price=quote.get("last", 100.0),
            vix=18.5,
            timestamp=now_iso,
        )

    @_with_retry()
    def get_market_clock(self) -> Dict[str, Any]:
        """Fetch current market clock status."""
        now_iso = datetime.now(timezone.utc).isoformat()
        if not self._trading_client:
            return {
                "is_open": True,
                "timestamp": now_iso,
                "next_open": now_iso,
                "next_close": now_iso,
            }

        try:
            clock = self._trading_client.get_clock()
            return {
                "is_open": clock.is_open,
                "timestamp": clock.timestamp.isoformat(),
                "next_open": clock.next_open.isoformat(),
                "next_close": clock.next_close.isoformat(),
            }
        except Exception as e:
            self._handle_exception(e, "Failed to fetch market clock")

    @_with_retry()
    def get_underlying_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch latest quote for an underlying equity symbol."""
        now_iso = datetime.now(timezone.utc).isoformat()
        if not self._stock_data_client:
            return {
                "symbol": symbol,
                "bid": 100.0,
                "ask": 100.05,
                "last": 100.02,
                "timestamp": now_iso,
            }

        try:
            from alpaca.data.requests import StockLatestQuoteRequest

            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self._stock_data_client.get_stock_latest_quote(req)
            quote = quotes[symbol]
            return {
                "symbol": symbol,
                "bid": float(quote.bid_price or 0.0),
                "ask": float(quote.ask_price or 0.0),
                "last": float((quote.bid_price + quote.ask_price) / 2 if quote.bid_price and quote.ask_price else 0.0),
                "timestamp": quote.timestamp.isoformat() if quote.timestamp else now_iso,
            }
        except Exception as e:
            self._handle_exception(e, f"Failed to fetch quote for {symbol}")

    @_with_retry()
    def get_recent_bars(
        self, symbol: str, timeframe: str = "1Day", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch historical price bars for an underlying symbol."""
        if not self._stock_data_client:
            return []

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            tf = TimeFrame.Day if "day" in timeframe.lower() else TimeFrame.Hour
            start_dt = datetime.now(timezone.utc) - timedelta(days=limit * 2 + 10)
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start_dt, limit=limit)
            bars_res = self._stock_data_client.get_stock_bars(req)
            bars_list = bars_res[symbol]
            return [
                {
                    "timestamp": b.timestamp.isoformat(),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": int(b.volume),
                }
                for b in bars_list
            ]
        except Exception as e:
            self._handle_exception(e, f"Failed to fetch bars for {symbol}")

    @_with_retry()
    def get_option_chain(
        self,
        symbol: str,
        expiration_gte: Optional[str] = None,
        expiration_lte: Optional[str] = None,
    ) -> OptionChainSnapshot:
        """Fetch option chain for underlying symbol and normalize into OptionContract instances.
        
        CRITICAL RULE: Missing Greeks (delta, gamma, theta, vega) or implied volatility are explicitly
        handled and set to None. They are NEVER replaced with zero.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        if not self._option_data_client:
            contracts = [
                OptionContract(
                    symbol=f"{symbol}260918P00090000",
                    underlying_symbol=symbol,
                    strike_price=90.0,
                    expiration_date="2026-09-18",
                    contract_type="put",
                    bid=1.20,
                    ask=1.25,
                    last_price=1.22,
                    volume=150,
                    open_interest=500,
                    implied_volatility=0.22,
                    delta=-0.15,
                    gamma=0.02,
                    theta=-0.05,
                    vega=0.10,
                    quote_timestamp=now_iso,
                ),
                OptionContract(
                    symbol=f"{symbol}260918C00110000",
                    underlying_symbol=symbol,
                    strike_price=110.0,
                    expiration_date="2026-09-18",
                    contract_type="call",
                    bid=1.10,
                    ask=1.15,
                    last_price=1.12,
                    volume=140,
                    open_interest=450,
                    implied_volatility=None,
                    delta=None,
                    gamma=None,
                    theta=None,
                    vega=None,
                    quote_timestamp=now_iso,
                ),
            ]
            return OptionChainSnapshot(
                underlying_symbol=symbol,
                underlying_price=100.0,
                timestamp=now_iso,
                contracts=contracts,
            )

        try:
            from alpaca.data.requests import OptionChainRequest

            # Fetch live underlying price for ATM reference
            underlying_p = 100.0
            try:
                quote = self.get_underlying_quote(symbol)
                underlying_p = float(quote.get("last") or quote.get("ask") or 100.0)
            except Exception:
                underlying_p = 100.0

            # Compute default target DTE expiration window (e.g. 20-45 DTE) if not supplied
            target_dte_min = int(getattr(settings, "target_dte_min", 20))
            target_dte_max = int(getattr(settings, "target_dte_max", 45))
            
            exp_gte = expiration_gte or (datetime.now(timezone.utc) + timedelta(days=target_dte_min)).strftime("%Y-%m-%d")
            exp_lte = expiration_lte or (datetime.now(timezone.utc) + timedelta(days=target_dte_max)).strftime("%Y-%m-%d")

            req_args: Dict[str, Any] = {
                "underlying_symbol": symbol,
                "expiration_date_gte": exp_gte,
                "expiration_date_lte": exp_lte,
            }

            req = OptionChainRequest(**req_args)
            chain_resp = self._option_data_client.get_option_chain(req)

            normalized_contracts = []
            for contract_sym, snap in chain_resp.items():
                latest_quote = getattr(snap, "latest_quote", None)
                latest_trade = getattr(snap, "latest_trade", None)
                greeks = getattr(snap, "greeks", None)
                implied_vol = getattr(snap, "implied_volatility", None)

                bid = float(latest_quote.bid_price) if latest_quote and latest_quote.bid_price is not None else 0.0
                ask = float(latest_quote.ask_price) if latest_quote and latest_quote.ask_price is not None else 0.0
                last_p = float(latest_trade.price) if latest_trade and latest_trade.price is not None else 0.0

                delta_val = float(greeks.delta) if greeks and getattr(greeks, "delta", None) is not None else None
                gamma_val = float(greeks.gamma) if greeks and getattr(greeks, "gamma", None) is not None else None
                theta_val = float(greeks.theta) if greeks and getattr(greeks, "theta", None) is not None else None
                vega_val = float(greeks.vega) if greeks and getattr(greeks, "vega", None) is not None else None
                iv_val = float(implied_vol) if implied_vol is not None else None

                # Parse standard OCC option symbol: e.g., SPY260831P00697000
                strike_val = float(getattr(snap, "strike_price", 0.0) or 0.0)
                exp_val = str(getattr(snap, "expiration_date", "") or "")
                c_type_val = "call" if "C" in contract_sym else "put"

                if strike_val == 0.0 or not exp_val:
                    import re
                    m = re.match(r"^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", contract_sym)
                    if m:
                        _, yy, mm, dd, c_flag, strike_raw = m.groups()
                        if strike_val == 0.0:
                            strike_val = float(strike_raw) / 1000.0
                        if not exp_val:
                            exp_val = f"20{yy}-{mm}-{dd}"
                        c_type_val = "call" if c_flag == "C" else "put"

                contract_obj = OptionContract(
                    symbol=contract_sym,
                    underlying_symbol=symbol,
                    strike_price=strike_val,
                    expiration_date=exp_val,
                    contract_type=c_type_val,
                    bid=bid,
                    ask=ask,
                    last_price=last_p,
                    volume=int(getattr(latest_trade, "size", 0)) if latest_trade else 0,
                    open_interest=int(getattr(snap, "open_interest", 0)),
                    implied_volatility=iv_val,
                    delta=delta_val,
                    gamma=gamma_val,
                    theta=theta_val,
                    vega=vega_val,
                    quote_timestamp=latest_quote.timestamp.isoformat() if latest_quote and getattr(latest_quote, "timestamp", None) else now_iso,
                )
                normalized_contracts.append(contract_obj)

            return OptionChainSnapshot(
                underlying_symbol=symbol,
                underlying_price=underlying_p,
                timestamp=now_iso,
                contracts=normalized_contracts,
            )
        except Exception as e:
            self._handle_exception(e, f"Failed to fetch option chain for {symbol}")

    @_with_retry()
    def get_recent_news(self, symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch recent news articles for symbol."""
        if not self._news_client:
            return []

        try:
            from alpaca.data.requests import NewsRequest

            req = NewsRequest(symbols=symbol, limit=limit)
            news_res = self._news_client.get_news(req)
            articles = news_res.news if hasattr(news_res, "news") else news_res
            return [
                {
                    "headline": getattr(a, "headline", ""),
                    "summary": getattr(a, "summary", ""),
                    "author": getattr(a, "author", ""),
                    "created_at": a.created_at.isoformat() if getattr(a, "created_at", None) else "",
                    "symbols": getattr(a, "symbols", []),
                }
                for a in articles
            ]
        except Exception as e:
            self._handle_exception(e, f"Failed to fetch news for {symbol}")

    @_with_retry()
    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions in the Alpaca account."""
        if not self._trading_client:
            return []

        try:
            positions = self._trading_client.get_all_positions()
            return [
                {
                    "symbol": str(p.symbol),
                    "qty": float(p.qty),
                    "market_value": float(p.market_value),
                    "cost_basis": float(p.cost_basis),
                    "unrealized_pl": float(p.unrealized_pl),
                    "side": str(p.side),
                }
                for p in positions
            ]
        except Exception as e:
            self._handle_exception(e, "Failed to fetch open positions")

    def get_positions(self) -> List[Dict[str, Any]]:
        """Alias for get_open_positions."""
        return self.get_open_positions()

    @_with_retry()
    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Fetch active/open orders in the Alpaca account."""
        if not self._trading_client:
            return []

        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus

            req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            orders = self._trading_client.get_orders(req)
            return [
                {
                    "id": str(o.id),
                    "symbol": str(o.symbol),
                    "qty": float(o.qty or 0),
                    "side": str(o.side),
                    "type": str(o.type),
                    "status": str(o.status),
                    "created_at": o.created_at.isoformat() if getattr(o, "created_at", None) else "",
                }
                for o in orders
            ]
        except Exception as e:
            self._handle_exception(e, "Failed to fetch open orders")

    @_with_retry()
    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Fetch details for a specific order by ID."""
        if not self._trading_client:
            return {"id": order_id, "status": "simulated"}

        try:
            o = self._trading_client.get_order_by_id(order_id)
            return {
                "id": str(o.id),
                "symbol": str(o.symbol),
                "qty": float(o.qty or 0),
                "filled_qty": float(o.filled_qty or 0),
                "side": str(o.side),
                "type": str(o.type),
                "status": str(o.status),
                "created_at": o.created_at.isoformat() if getattr(o, "created_at", None) else "",
            }
        except Exception as e:
            self._handle_exception(e, f"Failed to fetch order {order_id}")
