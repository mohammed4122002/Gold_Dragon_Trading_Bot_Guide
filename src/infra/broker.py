"""طبقة الوسيط — واجهة مجردة + تنفيذ ورقي (Paper) + تنفيذ MT5.

الفصل مقصود: كل ما فوق هذه الطبقة (المحرك، إدارة المخاطرة، التتبع) لا يعرف
شيئاً عن MetaTrader. هذا ما يجعل الاختبار والـ Backtest ممكنين على Linux
دون تثبيت MT5 (المكتبة متاحة على Windows فقط).
"""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .logger import get_logger

log = get_logger(__name__)

Side = Literal["buy", "sell"]
PendingType = Literal["buy_stop", "sell_stop"]


class BrokerError(RuntimeError):
    """فشل تنفيذ أمر لدى الوسيط."""


@dataclass
class SymbolSpec:
    name: str
    digits: int = 2
    contract_size: float = 100.0
    min_lot: float = 0.01
    max_lot: float = 10.0
    lot_step: float = 0.01
    point: float = 0.01
    # أدنى مسافة سعرية يقبلها الوسيط بين أمر معلّق والسعر الحالي. صفر = بلا
    # قيد. تُقرأ من الوسيط عند الاتصال (MT5: trade_stops_level × point).
    # مهمة عند تضييق خطوة الشبكة: أمر أقرب من هذه المسافة يُرفض بـ retcode
    # 10016 (Invalid stops) — فتتوقف الشبكة عن التمدد بصمت.
    stops_level: float = 0.0

    def normalize_lot(self, lot: float) -> float:
        """تقريب الحجم لأقرب خطوة صالحة، ثم قصّه ضمن حدود الوسيط."""
        if lot <= 0:
            return 0.0
        steps = int(lot / self.lot_step + 1e-9)
        lot = steps * self.lot_step
        lot = round(lot, 8)
        if lot < self.min_lot:
            return 0.0  # أصغر من الحد الأدنى → لا صفقة (لا نرفع المخاطرة قسراً)
        return round(min(lot, self.max_lot), 8)

    def normalize_price(self, price: float) -> float:
        return round(price, self.digits)


@dataclass
class BrokerPosition:
    ticket: int
    symbol: str
    direction: Side
    volume: float
    entry: float
    sl: float
    tp: float
    opened_at: datetime
    comment: str = ""
    profit: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingOrder:
    ticket: int
    symbol: str
    order_type: PendingType
    volume: float
    price: float
    comment: str = ""
    placed_at: datetime | None = None


class Broker(ABC):
    spec: SymbolSpec

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def shutdown(self) -> None: ...

    @abstractmethod
    def balance(self) -> float: ...

    @abstractmethod
    def equity(self) -> float: ...

    @abstractmethod
    def tick(self) -> tuple[float, float]:
        """يعيد (bid, ask)."""

    @abstractmethod
    def market_order(
        self, direction: Side, volume: float, sl: float, tp: float, comment: str = ""
    ) -> int: ...

    @abstractmethod
    def modify(self, ticket: int, sl: float | None = None, tp: float | None = None) -> bool: ...

    @abstractmethod
    def close(self, ticket: int, volume: float | None = None, reason: str = "") -> float:
        """إغلاق كلي أو جزئي — يعيد الربح/الخسارة المحقق."""

    @abstractmethod
    def positions(self) -> list[BrokerPosition]: ...

    def realized_pnl(self, ticket: int) -> dict[str, Any] | None:
        """الربح/الخسارة المحقق لصفقة أُغلقت، إن أمكن للوسيط أن يخبرنا.

        كل وسيط يعرفها من مصدر مختلف (سجل داخلي، صفقات تاريخية، API)،
        لذلك المتتبّع يسأل عبر هذه الواجهة بدل التنقيب في تفاصيل التنفيذ.
        القيمة None تعني «لا أعرف» — ولا يجوز تفسيرها كصفر.
        """
        return None

    def spread(self) -> float:
        bid, ask = self.tick()
        return ask - bid

    def close_all(self, reason: str = "close_all") -> float:
        total = 0.0
        for pos in list(self.positions()):
            total += self.close(pos.ticket, reason=reason)
        return total

    # أوامر معلّقة — اختيارية: الوسطاء التي لا تحتاجها (مثل MetaApiBroker حالياً)
    # تبقى قابلة للتشغيل دون تعديل؛ من يستخدمها (شبكة Buy Stop/Sell Stop) يحتاج
    # وسيطاً يطبّقها فعلاً (PaperBroker أو MT5Broker).
    def place_pending(
        self, order_type: PendingType, volume: float, price: float, comment: str = ""
    ) -> int:
        raise NotImplementedError(f"{type(self).__name__} لا يدعم الأوامر المعلّقة")

    def cancel_pending(self, ticket: int) -> bool:
        raise NotImplementedError(f"{type(self).__name__} لا يدعم الأوامر المعلّقة")

    def pending_orders(self) -> list[PendingOrder]:
        raise NotImplementedError(f"{type(self).__name__} لا يدعم الأوامر المعلّقة")

    def cancel_all_pending(self, reason: str = "") -> int:
        count = 0
        for order in list(self.pending_orders()):
            if self.cancel_pending(order.ticket):
                count += 1
        return count


