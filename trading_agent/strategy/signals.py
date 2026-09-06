"""
Deterministic strategy signals and regime gate for Iron Condor trading.
Contains NO LLM calls and NO network calls.
"""

from typing import Dict, List, Optional, Any, Union
import numpy as np
import pandas as pd

from trading_agent.models import OptionChainSnapshot, OptionContract, MarketSnapshot


def _to_dataframe(bars: Union[pd.DataFrame, List[Dict[str, Any]], List[Any]]) -> pd.DataFrame:
    """Helper to convert various bar representations to a pandas DataFrame with low, high, close columns."""
    if isinstance(bars, pd.DataFrame):
        df = bars.copy()
    elif isinstance(bars, list):
        if not bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        if isinstance(bars[0], dict):
            df = pd.DataFrame(bars)
        else:
            # Handle object instances
            records = []
            for b in bars:
                records.append({
                    "open": getattr(b, "open", 0.0),
                    "high": getattr(b, "high", 0.0),
                    "low": getattr(b, "low", 0.0),
                    "close": getattr(b, "close", 0.0),
                    "volume": getattr(b, "volume", 0),
                })
            df = pd.DataFrame(records)
    else:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # Ensure required lower-case column names exist
    col_map = {col: col.lower() for col in df.columns}
    df.rename(columns=col_map, inplace=True)
    return df


def calculate_adx(
    bars: Union[pd.DataFrame, List[Dict[str, Any]]], period: int = 14
) -> Optional[float]:
    """Calculate Average Directional Index (ADX) over price bars using Wilder's smoothing.
    
    Returns None if there are insufficient price bars.
    """
    df = _to_dataframe(bars)
    if len(df) < period * 2 or not {"high", "low", "close"}.issubset(df.columns):
        return None

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    # 1. True Range (TR)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # 2. Directional Movement (+DM, -DM)
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Convert to Series for rolling / ewm calculations
    plus_dm_s = pd.Series(plus_dm, index=df.index)
    minus_dm_s = pd.Series(minus_dm, index=df.index)

    # 3. Wilder's Smoothing
    alpha = 1.0 / period
    tr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth = plus_dm_s.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = minus_dm_s.ewm(alpha=alpha, adjust=False).mean()

    # 4. Directional Indicators (+DI, -DI)
    # Avoid zero division
    tr_smooth_safe = np.where(tr_smooth == 0, np.nan, tr_smooth)
    plus_di = 100.0 * (plus_dm_smooth / tr_smooth_safe)
    minus_di = 100.0 * (minus_dm_smooth / tr_smooth_safe)

    # 5. Directional Index (DX) & ADX
    di_sum = plus_di + minus_di
    di_sum_safe = np.where(di_sum == 0, np.nan, di_sum)
    dx = 100.0 * ((plus_di - minus_di).abs() / di_sum_safe)

    adx_series = dx.ewm(alpha=alpha, adjust=False).mean()

    last_val = adx_series.iloc[-1]
    if pd.isna(last_val):
        return None
    return float(last_val)


def is_range_bound(
    bars: Union[pd.DataFrame, List[Dict[str, Any]]],
    adx_threshold: float = 20.0,
    period: int = 14,
) -> bool:
    """Check if market is range-bound based on ADX indicator being below threshold."""
    adx = calculate_adx(bars, period=period)
    if adx is None:
        return False
    return adx < adx_threshold


def calculate_bollinger_width(
    bars: Union[pd.DataFrame, List[Dict[str, Any]]],
    period: int = 20,
    num_std: float = 2.0,
) -> Optional[float]:
    """Calculate normalized Bollinger Bandwidth: (Upper - Lower) / SMA."""
    df = _to_dataframe(bars)
    if len(df) < period or "close" not in df.columns:
        return None

    close = df["close"].astype(float)
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    upper = sma + (num_std * std)
    lower = sma - (num_std * std)

    sma_safe = np.where(sma == 0, np.nan, sma)
    width_series = (upper - lower) / sma_safe

    last_val = width_series.iloc[-1]
    if pd.isna(last_val):
        return None
    return float(last_val)


