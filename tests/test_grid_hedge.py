"""اختبارات شبكة التحوّط Buy Stop/Sell Stop.

كل اختبار حماية هنا (القطع المبكر، فلتر الاتجاه، حد الخسارة اليومي) هو
الفرق الفعلي بين "احترق" و"ربح" — خطأ هنا يعني حساباً مصفّى، تماماً كملف
اختبارات ``RiskManager``.
"""

from __future__ import annotations

import pytest

from src.infra.broker import PaperBroker
from src.risk_manager import RiskManager
from src.strategies.grid_hedge import HedgeGridStrategy


@pytest.fixture
def broker(spec):
    paper = PaperBroker(spec, balance=1000.0, spread=0.20)
    paper.set_price(4118.72)
    return paper


@pytest.fixture
def risk(config, spec, tmp_path):
    return RiskManager(config, spec, state_file=tmp_path / "grid_risk_state.json")


@pytest.fixture
def grid(config, broker, risk):
    config.data["grid"]["basket_tp_usd"] = 1000.0  # لا يُغلق تلقائياً إلا حين نطلبه صراحة
    config.data["grid"]["max_basket_loss_pct"] = 1.0  # يعطّل القطع المبكر افتراضياً في الاختبارات
    config.data["grid"]["step_fixed"] = 3.0
    config.data["grid"]["max_levels"] = 12
    config.data["grid"]["trend_guard"]["max_directional_levels"] = 5
    return HedgeGridStrategy(config, broker, risk)


def test_initializes_straddle_around_price(grid, broker):
    out = grid.sync(price=4118.72, balance=broker.balance())
    assert out["status"] == "initialized"
    orders = {o.order_type: o.price for o in broker.pending_orders()}
    assert orders["buy_stop"] == pytest.approx(4121.72)
    assert orders["sell_stop"] == pytest.approx(4115.72)


def test_second_sync_without_price_movement_is_idempotent(grid, broker):
    grid.sync(price=4118.72, balance=broker.balance())
    out = grid.sync(price=4118.72, balance=broker.balance())
    assert out["status"] == "synced"
    assert len(broker.pending_orders()) == 2


def test_fill_extends_same_side_and_leaves_other_untouched(grid, broker):
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)
    out = grid.sync(price=4121.9, balance=broker.balance())

    assert out["status"] == "extended"
    positions = broker.positions()
    assert len(positions) == 1 and positions[0].direction == "buy"

    orders = {o.order_type: o.price for o in broker.pending_orders()}
    assert orders["sell_stop"] == pytest.approx(4115.72)  # لم يُلمس
    assert orders["buy_stop"] == pytest.approx(4121.72 + 3.0)  # امتداد جديد


def test_basket_tp_closes_all_and_reinitializes(grid, broker, risk):
    grid.step_fixed = 3.0
    grid.basket_tp_usd = 0.1  # أقل من الربح العائم المتوقع (~0.18$) لضمان بلوغه
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)  # فتح صفقة شراء رابحة قليلاً
    out = grid.sync(price=4121.9, balance=broker.balance())

    assert out["status"] == "initialized"  # أُغلقت السلة وأُعيدت التهيئة في نفس الدورة
    assert broker.positions() == []
    assert risk.state["daily_pnl"] > 0


def test_basket_tp_without_reinit_stops(grid, broker):
    grid.basket_tp_usd = 0.1
    grid.reinit_after_close = False
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)
    out = grid.sync(price=4121.9, balance=broker.balance())

    assert out["status"] == "basket_tp"
    assert broker.positions() == []
    assert broker.pending_orders() == []


def test_early_basket_loss_cutoff_flattens_before_daily_limit(grid, broker, risk):
    grid.max_basket_loss_pct = 0.005  # 5$ على رصيد 1000$
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)  # شراء عند 4121.72
    grid.sync(price=4121.9, balance=broker.balance())
    # انعكاس حاد يضرب السعر تحت الشراء بخسارة عائمة تتجاوز 0.5%
    broker.process_bar(high=4122.0, low=4100.0, close=4105.0)
    out = grid.sync(price=4105.0, balance=broker.balance())

    assert out["status"] in {"basket_loss_cutoff", "initialized"}
    assert broker.positions() == []
    assert risk.state["daily_pnl"] < 0


def test_max_levels_caps_total_slots(grid, broker):
    grid.max_levels = 3
    grid.basket_tp_usd = 1000.0
    price = 4118.72
    grid.sync(price=price, balance=broker.balance())
    for _ in range(5):
        price += 3.0
        broker.process_bar(high=price + 0.5, low=price - 3.5, close=price)
        grid.sync(price=price, balance=broker.balance())

    total = len(broker.positions()) + len(broker.pending_orders())
    assert total <= 3


