"""فلتر الأخبار — تجميد الدخول حول الأحداث عالية التأثير (البند 6.2).

المصدر تقويم JSON محلي حتى لا يعتمد البوت على خدمة خارجية قد تسقط في
أسوأ لحظة. إن غاب الملف، يعمل الفلتر في وضع "غير معروف" ويُبلّغ بذلك
بدلاً من افتراض أن السوق آمن.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..infra.logger import get_logger

log = get_logger(__name__)


class NewsFilter:
    def __init__(self, config: Any) -> None:
        self.enabled = bool(config.get("gold_dragon.news_filter", True))
        self.path: Path = config.path("news.calendar_file", "config/news_calendar.json")
        self.before = int(config.get("news.freeze_minutes_before", 30))
        self.after = int(config.get("news.freeze_minutes_after", 30))
        self.keywords = [k.lower() for k in config.get("news.high_impact_keywords", [])]
        self._events: list[dict[str, Any]] = []
        self._loaded_at: datetime | None = None
        self._missing_warned = False

    def _load(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        if self._loaded_at and (now - self._loaded_at) < timedelta(minutes=15):
            return self._events

        if not self.path.exists():
            if not self._missing_warned:
                log.warning("تقويم الأخبار غير موجود: %s — الفلتر في وضع 'غير معروف'",
                            self.path)
                self._missing_warned = True
            self._events, self._loaded_at = [], now
            return self._events

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            events = []
            for item in raw.get("events", []):
                when = datetime.fromisoformat(str(item["time"]).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                events.append({**item, "dt": when})
            self._events = sorted(events, key=lambda e: e["dt"])
        except Exception as exc:
            log.error("فشل قراءة تقويم الأخبار: %s", exc)
            self._events = []
        self._loaded_at = now
        return self._events

    def _impact_of(self, event: dict[str, Any]) -> str:
        impact = str(event.get("impact", "")).lower()
        if impact in {"high", "medium", "low"}:
            return impact
        title = str(event.get("title", "")).lower()
        return "high" if any(k in title for k in self.keywords) else "low"

    def check(self, now: datetime | None = None) -> dict[str, Any]:
        """حالة الأخبار الآن: هل نحن داخل نافذة تجميد؟"""
        if not self.enabled:
            return {"frozen": False, "impact": "none", "calendar": "disabled"}

        now = now or datetime.now(timezone.utc)
        events = self._load()
        if not events:
            # لا نُسقط الحماية بصمت: نُعلم المستدعي أن التقويم غير متاح
            return {"frozen": False, "impact": "unknown", "calendar": "missing",
                    "title": "", "note": "تقويم الأخبار غير متاح — تحقق يدوياً"}

        for event in events:
            when = event["dt"]
            impact = self._impact_of(event)
            if impact == "low":
                continue
            window = self.before if impact == "high" else self.before // 2
            if when - timedelta(minutes=window) <= now <= when + timedelta(minutes=self.after):
                return {
                    "frozen": impact == "high",
                    "impact": impact,
                    "title": event.get("title", ""),
                    "time": when.isoformat(),
                    "calendar": "ok",
                }

        upcoming = [e for e in events if e["dt"] > now]
        if not upcoming:
            # تقويم كل أحداثه في الماضي = تقويم مهمَل. اعتباره "لا أخبار"
            # يعطي أماناً زائفاً، فنعلنه صراحةً كغير معروف.
            return {"frozen": False, "impact": "unknown", "calendar": "stale",
                    "title": "", "note": "تقويم الأخبار قديم — حدّثه قبل التشغيل"}

        next_high = next((e for e in upcoming if self._impact_of(e) == "high"), None)
        if next_high and (next_high["dt"] - now) <= timedelta(hours=4):
            return {
                "frozen": False, "impact": "medium",
                "title": next_high.get("title", ""),
                "time": next_high["dt"].isoformat(),
                "calendar": "ok",
                "note": "خبر عالي التأثير خلال 4 ساعات — خفض الثقة",
            }

        return {"frozen": False, "impact": "none", "calendar": "ok", "title": ""}

    def next_event(self, now: datetime | None = None) -> dict[str, Any] | None:
        now = now or datetime.now(timezone.utc)
        return next((e for e in self._load() if e["dt"] > now), None)
