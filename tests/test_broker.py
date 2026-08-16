"""اختبارات الوسيط الورقي — أساس الـ dry-run والـ Backtest."""

from __future__ import annotations

import pytest

from src.infra.broker import BrokerError, PaperBroker


@pytest.fixture
def broker(spec):
    paper = PaperBroker(spec, balance=1000.0, spread=0.20)
    paper.set_price(2400.0)
    return paper


def test_lot_normalization(spec):
    assert spec.normalize_lot(0.0234) == 0.02      # تقريب لأسفل لخطوة 0.01
    assert spec.normalize_lot(0.004) == 0.0        # تحت الحد الأدنى → رفض
    assert spec.normalize_lot(50.0) == spec.max_lot


def test_buy_pnl_uses_contract_size(broker):
    ticket = broker.market_order("buy", 0.10, sl=2395.0, tp=2410.0)
    broker.set_price(2405.0)
    # حركة 5$ × 0.10 لوت × 100 أونصة = 50$ ناقص السبريد عند الدخول
    pnl = broker.close(ticket)
    assert pnl == pytest.approx(48.0, abs=0.5)
    assert broker.balance() == pytest.approx(1048.0, abs=0.5)


def test_sell_pnl_direction(broker):
    ticket = broker.market_order("sell", 0.10, sl=2410.0, tp=2390.0)
    broker.set_price(2395.0)
    assert broker.close(ticket) > 0


def test_stop_loss_hit_inside_bar(broker):
    broker.market_order("buy", 0.10, sl=2395.0, tp=2420.0)
    events = broker.process_bar(high=2402.0, low=2394.0, close=2400.0)
    assert len(events) == 1 and events[0]["reason"] == "sl"
    assert broker.balance() < 1000.0
    assert broker.positions() == []


def test_sl_wins_when_both_touched_in_same_bar(broker):
    """افتراض متحفظ مقصود: لا نمنح الـ Backtest تفاؤلاً مجانياً."""
    broker.market_order("buy", 0.10, sl=2395.0, tp=2410.0)
    events = broker.process_bar(high=2415.0, low=2390.0, close=2400.0)
    assert events[0]["reason"] == "sl"


def test_take_profit_hit(broker):
    broker.market_order("buy", 0.10, sl=2395.0, tp=2410.0)
    events = broker.process_bar(high=2412.0, low=2399.0, close=2411.0)
    assert events[0]["reason"] == "tp"
    assert broker.balance() > 1000.0


def test_partial_close_keeps_remaining_volume(broker):
    ticket = broker.market_order("buy", 0.10, sl=2395.0, tp=2420.0)
    broker.set_price(2405.0)
    broker.close(ticket, volume=0.03, reason="tp1_partial")
    position = broker.positions()[0]
    assert position.volume == pytest.approx(0.07)


def test_modify_updates_stop(broker):
    ticket = broker.market_order("buy", 0.10, sl=2395.0, tp=2420.0)
    assert broker.modify(ticket, sl=2399.0)
    assert broker.positions()[0].sl == 2399.0


def test_order_without_price_fails(spec):
    with pytest.raises(BrokerError):
        PaperBroker(spec).market_order("buy", 0.10, 2395.0, 2410.0)


def test_close_all(broker):
    broker.market_order("buy", 0.05, sl=2395.0, tp=2420.0)
    broker.market_order("buy", 0.05, sl=2395.0, tp=2420.0)
    broker.set_price(2402.0)
    broker.close_all("kill_switch")
    assert broker.positions() == []


# الأوامر المعلّقة (Buy Stop / Sell Stop) ---------------------------------
def test_place_pending_appears_in_pending_orders(broker):
    ticket = broker.place_pending("buy_stop", 0.01, 2410.0, comment="GDGRID")
    orders = broker.pending_orders()
    assert len(orders) == 1
    assert orders[0].ticket == ticket
    assert orders[0].order_type == "buy_stop"
    assert orders[0].price == 2410.0
    assert orders[0].comment == "GDGRID"


def test_cancel_pending_removes_order(broker):
    ticket = broker.place_pending("sell_stop", 0.01, 2390.0)
    assert broker.cancel_pending(ticket)
    assert broker.pending_orders() == []
    assert not broker.cancel_pending(ticket)  # مُلغى بالفعل


def test_buy_stop_fills_on_tick_when_ask_reaches_price(broker):
    broker.place_pending("buy_stop", 0.01, 2410.0)
    broker.set_price(2410.5)  # ask = 2410.5 + 0.20 > 2410.0
    assert broker.pending_orders() == []
    positions = broker.positions()
    assert len(positions) == 1
    assert positions[0].direction == "buy"
    assert positions[0].entry == pytest.approx(2410.0 + broker.slippage, abs=0.01)


def test_sell_stop_fills_on_tick_when_bid_reaches_price(broker):
    broker.place_pending("sell_stop", 0.01, 2390.0)
    broker.set_price(2389.5)  # bid = 2389.5 <= 2390.0
    assert broker.pending_orders() == []
    positions = broker.positions()
    assert len(positions) == 1
    assert positions[0].direction == "sell"


def test_pending_order_untouched_below_trigger(broker):
    broker.place_pending("buy_stop", 0.01, 2450.0)
    broker.set_price(2405.0)
    assert len(broker.pending_orders()) == 1
    assert broker.positions() == []


def test_pending_fills_inside_bar_high_low(broker):
    broker.place_pending("buy_stop", 0.01, 2410.0)
    broker.place_pending("sell_stop", 0.01, 2390.0)
    events = broker.process_bar(high=2412.0, low=2400.0, close=2408.0)
    # فقط Buy Stop انضرب — الشمعة لم تلمس 2390
    assert len(events) == 1
    assert len(broker.positions()) == 1
    assert broker.positions()[0].direction == "buy"
    assert len(broker.pending_orders()) == 1  # Sell Stop ما زال معلّقاً


def test_filled_pending_position_has_no_sl_tp(broker):
    """صفقات الشبكة تُدار كسلة، لا بوقف/هدف فردي."""
    broker.place_pending("buy_stop", 0.01, 2410.0)
    broker.set_price(2411.0)
    position = broker.positions()[0]
    assert position.sl == 0.0
    assert position.tp == 0.0
