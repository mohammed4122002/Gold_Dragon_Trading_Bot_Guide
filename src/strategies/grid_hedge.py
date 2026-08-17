"""شبكة تحوّط Buy Stop / Sell Stop — استراتيجية منفصلة تماماً عن Gold Dragon SMC.

الفكرة (نفس ما تُظهره لقطات تطبيقات التداول الشائعة لهذا النمط): أمر Buy
Stop فوق السعر وأمر Sell Stop تحته في آن واحد. كل ما ينضرب أمر تُفتح صفقة
وتُمدَّد الشبكة بأمر جديد بنفس الاتجاه أبعد قليلاً، وتُغلق كل صفقات "السلة"
معاً عند بلوغ ربح عائم إجمالي (Basket TP). تربح من التذبذب العرضي
(Sideways) لأن كل صفقة تُفتح عند قمة/قاع محلي مؤقت، وتخسر بعنف في اختراق
اتجاهي قوي بلا فلتر — الفرق بين اللافتتين "càng cháy" (تحرق) و"càng ăn"
(تربح) في الصور المرجعية هو بالضبط وجود أو غياب حدود الحماية أدناه.

═══ لماذا "الشبكة الأسرع" تحتاج هندسة مختلفة، لا مجرد أرقام أصغر ═══

تضييق الخطوة يرفع التردد، لكنه يضاعف أربع مشاكل تنفيذية تقتل الشبكة بصمت
على حساب حقيقي بينما تبدو ممتازة على الديمو:

1. **التكاليف تبتلع الهدف.** على الذهب بلوت 0.01: حركة 1$ = ربح 1$، والسبريد
   وحده ~0.20$ لكل صفقة + عمولة ECN. سلة من 5 صفقات تكلّف ~1.25$ ذهاباً
   وإياباً؛ هدف "ثابت" بقيمة 1.5$ يعني صافياً ~0.25$ — تعمل مجاناً وتُغني
   وسيطك. الحل هنا: ``basket_tp_mode: cost_plus`` يحسب الهدف = **تكلفة السلة
   المفتوحة فعلاً + الصافي المطلوب**، فيتمدد الهدف تلقائياً كلما كبرت السلة.

2. **الوسيط يرفض الأوامر القريبة.** لكل رمز ``stops_level`` (أدنى مسافة بين
   أمر معلّق والسوق). خطوة أضيق منها = رفض ``retcode 10016`` وتوقّف الشبكة
   عن التمدد **بصمت**. الحل: قصّ سعر كل أمر إلى مسافة آمنة قبل الإرسال.

3. **اتساع السبريد يفترس الشبكة الضيقة.** عند الأخبار/التدوير اليومي يتضاعف
   السبريد؛ خطوة 1.5$ مع سبريد 0.80$ تعني دخولاً خاسراً فوراً. الحل: بوابة
   سبريد تمنع **تسليح مستويات جديدة** (ولا تمنع الخروج أبداً).

4. **السلة تكبر حتى تنفجر.** كل-أو-لا-شيء يعني أن سلة عالقة في اتجاه تبقى
   تتضخم. الحل: **حصاد الأزواج** — إغلاق زوج شراء+بيع متقابل صافيه يغطي
   تكلفته ويزيد، فتتقلّص السلة تدريجياً ويُبنَك التقدّم بدل انتظار الكل.
   بالإضافة إلى **حد عمر السلة**: سلة مفتوحة ساعات = اتجاه، لا تذبذب.

لا حالة على القرص هنا عمداً: شكل الشبكة (الصفقات والأوامر المعلّقة) يُشتق
بالكامل من الوسيط في كل دورة — نفس فلسفة ``PositionTracker.sync_with_broker``
في البوت الأساسي. الشيء الوحيد الذي يحتاج بقاءً بعد إعادة التشغيل (حد
الخسارة اليومي/الأسبوعي والقفل الانتقامي) موجود أصلاً في ``RiskManager``
ومُعاد استخدامه هنا حرفياً عبر ``daily_guard()`` بدل تكرار منطقه.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from ..infra.broker import Broker, BrokerError, BrokerPosition, PendingOrder
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

        self.basket_tp_mode = str(config.get("grid.basket_tp_mode", "cost_plus"))
        self.basket_tp_usd = float(config.get("grid.basket_tp_usd", 5.0))
        self.basket_tp_per_lot_usd = float(config.get("grid.basket_tp_per_lot_usd", 8.0))
        self.basket_net_target_usd = float(config.get("grid.basket_net_target_usd", 1.0))
        self.commission_per_lot = float(config.get("grid.cost_commission_per_lot", 0.0))
        self.max_basket_loss_pct = float(config.get("grid.max_basket_loss_pct", 0.03))

        self.trend_guard_enabled = bool(config.get("grid.trend_guard.enabled", True))
        self.max_directional_levels = int(
            config.get("grid.trend_guard.max_directional_levels", 5)
        )
        self.reinit_after_close = bool(config.get("grid.reinitialize_after_close", True))

        # بوابة التنفيذ — تمنع التسليح فقط، ولا تمنع الخروج إطلاقاً
        self.max_spread = float(config.get("grid.execution_guard.max_spread", 0.0))
        self.min_stop_distance_mult = float(
            config.get("grid.execution_guard.min_stop_distance_mult", 1.2)
        )
        self.min_stop_distance_usd = float(
            config.get("grid.execution_guard.min_stop_distance_usd", 0.0)
        )

        self.harvest_enabled = bool(config.get("grid.pair_harvest.enabled", True))
        self.harvest_min_net = float(config.get("grid.pair_harvest.min_net_usd", 0.30))

        self.max_basket_age_minutes = float(config.get("grid.max_basket_age_minutes", 0))
        self.cooldown_seconds = float(config.get("grid.cooldown_seconds_after_close", 0))
        self._cooldown_until: datetime | None = None

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

    def _market(self, price: float) -> tuple[float, float]:
        """(bid, ask) من الوسيط، وإلا السعر الممرَّر لكليهما."""
        try:
            return self.broker.tick()
        except Exception:  # وسيط لم يستلم سعراً بعد — نكتفي بالسعر الممرَّر
            return price, price

    def spread_now(self, price: float) -> float:
        bid, ask = self._market(price)
        return max(ask - bid, 0.0)

    # التكاليف -----------------------------------------------------------------
    def position_cost(self, position: BrokerPosition, spread: float) -> float:
        """تكلفة إغلاق مركز واحد بالدولار: السبريد + عمولة الوسيط.

        متحفظة عمداً: سبريد الدخول مدفوع أصلاً ومنعكس في سعر الدخول، فاحتساب
        سبريد كامل هنا يبالغ قليلاً في التقدير. المبالغة في اتجاه الأمان
        مقصودة — نُغلق متأخرين قليلاً بهامش أكبر، لا مبكرين بخسارة تبدو ربحاً.
        """
        spec = self.broker.spec
        return (
            spread * spec.contract_size * position.volume
            + self.commission_per_lot * position.volume
        )

    def basket_cost(self, positions: list[BrokerPosition], spread: float) -> float:
        return sum(self.position_cost(p, spread) for p in positions)

    def _basket_target(self, positions: list[BrokerPosition], spread: float) -> float:
        """هدف الربح العائم الذي يُغلق السلة كاملة عنده."""
        if self.basket_tp_mode == "cost_plus":
            # الهدف يتمدد تلقائياً مع كل مستوى جديد: سلة أكبر = تكلفة أعلى
            # = هدف أعلى. هذا ما يمنع "ربحاً" يساوي عمولة الوسيط بالضبط.
            return self.basket_cost(positions, spread) + self.basket_net_target_usd
        if self.basket_tp_mode == "per_lot":
            volume = max(self.basket_volume(positions), self.lot_per_level)
            return self.basket_tp_per_lot_usd * volume
        return self.basket_tp_usd

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

    def min_pending_distance(self) -> float:
        """أدنى مسافة آمنة بين أمر معلّق والسعر الحالي.

        تأخذ الأكبر بين قيد الوسيط المعلن (``stops_level`` مضروباً بهامش
        أمان) وحد أدنى من الإعدادات — كثير من الوسطاء يعلنون صفراً ثم
        يرفضون ديناميكياً حسب السبريد اللحظي.
        """
        return max(
            self.broker.spec.stops_level * self.min_stop_distance_mult,
            self.min_stop_distance_usd,
        )

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

    def _reset_basket(self, positions: list[BrokerPosition], reason: str,
                      now: datetime | None = None) -> float:
        """إغلاق كل صفقات السلة وإلغاء كل الأوامر المعلّقة التابعة لها، وتسجيل النتيجة."""
        closed_pnl = self._close_basket(positions, reason) if positions else 0.0
        self._cancel_our_pending()
        if positions:
            self.risk.record_result(closed_pnl)
            self._start_cooldown(now)
        return closed_pnl

    def _start_cooldown(self, now: datetime | None) -> None:
        if self.cooldown_seconds <= 0:
            return
        base = now or datetime.now(timezone.utc)
        self._cooldown_until = base + timedelta(seconds=self.cooldown_seconds)

    def _in_cooldown(self, now: datetime | None) -> bool:
        """تهدئة بعد إغلاق سلة — تمنع إعادة التسليح فوراً داخل نفس الحركة العنيفة.

        الحركة التي أغلقت السلة بالقطع المبكر غالباً لم تنتهِ بعد؛ إعادة بناء
        شبكة فيها فوراً تعني تكرار نفس الخسارة مباشرة.
        """
        if self._cooldown_until is None:
            return False
        current = now or datetime.now(timezone.utc)
        if current >= self._cooldown_until:
            self._cooldown_until = None
            return False
        return True

    def _basket_age_minutes(self, positions: list[BrokerPosition],
                            now: datetime | None) -> float:
        if not positions:
            return 0.0
        current = now or datetime.now(timezone.utc)
        oldest = min(p.opened_at for p in positions)
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        return max((current - oldest).total_seconds() / 60.0, 0.0)

    # حصاد الأزواج --------------------------------------------------------------
    def harvest_pairs(self, positions: list[BrokerPosition], spread: float,
                      now: datetime | None = None) -> tuple[float, int]:
        """إغلاق أزواج (شراء+بيع) متقابلة صافيها يغطي تكلفتها ويزيد.

        يقلّص السلة تدريجياً بدل انتظار "كل-أو-لا-شيء": الزوج الأفضل ربحاً
        من كل جهة يُغلق معاً، فيُبنَك التقدّم وتنخفض المخاطرة المفتوحة، ويبقى
        في السلة الأزواج التي لم تنضج بعد. هذا ما يمنع السلة من التضخم حتى
        الانفجار في سوق متذبذب طويل.

        يعيد (الربح المحقق، عدد الصفقات المغلقة).
        """
        if not self.harvest_enabled:
            return 0.0, 0

        buys = sorted((p for p in positions if p.direction == "buy"),
                      key=lambda p: p.profit, reverse=True)
        sells = sorted((p for p in positions if p.direction == "sell"),
                       key=lambda p: p.profit, reverse=True)

        harvested_pnl = 0.0
        closed = 0
        while buys and sells:
            buy, sell = buys[0], sells[0]
            # عتبة الزوج = تكلفة إغلاقه + الحد الأدنى المطلوب للحصاد
            threshold = (
                self.position_cost(buy, spread)
                + self.position_cost(sell, spread)
                + self.harvest_min_net
            )
            if buy.profit + sell.profit < threshold:
                break  # الأزواج مرتّبة تنازلياً — ما بعده أسوأ
            harvested_pnl += self.broker.close(buy.ticket, reason="pair_harvest")
            harvested_pnl += self.broker.close(sell.ticket, reason="pair_harvest")
            closed += 2
            buys.pop(0)
            sells.pop(0)

        if closed:
            # إغلاق جزئي داخل سلة قائمة: يدخل الحد اليومي لكنه ليس "نتيجة
            # صفقة مكتملة"، فلا يمسّ عدّاد الخسارات المتتالية.
            self.risk.record_result(harvested_pnl, count_streak=False)
            log.info("حصاد أزواج: %d صفقة → %+.2f$", closed, harvested_pnl)
        return harvested_pnl, closed

    # التسليح -------------------------------------------------------------------
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

        # قصّ إلزامي لمسافة الوسيط الدنيا: خطوة ضيقة قد تضع الأمر أقرب مما
        # يقبله الوسيط فيُرفض ويتوقف تمدد الشبكة بصمت.
        bid, ask = self._market(price)
        min_distance = self.min_pending_distance()
        if side == "buy":
            next_price = max(next_price, ask + min_distance)
        else:
            next_price = min(next_price, bid - min_distance)
        next_price = self.broker.spec.normalize_price(next_price)

        try:
            self.broker.place_pending(
                "buy_stop" if side == "buy" else "sell_stop",
                self.lot_per_level, next_price, TAG,
            )
        except BrokerError as exc:
            # رفض التسليح ليس سبباً لإسقاط الدورة — الخروج والحماية أهم
            log.warning("رفض الوسيط تسليح جهة %s عند %.2f: %s", side, next_price, exc)
            return False
        return True

    # الدورة الرئيسية -----------------------------------------------------------
    def sync(self, price: float, balance: float, kill_switch_active: bool = False,
             kill_switch_reason: str = "", atr_value: float | None = None,
             now: datetime | None = None) -> dict[str, Any]:
        """دورة واحدة كاملة — قابلة للاستدعاء المباشر في الاختبارات.

        ``now`` يُمرَّر صراحةً في الاختبارات لضبط عمر السلة والتهدئة؛ في
        التشغيل الحقيقي يُترك None فيُقرأ الوقت الفعلي.
        """
        positions = self.our_positions()
        spread = self.spread_now(price)

        # 1) الحماية أولاً — الأرخص وأهم شيء (نفس ترتيب bot_engine: kill switch
        #    ثم مدير المخاطرة قبل أي منطق تداول).
        if kill_switch_active:
            count, volume = len(positions), self.basket_volume(positions)
            closed_pnl = self._reset_basket(positions, "kill_switch", now)
            return self._outcome("kill_switch", kill_switch_reason, closed_pnl=closed_pnl,
                                 positions_count=count, volume=volume)

        guard = self.risk.daily_guard(balance)
        if not guard.allowed:
            count, volume = len(positions), self.basket_volume(positions)
            closed_pnl = self._reset_basket(positions, "risk_blocked", now)
            return self._outcome("risk_blocked", guard.reason, closed_pnl=closed_pnl,
                                 positions_count=count, volume=volume)

        # 2) مخارج السلة الكاملة. ``closure`` تحمل بيانات الإغلاق (لتسجيلها في
        #    قاعدة البيانات لاحقاً) بمعزل عن ``status``/``detail`` النهائيين،
        #    لأن reinit_after_close=true (الافتراضي) يجعل الدورة تُكمل لإعادة
        #    بناء الشبكة وتُرجع outcome آخر في نهاية الدالة — فلا يجوز أن
        #    يُفقَد closed_pnl في ذلك المسار.
        closure: dict[str, Any] | None = None
        floating = self.basket_pnl(positions)
        age_minutes = self._basket_age_minutes(positions, now)

        exit_reason: str | None = None
        exit_detail = ""
        if positions and balance > 0 and (-floating / balance) >= self.max_basket_loss_pct:
            exit_reason, exit_detail = "basket_loss_cutoff", "خسارة عائمة"
        elif (
            positions
            and self.max_basket_age_minutes > 0
            and age_minutes >= self.max_basket_age_minutes
        ):
            # سلة معمّرة = السعر لم يعد يتذبذب حول الشبكة بل يتجه بعيداً عنها.
            # الخروج بالوضع الحالي أرخص من انتظار ارتداد قد لا يأتي.
            exit_reason, exit_detail = "basket_age_cutoff", f"عمر السلة {age_minutes:.0f}د"
        elif positions and floating >= self._basket_target(positions, spread):
            exit_reason, exit_detail = "basket_tp", "ربح السلة"

        if exit_reason:
            count, volume = len(positions), self.basket_volume(positions)
            closed_pnl = self._reset_basket(positions, exit_reason, now)
            positions = []
            closure = {"status": exit_reason,
                       "detail": f"{exit_detail} {closed_pnl:+.2f}$", "closed_pnl": closed_pnl,
                       "positions_count": count, "volume": volume}
            if not self.reinit_after_close:
                return self._outcome(**closure)

        # 3) حصاد الأزواج — إغلاق جزئي على ما تبقّى من السلة (إن بقيت)
        harvest_pnl, harvested = 0.0, 0
        if positions:
            harvest_pnl, harvested = self.harvest_pairs(positions, spread, now)
            if harvested:
                positions = self.our_positions()
                floating = self.basket_pnl(positions)

        # 4) تمديد/تهيئة الشبكة — ما إن تُغلق السلة (أو لم تكن موجودة أصلاً)
        pending = self.our_pending()
        base_extra: dict[str, Any] = {
            "floating": floating, "harvest_pnl": harvest_pnl, "harvested": harvested,
            "spread": spread, "basket_age_minutes": age_minutes,
        }

        def finish(status: str, detail: str, **extra: Any) -> dict[str, Any]:
            """يدمج بيانات الإغلاق/الحصاد في المخرَج النهائي أياً كان المسار."""
            merged = {**base_extra, **extra}
            if closure is not None:
                detail = f"{closure['detail']} ثم — {detail}"
                merged.update(closed_pnl=closure["closed_pnl"],
                              positions_count=closure["positions_count"],
                              volume=closure["volume"])
            return self._outcome(status, detail, **merged)

        if self._in_cooldown(now):
            return finish("cooldown", f"تهدئة بعد إغلاق سلة ({self.cooldown_seconds:.0f}ث)")

        if self.max_spread > 0 and spread > self.max_spread:
            # الخروج تمّ أعلاه بالفعل؛ الممنوع هنا هو الدخول بمستويات جديدة
            return finish(
                "spread_blocked",
                f"السبريد {spread:.2f}$ فوق الحد {self.max_spread:.2f}$ — لا تسليح جديد",
            )

        total_slots = len(positions) + len(pending)
        if total_slots >= self.max_levels:
            return finish("max_levels", f"بلغت الشبكة الحد الأقصى ({self.max_levels})")

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
        if harvested:
            detail += f" | حصاد {harvested} صفقة {harvest_pnl:+.2f}$"
        if buy_paused or sell_paused:
            paused_sides = " و".join(
                s for s, p in (("الشراء", buy_paused), ("البيع", sell_paused)) if p
            )
            detail += f" | فلتر الاتجاه أوقف تمديد جهة {paused_sides} ({streak} صفقات متتالية)"

        return finish(status, detail, extended=extended)

    def _outcome(self, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
        outcome = {"status": status, "detail": detail, "at": datetime.now(timezone.utc).isoformat(),
                   **extra}
        log.info("شبكة التحوّط: %s — %s", status, detail)
        return outcome
