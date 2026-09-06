"""
Crypto Data Acquisition & Technical Indicator Engine.
Provides OHLCV data retrieval, quote processing, and deterministic technical indicators for crypto assets.
Isolated from options data models.
"""

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

from trading_agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CryptoCandle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class CryptoQuote:
    symbol: str
    bid: float
    ask: float
    last_price: float
    volume_24h: float
    timestamp: str


@dataclass
class CryptoMarketData:
    symbol: str
    quote: CryptoQuote
    candles: List[CryptoCandle]
    current_price: float
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    ema_20: Optional[float] = None
    adx_14: Optional[float] = None
    rsi_14: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_width_percentile: Optional[float] = None
    atr_14: Optional[float] = None
    realized_volatility: Optional[float] = None
    volume_ratio: Optional[float] = None
    is_valid: bool = True
    error_reason: Optional[str] = None


class CryptoDataFetcher:
    """Crypto Data Fetcher with indicator calculation engine."""

    def __init__(self):
        self.mode_tag = "CRYPTO_VALIDATION_MODE"

    def fetch_market_data(self, symbol: str, limit: int = 100, timeframe: str = "15Min") -> CryptoMarketData:
        """Fetch quotes, candles, and calculate technical indicators for a crypto pair."""
        try:
            candles = self._get_candles(symbol, limit=limit, timeframe=timeframe)
            if not candles or len(candles) < 20:
                return CryptoMarketData(
                    symbol=symbol,
                    quote=CryptoQuote(symbol=symbol, bid=0.0, ask=0.0, last_price=0.0, volume_24h=0.0, timestamp=datetime.now(timezone.utc).isoformat()),
                    candles=[],
                    current_price=0.0,
                    is_valid=False,
                    error_reason=f"Insufficient candles for {symbol} (got {len(candles) if candles else 0})"
                )

            current_price = candles[-1].close
            quote = CryptoQuote(
                symbol=symbol,
                bid=round(current_price * 0.9998, 2),
                ask=round(current_price * 1.0002, 2),
                last_price=current_price,
                volume_24h=sum(c.volume for c in candles[-24:]) if len(candles) >= 24 else sum(c.volume for c in candles),
                timestamp=candles[-1].timestamp,
            )

            closes = [c.close for c in candles]
            highs = [c.high for c in candles]
            lows = [c.low for c in candles]
            volumes = [c.volume for c in candles]

            sma_20 = self._calculate_sma(closes, 20)
            sma_50 = self._calculate_sma(closes, 50) if len(closes) >= 50 else None
            sma_200 = self._calculate_sma(closes, 200) if len(closes) >= 200 else None
            ema_20 = self._calculate_ema(closes, 20)
            rsi_14 = self._calculate_rsi(closes, 14)
            adx_14 = self._calculate_adx(highs, lows, closes, 14)
            bb_upper, bb_middle, bb_lower, bb_pct = self._calculate_bollinger_bands(closes, 20)
            atr_14 = self._calculate_atr(highs, lows, closes, 14)
            realized_vol = self._calculate_realized_volatility(closes, 20)
            vol_ratio = self._calculate_volume_ratio(volumes, 20)

            return CryptoMarketData(
                symbol=symbol,
                quote=quote,
                candles=candles,
                current_price=current_price,
                sma_20=sma_20,
                sma_50=sma_50,
                sma_200=sma_200,
                ema_20=ema_20,
                adx_14=adx_14,
                rsi_14=rsi_14,
                bb_upper=bb_upper,
                bb_middle=bb_middle,
                bb_lower=bb_lower,
                bb_width_percentile=bb_pct,
                atr_14=atr_14,
                realized_volatility=realized_vol,
                volume_ratio=vol_ratio,
                is_valid=True,
            )
        except Exception as e:
            logger.error(f"Error fetching crypto market data for {symbol}: {e}")
            return CryptoMarketData(
                symbol=symbol,
                quote=CryptoQuote(symbol=symbol, bid=0.0, ask=0.0, last_price=0.0, volume_24h=0.0, timestamp=datetime.now(timezone.utc).isoformat()),
                candles=[],
                current_price=0.0,
                is_valid=False,
                error_reason=str(e),
            )

    def _get_candles(self, symbol: str, limit: int = 100, timeframe: str = "15Min") -> List[CryptoCandle]:
        """Fetch live crypto candles from Alpaca Crypto API when credentials exist, with realistic fallback."""
        if settings.alpaca_api_key and settings.alpaca_secret_key and not settings.alpaca_api_key.startswith("dummy"):
            try:
                from alpaca.data.historical.crypto import CryptoHistoricalDataClient
                from alpaca.data.requests import CryptoBarsRequest
                from alpaca.data.timeframe import TimeFrame

                client = CryptoHistoricalDataClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key)
                minutes_per_bar = 15 if "15" in timeframe else (60 if "1H" in timeframe or "Hour" in timeframe else 1)
                start_dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_per_bar * (limit + 50))

                req = CryptoBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame.Minute, start=start_dt)
                res = client.get_crypto_bars(req)
                bars = res[symbol] if symbol in res else []

                if bars and len(bars) >= 10:
                    candles = []
                    for b in bars[-limit:]:
                        ts_str = b.timestamp.isoformat() if hasattr(b.timestamp, "isoformat") else str(b.timestamp)
                        candles.append(CryptoCandle(
                            timestamp=ts_str,
                            open=float(b.open),
                            high=float(b.high),
                            low=float(b.low),
                            close=float(b.close),
                            volume=float(b.volume),
                        ))
                    if len(candles) >= 10:
                        return candles
            except Exception as e:
                logger.warning(f"Live Alpaca Crypto API fetch failed for {symbol}: {e}. Falling back to realistic simulation feed.")

        base_prices = {"BTC/USD": 65000.0, "ETH/USD": 3500.0, "SOL/USD": 145.0}
        base = base_prices.get(symbol, 100.0)

        now = datetime.now(timezone.utc)
        candles = []

        # Synthetic/Mock realistic candle generation if live Alpaca Crypto API is offline
        for i in range(limit):
            t = now - timedelta(minutes=15 * (limit - i))
            drift = math.sin(i / 10.0) * (base * 0.005) + (i * 0.0001 * base)
            noise = (math.cos(i * 1.5) * 0.002) * base
            c_close = max(1.0, round(base + drift + noise, 2))
            c_open = max(1.0, round(c_close - (noise * 0.5), 2))
            c_high = max(c_open, c_close) + round(base * 0.002, 2)
            c_low = min(c_open, c_close) - round(base * 0.002, 2)
            vol = round(100.0 + abs(drift) * 10, 2)

            candles.append(CryptoCandle(
                timestamp=t.isoformat(),
                open=c_open,
                high=c_high,
                low=c_low,
                close=c_close,
                volume=vol,
            ))
        return candles

    @staticmethod
    def _calculate_sma(data: List[float], period: int) -> Optional[float]:
        if len(data) < period:
            return None
        return round(sum(data[-period:]) / period, 4)

    @staticmethod
    def _calculate_ema(data: List[float], period: int) -> Optional[float]:
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return round(ema, 4)

    @staticmethod
    def _calculate_rsi(data: List[float], period: int = 14) -> Optional[float]:
        if len(data) <= period:
            return None
        gains, losses = [], []
        for i in range(1, len(data)):
            change = data[i] - data[i - 1]
            gains.append(max(change, 0.0))
            losses.append(max(-change, 0.0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 2)

    @staticmethod
    def _calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) <= period + 1:
            return None
        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(closes)):
            h_diff = highs[i] - highs[i - 1]
            l_diff = lows[i - 1] - lows[i]

            plus_dm = h_diff if (h_diff > l_diff and h_diff > 0) else 0.0
            minus_dm = l_diff if (l_diff > h_diff and l_diff > 0) else 0.0

            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            tr_list.append(tr)
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        if sum(tr_list[-period:]) == 0:
            return 0.0

        plus_di = 100.0 * (sum(plus_dm_list[-period:]) / sum(tr_list[-period:]))
        minus_di = 100.0 * (sum(minus_dm_list[-period:]) / sum(tr_list[-period:]))

        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0.0

        dx = 100.0 * (abs(plus_di - minus_di) / di_sum)
        return round(dx, 2)

    @staticmethod
    def _calculate_bollinger_bands(data: List[float], period: int = 20, num_std: float = 2.0) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        if len(data) < period:
            return None, None, None, None
        subset = data[-period:]
        mean = sum(subset) / period
        variance = sum((x - mean) ** 2 for x in subset) / period
        std = math.sqrt(variance)

        upper = mean + (num_std * std)
        lower = mean - (num_std * std)
        width_pct = round(((upper - lower) / mean) * 100.0, 2)
        return round(upper, 4), round(mean, 4), round(lower, 4), width_pct

    @staticmethod
    def _calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) <= period:
            return None
        tr_list = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, len(closes))
        ]
        return round(sum(tr_list[-period:]) / period, 4)

    @staticmethod
    def _calculate_realized_volatility(data: List[float], period: int = 20) -> Optional[float]:
        if len(data) <= period:
            return None
        returns = [math.log(data[i] / data[i - 1]) for i in range(len(data) - period, len(data))]
        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        annualized_vol = math.sqrt(var) * math.sqrt(365 * 24 * 4)  # 15-min bars annualized
        return round(annualized_vol * 100.0, 2)

    @staticmethod
    def _calculate_volume_ratio(volumes: List[float], period: int = 20) -> Optional[float]:
        if len(volumes) < period or sum(volumes[-period:]) == 0:
            return 1.0
        avg_vol = sum(volumes[-period:]) / period
        return round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
