"""بوابة السيكولوجيا (البند 9) — مطبّقة على بوت آلي.

البوت لا "يغضب"، لكن صاحبه يغضب — وهو من يتدخل يدوياً، يرفع المخاطرة،
أو يعيد تشغيل البوت بعد قفل. لذلك البوابة هنا تعمل كإقرار بشري صريح
يُحدَّث من الطرفية:

    python -m src.main psych --state calm
    python -m src.main psych --state angry --note "خسارة أمس"

الحالة تُخزَّن في ملف JSON مع ختم زمني. إن تقادمت الإجابة تُعامل كـ
«غير معروفة» (نصف درجة) لا كـ«هادئ» — الصمت ليس موافقة.

كما تشمل البوابة فلتر FOMO الآلي (البند 9.2): إذا قطع السعر جزءاً كبيراً
من المسافة نحو الهدف قبل الدخول، يُلغى الإعداد.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..infra.logger import get_logger

log = get_logger(__name__)

STATES: dict[str, float] = {
    "calm": 1.0,        # هادئ ومنضبط
    "tense": 0.5,       # متوتر لكن متحكم
    "angry": 0.0,       # مندفع/غاضب/منتقم
    "tired": 0.0,       # متعب
    "unknown": 0.5,
}

BLOCKING = {"angry", "tired"}


class PsychologyGate:
    def __init__(self, config: Any) -> None:
        self.enabled = bool(config.get("gold_dragon.psychology_gate.enabled", True))
        self.path: Path = config.path(
            "gold_dragon.psychology_gate.state_file", "state/psychology.json"
        )
        self.max_age = timedelta(
            hours=float(config.get("gold_dragon.psychology_gate.max_state_age_hours", 12))
        )
        self.block_on_zero = bool(
            config.get("gold_dragon.psychology_gate.block_on_zero", True)
        )

    # الحالة المعلنة ---------------------------------------------------------
    def set_state(self, state: str, note: str = "") -> dict[str, Any]:
        if state not in STATES:
            raise ValueError(f"حالة غير معروفة: {state} (المتاح: {sorted(STATES)})")
        payload = {
            "state": state,
            "note": note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        log.info("تحديث الحالة النفسية: %s %s", state, f"({note})" if note else "")
        return payload

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"state": "unknown", "age_hours": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(data["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - updated
            if age > self.max_age:
                return {"state": "unknown", "age_hours": age.total_seconds() / 3600,
                        "stale": True, "declared": data.get("state")}
            return {**data, "age_hours": age.total_seconds() / 3600, "stale": False}
        except Exception as exc:
            log.error("فشل قراءة ملف الحالة النفسية: %s", exc)
            return {"state": "unknown", "age_hours": None}

    def check(self, risk_locked: bool = False, lock_reason: str = "") -> dict[str, Any]:
        if not self.enabled:
            return {"score": 1.0, "blocked": False, "state": "disabled",
                    "reason": "البوابة معطّلة"}

        if risk_locked:
            # القفل الانتقامي جزء من هذه البوابة (البند 9.3)
            return {"score": 0.0, "blocked": True, "state": "locked",
                    "reason": lock_reason or "قفل تداول انتقامي نشط"}

        data = self._read()
        state = str(data.get("state", "unknown"))
        score = STATES.get(state, 0.5)
        blocked = self.block_on_zero and state in BLOCKING

        reason = {
            "calm": "الحالة: هادئ ومنضبط",
            "tense": "الحالة: متوتر لكن متحكم — ثقة منخفضة",
            "angry": "الحالة: غاضب/مندفع — ممنوع التداول",
            "tired": "الحالة: متعب — ممنوع التداول",
            "unknown": "لم تُعلن حالة نفسية حديثة — درجة محايدة",
        }.get(state, "حالة غير معروفة")

        if data.get("stale"):
            reason += f" (آخر إعلان قبل {data['age_hours']:.1f} ساعة — منتهي الصلاحية)"

        return {"score": score, "blocked": blocked, "state": state, "reason": reason,
                "age_hours": data.get("age_hours")}

    # فلتر FOMO --------------------------------------------------------------
    @staticmethod
    def fomo_check(price: float, poi_level: float, target: float,
                   max_travel: float = 0.30) -> dict[str, Any]:
        """هل تحرك السعر أكثر من ``max_travel`` من المسافة نحو الهدف؟ (البند 9.2)"""
        distance = abs(target - poi_level)
        if distance <= 0:
            return {"blocked": False, "traveled": 0.0}
        traveled = abs(price - poi_level) / distance
        blocked = traveled > max_travel
        return {
            "blocked": blocked,
            "traveled": round(traveled, 3),
            "reason": (
                f"السعر قطع {traveled:.0%} من المسافة نحو الهدف — لا تلحق، انتظر الاختبار"
                if blocked else "ضمن نطاق الدخول المقبول"
            ),
        }
