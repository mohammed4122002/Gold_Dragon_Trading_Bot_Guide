"""اختبارات محوّل MetaApi ضد نقل مزيّف.

ما تُثبته هذه الاختبارات: منطقنا صحيح — شكل الحمولات المرسلة، تفسير
الاستجابات، التعامل مع الأخطاء، وتحويل الشموع.

ما **لا** تُثبته: أن نهايات MetaApi الحقيقية بهذا الشكل بالضبط. ذلك يتحقق
فقط بتشغيل ``python -m src.main metaapi`` مقابل حساب حقيقي.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.infra.broker import SymbolSpec
from src.infra.metaapi import MetaApiBroker, MetaApiClient, MetaApiError, MetaApiFeed


class FakeClient(MetaApiClient):
    """يحاكي MetaApiClient دون شبكة، ويسجّل كل استدعاء للتحقق منه."""

    def __init__(self, **overrides):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses: dict[str, object] = {
            "account_information": {"balance": 10_000.0, "equity": 10_120.0,
                                    "currency": "USD", "type": "ACCOUNT_TRADE_MODE_DEMO",
                                    "leverage": 100, "broker": "Demo Broker"},
            "account_state": {"state": "DEPLOYED", "connectionStatus": "CONNECTED",
                              "platform": "mt5"},
            "symbol_specification": {"contractSize": 100, "minVolume": 0.01,
                                     "maxVolume": 50.0, "volumeStep": 0.01, "digits": 2},
            "current_price": {"bid": 2400.0, "ask": 2400.30},
            "positions": [],
            "trade": {"stringCode": "TRADE_RETCODE_DONE", "positionId": "555"},
            "deals": [],
            "candles": [],
        }
        self.responses.update(overrides)
        self.region = "new-york"
        self.account_id = "acct-123"
        self.token = "token"

    def _record(self, name: str, body: dict | None = None):
        self.calls.append((name, self.account_id, body))
        value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        return value

    def account_information(self): return self._record("account_information")
    def account_state(self): return self._record("account_state")
    def positions(self): return self._record("positions")
    def current_price(self, symbol): return self._record("current_price")
    def symbol_specification(self, symbol): return self._record("symbol_specification")
    def trade(self, payload): return self._record("trade", payload)
    def deals_by_position(self, position_id): return self._record("deals")
    def candles(self, symbol, timeframe, limit, start_time=None):
        return self._record("candles", {"timeframe": timeframe, "limit": limit})


def _position(**overrides) -> dict:
    base = {
        "id": "555", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "volume": 0.10,
        "openPrice": 2395.0, "stopLoss": 2390.0, "takeProfit": 2410.0,
        "time": "2026-03-02T12:00:00.000Z", "magic": 234000, "profit": 50.0,
        "comment": "GD",
    }
    base.update(overrides)
    return base


def _candles(count: int = 120, minutes: int = 15) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes * count)
    out = []
    for i in range(count):
        close = 2400.0 + i * 0.5
        out.append({
            "time": (start + timedelta(minutes=minutes * i)).isoformat().replace(
                "+00:00", "Z"),
            "open": close - 0.3, "high": close + 1.0, "low": close - 1.0,
            "close": close, "tickVolume": 120, "spread": 0.3,
        })
    return out


@pytest.fixture
def spec():
    return SymbolSpec("XAUUSD", 2, 100.0, 0.01, 10.0, 0.01)


@pytest.fixture
def broker(spec):
    return MetaApiBroker(spec, FakeClient())


# ═══ الاتصال والمواصفات ════════════════════════════════════════════
def test_connect_applies_broker_symbol_spec(spec):
    """مواصفات الوسيط تتقدم على الإعدادات — وإلا حسبنا الأحجام بأرقام خاطئة."""
    client = FakeClient(symbol_specification={"contractSize": 50, "minVolume": 0.05,
                                              "maxVolume": 20.0, "volumeStep": 0.05,
                                              "digits": 3})
    broker = MetaApiBroker(spec, client)
    broker.connect()

    assert broker.spec.contract_size == 50.0
    assert broker.spec.min_lot == 0.05
    assert broker.spec.lot_step == 0.05
    assert broker.spec.digits == 3


def test_connect_refuses_undeployed_account(spec):
    client = FakeClient(account_state={"state": "UNDEPLOYED", "connectionStatus": ""})
    with pytest.raises(MetaApiError, match="منشور"):
        MetaApiBroker(spec, client).connect()


def test_missing_credentials_rejected():
    with pytest.raises(MetaApiError, match="ناقصة"):
        MetaApiClient(token="", account_id="x")


# ═══ الحساب والأسعار ═══════════════════════════════════════════════
def test_balance_and_equity(broker):
    assert broker.balance() == 10_000.0
    assert broker.equity() == 10_120.0


def test_tick_returns_bid_ask(broker):
    bid, ask = broker.tick()
    assert (bid, ask) == (2400.0, 2400.30)
    assert broker.spread() == pytest.approx(0.30)


def test_incomplete_price_raises(spec):
    broker = MetaApiBroker(spec, FakeClient(current_price={"bid": 2400.0}))
    with pytest.raises(MetaApiError, match="غير مكتمل"):
        broker.tick()


# ═══ الأوامر ═══════════════════════════════════════════════════════
def test_market_order_payload_shape(broker):
    ticket = broker.market_order("buy", 0.10, sl=2390.0, tp=2410.0, comment="GD_ACS8")

    assert ticket == 555
    _, _, payload = broker.client.calls[-1]
    assert payload["actionType"] == "ORDER_TYPE_BUY"
    assert payload["symbol"] == "XAUUSD"
    assert payload["volume"] == 0.10
    assert payload["stopLoss"] == 2390.0
    assert payload["takeProfit"] == 2410.0
    assert payload["magic"] == 234000
    assert len(payload["comment"]) <= 31


def test_sell_order_uses_sell_action(broker):
    broker.market_order("sell", 0.10, sl=2410.0, tp=2390.0)
    assert broker.client.calls[-1][2]["actionType"] == "ORDER_TYPE_SELL"


def test_volume_below_minimum_is_refused(broker):
    with pytest.raises(MetaApiError, match="حجم"):
        broker.market_order("buy", 0.004, sl=2390.0, tp=2410.0)


def test_rejected_order_raises(spec):
    client = FakeClient(trade={"stringCode": "TRADE_RETCODE_NO_MONEY",
                               "message": "not enough money"})
    broker = MetaApiBroker(spec, client)
    with pytest.raises(MetaApiError, match="رفض الأمر"):
        broker.market_order("buy", 0.10, sl=2390.0, tp=2410.0)


def test_response_without_position_id_raises(spec):
    broker = MetaApiBroker(spec, FakeClient(trade={"stringCode": "TRADE_RETCODE_DONE"}))
    with pytest.raises(MetaApiError, match="معرّف مركز"):
        broker.market_order("buy", 0.10, sl=2390.0, tp=2410.0)


def test_modify_sends_position_modify(spec):
    broker = MetaApiBroker(spec, FakeClient(positions=[_position()]))
    assert broker.modify(555, sl=2396.0)

    payload = broker.client.calls[-1][2]
    assert payload["actionType"] == "POSITION_MODIFY"
    assert payload["positionId"] == "555"
    assert payload["stopLoss"] == 2396.0
    assert payload["takeProfit"] == 2410.0  # القيمة القائمة تُحفظ


def test_modify_unknown_position_returns_false(broker):
    assert broker.modify(999, sl=2396.0) is False


def test_full_close_uses_close_id(spec):
    broker = MetaApiBroker(spec, FakeClient(positions=[_position()]))
    broker.close(555, reason="tp3")
    assert broker.client.calls[-2][2]["actionType"] == "POSITION_CLOSE_ID"


def test_partial_close_uses_partial_action(spec):
    broker = MetaApiBroker(spec, FakeClient(positions=[_position(volume=0.10)]))
    broker.close(555, volume=0.03, reason="tp1_partial")

    payload = broker.client.calls[-2][2]
    assert payload["actionType"] == "POSITION_PARTIAL"
    assert payload["volume"] == 0.03


# ═══ المراكز والأرباح المحققة ══════════════════════════════════════
def test_positions_mapping(spec):
    broker = MetaApiBroker(spec, FakeClient(positions=[_position()]))
    positions = broker.positions()

    assert len(positions) == 1
    position = positions[0]
    assert position.ticket == 555
    assert position.direction == "buy"
    assert position.entry == 2395.0
    assert position.sl == 2390.0
    assert position.opened_at.tzinfo is not None


def test_positions_ignore_other_symbols_and_magics(spec):
    client = FakeClient(positions=[
        _position(id="1", symbol="EURUSD"),
        _position(id="2", magic=999999),      # صفقة يدوية من المستخدم
        _position(id="3"),
    ])
    tickets = {p.ticket for p in MetaApiBroker(spec, client).positions()}
    assert tickets == {3}


def test_realized_pnl_sums_costs(spec):
    client = FakeClient(deals=[
        {"entryType": "DEAL_ENTRY_IN", "profit": 0, "price": 2395.0},
        {"entryType": "DEAL_ENTRY_OUT", "profit": 45.0, "swap": -1.5,
         "commission": -3.5, "price": 2400.0},
    ])
    result = MetaApiBroker(spec, client).realized_pnl(555)

    assert result["pnl"] == pytest.approx(40.0)
    assert result["exit"] == 2400.0


def test_realized_pnl_unknown_returns_none(broker):
    """لا صفقات مسجّلة ⇒ None، ولا يجوز تفسيرها كصفر."""
    assert broker.realized_pnl(555) is None


# ═══ الشموع ════════════════════════════════════════════════════════
def test_feed_returns_valid_frame():
    feed = MetaApiFeed(FakeClient(candles=_candles(200)), "XAUUSD")
    frame = feed.get_bars("M15", 150)

    assert list(frame.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert frame["time"].is_monotonic_increasing
    assert len(frame) <= 150
    assert (frame["volume"] > 0).all()   # tickVolume مُحوّل


def test_feed_maps_timeframe_code():
    client = FakeClient(candles=_candles(200, minutes=60))
    MetaApiFeed(client, "XAUUSD").get_bars("H1", 100)
    assert client.calls[-1][2]["timeframe"] == "1h"


def test_feed_rejects_unknown_timeframe():
    with pytest.raises(MetaApiError, match="غير مدعوم"):
        MetaApiFeed(FakeClient(), "XAUUSD").get_bars("W1", 100)


def test_feed_rejects_empty_candles():
    with pytest.raises(MetaApiError, match="فارغة"):
        MetaApiFeed(FakeClient(candles=[]), "XAUUSD").get_bars("M15", 100)


def test_correlation_missing_symbol_does_not_break_gate():
    """رمز ارتباط لا يوفّره الوسيط يُسقط من الخريطة لا من التشغيل."""
    feed = MetaApiFeed(FakeClient(candles=_candles(30, minutes=60)), "XAUUSD",
                       {"DXY": "USDX", "US10Y": "TNX"})
    data = feed.get_correlation_data()
    assert set(data) == {"DXY", "US10Y"}
    for asset in data.values():
        assert {"price", "change_pct", "trend"} <= asset.keys()


# ═══ التكامل مع الإعدادات ══════════════════════════════════════════
def test_build_broker_selects_metaapi(config, monkeypatch):
    from src.main import build_broker

    monkeypatch.setenv("GD_METAAPI_TOKEN", "t")
    monkeypatch.setenv("GD_METAAPI_ACCOUNT_ID", "a")
    config.data["broker"]["provider"] = "metaapi"
    assert isinstance(build_broker(config), MetaApiBroker)


def test_provider_and_feed_must_match(config):
    from src.config_loader import ConfigError, _validate

    config.data["bot"]["dry_run"] = False
    config.data["broker"]["provider"] = "metaapi"
    config.data["bot"]["feed"] = "mt5"
    with pytest.raises(ConfigError, match="عدم تطابق"):
        _validate(config)


def test_metaapi_live_combination_is_valid(config):
    from src.config_loader import _validate

    config.data["bot"]["dry_run"] = False
    config.data["broker"]["provider"] = "metaapi"
    config.data["bot"]["feed"] = "metaapi"
    _validate(config)  # يجب ألا يرفع
