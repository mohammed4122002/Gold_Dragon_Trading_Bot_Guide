"""هيكل السوق: التحيز، BOS/CHoCH، ونطاق Premium/Discount.

البنود 2.أ و 2.ب و 2.ج من Gold Dragon Pro v4.
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd

from .indicators import atr, body_ratio, swing_highs, swing_lows

Direction = Literal["bullish", "bearish", "ranging", "neutral"]


def calculate_bias(df: pd.DataFrame, lookback: int = 2) -> dict[str, Any]:
    """تحديد التحيز من تسلسل HH/HL أو LH/LL.

    الثقة تتدرج: تسلسل من ثلاث قمم/قيعان متوافقة أقوى من اثنتين.
    """
    highs = swing_highs(df, lookback)
    lows = swing_lows(df, lookback)

    if len(highs) < 2 or len(lows) < 2:
        return {"direction": "neutral", "confidence": 0.0,
                "reason": "عدد الـ swings غير كافٍ لتحديد الهيكل",
                "swing_highs": highs, "swing_lows": lows}

    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]

    def extended(seq: list[tuple[int, float]], rising: bool) -> bool:
        if len(seq) < 3:
            return False
        return (seq[-2][1] > seq[-3][1]) if rising else (seq[-2][1] < seq[-3][1])

    if hh and hl:
        confirmed = extended(highs, True) and extended(lows, True)
        return {"direction": "bullish", "confidence": 0.9 if confirmed else 0.7,
                "reason": "HH + HL" + (" مع تسلسل ممتد" if confirmed else ""),
                "swing_highs": highs, "swing_lows": lows}
    if lh and ll:
        confirmed = extended(highs, False) and extended(lows, False)
        return {"direction": "bearish", "confidence": 0.9 if confirmed else 0.7,
                "reason": "LH + LL" + (" مع تسلسل ممتد" if confirmed else ""),
                "swing_highs": highs, "swing_lows": lows}

    return {"direction": "ranging", "confidence": 0.4,
            "reason": "تسلسل متضارب (HH+LL أو LH+HL) → نطاق عرضي",
            "swing_highs": highs, "swing_lows": lows}


def detect_bos(df: pd.DataFrame, lookback: int = 2) -> dict[str, Any] | None:
    """آخر Break of Structure مؤكد بالإغلاق (لا بالظل)."""
    highs = swing_highs(df, lookback)
    lows = swing_lows(df, lookback)
    closes = df["close"].to_numpy()

    latest: dict[str, Any] | None = None
    for idx, level in highs:
        after = closes[idx + lookback + 1:]
        breaks = [i for i, c in enumerate(after) if c > level]
        if breaks:
            bar = idx + lookback + 1 + breaks[0]
            if latest is None or bar > latest["bar"]:
                latest = {"type": "BOS", "direction": "bullish", "level": level, "bar": bar}
    for idx, level in lows:
        after = closes[idx + lookback + 1:]
        breaks = [i for i, c in enumerate(after) if c < level]
        if breaks:
            bar = idx + lookback + 1 + breaks[0]
            if latest is None or bar > latest["bar"]:
                latest = {"type": "BOS", "direction": "bearish", "level": level, "bar": bar}
    return latest


def detect_choch(df: pd.DataFrame, expected: str, lookback: int = 2,
                 window: int = 25) -> dict[str, Any] | None:
    """تغيّر في الشخصية (Change of Character) في اتجاه ``expected``.

    CHoCH صعودي = إغلاق فوق آخر قمة swing بينما الهيكل السابق هابط،
    ويجب أن يحدث ضمن آخر ``window`` شمعة حتى يبقى صالحاً للتريغر.
    """
    if expected not in {"bullish", "bearish"}:
        return None

    closes = df["close"].to_numpy()
    n = len(closes)
    levels = swing_highs(df, lookback) if expected == "bullish" else swing_lows(df, lookback)
    if not levels:
        return None

    for idx, level in reversed(levels):
        start = idx + lookback + 1
        if start >= n:
            continue
        for bar in range(start, n):
            broke = closes[bar] > level if expected == "bullish" else closes[bar] < level
            if broke:
                if n - bar > window:
                    return None  # كسر قديم — لا يصلح كتريغر حالي
                return {
                    "type": "CHoCH", "direction": expected, "level": float(level),
                    "bar": bar, "bars_ago": n - 1 - bar,
                    "strength": body_ratio(df.iloc[bar]),
                }
    return None


def has_displacement(df: pd.DataFrame, direction: str, atr_period: int = 14,
                     multiple: float = 1.5, window: int = 5) -> dict[str, Any]:
    """إزاحة قوية: شمعة جسمها > ``multiple`` × ATR في الاتجاه المطلوب.

    البند 2.ب/1 — قوة الإزاحة تُقاس بالجسم لا بالمدى الكامل، لأن الظل
    الطويل علامة رفض لا علامة اندفاع.
    """
    reference = atr(df, atr_period)
    if reference <= 0 or len(df) < 2:
        return {"found": False, "strength": 0.0, "bar": None}

    recent = df.iloc[-window:]
    best: dict[str, Any] = {"found": False, "strength": 0.0, "bar": None}
    for pos in range(len(recent)):
        candle = recent.iloc[pos]
        move = float(candle["close"]) - float(candle["open"])
        aligned = move > 0 if direction == "bullish" else move < 0
        if not aligned:
            continue
        strength = abs(move) / reference
        if strength >= multiple and strength > best["strength"]:
            best = {
                "found": True,
                "strength": strength,
                "bar": len(df) - len(recent) + pos,
                "body_ratio": body_ratio(candle),
                "atr": reference,
            }
    return best


def premium_discount(df: pd.DataFrame, lookback: int = 2) -> dict[str, Any]:
    """تقسيم آخر Swing كامل إلى Premium / Discount / Equilibrium (البند 2.أ/3)."""
    highs = swing_highs(df, lookback)
    lows = swing_lows(df, lookback)
    if not highs or not lows:
        swing_high = float(df["high"].max())
        swing_low = float(df["low"].min())
    else:
        swing_high = max(h for _, h in highs[-3:])
        swing_low = min(low for _, low in lows[-3:])

    rng = swing_high - swing_low
    price = float(df["close"].iloc[-1])
    if rng <= 0:
        return {"zone": "unknown", "position": 0.5, "equilibrium": price,
                "high": swing_high, "low": swing_low}

    position = (price - swing_low) / rng
    if 0.45 <= position <= 0.55:
        zone = "equilibrium"
    elif position > 0.55:
        zone = "premium"
    else:
        zone = "discount"

    return {
        "zone": zone,
        "position": position,
        "equilibrium": swing_low + rng * 0.5,
        "high": swing_high,
        "low": swing_low,
    }


def zone_allows(zone: str, direction: str) -> bool:
    """شراء من Discount وبيع من Premium فقط (البند 3.4/5)."""
    if direction in {"buy", "bullish"}:
        return zone == "discount"
    if direction in {"sell", "bearish"}:
        return zone == "premium"
    return False
