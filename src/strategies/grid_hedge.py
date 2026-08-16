"""شبكة تحوّط Buy Stop / Sell Stop — استراتيجية منفصلة تماماً عن Gold Dragon SMC.

الفكرة (نفس ما تُظهره لقطات تطبيقات التداول الشائعة لهذا النمط): أمر Buy
Stop فوق السعر وأمر Sell Stop تحته في آن واحد. كل ما ينضرب أمر تُفتح صفقة
وتُمدَّد الشبكة بأمر جديد بنفس الاتجاه أبعد قليلاً، وتُغلق كل صفقات "السلة"
معاً عند بلوغ ربح عائم إجمالي (Basket TP). تربح من التذبذب العرضي
(Sideways) لأن كل صفقة تُفتح عند قمة/قاع محلي مؤقت، وتخسر بعنف في اختراق
اتجاهي قوي بلا فلتر — الفرق بين اللافتتين "càng cháy" (تحرق) و"càng ăn"
(تربح) في الصور المرجعية هو بالضبط وجود أو غياب حدود الحماية أدناه.

لا حالة على القرص هنا عمداً: شكل الشبكة (الصفقات والأوامر المعلّقة) يُشتق
بالكامل من الوسيط في كل دورة — نفس فلسفة ``PositionTracker.sync_with_broker``
في البوت الأساسي. الشيء الوحيد الذي يحتاج بقاءً بعد إعادة التشغيل (حد
الخسارة اليومي/الأسبوعي والقفل الانتقامي) موجود أصلاً في ``RiskManager``
ومُعاد استخدامه هنا حرفياً عبر ``daily_guard()`` بدل تكرار منطقه.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from ..infra.broker import Broker, BrokerPosition, PendingOrder
from ..infra.logger import get_logger
from ..risk_manager import RiskManager

log = get_logger(__name__)

TAG = "GDGRID"

Side = Literal["buy", "sell"]


class HedgeGridStrategy:
    def __init__(self, config: Any, broker: Broker, risk: RiskManager) -> None:
        self.config = config
        self.broker = broker
        self.risk = risk

        self.step_mode = str(config.get("grid.step_mode", "fixed"))
        self.step_fixed = float(config.get("grid.step_fixed", 3.0))
        self.step_atr_multiple = float(config.get("grid.step_atr_multiple", 0.5))

        self.lot_per_level = float(config.get("grid.lot_per_level", 0.01))
        self.max_levels = int(config.get("grid.max_levels", 12))

        self.basket_tp_mode = str(config.get("grid.basket_tp_mode", "fixed"))
        self.basket_tp_usd = float(config.get("grid.basket_tp_usd", 5.0))
        self.basket_tp_per_lot_usd = float(config.get("grid.basket_tp_per_lot_usd", 8.0))
        self.max_basket_loss_pct = float(config.get("grid.max_basket_loss_pct", 0.03))

        self.trend_guard_enabled = bool(config.get("grid.trend_guard.enabled", True))
        self.max_directional_levels = int(
            config.get("grid.trend_guard.max_directional_levels", 5)
        )
        self.reinit_after_close = bool(config.get("grid.reinitialize_after_close", True))

    # القراءة من الوسيط --------------------------------------------------------
    def our_positions(self) -> list[BrokerPosition]:
        return [p for p in self.broker.positions() if p.comment == TAG]

    def our_pending(self) -> list[PendingOrder]:
        return [o for o in self.broker.pending_orders() if o.comment == TAG]

    @staticmethod
    def basket_pnl(positions: list[BrokerPosition]) -> float:
        return sum(p.profit for p in positions)

    @staticmethod
    def basket_volume(positions: list[BrokerPosition]) -> float:
        return sum(p.volume for p in positions)

    def _directional_streak(self, positions: list[BrokerPosition]) -> tuple[Side | None, int]:
        """اتجاه وطول آخر سلسلة صفقات متتالية بلا انقطاع من الاتجاه المعاكس.

        مرتّبة زمنياً بحسب ``opened_at`` — لا حاجة لحالة منفصلة، الوسيط
        يحفظ توقيت كل صفقة أصلاً.
        """
        if not positions:
            return None, 0
        ordered = sorted(positions, key=lambda p: p.opened_at)
        direction = ordered[-1].direction
        streak = 0
        for pos in reversed(ordered):
            if pos.direction != direction:
                break
            streak += 1
        return direction, streak

    # المسافة بين المستويات ----------------------------------------------------
    def compute_step(self, atr_value: float | None = None) -> float:
        if self.step_mode == "atr" and atr_value:
            return max(atr_value * self.step_atr_multiple, self.broker.spec.point * 10)
        return self.step_fixed

    def _basket_target(self, positions: list[BrokerPosition]) -> float:
        if self.basket_tp_mode == "per_lot":
            volume = max(self.basket_volume(positions), self.lot_per_level)
            return self.basket_tp_per_lot_usd * volume
        return self.basket_tp_usd

    # الإجراءات ----------------------------------------------------------------
    def _close_basket(self, positions: list[BrokerPosition], reason: str) -> float:
        total = 0.0
        for pos in positions:
            total += self.broker.close(pos.ticket, reason=reason)
        return total

    def _cancel_our_pending(self) -> int:
        count = 0
        for order in self.our_pending():
            if self.broker.cancel_pending(order.ticket):
                count += 1
        return count

    def _reset_basket(self, positions: list[BrokerPosition], reason: str) -> float:
        """إغلاق كل صفقات السلة وإلغاء كل الأوامر المعلّقة التابعة لها، وتسجيل النتيجة."""
        closed_pnl = self._close_basket(positions, reason) if positions else 0.0
        self._cancel_our_pending()
        if positions:
            self.risk.record_result(closed_pnl)
        return closed_pnl

    def _ensure_side(self, side: Side, positions: list[BrokerPosition],
                     has_pending: bool, price: float, step: float, paused: bool) -> bool:
        """تمديد جانب واحد من الشبكة إن لم يكن مسلّحاً بالفعل — يعيد True إن أضاف أمراً."""
        if has_pending or paused:
            return False

        same_side = [p for p in positions if p.direction == side]
        if same_side:
            reference = (
                max(p.entry for p in same_side) if side == "buy"
                else min(p.entry for p in same_side)
            )
        else:
            reference = price
        next_price = reference + step if side == "buy" else reference - step

        self.broker.place_pending(
            "buy_stop" if side == "buy" else "sell_stop", self.lot_per_level, next_price, TAG
        )
        return True

    # الدورة الرئيسية -----------------------------------------------------------
    def sync(self, price: float, balance: float, kill_switch_active: bool = False,
             kill_switch_reason: str = "", atr_value: float | None = None) -> dict[str, Any]:
        """دورة واحدة كاملة — قابلة للاستدعاء المباشر في الاختبارات."""
        positions = self.our_positions()

        # 1) الحماية أولاً — الأرخص وأهم شيء (نفس ترتيب bot_engine: kill switch
        #    ثم مدير المخاطرة قبل أي منطق تداول).
        if kill_switch_active:
            closed_pnl = self._reset_basket(positions, "kill_switch")
            return self._outcome("kill_switch", kill_switch_reason, closed_pnl=closed_pnl)

        guard = self.risk.daily_guard(balance)
        if not guard.allowed:
            closed_pnl = self._reset_basket(positions, "risk_blocked")
            return self._outcome("risk_blocked", guard.reason, closed_pnl=closed_pnl)

        # 2) قطع مبكر لخسارة السلة العائمة — مستقل عن حد الخسارة اليومي،
        #    خط دفاع أول يعيد ضبط هذه الشبكة فقط دون قفل التداول ليوم كامل.
        floating = self.basket_pnl(positions)
        if positions and balance > 0 and (-floating / balance) >= self.max_basket_loss_pct:
            closed_pnl = self._reset_basket(positions, "basket_loss_cutoff")
            positions = []
            outcome = self._outcome(
                "basket_loss_cutoff", f"خسارة عائمة {closed_pnl:+.2f}$", closed_pnl=closed_pnl
            )
            if not self.reinit_after_close:
                return outcome

        # 3) هدف ربح السلة
        elif positions and floating >= self._basket_target(positions):
            closed_pnl = self._reset_basket(positions, "basket_tp")
            positions = []
            outcome = self._outcome(
                "basket_tp", f"ربح السلة {closed_pnl:+.2f}$", closed_pnl=closed_pnl
            )
            if not self.reinit_after_close:
                return outcome

        # 4) تمديد/تهيئة الشبكة — ما إن تُغلق السلة (أو لم تكن موجودة أصلاً)
        pending = self.our_pending()
        total_slots = len(positions) + len(pending)
        if total_slots >= self.max_levels:
            return self._outcome(
                "max_levels", f"بلغت الشبكة الحد الأقصى ({self.max_levels})", floating=floating
            )

        step = self.compute_step(atr_value)
        streak_direction, streak = self._directional_streak(positions)
        has_buy_pending = any(o.order_type == "buy_stop" for o in pending)
        has_sell_pending = any(o.order_type == "sell_stop" for o in pending)

        buy_paused = (
            self.trend_guard_enabled
            and streak_direction == "buy"
            and streak >= self.max_directional_levels
        )
        sell_paused = (
            self.trend_guard_enabled
            and streak_direction == "sell"
            and streak >= self.max_directional_levels
        )

        added_buy = False
        if total_slots < self.max_levels:
            added_buy = self._ensure_side(
                "buy", positions, has_buy_pending, price, step, buy_paused
            )
            total_slots += int(added_buy)
        added_sell = False
        if total_slots < self.max_levels:
            added_sell = self._ensure_side(
                "sell", positions, has_sell_pending, price, step, sell_paused
            )

        extended = added_buy or added_sell
        status = "initialized" if not positions and not pending else (
            "extended" if extended else "synced"
        )
        detail = (
            f"صفقات {len(positions)} | معلّق {len(pending) + int(added_buy) + int(added_sell)} "
            f"| عائم {floating:+.2f}$"
        )
        if buy_paused or sell_paused:
            paused_sides = " و".join(
                s for s, p in (("الشراء", buy_paused), ("البيع", sell_paused)) if p
            )
            detail += f" | فلتر الاتجاه أوقف تمديد جهة {paused_sides} ({streak} صفقات متتالية)"

        return self._outcome(status, detail, floating=floating, extended=extended)

    def _outcome(self, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
        outcome = {"status": status, "detail": detail, "at": datetime.now(timezone.utc).isoformat(),
                   **extra}
        log.info("شبكة التحوّط: %s — %s", status, detail)
        return outcome
