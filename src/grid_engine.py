"""محرك شبكة التحوّط — حلقة منفصلة عن ``GoldDragonBot``.

الاستراتيجيتان لا تتشاركان تنفيذاً: Gold Dragon SMC يفتح صفقة واحدة مُدارة
بوقف/هدف واضحين، والشبكة تفتح سلة صفقات صغيرة بلا وقف فردي تُدار كوحدة.
تشغيلهما معاً على نفس الحساب ممكن (أوامر كل منهما موسومة بـ ``comment``
مختلف)، لذلك لكل منهما ``RiskManager`` و``KillSwitch`` بحالة منفصلة على
القرص — حد الخسارة اليومي واحد في الإعدادات (``risk.yaml``) لكن عدّاده
مستقل لكل بوت حتى لا يُغلق أحدهما التداول بسبب خسارة الآخر.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .core.indicators import atr
from .infra.broker import Broker, PaperBroker
from .infra.data_feed import DataFeed, DataFeedError
from .infra.data_logger import DataLogger
from .infra.logger import get_logger
from .infra.notification_service import NotificationService
from .kill_switch import KillSwitch
from .risk_manager import RiskManager
from .strategies.grid_hedge import HedgeGridStrategy

log = get_logger(__name__)


class GridHedgeBot:
    def __init__(self, config: Any, broker: Broker, feed: DataFeed,
                 notifier: NotificationService | None = None,
                 data_logger: DataLogger | None = None) -> None:
        self.config = config
        self.broker = broker
        self.feed = feed
        self.notifier = notifier or NotificationService(config)
        self.data_logger = data_logger or DataLogger(config.path("storage.database"))

        self.risk = RiskManager(
            config, broker.spec,
            state_file=config.path("grid.risk_state_file", "state/grid_risk_state.json"),
        )
        self.kill_switch = KillSwitch(
            config, broker, self.notifier,
            state_file=config.path("grid.kill_switch_state_file", "state/grid_kill_switch.json"),
        )
        self.strategy = HedgeGridStrategy(config, broker, self.risk)

        self.symbol = config.symbol
        self.timeframe = str(config.get("bot.timeframes.trigger", "M15"))
        self.atr_period = int(config.get("grid.step_atr_period", 14))
        self.atr_timeframe = str(config.get("grid.step_atr_timeframe", "M15"))
        self.scan_interval = int(config.get("grid.scan_interval_seconds", 15))

        self.is_running = False
        self._last_bar_time: datetime | None = None
        self.started_at = datetime.now(timezone.utc)
        self.last_scan: dict[str, Any] | None = None

    # ═══════════════════════════════════════════════════════════════
    def start(self) -> None:
        """الحلقة الرئيسية. تتوقف بأمان عند Ctrl+C أو Kill Switch."""
        self.broker.connect()
        self.is_running = True
        mode = "🧪 تجريبي (dry-run)" if self.config.dry_run else "🔴 حقيقي"
        self.notifier.send(
            f"🕸️ *شبكة التحوّط Buy Stop/Sell Stop* بدأت التشغيل\nالوضع: {mode}\n"
            f"الرمز: `{self.symbol}` | الرصيد: `{self.broker.balance():.2f}$`",
            level="warning",
        )
        try:
            while self.is_running:
                started = time.time()
                try:
                    self.tick()
                except Exception as exc:  # حلقة التشغيل لا تسقط بخطأ دورة واحدة
                    log.exception("خطأ في دورة شبكة التحوّط")
                    self.notifier.send(f"⚠️ خطأ في دورة الشبكة: {exc}", level="warning")
                elapsed = time.time() - started
                time.sleep(max(self.scan_interval - elapsed, 1))
        except KeyboardInterrupt:
            log.info("إيقاف يدوي بواسطة المستخدم")
        finally:
            self.stop()

    def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        self.notifier.send("🛑 توقف شبكة التحوّط", level="warning")
        try:
            self.broker.shutdown()
        except Exception as exc:
            log.error("خطأ أثناء إغلاق الاتصال بالوسيط: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    def tick(self) -> dict[str, Any]:
        """دورة واحدة كاملة — قابلة للاستدعاء المباشر في الاختبارات."""
        try:
            bars = self.feed.get_bars(self.timeframe, max(self.atr_period + 60, 120))
        except DataFeedError as exc:
            return self._finish({"status": "no_data", "detail": str(exc)})

        self._advance_paper_clock(bars)

        atr_value: float | None = None
        if self.strategy.step_mode == "atr":
            atr_bars = bars
            if self.atr_timeframe != self.timeframe:
                try:
                    atr_bars = self.feed.get_bars(self.atr_timeframe, self.atr_period + 60)
                except DataFeedError:
                    atr_bars = bars
            atr_value = atr(atr_bars, self.atr_period)

        price = float(bars["close"].iloc[-1])
        balance = self.broker.balance()
        snapshot = self.risk.snapshot(balance)

        triggered = self.kill_switch.auto_check(
            {
                "drawdown": snapshot["drawdown"],
                "daily_pnl_pct": snapshot["daily_pnl"] / balance if balance else 0.0,
                "consecutive_losses": snapshot["consecutive_losses"],
            },
            stop_engine=self.stop,
        )

        outcome = self.strategy.sync(
            price=price,
            balance=balance,
            kill_switch_active=self.kill_switch.active or triggered,
            kill_switch_reason=self.kill_switch.reason,
            atr_value=atr_value,
        )
        self._log_basket_close(outcome)
        return self._finish(outcome)

    def _log_basket_close(self, outcome: dict[str, Any]) -> None:
        """يسجّل في trades.db فقط عند إغلاق سلة فعلية (وجود positions_count).

        outcome يحمل هذا المفتاح حصراً في مسارات الإغلاق الأربعة داخل
        ``HedgeGridStrategy.sync`` (kill_switch/risk_blocked/basket_loss_cutoff/
        basket_tp) — وبقيمة > 0 فقط إن كان هناك صفقات فعلية أُغلقت، لا مجرد
        محاولة على شبكة فارغة.
        """
        count = outcome.get("positions_count", 0)
        if not count:
            return
        try:
            self.data_logger.log_grid_basket(
                status=outcome["status"], pnl=float(outcome.get("closed_pnl", 0.0)),
                positions=int(count), volume=float(outcome.get("volume", 0.0)),
                payload=outcome,
            )
        except Exception as exc:  # التسجيل لا يجوز أن يُسقط دورة التداول
            log.error("فشل تسجيل إغلاق سلة الشبكة: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    def _advance_paper_clock(self, bars: pd.DataFrame) -> None:
        """تغذية الوسيط الورقي بشموع حقيقية — نفس منطق ``GoldDragonBot.advance_paper_clock``.

        ضرورية لمحاكاة انضراب الأوامر المعلّقة داخل الشمعة (high/low)، لا عند
        إغلاقها فقط — شبكة تعتمد على مستويات سعرية بينية تحتاج هذه الدقة.
        """
        if not isinstance(self.broker, PaperBroker) or bars.empty:
            return

        last = bars.iloc[-1]
        last_time = pd.Timestamp(last["time"]).to_pydatetime()

        if self._last_bar_time is None:
            self.broker.set_price(float(last["close"]), last_time)
            self._last_bar_time = last_time
            return

        for _, bar in bars.iterrows():
            when = pd.Timestamp(bar["time"]).to_pydatetime()
            if when <= self._last_bar_time:
                continue
            self.broker.process_bar(
                float(bar["high"]), float(bar["low"]), float(bar["close"]), when
            )
            self._last_bar_time = when

        self.broker.set_price(float(last["close"]), last_time)

    def _finish(self, outcome: dict[str, Any]) -> dict[str, Any]:
        outcome.setdefault("at", datetime.now(timezone.utc).isoformat())
        self.last_scan = outcome
        return outcome
