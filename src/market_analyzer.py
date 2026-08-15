"""محلل السوق — يجمع البيانات، يشغّل البوابات، ثم يستدعي نواة Gold Dragon."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .core.gold_dragon_core import AnalysisResult, GoldDragonCore
from .infra.data_feed import DataFeed, DataFeedError
from .infra.logger import get_logger
from .strategies import CorrelationGate, NewsFilter, PsychologyGate, SessionManager

log = get_logger(__name__)


class MarketAnalyzer:
    def __init__(self, config: Any, feed: DataFeed) -> None:
        self.config = config
        self.feed = feed
        self.core = GoldDragonCore(config)
        self.sessions = SessionManager(config)
        self.news = NewsFilter(config)
        self.correlation = CorrelationGate(config)
        self.psychology = PsychologyGate(config)

        self.bars = config.get("bot.bars", {}) or {}
        self._correlation_cache: dict[str, dict[str, Any]] = {}
        self._correlation_at: datetime | None = None

        # آخر سياق وشموع مستخدمة — يقرأها المحرك للتقارير وإدارة الصفقات
        self.last_context: dict[str, Any] = {}
        self.last_bars: dict[str, pd.DataFrame | None] = {}

    # البيانات ---------------------------------------------------------------
    def fetch(self, timeframe: str, count: int | None = None) -> pd.DataFrame:
        count = count or int(self.bars.get(timeframe, 300))
        return self.feed.get_bars(timeframe, count)

    def refresh_correlation(self, force: bool = False) -> dict[str, dict[str, Any]]:
        ttl = float(self.config.get("bot.correlation_refresh_seconds", 900))
        now = datetime.now(timezone.utc)
        fresh = (
            self._correlation_at is not None
            and (now - self._correlation_at).total_seconds() < ttl
        )
        if fresh and not force:
            return self._correlation_cache
        try:
            self._correlation_cache = self.feed.get_correlation_data()
            self._correlation_at = now
        except Exception as exc:
            log.error("فشل تحديث بيانات الارتباط: %s", exc)
        return self._correlation_cache

    # السياق -----------------------------------------------------------------
    def build_context(self, direction_hint: str, risk_locked: bool = False,
                      lock_reason: str = "", now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        session = self.sessions.current_session(now)
        news = self.news.check(now)
        psychology = self.psychology.check(risk_locked, lock_reason)
        market = self.refresh_correlation()
        correlation = self.correlation.evaluate(direction_hint, market)

        return {
            "now": now,
            "session": session,
            "news": news,
            "psychology": psychology,
            "correlation": correlation,
            "minutes_to_killzone": self.sessions.minutes_to_next_killzone(now),
        }

    # التحليل ----------------------------------------------------------------
    def analyze(self, risk_locked: bool = False, lock_reason: str = "",
                now: datetime | None = None) -> AnalysisResult | None:
        """تحليل كامل. يعيد None إذا تعذّر جلب بيانات صالحة.

        نُجري التحليل على مرحلتين: الأولى بلا بوابة ارتباط لاستخراج الاتجاه
        المرشح، ثم نُعيد التقييم بعد تقييم الارتباط في اتجاه محدد — لأن
        الارتباط لا معنى له قبل معرفة اتجاه الصفقة المرشح.
        """
        try:
            df_h4 = self._safe_fetch("H4")
            df_h1 = self.fetch("H1")
            df_m15 = self.fetch("M15")
        except DataFeedError as exc:
            log.error("بوابة البيانات رفضت التحليل: %s", exc)
            return None

        preliminary = self.core.analyze(df_h1, df_m15, df_h4, context={})
        hint = self._direction_hint(preliminary)

        context = self.build_context(hint, risk_locked, lock_reason, now)
        self.last_context = context
        self.last_bars = {"H4": df_h4, "H1": df_h1, "M15": df_m15}
        return self.core.analyze(df_h1, df_m15, df_h4, context)

    def _safe_fetch(self, timeframe: str) -> pd.DataFrame | None:
        try:
            return self.fetch(timeframe)
        except DataFeedError as exc:
            log.warning("تعذّر جلب %s (%s) — سيُكمل التحليل بدونه", timeframe, exc)
            return None

    @staticmethod
    def _direction_hint(result: AnalysisResult) -> str:
        if result.sweep.get("status") == "confirmed":
            return str(result.sweep["expected_direction"])
        direction = result.bias.get("direction")
        return direction if direction in {"bullish", "bearish"} else "bullish"

    # تقرير نصي --------------------------------------------------------------
    def format_report(self, result: AnalysisResult, context: dict[str, Any] | None = None) -> str:
        """عقد المخرجات المختصر (البند 14) للإشعارات والطرفية."""
        lines = [
            "🐉 *Gold Dragon — تحليل*",
            f"السعر: `{result.price:.2f}`",
            f"(1) Bias: {result.bias.get('direction')} "
            f"(ثقة {result.bias.get('confidence', 0):.0%}) — {result.bias.get('reason', '')}",
        ]

        if result.correlation:
            lines.append(f"(2) Correlation: {result.correlation.get('verdict')} — "
                         f"{result.correlation.get('reason', '')}")

        liquidity = result.liquidity
        lines.append(
            f"(3) Liquidity: PDH `{_fmt(liquidity.get('pdh'))}` / "
            f"PDL `{_fmt(liquidity.get('pdl'))}` | "
            f"BSL {len(liquidity.get('bsl', []))} / SSL {len(liquidity.get('ssl', []))}"
        )
        lines.append(
            f"(4) Sweep: {result.sweep.get('type', 'none')} "
            f"({result.sweep.get('status', '-')}, {result.sweep.get('classification', '-')})"
        )

        if result.pois:
            poi = result.pois[0]
            lines.append(
                f"(5) POI: {poi.type}@{poi.timeframe} `{poi.bottom:.2f}–{poi.top:.2f}` "
                f"ثقة {poi.confidence:.0%} — {poi.reason}"
            )
        else:
            lines.append("(5) POI: لا توجد منطقة معتمدة")

        if result.acs:
            lines.append(f"(6) ACS: `{result.acs.score:.1f}/10` ({result.acs.grade})")

        if result.signal:
            sig = result.signal
            lines.append(
                f"(7) الخطة: {sig.direction.upper()} @ `{sig.entry:.2f}` | "
                f"SL `{sig.sl:.2f}` | TP `{sig.tp1:.2f}/{sig.tp2:.2f}/{sig.tp3:.2f}` | "
                f"RR `1:{sig.rr:.1f}`"
            )
        else:
            reasons = " • ".join(result.rejections) or "لا إشارة"
            lines.append(f"(7) الخطة: ❌ لا دخول — {reasons}")

        if context:
            news = context.get("news", {})
            session = context.get("session", {})
            psychology = context.get("psychology", {})
            lines.append(f"(9) الأخبار: {news.get('impact', '-')} {news.get('title', '')}")
            lines.append(f"(10) السيكولوجيا: {psychology.get('state', '-')} — "
                         f"{psychology.get('reason', '')}")
            lines.append(f"(11) الجلسة: {session.get('name', '-')}")

        lines.append("\n⚠️ أطروحة تحليلية وليست توصية مالية.")
        return "\n".join(lines)


def _fmt(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"
