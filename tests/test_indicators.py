"""اختبارات المؤشرات وهيكل السوق."""

from __future__ import annotations

import pytest

from src.core.indicators import atr, body_ratio, swing_highs
from src.core.structure import calculate_bias, detect_choch, has_displacement, premium_discount
from tests.conftest import make_bars, trend_bars


def test_atr_on_constant_range_bars():
    rows = [(100.0, 102.0, 98.0, 100.0)] * 30
    assert atr(make_bars(rows), 14) == pytest.approx(4.0, abs=0.01)


def test_swing_detection_finds_the_peak():
    rows = [
        (10, 11, 9, 10), (11, 12, 10, 11), (12, 15, 11, 14),  # قمة عند 15
        (14, 13, 11, 12), (12, 12, 10, 11), (11, 11, 9, 10),
    ]
    highs = swing_highs(make_bars([(float(a), float(b), float(c), float(d))
                                   for a, b, c, d in rows]), lookback=2)
    assert highs and highs[0][1] == 15.0


def test_bias_bullish_on_rising_structure():
    bias = calculate_bias(trend_bars(60, step=2.0))
    assert bias["direction"] == "bullish"
    assert bias["confidence"] >= 0.7


def test_bias_bearish_on_falling_structure():
    bias = calculate_bias(trend_bars(60, step=-2.0))
    assert bias["direction"] == "bearish"


def test_body_ratio_of_doji_is_small():
    doji = make_bars([(100.0, 105.0, 95.0, 100.2)]).iloc[0]
    assert body_ratio(doji) < 0.05


def test_displacement_requires_body_beyond_atr_multiple():
    rows = [(100.0, 100.5, 99.5, 100.0)] * 25          # هدوء: ATR ≈ 1
    rows.append((100.0, 106.0, 99.9, 105.5))            # اندفاع صعودي
    df = make_bars(rows)

    strong = has_displacement(df, "bullish", multiple=1.5)
    assert strong["found"] and strong["strength"] > 3

    # نفس الشمعة لا تُعد إزاحة هبوطية
    assert not has_displacement(df, "bearish", multiple=1.5)["found"]


def test_premium_discount_zones():
    df = trend_bars(60, start_price=2000.0, step=5.0)
    zone = premium_discount(df)
    # السعر عند قمة الحركة → منطقة Premium
    assert zone["zone"] == "premium"
    assert zone["low"] < zone["equilibrium"] < zone["high"]


def test_choch_detects_break_of_last_swing_high():
    rows = [(100.0, 101.0, 99.0, 100.0)] * 3
    rows += [(100.0, 104.0, 99.5, 100.5)]      # قمة swing عند 104
    rows += [(100.0, 101.0, 98.0, 99.0)] * 4   # هبوط
    rows += [(99.0, 106.0, 98.5, 105.5)]       # إغلاق فوق القمة → CHoCH صعودي
    df = make_bars([(float(a), float(b), float(c), float(d)) for a, b, c, d in rows])

    choch = detect_choch(df, "bullish", lookback=2)
    assert choch is not None
    assert choch["direction"] == "bullish"
    assert choch["level"] == pytest.approx(104.0)


def test_choch_ignores_stale_break():
    rows = [(100.0, 104.0, 99.0, 100.0)]
    rows += [(100.0, 101.0, 98.0, 99.0)] * 3
    rows += [(99.0, 106.0, 98.5, 105.5)]       # الكسر يحدث مبكراً
    rows += [(105.0, 105.5, 104.5, 105.0)] * 40  # ثم يتقادم
    df = make_bars([(float(a), float(b), float(c), float(d)) for a, b, c, d in rows])

    assert detect_choch(df, "bullish", lookback=2, window=10) is None
