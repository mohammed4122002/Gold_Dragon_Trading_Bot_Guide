"""اختبار تكامل محرك شبكة التحوّط — وسيط ورقي + مصدر بيانات متنامٍ يحاكي التدفق الحي.

مصدر البيانات هنا يكبر بين كل ``tick()`` والذي يليه (``GrowingFeed.advance``)
تماماً كما يكبر تدفق شموع حقيقي — بعكس اختبارات ``test_pipeline.py`` التي
تُطعم البوت الأساسي سيناريو ثابتاً كاملاً دفعة واحدة.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.core.indicators import atr as atr_fn
from src.grid_engine import GridHedgeBot
from src.infra.broker import PaperBroker
from src.infra.data_feed import DataFeed, DataFeedError
from tests.conftest import trend_bars


class GrowingFeed(DataFeed):
    def __init__(self, df: pd.DataFrame, start: int = 2) -> None:
        self.df = df
        self.cursor = start

    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame:
        if self.cursor < 2:
            raise DataFeedError("لا بيانات كافية بعد")
        return self.df.iloc[: self.cursor].reset_index(drop=True)

    def advance(self, n: int = 1) -> None:
        self.cursor = min(self.cursor + n, len(self.df))


@pytest.fixture
def uptrend_bars():
    return trend_bars(count=10, start_price=4118.72, step=3.0, wick=0.5, minutes=15, leg=10)


@pytest.fixture
def engine_config(config, tmp_path):
    """نفس فلسفة fixture ``grid`` في test_grid_hedge.py: حراس الشبكة السريعة
    مُطفأة هنا ليختبر هذا الملف *المحرك* (الدورة، مصدر البيانات، الحالة،
    التسجيل) لا سلوك تلك الحراس — لكلٍّ منها اختباراتها الخاصة."""
    config.data["grid"]["enabled"] = True
    config.data["grid"]["step_mode"] = "fixed"
    config.data["grid"]["step_fixed"] = 3.0
    config.data["grid"]["basket_tp_mode"] = "fixed"
    config.data["grid"]["basket_tp_usd"] = 100000.0
    config.data["grid"]["max_levels"] = 12
    config.data["grid"]["pair_harvest"]["enabled"] = False
    config.data["grid"]["max_basket_age_minutes"] = 0
    config.data["grid"]["cooldown_seconds_after_close"] = 0
    config.data["grid"]["execution_guard"]["max_spread"] = 0.0
    config.data["grid"]["execution_guard"]["min_stop_distance_usd"] = 0.0
    config.data["grid"]["risk_state_file"] = str(tmp_path / "grid_risk_state.json")
    config.data["grid"]["kill_switch_state_file"] = str(tmp_path / "grid_kill_switch.json")
    return config


def test_engine_initializes_grid_on_first_tick(engine_config, spec, uptrend_bars):
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    feed = GrowingFeed(uptrend_bars, start=2)
    bot = GridHedgeBot(engine_config, broker, feed)

    outcome = bot.tick()
    assert outcome["status"] == "initialized"
    assert len(broker.pending_orders()) == 2


def test_engine_extends_grid_across_ticks_as_price_rises(engine_config, spec, uptrend_bars):
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    feed = GrowingFeed(uptrend_bars, start=2)
    bot = GridHedgeBot(engine_config, broker, feed)

    bot.tick()  # تهيئة
    statuses = []
    for _ in range(len(uptrend_bars) - 2):
        feed.advance(1)
        statuses.append(bot.tick()["status"])

    assert "extended" in statuses
    buy_positions = [p for p in broker.positions() if p.direction == "buy"]
    assert len(buy_positions) >= 1


def test_engine_uses_separate_risk_and_kill_switch_state(
    engine_config, spec, uptrend_bars, tmp_path
):
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    feed = GrowingFeed(uptrend_bars, start=2)
    bot = GridHedgeBot(engine_config, broker, feed)
    bot.tick()

    assert bot.risk.state_path == tmp_path / "grid_risk_state.json"
    assert bot.kill_switch.path == tmp_path / "grid_kill_switch.json"
    assert bot.risk.state_path != engine_config.path("kill_switch.state_file")


def test_engine_atr_step_mode_uses_real_atr(engine_config, spec, uptrend_bars):
    engine_config.data["grid"]["step_mode"] = "atr"
    engine_config.data["grid"]["step_atr_period"] = 5
    engine_config.data["grid"]["step_atr_multiple"] = 1.0
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    feed = GrowingFeed(uptrend_bars, start=8)
    bot = GridHedgeBot(engine_config, broker, feed)

    expected_atr = atr_fn(uptrend_bars.iloc[:8].reset_index(drop=True), 5)
    outcome = bot.tick()

    assert outcome["status"] == "initialized"
    orders = {o.order_type: o.price for o in broker.pending_orders()}
    spacing = orders["buy_stop"] - orders["sell_stop"]
    assert spacing == pytest.approx(2 * expected_atr, rel=0.05)


def test_engine_no_data_is_handled_gracefully(engine_config, spec, uptrend_bars):
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    feed = GrowingFeed(uptrend_bars, start=0)
    bot = GridHedgeBot(engine_config, broker, feed)

    outcome = bot.tick()
    assert outcome["status"] == "no_data"


# ═══ تكامل التسجيل في DataLogger ═══════════════════════════════════
def test_engine_logs_basket_close_to_data_logger(engine_config, spec, uptrend_bars, tmp_path):
    from src.infra.data_logger import DataLogger

    engine_config.data["grid"]["basket_tp_usd"] = 0.05  # يُغلق عند أول ربح عائم صغير
    engine_config.data["storage"]["database"] = str(tmp_path / "trades.db")

    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    feed = GrowingFeed(uptrend_bars, start=2)
    data_logger = DataLogger(tmp_path / "trades.db")
    bot = GridHedgeBot(engine_config, broker, feed, data_logger=data_logger)

    bot.tick()  # تهيئة
    for _ in range(len(uptrend_bars) - 2):
        feed.advance(1)
        bot.tick()

    summary = data_logger.grid_summary()
    assert summary["baskets"] >= 1, "لم يُسجَّل أي إغلاق سلة رغم هدف ربح ضئيل جداً"
    assert summary["net_pnl"] != 0.0


def test_engine_defaults_to_own_data_logger_when_none_given(engine_config, spec, uptrend_bars):
    """إن لم يُمرَّر data_logger، يبني واحداً من storage.database — لا يسقط صامتاً."""
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    feed = GrowingFeed(uptrend_bars, start=2)
    bot = GridHedgeBot(engine_config, broker, feed)

    assert bot.data_logger is not None
    bot.tick()  # يجب ألا يرمي استثناءً
