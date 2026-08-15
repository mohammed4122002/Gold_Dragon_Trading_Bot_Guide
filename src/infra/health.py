"""نقطة فحص صحي خفيفة — لمراقبة البوت من المتصفح أو من منصة الاستضافة.

سبب وجودها: منصات مثل Railway تحتاج إشارة حياة، وأنت تحتاج نافذة سريعة
على حالة البوت دون فتح السجلات. تعمل على مكتبة Python القياسية فقط في
خيط خلفي (daemon) فلا تُعطّل حلقة التداول ولا تمنع الخروج.

المسارات:
    GET /            → صفحة HTML مختصرة
    GET /health      → 200 إذا كانت الحلقة تعمل، 503 إن توقفت
    GET /status      → JSON بحالة البوت والمخاطرة وآخر دورة

⚠️ لا تكشف هذه الواجهة أي سر (توكن، كلمة مرور، رقم حساب)، لكنها تكشف
حالة حسابك التجريبي. أبقِ الرابط خاصاً.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .logger import get_logger

log = get_logger(__name__)


def _snapshot(bot: Any) -> dict[str, Any]:
    """تجميع حالة البوت. أي خطأ هنا لا يجوز أن يُسقط الخادم."""
    now = datetime.now(timezone.utc)
    data: dict[str, Any] = {
        "name": "Gold Dragon Bot",
        "running": bool(getattr(bot, "is_running", False)),
        "mode": "dry-run" if bot.config.dry_run else "live",
        "feed": bot.config.get("bot.feed"),
        "symbol": bot.config.symbol,
        "started_at": getattr(bot, "started_at", now).isoformat(),
        "uptime_seconds": int((now - getattr(bot, "started_at", now)).total_seconds()),
        "server_time": now.isoformat(),
    }

    try:
        balance = bot.broker.balance()
        data["balance"] = round(balance, 2)
        data["equity"] = round(bot.broker.equity(), 2)
        data["open_positions"] = len(bot.broker.positions())
    except Exception as exc:
        data["broker_error"] = str(exc)
        balance = 0.0

    try:
        data["risk"] = bot.risk.snapshot(balance)
        data["kill_switch"] = {"active": bot.kill_switch.active,
                               "reason": bot.kill_switch.reason}
        data["performance"] = bot.data_logger.summary()
    except Exception as exc:
        data["state_error"] = str(exc)

    last = getattr(bot, "last_scan", None)
    if last:
        data["last_scan"] = {"status": last.get("status"), "detail": last.get("detail"),
                             "at": last.get("at")}
        result = last.get("result")
        if result is not None:
            data["last_analysis"] = {
                "price": getattr(result, "price", None),
                "bias": (result.bias or {}).get("direction"),
                "acs": round(result.acs_score, 2),
                "sweep": (result.sweep or {}).get("type"),
            }
    return data


_PAGE = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<title>Gold Dragon Bot</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{font-family:system-ui,sans-serif;background:#0f1117;color:#e6e6e6;
      margin:0;padding:2rem;line-height:1.7}}
 .card{{max-width:640px;margin:auto;background:#171a23;border:1px solid #262b38;
        border-radius:12px;padding:1.5rem}}
 h1{{margin:0 0 1rem;font-size:1.3rem}}
 .row{{display:flex;justify-content:space-between;border-bottom:1px solid #21262f;
       padding:.5rem 0}}
 .row span:last-child{{font-family:ui-monospace,monospace;color:#f5c451}}
 .ok{{color:#4ade80}} .warn{{color:#f87171}}
 a{{color:#6ea8fe}}
</style>
<div class="card">
<h1>🐉 Gold Dragon Bot</h1>
{rows}
<p style="margin-top:1rem"><a href="/status">‎/status (JSON)</a></p>
</div></html>"""


def _render(data: dict[str, Any]) -> str:
    risk = data.get("risk", {}) or {}
    scan = data.get("last_scan", {}) or {}
    analysis = data.get("last_analysis", {}) or {}
    fields = [
        ("الحالة", "يعمل ✅" if data["running"] else "متوقف ⛔"),
        ("الوضع", data["mode"]),
        ("مصدر البيانات", data["feed"]),
        ("مدة التشغيل", f"{data['uptime_seconds'] // 3600}س "
                        f"{data['uptime_seconds'] % 3600 // 60}د"),
        ("الرصيد", f"{data.get('balance', '—')}$"),
        ("الملكية", f"{data.get('equity', '—')}$"),
        ("مراكز مفتوحة", data.get("open_positions", "—")),
        ("السعر الحالي", analysis.get("price", "—")),
        ("التحيز", analysis.get("bias", "—")),
        ("ACS", analysis.get("acs", "—")),
        ("آخر دورة", f"{scan.get('status', '—')} — {scan.get('detail', '')}"[:80]),
        ("خسارات متتالية", risk.get("consecutive_losses", "—")),
        ("Drawdown", f"{risk.get('drawdown', 0):.1%}" if risk else "—"),
        ("قفل المخاطرة", risk.get("lock_reason") or "لا يوجد"),
        ("Kill Switch", (data.get("kill_switch") or {}).get("reason") or "غير مفعّل"),
    ]
    rows = "".join(
        f'<div class="row"><span>{label}</span><span>{value}</span></div>'
        for label, value in fields
    )
    return _PAGE.format(rows=rows)


class _Handler(BaseHTTPRequestHandler):
    bot: Any = None

    def _send(self, code: int, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 — اسم مفروض من المكتبة القياسية
        path = self.path.split("?")[0].rstrip("/") or "/"
        try:
            data = _snapshot(self.bot)
        except Exception as exc:
            self._send(500, json.dumps({"error": str(exc)}), "application/json")
            return

        if path == "/health":
            code = 200 if data["running"] else 503
            self._send(code, json.dumps({"status": "ok" if code == 200 else "stopped"}),
                       "application/json")
        elif path == "/status":
            self._send(200, json.dumps(data, ensure_ascii=False, default=str),
                       "application/json")
        elif path == "/":
            self._send(200, _render(data), "text/html")
        else:
            self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *args: Any) -> None:
        """كتم سجلّ الوصول — لا نريد سطراً لكل فحص صحي كل 30 ثانية."""


def start_health_server(bot: Any, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer | None:
    """تشغيل الخادم في خيط خلفي. يعيد None إذا تعذّر الربط."""
    handler = type("BoundHandler", (_Handler,), {"bot": bot})
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        log.error("تعذّر تشغيل نقطة الفحص الصحي على المنفذ %s: %s", port, exc)
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="health-server")
    thread.start()
    log.info("نقطة الفحص الصحي تعمل على %s:%s", host, port)
    return server
