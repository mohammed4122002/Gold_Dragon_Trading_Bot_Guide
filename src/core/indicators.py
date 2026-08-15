"""مؤشرات أساسية — ATR، الفراكتلات، وقياسات الشمعة."""

from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """متوسط المدى الحقيقي (Wilder) لآخر شمعة."""
    if len(df) < period + 1:
        return float((df["high"] - df["low"]).mean() or 0.0)

    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    prev_close = df["close"].shift(1).to_numpy()
    true_range = np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
    )
    true_range = true_range[1:]  # أول قيمة بلا إغلاق سابق
    return float(pd.Series(true_range).ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """سلسلة ATR كاملة — تُستخدم في الـ Backtest حيث نحتاج قيمة لكل شمعة."""
    high, low = df["high"], df["low"]
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean().bfill()


def swing_highs(df: pd.DataFrame, lookback: int = 2) -> list[tuple[int, float]]:
    """قمم فراكتلية: شمعة أعلى من ``lookback`` شمعة على كل جانب."""
    highs = df["high"].to_numpy()
    out: list[tuple[int, float]] = []
    for i in range(lookback, len(highs) - lookback):
        window = highs[i - lookback: i + lookback + 1]
        if highs[i] == window.max() and (window[:lookback] < highs[i]).all():
            out.append((i, float(highs[i])))
    return out


def swing_lows(df: pd.DataFrame, lookback: int = 2) -> list[tuple[int, float]]:
    """قيعان فراكتلية."""
    lows = df["low"].to_numpy()
    out: list[tuple[int, float]] = []
    for i in range(lookback, len(lows) - lookback):
        window = lows[i - lookback: i + lookback + 1]
        if lows[i] == window.min() and (window[:lookback] > lows[i]).all():
            out.append((i, float(lows[i])))
    return out


def body(candle: pd.Series) -> float:
    return abs(float(candle["close"]) - float(candle["open"]))


def candle_range(candle: pd.Series) -> float:
    return float(candle["high"]) - float(candle["low"])


def body_ratio(candle: pd.Series) -> float:
    """نسبة الجسم إلى المدى الكامل — مقياس قوة الشمعة (البند 5.1/2)."""
    rng = candle_range(candle)
    return body(candle) / rng if rng > 0 else 0.0


def is_bullish(candle: pd.Series) -> bool:
    return float(candle["close"]) > float(candle["open"])


def relative_volume(df: pd.DataFrame, lookback: int = 20) -> float:
    """حجم الشمعة الأخيرة نسبة إلى متوسط آخر ``lookback`` شمعة."""
    if "volume" not in df.columns or len(df) < lookback + 1:
        return 1.0
    avg = float(df["volume"].iloc[-lookback - 1: -1].mean())
    return float(df["volume"].iloc[-1]) / avg if avg > 0 else 1.0
