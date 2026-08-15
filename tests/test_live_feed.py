"""اختبارات مصادر البيانات الحية.

الاختبارات تعمل على حمولات JSON مسجّلة (نفس شكل استجابة المزوّد) بدل
الاتصال بالشبكة: لا تعتمد CI على توفّر خدمة خارجية، ولا يتغير الاختبار
بتغيّر السوق.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from src.infra.live_feed import LiveFeedError, TwelveDataFeed, YahooFeed, resample


def _yahoo_payload(bars: int = 120, start_price: float = 2400.0,
                   step_minutes: int = 15) -> dict:
    """استجابة Yahoo مصغّرة بنفس بنية الحقول الحقيقية."""
    start = int((datetime.now(timezone.utc) - timedelta(minutes=step_minutes * bars))
                .timestamp())
    stamps = [start + i * step_minutes * 60 for i in range(bars)]
    closes = [start_price + i * 0.5 for i in range(bars)]
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "XAUUSD=X"},
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [c - 0.3 for c in closes],
                                "high": [c + 1.0 for c in closes],
                                "low": [c - 1.0 for c in closes],
                                "close": closes,
                                "volume": [100] * bars,
                            }
                        ]
                    },
                }
            ],
        }
    }


def _twelve_payload(bars: int = 120, start_price: float = 2400.0) -> dict:
    now = datetime.now(timezone.utc)
    values = []
    for i in range(bars):  # Twelve Data يعيد الأحدث أولاً
        close = start_price + (bars - i) * 0.5
        values.append(
            {
                "datetime": (now - timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
                "open": f"{close - 0.3:.2f}", "high": f"{close + 1:.2f}",
                "low": f"{close - 1:.2f}", "close": f"{close:.2f}", "volume": "100",
            }
        )
    return {"status": "ok", "values": values}


@pytest.fixture
def yahoo(monkeypatch):
    calls: list[str] = []

    def fake_get(url, params=None, retries=2):
        calls.append(url)
        return _yahoo_payload()

    monkeypatch.setattr("src.infra.live_feed._http_get_json", fake_get)
    feed = YahooFeed()
    return feed, calls


# ═══ Yahoo ═════════════════════════════════════════════════════════
def test_yahoo_returns_valid_frame(yahoo):
    feed, _ = yahoo
    frame = feed.get_bars("M15", 100)

    assert list(frame.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(frame) == 100
    assert (frame["high"] >= frame["low"]).all()
    assert frame["time"].is_monotonic_increasing


def test_forming_candle_is_dropped(yahoo):
    """الشمعة الجارية تتغير باستمرار — قرار مبني عليها قرار متبدّل."""
    feed, _ = yahoo
    payload = _yahoo_payload(bars=50)
    last_close = payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][-1]

    frame = feed.get_bars("M15", 50)
    assert float(frame["close"].iloc[-1]) != pytest.approx(last_close)


def test_cache_prevents_repeated_requests(yahoo):
    feed, calls = yahoo
    feed.get_bars("M15", 100)
    feed.get_bars("M15", 100)
    feed.get_bars("M15", 100)
    assert len(calls) == 1, "الذاكرة المؤقتة لم تمنع الطلبات المكررة"


def test_cache_expires(yahoo, monkeypatch):
    feed, calls = yahoo
    feed.ttl["M15"] = 0
    feed.get_bars("M15", 100)
    time.sleep(0.01)
    feed.get_bars("M15", 100)
    assert len(calls) == 2


def test_yahoo_error_is_wrapped(monkeypatch):
    monkeypatch.setattr(
        "src.infra.live_feed._http_get_json",
        lambda url, params=None, retries=2: {"chart": {"error": {"code": "Not Found"}}},
    )
    with pytest.raises(LiveFeedError, match="رفض"):
        YahooFeed().get_bars("M15", 100)


def test_empty_response_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "src.infra.live_feed._http_get_json",
        lambda url, params=None, retries=2: {"chart": {"result": []}},
    )
    with pytest.raises(LiveFeedError):
        YahooFeed().get_bars("M15", 100)


def test_unsupported_timeframe(yahoo):
    feed, _ = yahoo
    with pytest.raises(LiveFeedError, match="غير مدعوم"):
        feed.get_bars("W1", 100)


def test_h4_is_aggregated_from_h1(monkeypatch):
    monkeypatch.setattr(
        "src.infra.live_feed._http_get_json",
        lambda url, params=None, retries=2: _yahoo_payload(bars=400, step_minutes=60),
    )
    frame = YahooFeed().get_bars("H4", 80)
    gaps = frame["time"].diff().dropna().dt.total_seconds().unique()
    assert set(gaps) == {4 * 3600}, "التجميع لم يُنتج شموع أربع ساعات"


def test_correlation_data_shape(yahoo):
    feed, _ = yahoo
    data = feed.get_correlation_data()
    assert "DXY" in data and "US10Y" in data
    for asset in data.values():
        assert {"price", "change_pct", "trend"} <= asset.keys()
        assert asset["trend"] in {"up", "down", "neutral"}


def test_correlation_skips_failing_symbol(monkeypatch):
    def flaky(url, params=None, retries=2):
        if "TNX" in url:
            raise LiveFeedError("رمز غير متاح")
        return _yahoo_payload(bars=30, step_minutes=60)

    monkeypatch.setattr("src.infra.live_feed._http_get_json", flaky)
    data = YahooFeed().get_correlation_data()
    assert "US10Y" not in data and "DXY" in data  # عطل رمز لا يُسقط البوابة


# ═══ Twelve Data ═══════════════════════════════════════════════════
def test_twelvedata_parses_and_sorts(monkeypatch):
    monkeypatch.setattr(
        "src.infra.live_feed._http_get_json",
        lambda url, params=None, retries=2: _twelve_payload(),
    )
    frame = TwelveDataFeed(api_key="test").get_bars("M15", 100)
    assert frame["time"].is_monotonic_increasing
    assert frame["close"].dtype.kind == "f"


def test_twelvedata_requires_key():
    with pytest.raises(LiveFeedError, match="مفتاح"):
        TwelveDataFeed(api_key="")


def test_twelvedata_api_error(monkeypatch):
    monkeypatch.setattr(
        "src.infra.live_feed._http_get_json",
        lambda url, params=None, retries=2: {"status": "error", "message": "limit reached"},
    )
    with pytest.raises(LiveFeedError, match="limit reached"):
        TwelveDataFeed(api_key="test").get_bars("M15", 100)


# ═══ أدوات ═════════════════════════════════════════════════════════
def test_resample_aggregates_ohlc():
    import pandas as pd

    times = [datetime(2026, 3, 2, 8, tzinfo=timezone.utc) + timedelta(hours=i)
             for i in range(8)]
    frame = pd.DataFrame({
        "time": times,
        "open": [1, 2, 3, 4, 5, 6, 7, 8], "high": [2, 3, 4, 5, 6, 7, 8, 9],
        "low": [0, 1, 2, 3, 4, 5, 6, 7], "close": [2, 3, 4, 5, 6, 7, 8, 9],
        "volume": [10] * 8,
    })
    out = resample(frame, "4h")

    assert len(out) == 2
    assert out["open"].iloc[0] == 1 and out["close"].iloc[0] == 5
    assert out["high"].iloc[0] == 5 and out["low"].iloc[0] == 0
    assert out["volume"].iloc[0] == 40


# ═══ التكامل مع مصنع المصادر ═══════════════════════════════════════
def test_build_feed_selects_yahoo(config):
    from src.infra.data_feed import build_feed

    config.data["bot"]["feed"] = "yahoo"
    assert isinstance(build_feed(config), YahooFeed)


def test_live_feed_rejected_for_real_trading(config):
    """أوامر حقيقية بأسعار مزوّد خارجي = تنفيذ على سعر غير سعر الوسيط."""
    from src.config_loader import ConfigError, _validate

    config.data["bot"]["dry_run"] = False
    config.data["bot"]["feed"] = "yahoo"
    with pytest.raises(ConfigError, match="mt5"):
        _validate(config)
