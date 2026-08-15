"""نواة بروتوكول Gold Dragon Pro v4 — الربط بين كل طبقات التحليل.

المخرج الوحيد المعتمد هو :class:`AnalysisResult`: تحليل كامل قابل للتفسير،
يحمل إما إشارة جاهزة أو سبب رفض صريح. لا قرار ضمني ولا صمت.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..models import POI, Signal
from .acs import ACSResult, calculate_acs
from .indicators import atr
from .liquidity import detect_sweep, map_liquidity, nearest_target
from .poi_finder import find_all_pois
from .structure import calculate_bias, detect_choch, has_displacement, premium_discount


@dataclass
class AnalysisResult:
    """نتيجة تحليل واحدة — مطابقة لعقد المخرجات في البند 14."""

    bias: dict[str, Any]
    ltf_bias: dict[str, Any]
    liquidity: dict[str, Any]
    sweep: dict[str, Any]
    zone: dict[str, Any]
    pois: list[POI] = field(default_factory=list)
    choch: dict[str, Any] | None = None
    displacement: dict[str, Any] = field(default_factory=dict)
    correlation: dict[str, Any] = field(default_factory=dict)
    acs: ACSResult | None = None
    signal: Signal | None = None
    rejections: list[str] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    price: float = 0.0

    @property
    def acs_score(self) -> float:
        return self.acs.score if self.acs else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "bias": self.bias,
            "ltf_bias": self.ltf_bias,
            "zone": self.zone,
            "sweep": {k: v for k, v in self.sweep.items() if k != "displacement"},
            "poi_count": len(self.pois),
            "top_poi": self.pois[0].to_dict() if self.pois else None,
            "choch": self.choch,
            "correlation": self.correlation,
            "acs": self.acs.to_dict() if self.acs else None,
            "signal": self.signal.to_dict() if self.signal else None,
            "rejections": self.rejections,
            "alerts": self.alerts,
            "verdict": "signal" if self.signal else "no-trade",
        }


class GoldDragonCore:
    """محرّك التحليل. عديم الحالة تقريباً — كل استدعاء مستقل وقابل للاختبار."""

    def __init__(self, config: Any) -> None:
        self.config = config
        gd = "gold_dragon."
        self.min_acs = float(config.get(gd + "min_acs", 7.0))
        self.require_sweep = bool(config.get(gd + "require_sweep", True))
        self.require_choch = bool(config.get(gd + "require_choch", True))
        self.max_poi_distance = float(config.get(gd + "max_poi_distance", 12.0))
        self.lookback = int(config.get(gd + "swing_lookback", 2))
        self.tolerance = float(config.get(gd + "equal_level_tolerance", 0.5))
        self.atr_period = int(config.get(gd + "atr_period", 14))
        self.displacement_multiple = float(config.get(gd + "displacement_atr_multiple", 1.5))

        mgmt = "risk.management."
        self.tp_r = (
            float(config.get(mgmt + "tp1_r", 1.0)),
            float(config.get(mgmt + "tp2_r", 3.0)),
            float(config.get(mgmt + "tp3_r", 5.0)),
        )
        self.sl_buffer_atr = float(config.get(mgmt + "sl_buffer_atr", 0.25))
        self.max_entry_distance_r = float(
            config.get("gold_dragon.max_entry_distance_r", 1.0)
        )
        self.min_rr = {
            "with_trend": float(config.get("risk.min_rr.with_trend", 3.0)),
            "counter_trend": float(config.get("risk.min_rr.counter_trend", 5.0)),
            "range": float(config.get("risk.min_rr.range", 2.5)),
        }

    # ═══════════════════════════════════════════════════════════════
    def analyze(
        self,
        df_h1: pd.DataFrame,
        df_m15: pd.DataFrame,
        df_h4: pd.DataFrame | None = None,
        context: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        context = context or {}
        price = float(df_m15["close"].iloc[-1])
        reference_atr = atr(df_m15, self.atr_period) or 1.0

        bias = calculate_bias(df_h1, self.lookback)
        ltf_bias = calculate_bias(df_m15, self.lookback)
        if df_h4 is not None and not df_h4.empty:
            bias["h4"] = calculate_bias(df_h4, self.lookback)

        liquidity = map_liquidity(df_h1, df_m15, df_h4, self.tolerance, self.lookback)
        sweep = detect_sweep(df_m15, liquidity, 6, self.atr_period, self.displacement_multiple)
        zone = premium_discount(df_h1, self.lookback)

        result = AnalysisResult(
            bias=bias, ltf_bias=ltf_bias, liquidity=liquidity, sweep=sweep,
            zone=zone, price=price, correlation=context.get("correlation", {}) or {},
        )

        direction = self._decide_direction(bias, sweep)
        if direction is None:
            result.rejections.append("لا يوجد اتجاه صالح (لا سويپ مؤكد ولا تحيز واضح)")
            result.acs = self._score(result, None, None, context)
            result.alerts = self._build_alerts(result, context)
            return result

        result.choch = detect_choch(df_m15, direction, self.lookback)
        result.displacement = has_displacement(
            df_m15, direction, self.atr_period, self.displacement_multiple
        )
        result.pois = find_all_pois(
            df_h1, df_m15, direction, price, self.max_poi_distance,
            self.lookback, self.displacement_multiple,
        )

        best_poi = result.pois[0] if result.pois else None
        result.acs = self._score(result, best_poi, direction, context)

        # بوابات الرفض الصلبة قبل أي بناء لإشارة
        blockers = self._hard_blockers(result, best_poi, direction)
        result.rejections.extend(blockers)
        if blockers:
            result.alerts = self._build_alerts(result, context)
            return result

        assert best_poi is not None  # مضمون: غياب POI من ضمن blockers
        signal = self._build_signal(result, best_poi, direction, reference_atr, df_m15)
        if signal is None:
            result.alerts = self._build_alerts(result, context)
            return result

        rr_floor = self.min_rr[signal.setup]
        if signal.rr < rr_floor:
            result.rejections.append(
                f"RR {signal.rr:.2f} أقل من الحد الأدنى {rr_floor} لإعداد {signal.setup}"
            )
            result.alerts = self._build_alerts(result, context)
            return result

        if not signal.is_consistent():
            result.rejections.append("إشارة غير متسقة منطقياً (ترتيب SL/Entry/TP خاطئ)")
            result.alerts = self._build_alerts(result, context)
            return result

        result.signal = signal
        result.alerts = self._build_alerts(result, context)
        return result

    # ═══════════════════════════════════════════════════════════════
    def _decide_direction(self, bias: dict[str, Any], sweep: dict[str, Any]) -> str | None:
        """السويپ المؤكد يتقدم على التحيز — لأنه الحدث الأحدث في السوق."""
        if sweep.get("status") == "confirmed":
            return str(sweep["expected_direction"])
        if self.require_sweep:
            return None
        if bias.get("direction") in {"bullish", "bearish"}:
            return str(bias["direction"])
        return None

    def _score(self, result: AnalysisResult, poi: POI | None, direction: str | None,
               context: dict[str, Any]) -> ACSResult:
        return calculate_acs(
            {
                "bias": result.bias,
                "ltf_bias": result.ltf_bias,
                "poi": poi,
                "sweep": result.sweep,
                "session": context.get("session", {}),
                "choch": result.choch,
                "fresh_fvg": any(p.type == "FVG" and p.is_fresh for p in result.pois),
                "news": context.get("news", {}),
                "zone": result.zone,
                "direction": direction,
                "displacement": result.displacement,
                "correlation": context.get("correlation", {}),
                "psychology": context.get("psychology", {}),
            }
        )

    def _hard_blockers(self, result: AnalysisResult, poi: POI | None,
                       direction: str) -> list[str]:
        blockers: list[str] = []
        acs = result.acs

        if acs and acs.hard_reject:
            blockers.append(acs.hard_reject)
        if poi is None:
            blockers.append("لا توجد POI معتمدة ضمن المسافة المسموحة")
        if self.require_choch and not result.choch:
            blockers.append("لا يوجد CHoCH مؤكد على M15 — ممنوع الدخول (البند 3.4)")
        if acs and acs.score < self.min_acs:
            blockers.append(f"ACS {acs.score:.1f}/10 أقل من الحد الأدنى {self.min_acs}")
        return blockers

    def _build_signal(self, result: AnalysisResult, poi: POI, direction: str,
                      reference_atr: float, df_m15: pd.DataFrame) -> Signal | None:
        side = "buy" if direction == "bullish" else "sell"
        price = result.price
        buffer_ = reference_atr * self.sl_buffer_atr

        # الدخول بسعر السوق عند تأكد التريغر، لا عند حافة POI.
        # السبب: مدير الأوامر يرسل أوامر سوقية فقط، فلو بنينا الخطة على سعر
        # معلّق لن يُنفَّذ لكانت كل الأرقام (الحجم، RR، المخاطرة) وهمية.
        # دور POI هنا: تحديد الوقف والتحقق من أننا لم ندخل متأخرين.
        entry = price

        # الوقف: خلف حافة POI البعيدة أو خلف قمة/قاع السويپ — أيهما أبعد
        if side == "buy":
            sl = poi.distal - buffer_
            if result.sweep.get("type") == "SSL_Sweep":
                sweep_low = float(df_m15["low"].iloc[-8:].min())
                sl = min(sl, sweep_low - buffer_)
        else:
            sl = poi.distal + buffer_
            if result.sweep.get("type") == "BSL_Sweep":
                sweep_high = float(df_m15["high"].iloc[-8:].max())
                sl = max(sl, sweep_high + buffer_)

        risk = abs(entry - sl)
        if risk <= 0:
            result.rejections.append("مسافة وقف صفرية — إشارة مرفوضة")
            return None
        if risk > reference_atr * 6:
            result.rejections.append(
                f"مسافة الوقف {risk:.2f}$ واسعة جداً (> 6×ATR) — إشارة مرفوضة"
            )
            return None

        # فلتر التأخر (FOMO): الدخول بعيداً عن حافة POI يعني أننا نلحق الحركة
        entry_distance = poi.distance_from(entry)
        if entry_distance > risk * self.max_entry_distance_r:
            result.rejections.append(
                f"السعر ابتعد {entry_distance:.2f}$ عن حافة POI "
                f"(> {self.max_entry_distance_r}R) — لا تلحق الحركة"
            )
            return None

        sign = 1.0 if side == "buy" else -1.0
        tp1, tp2, tp3 = (entry + sign * risk * r for r in self.tp_r)

        setup = self._classify_setup(result.bias, direction)
        notes: list[str] = []

        obstacle = nearest_target(result.liquidity, entry, side)
        if obstacle is not None and abs(obstacle - entry) < abs(tp1 - entry):
            notes.append(
                f"مجمّع سيولة عند {obstacle:.2f} قبل TP1 — احتمال ارتداد مبكر"
            )

        return Signal(
            direction=side,
            entry=round(entry, 2),
            sl=round(sl, 2),
            tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2),
            acs=round(result.acs.score, 2) if result.acs else 0.0,
            acs_breakdown=result.acs.breakdown if result.acs else {},
            poi_type=f"{poi.type}@{poi.timeframe}",
            invalidation=round(float(result.sweep.get("level", sl)), 2),
            setup=setup,
            notes=notes,
        )

    def _classify_setup(self, bias: dict[str, Any], direction: str) -> str:
        htf = bias.get("direction")
        if htf == "ranging":
            return "range"
        return "with_trend" if htf == direction else "counter_trend"

    def _build_alerts(self, result: AnalysisResult,
                      context: dict[str, Any]) -> list[dict[str, Any]]:
        """بروتوكول الإشعارات المتدرج (البند 6.3)."""
        alerts: list[dict[str, Any]] = []
        proximity = float(self.config.get("gold_dragon.alert_protocol.poi_proximity", 2.0))

        news = context.get("news", {}) or {}
        if news.get("frozen") or news.get("impact") == "high":
            alerts.append({"level": 5, "text": f"تجميد — خبر عاجل: {news.get('title', '')}"})

        session = context.get("session", {}) or {}
        minutes = context.get("minutes_to_killzone")
        if not session.get("inside_killzone") and isinstance(minutes, int) and minutes <= 30:
            alerts.append({"level": 4, "text": f"Killzone بعد {minutes} دقيقة"})

        if result.signal:
            alerts.append({"level": 3, "text": (
                f"تريغر مؤكد {result.signal.direction.upper()} @ {result.signal.entry:.2f}"
            )})
        elif (result.sweep.get("type", "none") != "none"
              and result.sweep.get("status") != "confirmed"):
            alerts.append({"level": 2, "text": (
                f"سويپ محتمل عند {result.sweep.get('level', 0):.2f} — راقب M15"
            )})

        if result.pois:
            nearest = min(p.distance_from(result.price) for p in result.pois)
            if nearest <= proximity:
                alerts.append({"level": 1, "text": (
                    f"POI على بعد {nearest:.2f}$ — استعد"
                )})

        return sorted(alerts, key=lambda a: -a["level"])
