"""زر القتل — الإيقاف الاضطراري (البند 6.2 من الدليل + البند 12.4).

قاعدتان تحكمان التصميم:
1. التفعيل يُخزَّن على القرص، فلا تُعيده إعادة التشغيل إلى العمل تلقائياً.
2. مستوى ``terminate`` لا يُفكّ إلا بتدخل بشري صريح من الطرفية.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .infra.logger import get_logger

log = get_logger(__name__)


class KillSwitch:
    def __init__(self, config: Any, broker: Any, notifier: Any) -> None:
        self.enabled = bool(config.get("kill_switch.enabled", True))
        self.broker = broker
        self.notifier = notifier
        self.path: Path = config.path("kill_switch.state_file", "state/kill_switch.json")

        self.drawdown_pause = float(config.get("kill_switch.drawdown_pause", 0.20))
        self.pause_hours = float(config.get("kill_switch.drawdown_pause_hours", 24))
        self.drawdown_terminate = float(config.get("kill_switch.drawdown_terminate", 0.50))
        self.daily_loss_terminate = float(config.get("kill_switch.daily_loss_terminate", 0.10))
        self.loss_streak_terminate = int(config.get("kill_switch.consecutive_losses_terminate", 5))
        self.close_positions = bool(config.get("kill_switch.close_positions_on_trigger", True))

        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.error("تعذّرت قراءة حالة Kill Switch: %s", exc)
        return {"active": False, "level": None, "reason": "", "until": None, "at": None}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    @property
    def active(self) -> bool:
        if not self.state.get("active"):
            return False
        if self.state.get("level") == "terminate":
            return True
        until = self.state.get("until")
        if until and datetime.fromisoformat(until) <= datetime.now(timezone.utc):
            log.info("انتهت مدة إيقاف Kill Switch — استئناف")
            self.state = {"active": False, "level": None, "reason": "", "until": None,
                          "at": None}
            self._save()
            return False
        return True

    @property
    def reason(self) -> str:
        return str(self.state.get("reason", ""))

    def trigger(self, reason: str, level: str = "pause",
               stop_engine: Callable[[], None] | None = None) -> None:
        """تفعيل: إغلاق كل المراكز، إشعار، وتجميد الدخول."""
        now = datetime.now(timezone.utc)
        until = None if level == "terminate" else (
            now + timedelta(hours=self.pause_hours)
        ).isoformat()

        self.state = {"active": True, "level": level, "reason": reason,
                      "until": until, "at": now.isoformat()}
        self._save()

        closed = 0.0
        if self.close_positions:
            try:
                closed = self.broker.close_all(reason="kill_switch")
            except Exception as exc:  # الإشعار أهم من نجاح الإغلاق
                log.critical("فشل إغلاق المراكز أثناء Kill Switch: %s", exc)

        text = (
            f"🚨 *KILL SWITCH ({level.upper()})*\n"
            f"السبب: {reason}\n"
            f"إغلاق المراكز: {closed:+.2f}$\n"
            + ("⛔ إيقاف نهائي — يتطلب تدخلاً بشرياً: `python -m src.main kill --reset`"
               if level == "terminate" else f"⏸️ إيقاف حتى {until}")
        )
        log.critical("KILL SWITCH (%s): %s", level, reason)
        self.notifier.send(text, level="critical")

        if stop_engine is not None:
            stop_engine()

    def reset(self, note: str = "") -> None:
        """فكّ يدوي — لا يُستدعى تلقائياً أبداً."""
        log.warning("إعادة تعيين Kill Switch يدوياً %s", f"({note})" if note else "")
        self.state = {"active": False, "level": None, "reason": "", "until": None,
                      "at": None}
        self._save()
        self.notifier.send("✅ تم فك Kill Switch يدوياً", level="warning")

    def auto_check(self, metrics: dict[str, Any],
                   stop_engine: Callable[[], None] | None = None) -> bool:
        """فحص دوري. يعيد True إذا تفعّل الآن."""
        if not self.enabled or self.active:
            return False

        drawdown = float(metrics.get("drawdown", 0.0))
        daily_loss = abs(min(float(metrics.get("daily_pnl_pct", 0.0)), 0.0))
        streak = int(metrics.get("consecutive_losses", 0))

        if drawdown >= self.drawdown_terminate:
            self.trigger(f"Drawdown {drawdown:.1%} تجاوز حد الإنهاء", "terminate", stop_engine)
            return True
        if daily_loss >= self.daily_loss_terminate:
            self.trigger(f"خسارة يومية {daily_loss:.1%}", "terminate", stop_engine)
            return True
        if streak >= self.loss_streak_terminate:
            self.trigger(f"{streak} خسارات متتالية", "terminate", stop_engine)
            return True
        if drawdown >= self.drawdown_pause:
            self.trigger(f"Drawdown {drawdown:.1%} تجاوز حد الإيقاف المؤقت", "pause",
                         stop_engine)
            return True
        return False