def bollinger_width_percentile(
    bars: Union[pd.DataFrame, List[Dict[str, Any]]],
    lookback: int = 90,
    period: int = 20,
) -> Optional[float]:
    """Calculate percentile rank of current Bollinger Bandwidth relative to historical lookback window."""
    df = _to_dataframe(bars)
    if len(df) < period or "close" not in df.columns:
        return None

    close = df["close"].astype(float)
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    upper = sma + (2.0 * std)
    lower = sma - (2.0 * std)

    sma_safe = np.where(sma == 0, np.nan, sma)
    width_series = ((upper - lower) / sma_safe).dropna()

    if len(width_series) < 5:
        return None

    history = width_series.tail(lookback)
    current_val = history.iloc[-1]

    count_less = (history <= current_val).sum()
    percentile = (count_less / len(history)) * 100.0
    return float(percentile)


def extract_atm_iv(
    option_chain: Union[OptionChainSnapshot, Dict[str, Any], List[Any]],
    underlying_price: float,
) -> Optional[float]:
    """Extract Average Implied Volatility for Near-The-Money (ATM) option contracts.
    
    Handles missing IV gracefully. Ignores zero or None IV values.
    """
    if underlying_price <= 0:
        return None

    contracts = []
    if isinstance(option_chain, OptionChainSnapshot):
        contracts = option_chain.contracts
    elif isinstance(option_chain, dict):
        contracts = option_chain.get("contracts", [])
    elif isinstance(option_chain, list):
        contracts = option_chain

    if not contracts:
        return None

    # Filter contracts with valid, non-zero implied volatility
    valid_contracts = []
    for c in contracts:
        iv = getattr(c, "implied_volatility", None) if not isinstance(c, dict) else c.get("implied_volatility")
        strike = getattr(c, "strike_price", None) if not isinstance(c, dict) else c.get("strike_price")
        if iv is not None and iv > 0 and strike is not None and strike > 0:
            valid_contracts.append((strike, float(iv)))

    if not valid_contracts:
        return None

    # Find minimum distance to underlying price
    distances = [abs(strike - underlying_price) for strike, iv in valid_contracts]
    min_dist = min(distances)

    # Select contracts close to ATM (within min_dist + 1% of underlying price)
    threshold = max(min_dist, underlying_price * 0.02)
    atm_ivs = [iv for strike, iv in valid_contracts if abs(strike - underlying_price) <= threshold]

    if not atm_ivs:
        return None

    return float(np.mean(atm_ivs))


def calculate_iv_rank(
    current_atm_iv: Optional[float],
    historical_iv: Optional[List[float]],
    min_required_days: int = 15,
) -> Optional[float]:
    """Calculate IV Rank relative to historical daily IV series: (Current - Min) / (Max - Min) * 100.
    
    Requirements:
    - Returns 0.0 to 100.0 when sufficient distinct trading days exist.
    - If fewer than min_required_days exist, returns None (explicit unavailable state).
    - Ignores None, NaN, and non-positive IV values.
    - Handles zero historical IV range (max == min) safely by returning 50.0.
    """
    if current_atm_iv is None or current_atm_iv <= 0:
        return None

    if not historical_iv:
        return None

    clean_ivs = [
        float(iv) for iv in historical_iv
        if iv is not None and not (isinstance(iv, float) and np.isnan(iv)) and float(iv) > 0
    ]

    if len(clean_ivs) < min_required_days:
        return None

    min_iv = min(clean_ivs)
    max_iv = max(clean_ivs)

    if max_iv == min_iv:
        # Zero historical IV range: return neutral 50.0 rank
        return 50.0

    rank = ((current_atm_iv - min_iv) / (max_iv - min_iv)) * 100.0
    return float(round(np.clip(rank, 0.0, 100.0), 6))


