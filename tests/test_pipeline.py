"""اختبار المسار الكامل: بيانات → تحليل → بوابات → تنفيذ → إدارة.

يستخدم سيناريو مبنياً يدوياً تتوفر فيه كل أركان الدخول، ثم يتحقق من أن
كل بوابة قادرة على إسقاط الصفقة منفردة.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.bot_engine import GoldDragonBot
from src.core.gold_dragon_core import GoldDragonCore
from src.infra.broker import PaperBroker, SymbolSpec
from src.infra.data_feed import DataFeed
from src.models import TradeStage
from src.strategies import SessionManager
from tests.conftest import bullish_sweep_scenario

GOOD_CONTEXT = {
    "session": {"name": "Overlap", "inside_killzone": True, "blocked": False},
    "news": {"impact": "none", "frozen": False},
    "psychology": {"score": 1.0, "blocked": False},
    "correlation": {"verdict": "aligned"},
}


class StubFeed(DataFeed):
    """مصدر يعيد سيناريو ثابتاً — لا عشوائية في الاختبارات."""

    def __init__(self):
        self.m15, self.h1 = bullish_sweep_scenario()

    def get_bars(self, timeframe: str, count: int):
        return self.h1 if timeframe in {"H1", "H4"} else self.m15

    def get_correlation_data(self):
        return {
            "DXY": {"price": 103.0, "change_pct": -0.7, "trend": "down"},
            "US10Y": {"price": 4.0, "change_pct": -0.6, "trend": "down"},
            "VIX": {"price": 14.0, "change_pct": -1.0, "trend": "down"},
        }


@pytest.fixture
def scenario():
    return bullish_sweep_scenario()


@pytest.fixture
def core(config):
    return GoldDragonCore(config)


# ═══ النواة ════════════════════════════════════════════════════════
def test_complete_setup_produces_signal(core, scenario):
    m15, h1 = scenario
    result = core.analyze(h1, m15, None, GOOD_CONTEXT)

    assert result.signal is not None, result.rejections
    signal = result.signal
    assert signal.direction == "buy"
    assert signal.sl < signal.entry < signal.tp1 < signal.tp2 < signal.tp3
    assert signal.rr >= 3.0
    assert result.acs.score >= 7.0
    assert result.sweep["type"] == "SSL_Sweep"
    assert result.choch["direction"] == "bullish"


def test_stop_sits_below_the_swept_low(core, scenario):
    m15, h1 = scenario
    signal = core.analyze(h1, m15, None, GOOD_CONTEXT).signal
    swept_low = float(m15["low"].iloc[-8:].min())
    assert signal.sl < swept_low, "الوقف يجب أن يقع خلف قاع السويپ لا داخله"


def test_correlation_conflict_kills_the_setup(core, scenario):
    m15, h1 = scenario
    context = {**GOOD_CONTEXT, "correlation": {"verdict": "conflict"}}
    result = core.analyze(h1, m15, None, context)
    assert result.signal is None
    assert any("Correlation" in r for r in result.rejections)


def test_news_freeze_kills_the_setup(core, scenario):
    m15, h1 = scenario
    context = {**GOOD_CONTEXT, "news": {"impact": "high", "frozen": True, "title": "NFP"}}
    assert core.analyze(h1, m15, None, context).signal is None


def test_blocked_session_kills_the_setup(core, scenario):
    m15, h1 = scenario
    context = {**GOOD_CONTEXT,
               "session": {"name": "outside", "inside_killzone": False,
                           "blocked": True, "reason": "خارج الجلسات"}}
    assert core.analyze(h1, m15, None, context).signal is None


def test_angry_trader_kills_the_setup(core, scenario):
    m15, h1 = scenario
    context = {**GOOD_CONTEXT,
               "psychology": {"score": 0.0, "blocked": True, "reason": "غاضب"}}
    assert core.analyze(h1, m15, None, context).signal is None


def test_requires_choch_when_configured(config, scenario):
    m15, h1 = scenario
    # قطع الشموع قبل شمعة الإزاحة → لا CHoCH بعد
    truncated = m15.iloc[:-2].reset_index(drop=True)
    result = GoldDragonCore(config).analyze(h1, truncated, None, GOOD_CONTEXT)
    assert result.signal is None


def test_no_sweep_means_no_direction(config, scenario):
    m15, h1 = scenario
    # إزالة شمعة السويپ وما بعدها
    result = GoldDragonCore(config).analyze(
        h1, m15.iloc[:-4].reset_index(drop=True), None, GOOD_CONTEXT
    )
    assert result.signal is None
    assert result.acs is not None


# ═══ المحرك الكامل ═════════════════════════════════════════════════
@pytest.fixture
def bot(config, monkeypatch, tmp_path, request):
    """محرك كامل بوسيط ورقي وجلسة مثبّتة (حتى لا يتعلق الاختبار بساعة التشغيل)."""
    monkeypatch.setattr(
        SessionManager, "current_session",
        lambda self, now=None: {"name": "Overlap", "inside_killzone": True,
                                "blocked": False, "quality": 1.5, "reason": "test"},
    )
    spec = SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)
    broker = PaperBroker(spec, balance=request.param if hasattr(request, "param") else 5000.0,
                         spread=0.20)
    broker.set_price(2393.5)

    engine = GoldDragonBot(config, broker, StubFeed())
    engine.analyzer.psychology.set_state("calm")
    return engine


def test_engine_executes_and_sizes_correctly(bot):
    outcome = bot.scan_market()
    assert outcome["status"] == "executed", outcome["detail"]

    trade = bot.tracker.open_trades[0]
    risk_dollars = abs(trade.entry - trade.sl) * trade.volume * 100
    # المخاطرة المستهدفة 1% من 5000$ = 50$ (± خطوة اللوت)
    assert risk_dollars == pytest.approx(50.0, rel=0.35)
    assert trade.volume >= 0.01


def test_engine_refuses_second_trade_beyond_position_limit(bot):
    bot.risk.max_positions = 1
    assert bot.scan_market()["status"] == "executed"
    assert bot.scan_market()["status"] == "risk_blocked"


def test_engine_respects_kill_switch(bot):
    bot.kill_switch.trigger("اختبار يدوي", level="pause")
    assert bot.scan_market()["status"] == "kill_switch"


def test_engine_blocked_when_risk_locked(bot):
    for _ in range(3):
        bot.risk.record_result(-10.0)
    outcome = bot.scan_market()
    assert outcome["status"] == "risk_blocked"
    assert bot.broker.positions() == []


@pytest.mark.parametrize("bot", [50_000.0], indirect=True)
def test_trade_lifecycle_partial_then_breakeven(bot):
    """حساب كبير: الإغلاق الجزئي ممكن فعلياً."""
    assert bot.scan_market()["status"] == "executed"
    trade = bot.tracker.open_trades[0]
    assert trade.initial_volume >= 0.05, "الحساب صغير جداً لاختبار الإغلاق الجزئي"

    bot.broker.set_price(trade.tp1 + 0.5)   # بلوغ TP1
    bot.tracker.manage(None)
    assert trade.stage >= TradeStage.PARTIAL_1_DONE
    assert trade.volume < trade.initial_volume

    bot.tracker.manage(None)                # جولة ثانية → نقل الوقف
    assert trade.is_at_breakeven() or trade.stage >= TradeStage.BREAKEVEN_DONE
    # الوقف صار في جهة الربح
    assert trade.sl >= trade.entry


@pytest.mark.parametrize("bot", [2_500.0], indirect=True)
def test_small_account_skips_partials_but_still_protects(bot):
    """حساب صغير: 30% من 0.01 لوت غير قابلة للإغلاق — يجب ألا يُدّعى غير ذلك."""
    assert bot.scan_market()["status"] == "executed"
    trade = bot.tracker.open_trades[0]

    bot.broker.set_price(trade.tp1 + 0.5)
    bot.tracker.manage(None)
    bot.tracker.manage(None)

    assert trade.meta.get("partial_skipped") is True
    assert trade.volume == trade.initial_volume       # لم يُغلق شيء فعلياً
    assert trade.sl >= trade.entry                    # لكن الحماية طُبّقت


@pytest.mark.parametrize("bot", [800.0], indirect=True)
def test_tiny_account_is_refused_not_rounded_up(bot):
    """رصيد لا يحتمل المخاطرة مع وقف واسع → رفض صريح، لا تقريب لأعلى."""
    outcome = bot.scan_market()
    assert outcome["status"] == "size_rejected"
    assert bot.broker.positions() == []


def test_stop_loss_closes_and_records_loss(bot):
    assert bot.scan_market()["status"] == "executed"
    trade = bot.tracker.open_trades[0]

    bot.broker.process_bar(high=trade.entry, low=trade.sl - 1.0, close=trade.sl - 0.5,
                           when=datetime.now(timezone.utc))
    closed = bot.check_positions()  # يزامن ويسجّل النتيجة
    assert bot.tracker.open_trades == []
    assert bot.risk.state["consecutive_losses"] == 1
    assert closed is not None


def test_analysis_is_persisted(bot):
    bot.scan_market()
    stats = bot.data_logger.summary()
    assert stats is not None
    assert bot.data_logger.closed_trades() == []  # لا صفقات مغلقة بعد