def test_trend_guard_pauses_overextended_side(grid, broker):
    grid.max_levels = 20
    grid.basket_tp_usd = 100000.0
    grid.max_directional_levels = 2
    price = 4118.72
    grid.sync(price=price, balance=broker.balance())
    for _ in range(4):
        price += 3.0
        broker.process_bar(high=price + 0.5, low=price - 3.5, close=price)
        grid.sync(price=price, balance=broker.balance())

    buy_positions = [p for p in broker.positions() if p.direction == "buy"]
    assert len(buy_positions) == 2  # توقف عند حد الاتجاه ولم يستمر بالتمدد
    pending_types = {o.order_type for o in broker.pending_orders()}
    assert "buy_stop" not in pending_types  # جهة الشراء متوقفة
    assert "sell_stop" in pending_types  # الجهة المقابلة تبقى مسلّحة


def test_trend_guard_disabled_keeps_extending(grid, broker):
    grid.max_levels = 20
    grid.basket_tp_usd = 100000.0
    grid.trend_guard_enabled = False
    price = 4118.72
    grid.sync(price=price, balance=broker.balance())
    for _ in range(4):
        price += 3.0
        broker.process_bar(high=price + 0.5, low=price - 3.5, close=price)
        grid.sync(price=price, balance=broker.balance())

    buy_positions = [p for p in broker.positions() if p.direction == "buy"]
    assert len(buy_positions) == 4


def test_kill_switch_active_flattens_and_blocks(grid, broker, risk):
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)
    grid.sync(price=4121.9, balance=broker.balance())
    assert broker.positions() != []

    out = grid.sync(price=4121.9, balance=broker.balance(),
                    kill_switch_active=True, kill_switch_reason="اختبار")

    assert out["status"] == "kill_switch"
    assert broker.positions() == []
    assert broker.pending_orders() == []


def test_daily_loss_lock_blocks_new_grid_and_flattens_existing(grid, broker, risk):
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)
    grid.sync(price=4121.9, balance=broker.balance())
    assert broker.positions() != []

    # يتجاوز حد الخسارة اليومي الافتراضي (5%) على رصيد 1000$. لا يُقفل فوراً —
    # RiskManager يُقيّم الأقفال كسولاً عند أول استدعاء لـ daily_guard/can_trade،
    # تماماً كما تختبر tests/test_risk_manager.py::test_daily_loss_limit_blocks_trading.
    risk.record_result(-60.0)

    out = grid.sync(price=4121.9, balance=broker.balance())
    assert out["status"] == "risk_blocked"
    assert risk.is_locked
    assert broker.positions() == []
    assert broker.pending_orders() == []

    # يبقى مقفلاً في الدورة التالية أيضاً — لا يُعاد فتح شبكة جديدة
    out2 = grid.sync(price=4121.9, balance=broker.balance())
    assert out2["status"] == "risk_blocked"
    assert broker.positions() == []


def test_basket_pnl_and_volume_helpers(grid, broker):
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)
    grid.sync(price=4121.9, balance=broker.balance())

    positions = grid.our_positions()
    assert grid.basket_volume(positions) == pytest.approx(0.01)
    assert grid.basket_pnl(positions) == pytest.approx(
        sum(p.profit for p in positions)
    )


def test_atr_step_mode_uses_atr_value(config, broker, risk):
    config.data["grid"]["step_mode"] = "atr"
    config.data["grid"]["step_atr_multiple"] = 2.0
    strategy = HedgeGridStrategy(config, broker, risk)
    step = strategy.compute_step(atr_value=1.5)
    assert step == pytest.approx(3.0)


def test_atr_step_mode_falls_back_when_no_atr_value(config, broker, risk):
    config.data["grid"]["step_mode"] = "atr"
    strategy = HedgeGridStrategy(config, broker, risk)
    step = strategy.compute_step(atr_value=None)
    assert step == strategy.step_fixed


# ═══ بيانات إغلاق السلة (لتسجيلها في DataLogger لاحقاً) ═══════════
def test_basket_tp_outcome_carries_closure_details_even_with_reinit(grid, broker):
    """أهم اختبار في هذا الملف: reinit_after_close=True (الافتراضي) لا يجوز
    أن يمحو positions_count/closed_pnl من outcome — وإلا لن يُسجَّل شيء أبداً
    في قاعدة البيانات في الحالة الشائعة (البند 3 من grid.yaml هو الافتراضي)."""
    grid.basket_tp_usd = 0.1
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)
    out = grid.sync(price=4121.9, balance=broker.balance())

    assert out["status"] == "initialized"  # أُعيدت التهيئة كما هو متوقع
    assert out["positions_count"] == 1
    assert out["closed_pnl"] == pytest.approx(0.18, abs=0.05)
    assert out["volume"] == pytest.approx(0.01)


def test_basket_loss_cutoff_outcome_carries_closure_details_with_reinit(grid, broker):
    grid.max_basket_loss_pct = 0.005
    grid.sync(price=4118.72, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4118.0, close=4121.9)
    grid.sync(price=4121.9, balance=broker.balance())
    broker.process_bar(high=4122.0, low=4100.0, close=4105.0)
    out = grid.sync(price=4105.0, balance=broker.balance())

    assert out.get("positions_count", 0) >= 1
    assert out.get("closed_pnl", 0) < 0


def test_no_closure_metadata_when_nothing_closed(grid, broker):
    out = grid.sync(price=4118.72, balance=broker.balance())
    assert out["status"] == "initialized"
    assert "positions_count" not in out
    assert "closed_pnl" not in out
