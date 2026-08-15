"""خدمة الإشعارات — Telegram أو الطرفية.

مبني على ``requests`` بشكل متزامن. النسخة الأصلية في الدليل استخدمت
``asyncio.run`` داخل دالة متزامنة، وهو ما ينهار إذا كان هناك حدث حلقة قائمة،
كما أنه ينشئ حلقة جديدة عند كل رسالة.

فشل الإشعار لا يوقف البوت أبداً — يُسجَّل فقط.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from .logger import get_logger

log = get_logger(__name__)

Level = Literal["info", "warning", "critical"]
_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}
_TELEGRAM_LIMIT = 4000


class NotificationService:
    def __init__(self, config: Any) -> None:
        self.enabled = bool(config.get("notifications.enabled", True))
        self.provider = str(config.get("notifications.provider", "console"))
        self.min_level = str(config.get("notifications.min_level", "info"))
        self.token = config.secret("GD_TELEGRAM_TOKEN")
        self.chat_id = config.secret("GD_TELEGRAM_CHAT_ID")
        self.sent: list[str] = []  # آخر الرسائل — مفيد للاختبارات والتشخيص

        if self.provider == "telegram" and not (self.token and self.chat_id):
            log.warning(
                "provider=telegram لكن GD_TELEGRAM_TOKEN/GD_TELEGRAM_CHAT_ID غير مضبوطين "
                "— التحويل إلى console"
            )
            self.provider = "console"

    def send(self, message: str, level: Level = "info") -> bool:
        if not self.enabled:
            return False
        if _ORDER.get(level, 0) < _ORDER.get(self.min_level, 0):
            return False

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        body = f"{message}\n\n🕒 {stamp}"
        self.sent.append(message)
        del self.sent[:-200]

        if self.provider == "telegram":
            return self._send_telegram(body)

        log.info("[NOTIFY:%s] %s", level, message.replace("\n", " | "))
        return True

    def _send_telegram(self, body: str) -> bool:
        try:
            import requests  # استيراد كسول — لا يلزم في وضع console
        except ImportError:
            log.error("requests غير مثبتة — تعذّر إرسال إشعار Telegram")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": body[:_TELEGRAM_LIMIT],
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                log.error("Telegram رفض الرسالة: %s %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:  # الشبكة قد تسقط — لا نُسقط البوت معها
            log.error("فشل إرسال إشعار Telegram: %s", exc)
            return False

    # قوالب جاهزة ------------------------------------------------------------
    def send_trade_alert(self, signal: Any, lot_size: float, risk_amount: float) -> None:
        msg = (
            "🐉 *Gold Dragon Signal*\n\n"
            f"📊 الاتجاه: *{signal.direction.upper()}*\n"
            f"💰 الدخول: `{signal.entry:.2f}`\n"
            f"🛑 SL: `{signal.sl:.2f}`\n"
            f"🎯 TP1: `{signal.tp1:.2f}`  TP2: `{signal.tp2:.2f}`  TP3: `{signal.tp3:.2f}`\n"
            f"📈 RR: `1:{signal.rr:.1f}`\n"
            f"⭐ ACS: `{signal.acs:.1f}/10`\n"
            f"📦 الحجم: `{lot_size:.2f}` لوت (مخاطرة `{risk_amount:.2f}$`)\n"
            f"🧩 POI: {signal.poi_type} | السيناريو: {signal.scenario}\n"
            f"❌ Invalidation: `{signal.invalidation:.2f}`\n\n"
            "⚠️ أطروحة تحليلية — القرار والمخاطرة عليك وحدك."
        )
        self.send(msg, level="warning")

    def send_rejection(self, reason: str, detail: str = "") -> None:
        self.send(f"🚫 *رفض إشارة* — {reason}\n{detail}".strip(), level="info")

    def send_alert_level(self, level: int, text: str) -> None:
        icons = {1: "🔔", 2: "👀", 3: "🎯", 4: "⏰", 5: "📰"}
        self.send(f"{icons.get(level, '🔔')} *Level {level}* — {text}", level="info")