# ═══════════════════════════════════════════════════════════════════
#  PaperBroker — محاكاة تنفيذ كاملة (dry-run + backtest)
# ═══════════════════════════════════════════════════════════════════
class PaperBroker(Broker):
    """وسيط ورقي: يملأ الأوامر بسعر السوق الحالي ويحاكي SL/TP والعمولة.

    يُغذّى بالأسعار عبر :meth:`set_price` (سعر لحظي) أو :meth:`process_bar`
    (شمعة كاملة) — الأخيرة تفحص ضرب SL/TP داخل الشمعة.
    """

    def __init__(
        self,
        spec: SymbolSpec,
        balance: float = 1000.0,
        spread: float = 0.20,
        commission_per_lot: float = 0.0,
        slippage: float = 0.0,
    ) -> None:
        self.spec = spec
        self._balance = balance
        self._spread = spread
        self.commission_per_lot = commission_per_lot
        self.slippage = slippage
        self._bid = 0.0
        self._positions: dict[int, BrokerPosition] = {}
        self._pending: dict[int, PendingOrder] = {}
        self._tickets = itertools.count(1000)
        self.closed: list[dict[str, Any]] = []
        self.now: datetime = datetime.now(timezone.utc)

    # دورة الحياة ------------------------------------------------------------
    def connect(self) -> None:
        log.info("PaperBroker جاهز — رصيد افتتاحي %.2f", self._balance)

    def shutdown(self) -> None:
        log.info("PaperBroker متوقف — رصيد نهائي %.2f", self._balance)

    # التسعير ----------------------------------------------------------------
    def set_price(self, bid: float, when: datetime | None = None) -> None:
        self._bid = bid
        if when:
            self.now = when
        if self._pending:
            self._fill_pending(high=bid + self._spread, low=bid)

    def tick(self) -> tuple[float, float]:
        if self._bid <= 0:
            raise BrokerError("PaperBroker لم يستلم سعراً بعد — استدعِ set_price أولاً")
        return self._bid, self._bid + self._spread

    # الحساب -----------------------------------------------------------------
    def balance(self) -> float:
        return self._balance

    def equity(self) -> float:
        return self._balance + sum(self._unrealized(p) for p in self._positions.values())

    def _unrealized(self, pos: BrokerPosition) -> float:
        bid, ask = self.tick()
        price = bid if pos.direction == "buy" else ask
        return self._pnl(pos, price)

    def _pnl(self, pos: BrokerPosition, exit_price: float) -> float:
        move = (
            exit_price - pos.entry if pos.direction == "buy" else pos.entry - exit_price
        )
        gross = move * self.spec.contract_size * pos.volume
        return gross - self.commission_per_lot * pos.volume

    # الأوامر ----------------------------------------------------------------
    def _open_position(
        self, direction: Side, volume: float, price: float, sl: float, tp: float, comment: str
    ) -> int:
        ticket = next(self._tickets)
        self._positions[ticket] = BrokerPosition(
            ticket=ticket,
            symbol=self.spec.name,
            direction=direction,
            volume=volume,
            entry=self.spec.normalize_price(price),
            sl=self.spec.normalize_price(sl),
            tp=self.spec.normalize_price(tp),
            opened_at=self.now,
            comment=comment,
        )
        return ticket

    def market_order(
        self, direction: Side, volume: float, sl: float, tp: float, comment: str = ""
    ) -> int:
        volume = self.spec.normalize_lot(volume)
        if volume <= 0:
            raise BrokerError("حجم غير صالح بعد التقريب (أصغر من الحد الأدنى)")

        bid, ask = self.tick()
        price = (ask + self.slippage) if direction == "buy" else (bid - self.slippage)
        ticket = self._open_position(direction, volume, price, sl, tp, comment)
        log.info(
            "PAPER فتح #%s %s %.2f لوت @ %.2f (SL %.2f / TP %.2f)",
            ticket, direction, volume, price, sl, tp,
        )
        return ticket

    # الأوامر المعلّقة (Buy Stop / Sell Stop) ---------------------------------
    def _validate_pending_distance(self, order_type: PendingType, price: float) -> None:
        """محاكاة رفض الوسيط لأمر معلّق أقرب من اللازم أو بالجهة الخاطئة.

        وسيط حقيقي يرفض بـ retcode 10016 (Invalid stops) أمر Buy Stop تحت
        السوق أو أقرب من ``stops_level``. بدون محاكاة هذا الرفض هنا، تنجح
        الشبكة الضيقة في الاختبار وتفشل صامتة عند الوسيط الحقيقي.
        """
        if self._bid <= 0:
            return  # لا سعر بعد — لا شيء نقيس عليه
        bid, ask = self.tick()
        distance = (price - ask) if order_type == "buy_stop" else (bid - price)
        if distance <= 0:
            raise BrokerError(
                f"أمر {order_type} بسعر {price:.2f} بالجهة الخاطئة من السوق "
                f"(bid={bid:.2f} ask={ask:.2f})"
            )
        if distance < self.spec.stops_level - 1e-9:
            raise BrokerError(
                f"أمر {order_type} بسعر {price:.2f} يبعد {distance:.2f} فقط عن السوق — "
                f"أدنى مسافة يقبلها الوسيط {self.spec.stops_level:.2f}"
            )

    def place_pending(
        self, order_type: PendingType, volume: float, price: float, comment: str = ""
    ) -> int:
        volume = self.spec.normalize_lot(volume)
        if volume <= 0:
            raise BrokerError("حجم غير صالح بعد التقريب (أصغر من الحد الأدنى)")
        self._validate_pending_distance(order_type, price)
        ticket = next(self._tickets)
        self._pending[ticket] = PendingOrder(
            ticket=ticket,
            symbol=self.spec.name,
            order_type=order_type,
            volume=volume,
            price=self.spec.normalize_price(price),
            comment=comment,
            placed_at=self.now,
        )
        log.info("PAPER أمر معلّق #%s %s %.2f لوت @ %.2f", ticket, order_type, volume, price)
        return ticket

    def cancel_pending(self, ticket: int) -> bool:
        removed = self._pending.pop(ticket, None)
        if removed:
            log.info("PAPER إلغاء أمر معلّق #%s", ticket)
        return removed is not None

    def pending_orders(self) -> list[PendingOrder]:
        return list(self._pending.values())

    def _fill_pending(self, high: float, low: float) -> list[dict[str, Any]]:
        """فحص انضراب الأوامر المعلّقة ضمن [low, high] (تِك أو مدى شمعة كاملة)."""
        events: list[dict[str, Any]] = []
        for ticket, order in list(self._pending.items()):
            triggered = (
                high >= order.price if order.order_type == "buy_stop" else low <= order.price
            )
            if not triggered:
                continue
            direction: Side = "buy" if order.order_type == "buy_stop" else "sell"
            fill_price = (
                order.price + self.slippage if direction == "buy"
                else order.price - self.slippage
            )
            new_ticket = self._open_position(
                direction, order.volume, fill_price, 0.0, 0.0, order.comment
            )
            del self._pending[ticket]
            log.info(
                "PAPER تنفيذ أمر معلّق #%s → مركز #%s %s %.2f لوت @ %.2f",
                ticket, new_ticket, direction, order.volume, fill_price,
            )
            events.append({
                "pending_ticket": ticket, "ticket": new_ticket, "direction": direction,
                "price": fill_price, "comment": order.comment,
            })
        return events

    def modify(self, ticket: int, sl: float | None = None, tp: float | None = None) -> bool:
        pos = self._positions.get(ticket)
        if not pos:
            return False
        if sl is not None:
            pos.sl = self.spec.normalize_price(sl)
        if tp is not None:
            pos.tp = self.spec.normalize_price(tp)
        return True

    def close(self, ticket: int, volume: float | None = None, reason: str = "") -> float:
        pos = self._positions.get(ticket)
        if not pos:
            return 0.0

        bid, ask = self.tick()
        price = bid if pos.direction == "buy" else ask
        return self._close_at(pos, price, volume, reason)

    def _close_at(
        self, pos: BrokerPosition, price: float, volume: float | None, reason: str
    ) -> float:
        qty = pos.volume if volume is None else min(volume, pos.volume)
        qty = self.spec.normalize_lot(qty) or pos.volume
        part = BrokerPosition(**{**pos.__dict__, "volume": qty})
        pnl = self._pnl(part, price)

        self._balance += pnl
        pos.volume = round(pos.volume - qty, 8)
        self.closed.append(
            {
                "ticket": pos.ticket, "direction": pos.direction, "volume": qty,
                "entry": pos.entry, "exit": price, "pnl": pnl,
                "reason": reason, "closed_at": self.now,
            }
        )
        if pos.volume < self.spec.min_lot:
            self._positions.pop(pos.ticket, None)
        log.info("PAPER إغلاق #%s %.2f لوت @ %.2f → %+.2f$ (%s)",
                 pos.ticket, qty, price, pnl, reason or "manual")
        return pnl

    def positions(self) -> list[BrokerPosition]:
        for pos in self._positions.values():
            pos.profit = self._unrealized(pos)
        return list(self._positions.values())

    def realized_pnl(self, ticket: int) -> dict[str, Any] | None:
        """تجميع كل الأجزاء المغلقة لنفس التذكرة (إغلاق جزئي + نهائي)."""
        parts = [c for c in self.closed if c["ticket"] == ticket]
        if not parts:
            return None
        return {"pnl": sum(p["pnl"] for p in parts), "exit": parts[-1]["exit"],
                "reason": parts[-1]["reason"]}

    # محاكاة الشموع ----------------------------------------------------------
    def process_bar(self, high: float, low: float, close: float,
                    when: datetime | None = None) -> list[dict[str, Any]]:
        """فحص ضرب SL/TP داخل شمعة كاملة.

        عندما تلامس الشمعة SL و TP معاً نفترض الأسوأ (SL أولاً) — افتراض
        متحفظ متعمد حتى لا يبدو الـ Backtest أفضل من الواقع.
        """
        if when:
            self.now = when
        events: list[dict[str, Any]] = self._fill_pending(high=high, low=low)

        for pos in list(self._positions.values()):
            hit_sl = (low <= pos.sl) if pos.direction == "buy" else (high >= pos.sl)
            hit_tp = (high >= pos.tp) if pos.direction == "buy" else (low <= pos.tp)
            if pos.sl <= 0:
                hit_sl = False
            if pos.tp <= 0:
                hit_tp = False

            if hit_sl:
                pnl = self._close_at(pos, pos.sl, None, "sl")
                events.append({"ticket": pos.ticket, "reason": "sl",
                               "price": pos.sl, "pnl": pnl})
            elif hit_tp:
                pnl = self._close_at(pos, pos.tp, None, "tp")
                events.append({"ticket": pos.ticket, "reason": "tp",
                               "price": pos.tp, "pnl": pnl})

        self.set_price(close, when)
        return events


