"""اختبارات وضع forward test: تغذية الوسيط الورقي بشموع السوق الحقيقية.

هذا هو الوضع الذي سيعمل به البوت على الاستضافة السحابية، فمنطق تقديم
«الساعة الورقية» يجب أن يكون مضبوطاً: لا إعادة تشغيل للتاريخ، ولا تجاهل
لشمعة جديدة، ولا ضرب وقف وهمي.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from src.bot_engine import GoldDragonBot
from src.infra.broker import PaperBroker, SymbolSpec
from src.infra.data_feed import DataFeed
from src.strategies import SessionManager
from tests.conftest import bullish_sweep_scenario


class ReplayFeed(DataFeed):
    """يعيد نافذة متحركة من سيناريو ثابت — يحاكي وصول شموع جديدة."""

    def __init__(self, extra_bars: pd.DataFrame | None = None):
        self.m15, self.h1 = bullish_sweep_scenario()
        self.extra = extra_bars
        self.cursor = len(self.m15)

    def advance(self, count: int = 1) -> None:
        self.cursor = min(self.cursor + count, len(self.full_m15))

    @property
    def full_m15(self) -> pd.DataFrame:
        if self.extra is None:
            return self.m15
        return pd.concat([self.m15, self.extra], ignore_index=True)

    def get_bars(self, timeframe: str, count: int):
        if timeframe in {"H1", "H4"}:
            return self.h1
        return self.full_m15.iloc[:self.cursor].reset_index(drop=True)

    def get_correlation_data(self):
        return {"DXY": {"price": 103.0, "change_pct": -0.7, "trend": "down"},
                "US10Y": {"price": 4.0, "change_pct": -0.6, "trend": "down"}}


def _extra_bars(base: pd.DataFrame, prices: list[tuple[float, float, float]]) -> pd.DataFrame:
    """بناء شموع لاحقة من (high, low, close)."""
    start = base["time"].iloc[-1]
    rows = []
    for i, (high, low, close) in enumerate(prices, start=1):
        rows.append({"time": start + timedelta(minutes=15 * i), "open": close,
                     "high": high, "low": low, "close": close, "volume": 1000.0})
    return pd.DataFrame(rows)


@pytest.fixture
def bot(config, monkeypatch):
    monkeypatch.setattr(
        SessionManager, "current_session",
        lambda self, now=None: {"name": "Overlap", "inside_killzone": True,
                                "blocked": False, "quality": 1.5, "reason": "test"},
    )
    spec = SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)
    broker = PaperBroker(spec, balance=20_000.0, spread=0.20)
    engine = GoldDragonBot(config, broker, ReplayFeed())
    engine.analyzer.psychology.set_state("calm")
    return engine


def test_first_cycle_seeds_price_without_replaying_history(bot):
    """أول دورة تضبط السعر فقط — لا تُعيد تشغيل الشموع الماضية."""
    bot.analyzer.analyze()          # يملأ آخر الشموع المستخدمة
    events = bot.advance_paper_clock()

    assert events == [], "أعاد تشغيل التاريخ بدل الاكتفاء بضبط السعر"
    assert bot._last_bar_time is not None

    # المهم: صار للوسيط سعر صالح
    bid, ask = bot.broker.tick()
    assert bid > 0 and ask > bid


def test_paper_broker_receives_price_before_management(bot):
    """قبل الإصلاح كان الوسيط الورقي بلا سعر فينهار عند أول إدارة."""
    outcome = bot.scan_market()
    assert outcome["status"] in {"executed", "no_signal", "risk_blocked"}
    assert bot.broker.tick()[0] > 0


def test_same_bar_is_not_processed_twice(bot):
    bot.scan_market()
    marker = bot._last_bar_time
    assert bot.advance_paper_clock() == []      # لا شموع جديدة
    assert bot._last_bar_time == marker


def test_new_bar_hitting_stop_closes_the_trade(config, monkeypatch):
    """شمعة جديدة يخترق قاعها الوقف ⇒ إغلاق وتسجيل خسارة في مدير المخاطرة."""
    monkeypatch.setattr(
        SessionManager, "current_session",
        lambda self, now=None: {"name": "Overlap", "inside_killzone": True,
                                "blocked": False, "quality": 1.5, "reason": "test"},
    )
    spec = SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)
    broker = PaperBroker(spec, balance=20_000.0, spread=0.20)
    feed = ReplayFeed()
    engine = GoldDragonBot(config, broker, feed)
    engine.analyzer.psychology.set_state("calm")

    assert engine.scan_market()["status"] == "executed"
    trade = engine.tracker.open_trades[0]

    # شمعة تهبط تحت الوقف
    feed.extra = _extra_bars(feed.m15, [(trade.entry, trade.sl - 2.0, trade.sl - 1.0)])
    feed.cursor = len(feed.full_m15)

    engine.scan_market()

    # الصفقة الأولى أُغلقت وسُجّلت خسارتها (قد يفتح المحرك صفقة جديدة
    # في نفس الدورة — خسارة واحدة لا تمنع الدخول، القفل عند ثلاث)
    assert trade.ticket not in {t.ticket for t in engine.tracker.open_trades}
    assert trade.exit_reason == "sl"
    assert trade.pnl < 0
    assert engine.risk.state["consecutive_losses"] == 1


def test_new_bar_hitting_target_books_profit(config, monkeypatch):
    monkeypatch.setattr(
        SessionManager, "current_session",
        lambda self, now=None: {"name": "Overlap", "inside_killzone": True,
                                "blocked": False, "quality": 1.5, "reason": "test"},
    )
    spec = SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)
    broker = PaperBroker(spec, balance=20_000.0, spread=0.20)
    feed = ReplayFeed()
    engine = GoldDragonBot(config, broker, feed)
    engine.analyzer.psychology.set_state("calm")

    assert engine.scan_market()["status"] == "executed"
    trade = engine.tracker.open_trades[0]

    feed.extra = _extra_bars(feed.m15, [(trade.tp3 + 2.0, trade.entry, trade.tp3 + 1.0)])
    feed.cursor = len(feed.full_m15)

    engine.scan_market()

    assert trade.ticket not in {t.ticket for t in engine.tracker.open_trades}
    assert trade.exit_reason == "tp"
    assert trade.pnl > 0
    assert engine.risk.state["consecutive_losses"] == 0


def test_health_snapshot_reports_state(bot):
    from src.infra.health import _snapshot

    bot.is_running = True
    bot.scan_market()
    data = _snapshot(bot)

    assert data["running"] is True
    assert data["mode"] == "dry-run"
    assert "balance" in data and "risk" in data
    assert data["last_scan"]["status"]
    # لا يجوز أن تتسرب أي أسرار إلى واجهة المراقبة
    text = str(data).lower()
    assert "token" not in text and "password" not in text
