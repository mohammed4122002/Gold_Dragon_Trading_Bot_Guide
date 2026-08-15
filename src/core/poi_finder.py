"""إيجاد مناطق الاهتمام المعتمدة — Order Blocks و FVG (البند 5)."""

from __future__ import annotations

import pandas as pd

from ..models import POI
from .indicators import atr, body_ratio, is_bullish
from .structure import premium_discount


def find_fvgs(df: pd.DataFrame, direction: str, timeframe: str,
              min_size_atr: float = 0.15, max_age: int = 120) -> list[POI]:
    """فجوات القيمة العادلة: فجوة ثلاثية الشموع بين ظل الأولى وظل الثالثة."""
    reference = atr(df) or 1.0
    min_size = reference * min_size_atr
    out: list[POI] = []
    start = max(2, len(df) - max_age)

    for i in range(start, len(df)):
        first, third = df.iloc[i - 2], df.iloc[i]
        if direction == "bullish":
            gap_bottom, gap_top = float(first["high"]), float(third["low"])
        else:
            gap_bottom, gap_top = float(third["high"]), float(first["low"])

        size = gap_top - gap_bottom
        if size < min_size:
            continue

        middle = df.iloc[i - 1]
        aligned = is_bullish(middle) if direction == "bullish" else not is_bullish(middle)
        if not aligned or body_ratio(middle) < 0.5:
            continue  # الفجوة بلا شمعة إزاحة وسطية قوية لا تُعتد

        poi = POI(
            type="FVG",
            direction="bullish" if direction == "bullish" else "bearish",
            top=gap_top, bottom=gap_bottom, timeframe=timeframe, bar_index=i,
            confidence=0.7,
            reason=f"FVG {direction} بحجم {size:.2f}$ بعد إزاحة",
            meta={"size": size, "size_atr": size / reference},
        )
        poi.mitigations = _count_mitigations(df, poi, from_bar=i + 1)
        out.append(poi)

    return out


def find_order_blocks(df: pd.DataFrame, direction: str, timeframe: str,
                      displacement_atr: float = 1.5, max_age: int = 120) -> list[POI]:
    """Order Block: آخر شمعة عكسية قبل إزاحة قوية في اتجاه التحيز."""
    reference = atr(df) or 1.0
    out: list[POI] = []
    start = max(1, len(df) - max_age)

    for i in range(start, len(df)):
        candle = df.iloc[i]
        move = float(candle["close"]) - float(candle["open"])
        aligned = move > 0 if direction == "bullish" else move < 0
        if not aligned or abs(move) < reference * displacement_atr:
            continue

        # العودة للخلف بحثاً عن آخر شمعة معاكسة قبل الإزاحة
        origin = None
        for j in range(i - 1, max(i - 6, -1), -1):
            prev = df.iloc[j]
            opposite = not is_bullish(prev) if direction == "bullish" else is_bullish(prev)
            if opposite:
                origin = (j, prev)
                break
        if origin is None:
            continue

        idx, ob_candle = origin
        poi = POI(
            type="OB",
            direction="bullish" if direction == "bullish" else "bearish",
            top=float(ob_candle["high"]), bottom=float(ob_candle["low"]),
            timeframe=timeframe, bar_index=idx,
            confidence=0.8,
            reason=f"OB {direction} قبل إزاحة {abs(move) / reference:.1f}×ATR",
            meta={"displacement_atr": abs(move) / reference},
        )
        poi.mitigations = _count_mitigations(df, poi, from_bar=i + 1)
        out.append(poi)

    return out


def _count_mitigations(df: pd.DataFrame, poi: POI, from_bar: int) -> int:
    """عدد المرات التي عاد فيها السعر إلى المنطقة بعد تكوّنها.

    لمسات متتالية داخل المنطقة تُحسب اختباراً واحداً — الاختبار الجديد
    لا يُحتسب إلا بعد خروج السعر من المنطقة أولاً.
    """
    touches, inside = 0, False
    for i in range(from_bar, len(df)):
        candle = df.iloc[i]
        overlaps = float(candle["low"]) <= poi.top and float(candle["high"]) >= poi.bottom
        if overlaps and not inside:
            touches += 1
            inside = True
        elif not overlaps:
            inside = False
    return touches


def approve_pois(pois: list[POI], df: pd.DataFrame, price: float, direction: str,
                 max_distance: float = 12.0, lookback: int = 2) -> list[POI]:
    """تطبيق معايير القبول الصارمة (البند 5.1).

    يُرفض ما هو: مُستنفَد، خارج المنطقة الصحيحة (Premium/Discount)، بعيد
    أكثر من اللازم، أو خلف السعر أصلاً.
    """
    zone_info = premium_discount(df, lookback)
    approved: list[POI] = []

    for poi in pois:
        if poi.is_exhausted:
            continue

        # POI الشراء يجب أن تكون تحت السعر (والعكس) — وإلا فقد فات أوانها
        if direction == "bullish" and poi.top > price:
            continue
        if direction == "bearish" and poi.bottom < price:
            continue

        if poi.distance_from(price) > max_distance:
            continue

        # موقع المنطقة نفسها داخل آخر Swing كامل، لا موقع السعر الحالي
        span = zone_info["high"] - zone_info["low"]
        if span > 0:
            poi_position = (poi.mid - zone_info["low"]) / span
            if direction == "bullish" and poi_position > 0.55:
                continue
            if direction == "bearish" and poi_position < 0.45:
                continue
            poi.meta["zone_position"] = poi_position

        if poi.mitigations == 1:
            poi.confidence *= 0.7  # Partially Mitigated → ثقة 70%
            poi.reason += " (اختُبرت مرة واحدة)"

        poi.meta["distance"] = poi.distance_from(price)
        approved.append(poi)

    # الأقرب للسعر والأعلى ثقة أولاً
    approved.sort(key=lambda p: (-p.confidence, p.distance_from(price)))
    return approved


def find_all_pois(df_h1: pd.DataFrame, df_m15: pd.DataFrame, direction: str,
                  price: float, max_distance: float = 12.0,
                  lookback: int = 2, displacement_atr: float = 1.5) -> list[POI]:
    """جمع مناطق H1 و M15 وترتيبها حسب الأولوية (H1 يتقدم على M15)."""
    if direction not in {"bullish", "bearish"}:
        return []

    candidates: list[POI] = []
    candidates += find_order_blocks(df_h1, direction, "H1", displacement_atr)
    candidates += find_fvgs(df_h1, direction, "H1")
    candidates += find_order_blocks(df_m15, direction, "M15", displacement_atr)
    candidates += find_fvgs(df_m15, direction, "M15")

    approved = approve_pois(candidates, df_h1, price, direction, max_distance, lookback)
    weight = {"H1": 0, "M15": 1}
    approved.sort(key=lambda p: (weight.get(p.timeframe, 2), -p.confidence))
    return approved