def regime_and_volatility_gate(
    bars: Union[pd.DataFrame, List[Dict[str, Any]]],
    option_chain: Union[OptionChainSnapshot, Dict[str, Any], List[Any]],
    underlying_price: float,
    iv_history: Optional[List[float]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate market regime and volatility environment for Iron Condor eligibility.
    
    Returns structured result dict:
    {
        "eligible": bool,
        "adx": float | None,
        "bb_width_percentile": float | None,
        "current_atm_iv": float | None,
        "iv_rank": float | None,
        "iv_history_status": dict,
        "reasons": list[str]
    }
    """
    cfg = config or {}
    adx_threshold = cfg.get("adx_threshold", cfg.get("ADX_THRESHOLD", 25.0))
    min_iv_rank = cfg.get("min_iv_rank", cfg.get("MIN_IV_RANK", 30.0))
    min_iv_points = cfg.get("min_historical_iv_points", cfg.get("MIN_IV_HISTORY_DAYS", 15))

    reasons = []

    # 1. ADX calculation & range-bound check
    adx = calculate_adx(bars)
    if adx is None:
        reasons.append("Insufficient price bar history to calculate ADX.")
    elif adx > adx_threshold:
        reasons.append(f"Market is trending (ADX {adx:.1f} > threshold {adx_threshold:.1f}). Not range-bound.")

    # 2. Bollinger Bandwidth Percentile
    bb_width_pct = bollinger_width_percentile(bars)

    # 3. ATM IV extraction
    current_atm_iv = extract_atm_iv(option_chain, underlying_price)
    if current_atm_iv is None:
        reasons.append("Failed to extract valid ATM option implied volatility.")

    # 4. Clean historical IV series & calculate IV Rank
    clean_history = [
        float(iv) for iv in (iv_history or [])
        if iv is not None and not (isinstance(iv, float) and np.isnan(iv)) and float(iv) > 0
    ]
    history_days = len(clean_history)
    iv_status = "READY" if history_days >= min_iv_points else "INSUFFICIENT_HISTORY"

    iv_rank = None
    if current_atm_iv is not None:
        if history_days < min_iv_points:
            reasons.append(f"Insufficient historical IV data ({history_days} < {min_iv_points} points).")
        else:
            iv_rank = calculate_iv_rank(current_atm_iv, clean_history, min_required_days=min_iv_points)
            if iv_rank is None:
                reasons.append("Failed to compute IV rank from historical IV data.")
            elif iv_rank < min_iv_rank:
                reasons.append(f"IV rank ({iv_rank:.1f}%) is below minimum required threshold ({min_iv_rank:.1f}%).")

    eligible = len(reasons) == 0

    return {
        "eligible": eligible,
        "adx": round(adx, 2) if adx is not None else None,
        "bb_width_percentile": round(bb_width_pct, 2) if bb_width_pct is not None else None,
        "current_atm_iv": round(current_atm_iv, 4) if current_atm_iv is not None else None,
        "iv_rank": round(iv_rank, 2) if iv_rank is not None else None,
        "iv_history_status": {
            "history_days": history_days,
            "minimum_required": min_iv_points,
            "status": iv_status,
            "current_atm_iv": round(current_atm_iv, 4) if current_atm_iv is not None else None,
            "iv_rank": round(iv_rank, 2) if iv_rank is not None else None,
        },
        "reasons": reasons,
    }


class SignalGenerator:
    """Wrapper class integrating deterministic signal calculations."""

    def __init__(self, adx_threshold: float = 25.0, min_iv_rank: float = 30.0):
        self.adx_threshold = adx_threshold
        self.min_iv_rank = min_iv_rank

    def calculate_indicators(self, snapshot: MarketSnapshot) -> Dict[str, Any]:
        """Calculate technical indicators from MarketSnapshot."""
        bars = getattr(snapshot, "bars", [])
        chain = snapshot.option_chain
        price = snapshot.underlying_price
        iv_hist = getattr(snapshot, "iv_history", [0.20] * 30)

        config = {
            "adx_threshold": self.adx_threshold,
            "min_iv_rank": self.min_iv_rank,
        }

        return regime_and_volatility_gate(
            bars=bars,
            option_chain=chain or [],
            underlying_price=price,
            iv_history=iv_hist,
            config=config,
        )
