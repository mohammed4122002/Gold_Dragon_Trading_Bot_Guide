"""محرك البوت — الحلقة الرئيسية وربط كل الوحدات.

ترتيب البوابات مقصود ولا يجوز تغييره:

    Kill Switch → Risk Manager → Psychology → News → Killzone
    → تحليل Gold Dragon → ACS → حجم المركز → تنفيذ

الأرخص أولاً: لا نُتعب المعالج بتحليل كامل قبل التأكد من أن التداول
مسموح أصلاً.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .core.gold_dragon_core import AnalysisResult
from .infra.broker import Broker, PaperBroker
from .infra.data_logger import DataLogger
from .infra.logger import get_logger
from .infra.notification_service import NotificationService
from .kill_switch import KillSwitch
from .market_analyzer import MarketAnalyzer
from .order_manager import OrderManager
from .position_tracker import PositionTracker
from .risk_manager import RiskManager

log = get_logger(__name__)


class GoldDragonBot:
    def __init__(self, config: Any, broker: Broker, feed: Any,
                 notifier: NotificationService | None = None,
                 data_logger: DataLogger | None = None) -> None:
        self.config = config
        self.broker = broker
        self.notifier = notifier or NotificationService(config)
        self.data_logger = data_logger or DataLogger(config.path("storage.database"))

        self.analyzer = MarketAnalyzer(config, feed)
        self.risk = RiskManager(config, broker.spec)
        self.orders = OrderManager(config, broker)
        self.tracker = PositionTracker(config, self.orders, self.notifier, self.data_logger)
        self.kill_switch = KillSwitch(config, broker, self.notifier)

        self.is_running = False
        self.scan_interval = int(config.get("bot.scan_interval_seconds", 60))
        self.position_interval = int(config.get("bot.position_check_seconds", 300))
        self._last_position_check = 0.0
        self._last_report_day: str | None = None
        self._last_alerts: set[tuple[int, str]] = set()
        # آخر شمعة مُرّرت للوسيط الورقي — تمنع إعادة معالجة نفس الشمعة
        self._last_bar_time: datetime | None = None
        self.started_at = datetime.now(timezone.utc)
        self.last_scan: dict[str, Any] | None = None
        self._health_server: Any = None
        # مرجع اختياري لبوت شبكة التحوّط، إن شُغّل الاثنان معاً (`run-all`) —
        # صفحة المراقبة تعرض حالته أيضاً إذا كان مضبوطاً هنا قبل start().
        self.grid_bot: Any = None

    # ═══════════════════════════════════════════════════════════════
    def start(self) -> None:
        """الحلقة الرئيسية. تتوقف بأمان عند Ctrl+C أو Kill Switch."""
        self.broker.connect()
        self.is_running = True
        self._start_health_server()
        mode = "🧪 تجريبي (dry-run)" if self.config.dry_run else "🔴 حقيقي"
        self.notifier.send(
            f"🐉 *Gold Dragon Bot* بدأ التشغيل\nالوضع: {mode}\n"
            f"الرمز: `{self.config.symbol}` | الرصيد: `{self.broker.balance():.2f}$`",
            level="warning",
        )

        try:
            while self.is_running:
                started = time.time()
                try:
                    self.tick()
                except Exception as exc:  # حلقة التشغيل لا تسقط بخطأ دورة واحدة
                    log.exception("خطأ في دورة الفحص")
                    self.notifier.send(f"⚠️ خطأ في الدورة: {exc}", level="warning")
                elapsed = time.time() - started
                time.sleep(max(self.scan_interval - elapsed, 1))
        except KeyboardInterrupt:
            log.info("إيقاف يدوي بواسطة المستخدم")
        finally:
            self.stop()

    def _start_health_server(self) -> None:
        """تشغيل واجهة المراقبة إن حُدّد منفذ (منصات الاستضافة تضبط PORT)."""
        import os

        port = os.environ.get("PORT") or self.config.get("health.port")
        if not port or not self.config.get("health.enabled", True):
            return
        from .infra.health import start_health_server

        self._health_server = start_health_server(self, int(port), grid_bot=self.grid_bot)

    def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        self.notifier.send("🛑 توقف Gold Dragon Bot", level="warning")
        if self._health_server is not None:
            self._health_server.shutdown()
        try:
            self.broker.shutdown()
        except Exception as exc:
            log.error("خطأ أثناء إغلاق الاتصال بالوسيط: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    def tick(self) -> dict[str, Any]:
        """دورة واحدة كاملة — قابلة للاستدعاء المباشر في الاختبارات."""
        now = time.time()

        if now - self._last_position_check >= self.position_interval:
            self._last_position_check = now
            self.check_positions()

        self._daily_report_if_due()
        return self.scan_market()

    # ═══════════════════════════════════════════════════════════════
    def scan_market(self) -> dict[str, Any]:
        if self.kill_switch.active:
            return self._outcome("kill_switch", f"Kill Switch نشط: {self.kill_switch.reason}")

        # 1) البيانات والتحليل. القفل الانتقامي يُمرَّر للبوابة النفسية،
        #    لكننا نُكمل التحليل لأن التقارير والتنبيهات تبقى مفيدة أثناء القفل.
        result = self.analyzer.analyze(
            risk_locked=self.risk.is_locked, lock_reason=self.risk.lock_reason
        )
        if result is None:
            return self._outcome("no_data", "تعذّر جلب بيانات صالحة")

        # 2) تقديم الساعة في وضع التنفيذ الورقي، ثم مزامنة ما أغلقه الوسيط.
        #    يجب أن يسبق قرار المخاطرة حتى يُبنى على رصيد ومراكز حقيقية.
        self.advance_paper_clock()
        for trade in self.tracker.sync_with_broker():
            self.risk.record_result(trade.pnl)

        self.data_logger.log_analysis(result.to_dict())
        self._emit_alerts(result)

        balance = self.broker.balance()
        positions = self.broker.positions()

        snapshot = self.risk.snapshot(balance)
        triggered = self.kill_switch.auto_check(
            {
                "drawdown": snapshot["drawdown"],
                "daily_pnl_pct": snapshot["daily_pnl"] / balance if balance else 0.0,
                "consecutive_losses": snapshot["consecutive_losses"],
            },
            stop_engine=self.stop,
        )
        if triggered:
            return self._outcome("kill_switch", self.kill_switch.reason, result)

        open_risk = self.risk.open_risk_fraction(positions, balance)
        decision = self.risk.can_trade(balance, len(positions), open_risk)
        if not decision.allowed:
            return self._outcome("risk_blocked", decision.reason, result)

        if result.signal is None:
            return self._outcome("no_signal", " • ".join(result.rejections), result)

        return self.execute_signal(result, balance)

    # ═══════════════════════════════════════════════════════════════
    def execute_signal(self, result: AnalysisResult, balance: float) -> dict[str, Any]:
        signal = result.signal
        assert signal is not None

        sizing = self.risk.calculate_position_size(balance, signal.entry, signal.sl)
        if sizing["lots"] <= 0:
            self.data_logger.log_signal(signal, False, sizing["reason"])
            self.notifier.send_rejection("حجم مركز غير صالح", sizing["reason"])
            return self._outcome("size_rejected", sizing["reason"], result)

        signal.lot_size = sizing["lots"]
        signal.risk_amount = sizing["risk_amount"]

        trade = self.orders.execute(signal, sizing["lots"], sizing["risk_amount"])
        if trade is None:
            self.data_logger.log_signal(signal, False, "رفض التنفيذ عند الوسيط")
            return self._outcome("execution_failed", "رفض التنفيذ عند الوسيط", result)

        self.tracker.register(trade)
        self.data_logger.log_signal(signal, True)
        self.notifier.send_trade_alert(signal, sizing["lots"], sizing["risk_amount"])
        return self._outcome("executed", f"صفقة #{trade.ticket}", result, trade=trade)

    # ═══════════════════════════════════════════════════════════════
    def advance_paper_clock(self) -> list[dict[str, Any]]:
        """تغذية الوسيط الورقي بشموع السوق الحقيقية (وضع forward test).

        الوسيط الورقي لا يعرف السعر من تلقاء نفسه — نحن من يُقدّم ساعته.
        نمرّر كل شمعة مكتملة جديدة عبر ``process_bar`` ليُفحص ضرب SL/TP
        داخلها، فتكون نتائج المحاكاة مطابقة لما كان سيحدث فعلاً.

        لا أثر لهذه الدالة في التشغيل الحقيقي عبر MT5 (الوسيط يُدير أوامره).
        """
        broker = self.broker
        if not isinstance(broker, PaperBroker):
            return []

        df_m15 = self.analyzer.last_bars.get("M15")
        if df_m15 is None or df_m15.empty:
            return []

        last = df_m15.iloc[-1]
        last_time = pd.Timestamp(last["time"]).to_pydatetime()

        # أول دورة: لا مراكز مفتوحة بعد، ولا معنى لإعادة تشغيل التاريخ كله.
        # نكتفي بضبط السعر الحالي ونبدأ العدّ من هنا.
        if self._last_bar_time is None:
            broker.set_price(float(last["close"]), last_time)
            self._last_bar_time = last_time
            return []

        events: list[dict[str, Any]] = []
        for _, bar in df_m15.iterrows():
            when = pd.Timestamp(bar["time"]).to_pydatetime()
            if when <= self._last_bar_time:
                continue  # شمعة سبق تمريرها
            events += broker.process_bar(
                float(bar["high"]), float(bar["low"]), float(bar["close"]), when
            )
            self._last_bar_time = when

        # السعر الجاري (شمعة غير مكتملة أو تحديث بين الشموع)
        broker.set_price(float(last["close"]), last_time)
        return events

    def check_positions(self) -> list[dict[str, Any]]:
        df_m15 = self.analyzer.last_bars.get("M15")
        closed = self.tracker.sync_with_broker()
        for trade in closed:
            self.risk.record_result(trade.pnl)
        return self.tracker.manage(df_m15)

    def _daily_report_if_due(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_report_day == today:
            return
        if datetime.now(timezone.utc).hour < 21:  # نهاية الجلسة الأمريكية
            return
        self._last_report_day = today
        balance = self.broker.balance()
        report = self.tracker.daily_report(balance, self.risk.snapshot(balance))
        stats = self.data_logger.summary()
        report += (
            f"\n\n📈 *الإجمالي التاريخي*\nصفقات: `{stats['trades']}` | "
            f"نجاح: `{stats['win_rate']:.0%}` | PF: `{stats['profit_factor']:.2f}` | "
            f"متوسط R: `{stats['avg_r']:.2f}`"
        )
        self.notifier.send(report, level="warning")
        self.data_logger.log_event("daily_report", report)

    def _emit_alerts(self, result: AnalysisResult) -> None:
        """إرسال تنبيهات البروتوكول دون تكرار نفس التنبيه في كل دورة."""
        if not self.config.get("gold_dragon.alert_protocol.enabled", True):
            return
        current = {(a["level"], a["text"]) for a in result.alerts}
        for level, text in sorted(current - self._last_alerts, reverse=True):
            self.notifier.send_alert_level(level, text)
        self._last_alerts = current

    def _outcome(self, status: str, detail: str = "",
                 result: AnalysisResult | None = None, **extra: Any) -> dict[str, Any]:
        if status not in {"executed"}:
            log.info("نتيجة الدورة: %s — %s", status, detail)
        outcome = {"status": status, "detail": detail, "result": result,
                   "at": datetime.now(timezone.utc).isoformat(), **extra}
        self.last_scan = outcome
        return outcome
