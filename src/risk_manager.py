"""مدير المخاطرة — الطبقة الحامية (البند 8 + البند 9.3).

كل حالات القفل تُخزَّن على القرص بختم زمني مطلق. النسخة الأصلية في الدليل
اعتمدت ``threading.Timer`` لفك القفل، وهو ما يعني أن إعادة تشغيل العملية
تمسح القفل تماماً — أي أن قفل الـ 48 ساعة بعد ثلاث خسارات يُلغى بإعادة
تشغيل بسيطة. هنا القفل ينجو من إعادة التشغيل.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .infra.broker import SymbolSpec
from .infra.logger import get_logger

log = get_logger(__name__)


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    detail: dict[str, Any] | None = None


class RiskManager:
    def __init__(self, config: Any, spec: SymbolSpec, state_file: Path | str | None = None) -> None:
        self.config = config
        self.spec = spec

        self.risk_per_trade = float(config.get("risk.risk_per_trade", 0.01))
        self.max_risk_per_trade = float(config.get("risk.max_risk_per_trade", 0.02))
        self.max_total_risk = float(config.get("risk.max_total_risk", 0.04))
        self.max_positions = int(config.get("risk.max_concurrent_positions", 2))
        self.daily_loss_limit = float(config.get("risk.daily_loss_limit", 0.05))
        self.weekly_loss_limit = float(config.get("risk.weekly_loss_limit", 0.10))
        self.max_drawdown = float(config.get("risk.max_drawdown", 0.15))
        self.loss_streak_limit = int(config.get("risk.consecutive_losses_lock", 3))
        self.loss_streak_hours = float(config.get("risk.consecutive_losses_lock_hours", 48))

        self.state_path = Path(state_file) if state_file else config.path(
            "kill_switch.state_file", "state/kill_switch.json"
        ).with_name("risk_state.json")
        self.state = self._load_state()

    # الحالة -----------------------------------------------------------------
    def _default_state(self) -> dict[str, Any]:
        return {
            "day": self._today(),
            "week": self._week(),
            "daily_pnl": 0.0,
            "weekly_pnl": 0.0,
            "consecutive_losses": 0,
            "locked_until": None,
            "lock_reason": "",
            "peak_balance": 0.0,
            "trades_today": 0,
        }

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _week() -> str:
        now = datetime.now(timezone.utc).isocalendar()
        return f"{now.year}-W{now.week:02d}"

    def _load_state(self) -> dict[str, Any]:
        state = self._default_state()
        if self.state_path.exists():
            try:
                state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except Exception as exc:
                log.error("تعذّرت قراءة حالة المخاطرة (%s) — البدء من حالة نظيفة", exc)
        return self._roll_periods(state)

    def _roll_periods(self, state: dict[str, Any]) -> dict[str, Any]:
        """تصفير العدادات عند تغيّر اليوم/الأسبوع."""
        if state.get("day") != self._today():
            state["day"] = self._today()
            state["daily_pnl"] = 0.0
            state["trades_today"] = 0
        if state.get("week") != self._week():
            state["week"] = self._week()
            state["weekly_pnl"] = 0.0
        return state

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # القفل ------------------------------------------------------------------
    def lock(self, hours: float, reason: str) -> None:
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        current = self.state.get("locked_until")
        # لا نُقصّر قفلاً قائماً أطول
        if current and datetime.fromisoformat(current) > until:
            return
        self.state["locked_until"] = until.isoformat()
        self.state["lock_reason"] = reason
        self._save()
        log.warning("🔒 قفل التداول حتى %s — %s", until.isoformat(timespec="minutes"), reason)

    def unlock(self, reason: str = "manual") -> None:
        self.state["locked_until"] = None
        self.state["lock_reason"] = ""
        self.state["consecutive_losses"] = 0
        self._save()
        log.info("🔓 فتح القفل (%s)", reason)

    @property
    def is_locked(self) -> bool:
        until = self.state.get("locked_until")
        if not until:
            return False
        if datetime.fromisoformat(until) <= datetime.now(timezone.utc):
            self.state["locked_until"] = None
            self.state["lock_reason"] = ""
            self.state["consecutive_losses"] = 0
            self._save()
            return False
        return True

    @property
    def lock_reason(self) -> str:
        return str(self.state.get("lock_reason", "")) if self.is_locked else ""

    # القرار -----------------------------------------------------------------
    def can_trade(self, balance: float, open_positions: int = 0,
                  open_risk: float = 0.0) -> RiskDecision:
        self.state = self._roll_periods(self.state)
        peak = max(float(self.state.get("peak_balance", 0.0)), balance)
        self.state["peak_balance"] = peak

        if self.is_locked:
            return RiskDecision(False, f"قفل نشط: {self.lock_reason}")

        if open_positions >= self.max_positions:
            return RiskDecision(False, f"بلغت الحد الأقصى للصفقات المفتوحة ({self.max_positions})")

        if balance <= 0:
            return RiskDecision(False, "رصيد غير صالح")

        if open_risk + self.risk_per_trade > self.max_total_risk + 1e-9:
            return RiskDecision(
                False,
                f"المخاطرة المجمعة {(open_risk + self.risk_per_trade):.2%} "
                f"تتجاوز السقف {self.max_total_risk:.2%}",
            )

        daily_pct = float(self.state["daily_pnl"]) / balance if balance else 0.0
        if daily_pct <= -self.daily_loss_limit:
            self.lock(self._hours_to_midnight(), f"خسارة يومية {daily_pct:.2%}")
            return RiskDecision(False, f"تجاوز حد الخسارة اليومية ({daily_pct:.2%})")

        weekly_pct = float(self.state["weekly_pnl"]) / balance if balance else 0.0
        if weekly_pct <= -self.weekly_loss_limit:
            self.lock(24 * 7, f"خسارة أسبوعية {weekly_pct:.2%}")
            return RiskDecision(False, f"تجاوز حد الخسارة الأسبوعية ({weekly_pct:.2%})")

        if int(self.state["consecutive_losses"]) >= self.loss_streak_limit:
            self.lock(self.loss_streak_hours,
                      f"{self.state['consecutive_losses']} خسارات متتالية")
            return RiskDecision(False, "قفل التداول الانتقامي — 3 خسارات متتالية")

        drawdown = (peak - balance) / peak if peak > 0 else 0.0
        if drawdown >= self.max_drawdown:
            return RiskDecision(
                False, f"Drawdown {drawdown:.2%} تجاوز الحد {self.max_drawdown:.2%}"
            )

        self._save()
        return RiskDecision(True, "مسموح", {"drawdown": drawdown, "daily_pct": daily_pct})

    @staticmethod
    def _hours_to_midnight() -> float:
        now = datetime.now(timezone.utc)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max((midnight - now).total_seconds() / 3600, 0.1)

    # الحجم ------------------------------------------------------------------
    def calculate_position_size(self, balance: float, entry: float, stop_loss: float,
                                risk_pct: float | None = None) -> dict[str, Any]:
        """حساب حجم المركز باللوت.

        لـ XAUUSD: قيمة حركة 1$ لكل لوت = ``contract_size`` (100 أونصة).
        إذن: ``lots = (balance × risk%) ÷ (مسافة الوقف بالدولار × 100)``.

        النسخة الأصلية في الدليل حوّلت المسافة إلى "بِبس" ثم ضربت في 100
        وقيمة بِب افتراضية = 1، فنتج حجم مركز مضخّم بمقدار ×100 تقريباً.
        هذا الخطأ وحده كفيل بتصفية الحساب في صفقة واحدة.
        """
        risk_pct = float(risk_pct if risk_pct is not None else self.risk_per_trade)
        risk_pct = min(risk_pct, self.max_risk_per_trade)

        sl_distance = abs(float(entry) - float(stop_loss))
        if sl_distance <= 0 or balance <= 0:
            return {"lots": 0.0, "risk_amount": 0.0, "reason": "مدخلات غير صالحة"}

        risk_amount = balance * risk_pct
        value_per_dollar_move = self.spec.contract_size  # لكل 1.00 لوت
        raw_lots = risk_amount / (sl_distance * value_per_dollar_move)
        lots = self.spec.normalize_lot(raw_lots)

        if lots <= 0:
            return {
                "lots": 0.0,
                "risk_amount": 0.0,
                "raw_lots": raw_lots,
                "reason": (
                    f"الحجم المحسوب {raw_lots:.4f} أصغر من الحد الأدنى {self.spec.min_lot}. "
                    "الرصيد لا يحتمل هذه المخاطرة — لا تُرفع النسبة، تُرفض الصفقة."
                ),
            }

        actual_risk = lots * sl_distance * value_per_dollar_move
        if actual_risk > balance * self.max_risk_per_trade * 1.05:
            return {
                "lots": 0.0, "risk_amount": 0.0, "raw_lots": raw_lots,
                "reason": (
                    f"أصغر حجم متاح ({lots}) يخاطر بـ {actual_risk:.2f}$ "
                    f"({actual_risk / balance:.2%}) وهو فوق السقف — رفض"
                ),
            }

        return {
            "lots": lots,
            "risk_amount": actual_risk,
            "risk_pct": actual_risk / balance,
            "raw_lots": raw_lots,
            "sl_distance": sl_distance,
            "reason": "ok",
        }

    def open_risk_fraction(self, positions: list[Any], balance: float) -> float:
        """المخاطرة المفتوحة حالياً كنسبة من الرصيد (بافتراض ضرب كل الأوقاف)."""
        if balance <= 0:
            return 0.0
        total = 0.0
        for pos in positions:
            if not getattr(pos, "sl", 0):
                continue
            distance = abs(pos.entry - pos.sl)
            total += distance * self.spec.contract_size * pos.volume
        return total / balance

    # التسجيل ----------------------------------------------------------------
    def record_result(self, pnl: float) -> None:
        self.state = self._roll_periods(self.state)
        self.state["daily_pnl"] = float(self.state["daily_pnl"]) + pnl
        self.state["weekly_pnl"] = float(self.state["weekly_pnl"]) + pnl
        self.state["trades_today"] = int(self.state["trades_today"]) + 1

        if pnl < 0:
            self.state["consecutive_losses"] = int(self.state["consecutive_losses"]) + 1
            if int(self.state["consecutive_losses"]) >= self.loss_streak_limit:
                self.lock(self.loss_streak_hours,
                          f"{self.state['consecutive_losses']} خسارات متتالية")
        else:
            self.state["consecutive_losses"] = 0
        self._save()

    def snapshot(self, balance: float = 0.0) -> dict[str, Any]:
        peak = max(float(self.state.get("peak_balance", 0.0)), balance)
        return {
            "daily_pnl": round(float(self.state["daily_pnl"]), 2),
            "weekly_pnl": round(float(self.state["weekly_pnl"]), 2),
            "consecutive_losses": int(self.state["consecutive_losses"]),
            "locked": self.is_locked,
            "lock_reason": self.lock_reason,
            "trades_today": int(self.state["trades_today"]),
            "drawdown": round((peak - balance) / peak, 4) if peak > 0 else 0.0,
            "peak_balance": round(peak, 2),
        }
