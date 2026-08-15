"""اختبارات محرك الـ Backtest."""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest import Backtester
from tests.conftest import bullish_sweep_scenario, legs_bars


@pytest.fixture
def long_data():
    """بيانات كافية للتشغيل: مسار متعرّج واسع على M15 مع H1 مقابل."""
    points = [2400.0, 2440.0, 2380.0, 2450.0, 2370.0, 2430.0, 2390.0, 2440.0]
    m15 = legs_bars(points, bars_per_leg=60, minutes=15)
    h1 = legs_bars(points, bars_per_leg=20, minutes=60)
    return m15, h1


def test_backtest_runs_without_errors(config, long_data):
    m15, h1 = long_data
    result = Backtester(config, balance=10_000.0).run(m15, h1, warmup=120, window=200)

    assert result.initial_balance == 10_000.0
    assert isinstance(result.stats(), dict)
    assert len(result.equity_curve) > 0


def test_backtest_never_touches_live_risk_state(config, long_data, tmp_path):
    """حالة المخاطرة في الـ Backtest معزولة عن ملف التشغيل الحي."""
    m15, h1 = long_data
    tester = Backtester(config, balance=10_000.0)
    assert "backtest" in str(tester.risk.state_path)
    tester.run(m15, h1, warmup=120, window=200)


def test_backtest_respects_risk_limits(config, long_data):
    m15, h1 = long_data
    result = Backtester(config, balance=10_000.0).run(m15, h1, warmup=120, window=200)

    for trade in result.trades:
        risk = abs(trade["entry"] - trade["sl"]) * trade["initial_volume"] * 100
        # لا صفقة تتجاوز 2% من الرصيد الافتتاحي
        assert risk <= 10_000.0 * 0.02 * 1.1


def test_stats_report_is_readable(config, long_data):
    m15, h1 = long_data
    result = Backtester(config, balance=10_000.0).run(m15, h1, warmup=120, window=200)
    text = result.format()
    assert isinstance(text, str) and len(text) > 10


def test_rejections_are_counted(config):
    """بيانات هادئة بلا سويپ → لا صفقات، مع أسباب رفض مُحصاة."""
    flat = legs_bars([2400.0, 2401.0, 2400.0, 2401.0], bars_per_leg=120, minutes=15)
    h1 = legs_bars([2400.0, 2401.0, 2400.0, 2401.0], bars_per_leg=40, minutes=60)
    result = Backtester(config, balance=10_000.0).run(flat, h1, warmup=100, window=200)

    assert result.stats()["trades"] == 0
    assert sum(result.rejections.values()) > 0


def test_scenario_data_shape():
    m15, h1 = bullish_sweep_scenario()
    for frame in (m15, h1):
        assert list(frame.columns) == ["time", "open", "high", "low", "close", "volume"]
        assert pd.api.types.is_datetime64_any_dtype(frame["time"])
        assert (frame["high"] >= frame["low"]).all()


def test_backtest_executes_trades_on_valid_setups(config):
    """تكرار سيناريو مكتمل الأركان يجب أن يُنتج صفقات فعلية.

    الغرض ليس تقييم الربحية (البيانات مصطنعة) بل إثبات أن مسار
    التحليل → المخاطرة → التنفيذ سالك داخل الـ Backtest.
    """
    from datetime import timedelta

    import pandas as pd

    from tests.conftest import START

    m15, h1 = bullish_sweep_scenario()
    frames, cursor = [], START
    for _ in range(12):
        frame = m15.copy()
        frame["time"] = [cursor + timedelta(minutes=15 * j) for j in range(len(frame))]
        frames.append(frame)
        cursor = frame["time"].iloc[-1] + timedelta(minutes=15)
    repeated = pd.concat(frames, ignore_index=True)

    result = Backtester(config, balance=10_000.0).run(repeated, h1, warmup=40, window=200)
    assert result.stats()["trades"] > 0
    assert result.final_balance != result.initial_balance
