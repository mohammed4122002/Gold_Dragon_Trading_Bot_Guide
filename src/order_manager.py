"""مدير الأوامر — الطبقة الوحيدة المخوّلة بإرسال أوامر للوسيط."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .infra.broker import Broker, BrokerError
from .infra.logger import get_logger
from .models import Signal, Trade, TradeStage

log = get_logger(__name__)


class OrderManager:
    def __init__(self, config: Any, broker: Broker) -> None:
        self.config = config
        self.broker = broker
        self.symbol = config.symbol
        self.max_spread = float(config.get("broker.max_spread", 0.60))
        self.magic = int(config.get("broker.magic", 234000))

    # التحقق -----------------------------------------------------------------
    def validate(self, signal: Signal, lot_size: float) -> tuple[bool, str]:
        if not signal.is_consistent():
            return False, "ترتيب Entry/SL/TP غير منطقي"
        if lot_size <= 0:
            return False, "حجم مركز غير صالح"

        try:
            spread = self.broker.spread()
        except BrokerError as exc:
            return False, f"تعذّرت قراءة السعر: {exc}"

        if spread > self.max_spread:
            return False, f"السبريد {spread:.2f}$ فوق الحد {self.max_spread}$"

        bid, ask = self.broker.tick()
        market = ask if signal.direction == "buy" else bid
        # انزلاق كبير عن سعر الإشارة يعني أن الإعداد لم يعد هو نفسه
        drift = abs(market - signal.entry)
        if drift > signal.sl_distance * 0.5:
            return False, (
                f"السعر تحرك {drift:.2f}$ عن نقطة الدخول المخططة — الإعداد فات"
            )
        return True, "ok"

    # التنفيذ ----------------------------------------------------------------
    def execute(self, signal: Signal, lot_size: float, risk_amount: float) -> Trade | None:
        ok, reason = self.validate(signal, lot_size)
        if not ok:
            log.warning("رفض تنفيذ الإشارة: %s", reason)
            return None

        comment = f"GD_ACS{signal.acs:.0f}_{signal.poi_type}"[:31]
        try:
            # TP يُرسل عند TP3؛ الأهداف الوسيطة تُدار برمجياً بإغلاق جزئي
            ticket = self.broker.market_order(
                direction=signal.direction,
                volume=lot_size,
                sl=signal.sl,
                tp=signal.tp3,
                comment=comment,
            )
        except BrokerError as exc:
            log.error("فشل إرسال الأمر: %s", exc)
            return None

        position = next((p for p in self.broker.positions() if p.ticket == ticket), None)
        entry = position.entry if position else signal.entry

        trade = Trade(
            ticket=ticket,
            direction=signal.direction,
            symbol=self.symbol,
            entry=entry,
            sl=signal.sl,
            tp1=signal.tp1, tp2=signal.tp2, tp3=signal.tp3,
            volume=lot_size,
            initial_volume=lot_size,
            acs=signal.acs,
            poi_type=signal.poi_type,
            stage=TradeStage.OPEN,
            opened_at=datetime.now(timezone.utc),
            meta={"risk_amount": risk_amount, "setup": signal.setup,
                  "planned_entry": signal.entry, "invalidation": signal.invalidation},
        )
        log.info("فُتحت الصفقة #%s %s %.2f لوت @ %.2f", ticket, signal.direction,
                 lot_size, entry)
        return trade

    # الإدارة ----------------------------------------------------------------
    def partial_close(self, trade: Trade, fraction: float, reason: str) -> float:
        """إغلاق جزئي. يعيد 0.0 إذا كان الجزء أصغر من أصغر حجم يقبله الوسيط.

        هذه ليست حالة نادرة: حساب صغير + وقف واسع ينتج 0.01 لوت، و30% منها
        لا يمكن إغلاقها. عندها تُدار الصفقة بالوقف المتحرك وحده، ويُسجَّل ذلك
        صراحةً بدل أن يبدو الإغلاق الجزئي وكأنه تم.
        """
        volume = self.broker.spec.normalize_lot(trade.initial_volume * fraction)
        if volume <= 0:
            trade.meta["partial_skipped"] = True
            log.info(
                "تخطي الإغلاق الجزئي #%s — %.0f%% من %.2f لوت أصغر من الحد الأدنى %.2f",
                trade.ticket, fraction * 100, trade.initial_volume,
                self.broker.spec.min_lot,
            )
            return 0.0
        volume = min(volume, trade.volume)
        pnl = self.broker.close(trade.ticket, volume, reason)
        trade.volume = round(trade.volume - volume, 8)
        trade.pnl += pnl
        return pnl

    def move_to_breakeven(self, trade: Trade, offset_r: float = 0.02) -> bool:
        offset = trade.initial_risk * offset_r
        new_sl = trade.entry + offset if trade.direction == "buy" else trade.entry - offset
        if self.broker.modify(trade.ticket, sl=new_sl):
            trade.sl = new_sl
            log.info("نقل SL إلى نقطة التعادل #%s @ %.2f", trade.ticket, new_sl)
            return True
        return False

    def update_stop(self, trade: Trade, new_sl: float) -> bool:
        """تحريك الوقف في اتجاه الربح فقط — لا يُوسَّع الوقف أبداً."""
        improves = new_sl > trade.sl if trade.direction == "buy" else new_sl < trade.sl
        if not improves:
            return False
        if self.broker.modify(trade.ticket, sl=new_sl):
            trade.sl = new_sl
            log.info("تحديث الوقف المتحرك #%s → %.2f", trade.ticket, new_sl)
            return True
        return False

    def close_trade(self, trade: Trade, reason: str = "manual") -> float:
        pnl = self.broker.close(trade.ticket, None, reason)
        trade.pnl += pnl
        trade.stage = TradeStage.CLOSED
        trade.closed_at = datetime.now(timezone.utc)
        trade.exit_reason = reason
        try:
            bid, ask = self.broker.tick()
            trade.exit_price = bid if trade.direction == "buy" else ask
        except BrokerError:
            trade.exit_price = trade.entry
        trade.volume = 0.0
        return pnl
