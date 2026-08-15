"""نظام التوقيت المؤسساتي — Killzones ونوافذ المنع (البند 6)."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any


def _parse(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


def _in_window(now: time, start: time, end: time) -> bool:
    """يدعم النوافذ العابرة لمنتصف الليل (مثل جلسة طوكيو 23:00→02:00)."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


class SessionManager:
    def __init__(self, config: Any) -> None:
        self.killzones = config.get("sessions.killzones", []) or []
        self.no_trade = config.get("sessions.no_trade_windows", []) or []
        self.killzone_only = bool(config.get("gold_dragon.killzone_only", True))

    def current_session(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        clock = now.timetz().replace(tzinfo=None)

        for window in self.no_trade:
            if _in_window(clock, _parse(window["start"]), _parse(window["end"])):
                return {
                    "name": window.get("name", "no-trade"),
                    "inside_killzone": False,
                    "blocked": True,
                    "quality": 0.0,
                    "reason": f"نافذة ممنوعة: {window.get('name')}",
                }

        matches = [
            kz for kz in self.killzones
            if _in_window(clock, _parse(kz["start"]), _parse(kz["end"]))
        ]
        if matches:
            best = max(matches, key=lambda kz: float(kz.get("quality", 0)))
            return {
                "name": best["name"],
                "inside_killzone": True,
                "blocked": False,
                "quality": float(best.get("quality", 1.0)),
                "reason": f"داخل {best['name']} Killzone",
            }

        return {
            "name": "outside",
            "inside_killzone": False,
            "blocked": self.killzone_only,
            "quality": 0.0,
            "reason": "خارج جلسات التداول المعتمدة",
        }

    def can_trade_now(self, now: datetime | None = None) -> tuple[bool, str]:
        session = self.current_session(now)
        return (not session["blocked"]), session["reason"]

    def minutes_to_next_killzone(self, now: datetime | None = None) -> int | None:
        """الدقائق المتبقية لأقرب Killzone — لتنبيه Level 4."""
        now = now or datetime.now(timezone.utc)
        current = now.hour * 60 + now.minute
        deltas: list[int] = []
        for kz in self.killzones:
            start = _parse(kz["start"])
            start_minutes = start.hour * 60 + start.minute
            delta = start_minutes - current
            if delta < 0:
                delta += 24 * 60
            deltas.append(delta)
        return min(deltas) if deltas else None
