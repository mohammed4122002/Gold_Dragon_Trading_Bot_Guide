"""اختبارات صفحة المراقبة عند تشغيل بوت SMC وشبكة التحوّط معاً (run-all)."""

from __future__ import annotations

import pytest

from src.bot_engine import GoldDragonBot
from src.grid_engine import GridHedgeBot
from src.infra.broker import PaperBroker, SymbolSpec
from src.infra.data_logger import DataLogger
from src.infra.health import _render, _snapshot
from tests.conftest import bullish_sweep_scenario


class _StubFeed:
    def __init__(self):
        self.m15, self.h1 = bullish_sweep_scenario()

    def get_bars(self, timeframe, count):
        return self.h1 if timeframe in {"H1", "H4"} else self.m15

    def get_correlation_data(self):
        return {}


@pytest.fixture
def pair(config, tmp_path):
    spec = SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)
    logger = DataLogger(tmp_path / "trades.db")

    smc_broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    smc_broker.set_price(2393.5)
    bot = GoldDragonBot(config, smc_broker, _StubFeed(), data_logger=logger)

    grid_broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    grid_broker.set_price(2393.5)
    grid_bot = GridHedgeBot(config, grid_broker, _StubFeed(), data_logger=logger)

    bot.grid_bot = grid_bot
    return bot, grid_bot


def test_snapshot_without_grid_bot_has_no_grid_key(config, tmp_path):
    spec = SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    broker.set_price(2393.5)
    bot = GoldDragonBot(config, broker, _StubFeed(), data_logger=DataLogger(tmp_path / "t.db"))

    assert "grid" not in _snapshot(bot)


def test_snapshot_picks_up_grid_bot_from_attribute(pair):
    bot, grid_bot = pair
    data = _snapshot(bot)

    assert "grid" in data
    assert "balance" in data["grid"]
    assert data["grid"]["balance"] == pytest.approx(1000.0)
    assert "performance" in data["grid"]
    assert data["grid"]["performance"]["baskets"] == 0


def test_snapshot_explicit_grid_bot_overrides_attribute(pair):
    bot, grid_bot = pair
    data = _snapshot(bot, grid_bot=grid_bot)
    assert "grid" in data


def test_render_includes_grid_section_when_present(pair):
    bot, _ = pair
    html = _render(_snapshot(bot))
    assert "شبكة التحوّط" in html
    assert "سلال مُغلقة" in html


def test_render_omits_grid_section_when_absent(config, tmp_path):
    spec = SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)
    broker = PaperBroker(spec, balance=1000.0, spread=0.20)
    broker.set_price(2393.5)
    bot = GoldDragonBot(config, broker, _StubFeed(), data_logger=DataLogger(tmp_path / "t.db"))

    html = _render(_snapshot(bot))
    assert "شبكة التحوّط" not in html


def test_snapshot_survives_grid_bot_errors(pair, monkeypatch):
    """عطل في قراءة حالة الشبكة لا يجوز أن يُسقط الصفحة كلها."""
    bot, grid_bot = pair

    def boom():
        raise RuntimeError("محاكاة عطل")

    monkeypatch.setattr(grid_bot.broker, "balance", boom)
    data = _snapshot(bot)
    assert "grid" in data
    assert "broker_error" in data["grid"]
    # حالة البوت الأساسي تبقى سليمة رغم عطل الشبكة
    assert "balance" in data