# ═══════════════════════════════════════════════════════════════════
#  MT5Broker — تنفيذ حقيقي عبر MetaTrader5 Python API
# ═══════════════════════════════════════════════════════════════════
class MT5Broker(Broker):
    """غلاف MetaTrader5. يتطلب Windows + تثبيت ``MetaTrader5``.

    كل استيراد للمكتبة كسول حتى تبقى بقية الحزمة قابلة للتشغيل والاختبار
    على Linux/macOS.
    """

    def __init__(self, spec: SymbolSpec, login: int, password: str, server: str,
                 terminal_path: str = "", magic: int = 234000,
                 deviation: int = 20) -> None:
        self.spec = spec
        self.login = int(login)
        self.password = password
        self.server = server
        self.terminal_path = terminal_path
        self.magic = magic
        self.deviation = deviation
        self._mt5: Any = None

    def _api(self) -> Any:
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5  # type: ignore[import-not-found]
            except ImportError as exc:
                raise BrokerError(
                    "مكتبة MetaTrader5 غير متاحة. ثبّتها على Windows: "
                    "pip install MetaTrader5 — أو شغّل البوت بوضع dry_run."
                ) from exc
            self._mt5 = mt5
        return self._mt5

    def connect(self) -> None:
        mt5 = self._api()
        kwargs = {"path": self.terminal_path} if self.terminal_path else {}
        if not mt5.initialize(**kwargs):
            raise BrokerError(f"فشل تهيئة MT5: {mt5.last_error()}")
        if not mt5.login(self.login, password=self.password, server=self.server):
            mt5.shutdown()
            raise BrokerError(f"فشل تسجيل الدخول إلى MT5: {mt5.last_error()}")

        info = mt5.symbol_info(self.spec.name)
        if info is None:
            mt5.shutdown()
            raise BrokerError(f"الرمز {self.spec.name} غير متاح لدى الوسيط")
        if not info.visible:
            mt5.symbol_select(self.spec.name, True)

        # مواصفات الوسيط الفعلية تتقدم على ملف الإعدادات
        self.spec.digits = info.digits
        self.spec.contract_size = info.trade_contract_size
        self.spec.min_lot = info.volume_min
        self.spec.max_lot = info.volume_max
        self.spec.lot_step = info.volume_step
        self.spec.point = info.point
        # أدنى مسافة لأمر معلّق عن السوق. كثير من الوسطاء يعيدونها صفراً
        # (بلا قيد ثابت) ويطبّقون بدلها قيداً ديناميكياً على السبريد —
        # لذلك المسافة الفعلية المستخدمة في الشبكة تأخذ الأكبر بين هذه
        # القيمة وهامش أمان من الإعدادات.
        self.spec.stops_level = float(
            getattr(info, "trade_stops_level", 0) or 0
        ) * self.spec.point
        log.info("MT5 متصل | %s | contract_size=%s min_lot=%s stops_level=%.2f",
                 self.spec.name, self.spec.contract_size, self.spec.min_lot,
                 self.spec.stops_level)

    def shutdown(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()

    def balance(self) -> float:
        info = self._api().account_info()
        if info is None:
            raise BrokerError("تعذّر قراءة بيانات الحساب من MT5")
        return float(info.balance)

    def equity(self) -> float:
        info = self._api().account_info()
        if info is None:
            raise BrokerError("تعذّر قراءة بيانات الحساب من MT5")
        return float(info.equity)

    def tick(self) -> tuple[float, float]:
        tick = self._api().symbol_info_tick(self.spec.name)
        if tick is None:
            raise BrokerError("تعذّر قراءة السعر اللحظي")
        return float(tick.bid), float(tick.ask)

    def market_order(self, direction: Side, volume: float, sl: float, tp: float,
                     comment: str = "") -> int:
        mt5 = self._api()
        volume = self.spec.normalize_lot(volume)
        if volume <= 0:
            raise BrokerError("حجم غير صالح بعد التقريب")

        bid, ask = self.tick()
        order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        price = ask if direction == "buy" else bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.spec.name,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": self.spec.normalize_price(sl),
            "tp": self.spec.normalize_price(tp),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment[:31],  # MT5 يقصّ التعليق عند 31 حرفاً
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", "None")
            raise BrokerError(f"رفض الأمر: retcode={code} {getattr(result, 'comment', '')}")
        return int(result.order)

    # الأوامر المعلّقة (Buy Stop / Sell Stop) ---------------------------------
    def place_pending(
        self, order_type: PendingType, volume: float, price: float, comment: str = ""
    ) -> int:
        mt5 = self._api()
        volume = self.spec.normalize_lot(volume)
        if volume <= 0:
            raise BrokerError("حجم غير صالح بعد التقريب")

        mt5_type = mt5.ORDER_TYPE_BUY_STOP if order_type == "buy_stop" else mt5.ORDER_TYPE_SELL_STOP
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.spec.name,
            "volume": volume,
            "type": mt5_type,
            "price": self.spec.normalize_price(price),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", "None")
            raise BrokerError(f"رفض الأمر المعلّق: retcode={code} {getattr(result, 'comment', '')}")
        return int(result.order)

    def cancel_pending(self, ticket: int) -> bool:
        mt5 = self._api()
        request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
        result = mt5.order_send(request)
        ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
        if not ok:
            log.error("فشل إلغاء الأمر المعلّق #%s: %s", ticket, getattr(result, "retcode", "None"))
        return ok

    def pending_orders(self) -> list[PendingOrder]:
        mt5 = self._api()
        raw = mt5.orders_get(symbol=self.spec.name) or []
        out: list[PendingOrder] = []
        for order in raw:
            if order.magic != self.magic:
                continue
            if order.type == mt5.ORDER_TYPE_BUY_STOP:
                order_type: PendingType = "buy_stop"
            elif order.type == mt5.ORDER_TYPE_SELL_STOP:
                order_type = "sell_stop"
            else:
                continue  # أنواع أوامر أخرى (Limit مثلاً) لا تخص الشبكة
            out.append(
                PendingOrder(
                    ticket=int(order.ticket),
                    symbol=order.symbol,
                    order_type=order_type,
                    volume=float(order.volume_current),
                    price=float(order.price_open),
                    comment=order.comment,
                    placed_at=datetime.fromtimestamp(order.time_setup, tz=timezone.utc),
                )
            )
        return out

    def _filling_mode(self) -> Any:
        """اختيار وضع التعبئة المدعوم فعلياً — يختلف بين الوسطاء."""
        mt5 = self._api()
        info = mt5.symbol_info(self.spec.name)
        modes = getattr(info, "filling_mode", 0)
        if modes & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
            return mt5.ORDER_FILLING_FOK
        if modes & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def modify(self, ticket: int, sl: float | None = None, tp: float | None = None) -> bool:
        mt5 = self._api()
        pos = self._raw_position(ticket)
        if pos is None:
            return False
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": self.spec.normalize_price(sl if sl is not None else pos.sl),
            "tp": self.spec.normalize_price(tp if tp is not None else pos.tp),
            "magic": self.magic,
        }
        result = mt5.order_send(request)
        ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
        if not ok:
            log.error("فشل تعديل #%s: %s", ticket, getattr(result, "retcode", "None"))
        return ok

    def close(self, ticket: int, volume: float | None = None, reason: str = "") -> float:
        mt5 = self._api()
        pos = self._raw_position(ticket)
        if pos is None:
            return 0.0

        qty = self.spec.normalize_lot(pos.volume if volume is None else min(volume, pos.volume))
        if qty <= 0:
            return 0.0

        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        bid, ask = self.tick()
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": qty,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": bid if is_buy else ask,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"close:{reason}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise BrokerError(f"فشل الإغلاق #{ticket}: {getattr(result, 'retcode', 'None')}")
        return float(pos.profit)

    def _raw_position(self, ticket: int) -> Any:
        found = self._api().positions_get(ticket=ticket)
        return found[0] if found else None

    def realized_pnl(self, ticket: int) -> dict[str, Any] | None:
        """قراءة الصفقات التاريخية (deals) المرتبطة بهذا المركز."""
        mt5 = self._api()
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return None
        closing = [d for d in deals if d.entry != mt5.DEAL_ENTRY_IN]
        if not closing:
            return None
        return {
            "pnl": sum(float(d.profit) + float(d.swap) + float(d.commission)
                       for d in closing),
            "exit": float(closing[-1].price),
            "reason": "broker_close",
        }

    def positions(self) -> list[BrokerPosition]:
        mt5 = self._api()
        raw = mt5.positions_get(symbol=self.spec.name) or []
        out: list[BrokerPosition] = []
        for pos in raw:
            if pos.magic != self.magic:
                continue  # لا نلمس صفقات لم يفتحها هذا البوت
            out.append(
                BrokerPosition(
                    ticket=int(pos.ticket),
                    symbol=pos.symbol,
                    direction="buy" if pos.type == mt5.POSITION_TYPE_BUY else "sell",
                    volume=float(pos.volume),
                    entry=float(pos.price_open),
                    sl=float(pos.sl),
                    tp=float(pos.tp),
                    opened_at=datetime.fromtimestamp(pos.time, tz=timezone.utc),
                    comment=pos.comment,
                    profit=float(pos.profit),
                )
            )
        return out
