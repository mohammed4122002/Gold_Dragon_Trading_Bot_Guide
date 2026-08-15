"""نظام تقييم الفرصة المتقدم — Advanced Confluence Score (البند 7).

ملاحظة مهمة على البروتوكول: مصفوفة v4 تعرض عشرة معايير مجموع نقاطها
القصوى **14** بينما النص يصف المقياس بأنه «0–10». هنا نحسب المجموع الخام
(0–14) ثم نُطبّعه خطياً إلى 0–10، ونحتفظ بالتفصيل البنَدي كاملاً حتى يبقى
كل قرار قابلاً للتفسير والتدقيق لاحقاً.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_RAW = 14.0

# الحد الأقصى لكل بند حسب مصفوفة البند 7.1
WEIGHTS: dict[str, float] = {
    "htf_alignment": 2.0,
    "poi_quality": 2.0,
    "sweep_clarity": 2.0,
    "killzone": 1.0,
    "trigger_quality": 2.0,
    "news_clear": 1.0,
    "premium_discount": 1.0,
    "displacement": 1.0,
    "correlation": 1.0,
    "psychology": 1.0,
}

LABELS_AR: dict[str, str] = {
    "htf_alignment": "توافق HTF مع LTF",
    "poi_quality": "جودة POI",
    "sweep_clarity": "وضوح Sweep السيولة",
    "killzone": "التوقيت داخل Killzone",
    "trigger_quality": "نظافة التريغر على M15",
    "news_clear": "غياب الأخبار العاجلة",
    "premium_discount": "موقع Premium/Discount",
    "displacement": "حجم الإزاحة النسبي",
    "correlation": "توافق Correlation Gate",
    "psychology": "الحالة النفسية",
}


@dataclass
class ACSResult:
    raw: float
    score: float                     # مُطبّع على 0–10
    breakdown: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    hard_reject: str | None = None   # سبب رفض تلقائي يتجاوز الدرجة

    @property
    def grade(self) -> str:
        if self.hard_reject:
            return "rejected"
        if self.score >= 8:
            return "excellent"
        if self.score >= 6:
            return "acceptable"
        if self.score >= 4:
            return "watch"
        return "rejected"

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": round(self.raw, 2),
            "score": round(self.score, 2),
            "grade": self.grade,
            "breakdown": {k: round(v, 2) for k, v in self.breakdown.items()},
            "breakdown_ar": {LABELS_AR.get(k, k): round(v, 2)
                             for k, v in self.breakdown.items()},
            "notes": self.notes,
            "hard_reject": self.hard_reject,
        }


def _clamp(key: str, value: float) -> float:
    return max(0.0, min(float(value), WEIGHTS[key]))


def calculate_acs(context: dict[str, Any]) -> ACSResult:
    """حساب الدرجة من سياق التحليل الكامل.

    ``context`` يحمل مخرجات الوحدات: bias / poi / sweep / session / news /
    zone / displacement / correlation / psychology.
    """
    scores: dict[str, float] = {}
    notes: list[str] = []

    # 1) توافق الأطر ---------------------------------------------------------
    bias = context.get("bias") or {}
    ltf_bias = context.get("ltf_bias") or {}
    htf_dir, ltf_dir = bias.get("direction"), ltf_bias.get("direction")
    if htf_dir in {"bullish", "bearish"} and htf_dir == ltf_dir:
        scores["htf_alignment"] = 2.0
    elif htf_dir in {"bullish", "bearish"} and ltf_dir in {"ranging", "neutral", None}:
        scores["htf_alignment"] = 1.0
        notes.append("الإطار الأدنى غير حاسم — توافق جزئي")
    else:
        scores["htf_alignment"] = 0.0
        notes.append("تضارب بين H1 و M15")

    # 2) جودة POI ------------------------------------------------------------
    poi = context.get("poi")
    if poi is None:
        scores["poi_quality"] = 0.0
        notes.append("لا توجد POI معتمدة")
    elif poi.is_fresh and poi.confidence >= 0.75:
        scores["poi_quality"] = 2.0
    elif poi.mitigations <= 1:
        scores["poi_quality"] = 1.0
    else:
        scores["poi_quality"] = 0.0

    # 3) وضوح السويپ ---------------------------------------------------------
    sweep = context.get("sweep") or {}
    if sweep.get("status") == "confirmed":
        scores["sweep_clarity"] = 2.0
    elif sweep.get("type", "none") != "none":
        scores["sweep_clarity"] = 1.0
        notes.append("سويپ بلا إزاحة مؤكدة")
    else:
        scores["sweep_clarity"] = 0.0

    # 4) التوقيت -------------------------------------------------------------
    session = context.get("session") or {}
    if session.get("name") == "Overlap":
        scores["killzone"] = 1.0
    elif session.get("inside_killzone"):
        scores["killzone"] = 0.5
    else:
        scores["killzone"] = 0.0
        notes.append("خارج الـ Killzone")

    # 5) نظافة التريغر -------------------------------------------------------
    choch = context.get("choch")
    fvg_present = bool(context.get("fresh_fvg"))
    if choch and fvg_present and poi is not None:
        scores["trigger_quality"] = 2.0
    elif choch:
        scores["trigger_quality"] = 1.0
    else:
        scores["trigger_quality"] = 0.0
        notes.append("لا يوجد CHoCH على M15")

    # 6) الأخبار -------------------------------------------------------------
    news = context.get("news") or {}
    impact = news.get("impact", "none")
    scores["news_clear"] = {"high": 0.0, "medium": 0.5}.get(impact, 1.0)
    if impact == "high":
        notes.append(f"خبر عالي التأثير قريب: {news.get('title', '')}")

    # 7) Premium / Discount --------------------------------------------------
    zone = (context.get("zone") or {}).get("zone")
    direction = context.get("direction")
    correct_zone = (
        (direction in {"buy", "bullish"} and zone == "discount")
        or (direction in {"sell", "bearish"} and zone == "premium")
    )
    if correct_zone:
        scores["premium_discount"] = 1.0
    elif zone == "equilibrium":
        scores["premium_discount"] = 0.5
    else:
        scores["premium_discount"] = 0.0
        notes.append(f"السعر في منطقة {zone} بعكس اتجاه الصفقة")

    # 8) حجم الإزاحة ---------------------------------------------------------
    displacement = context.get("displacement") or {}
    strength = float(displacement.get("strength", 0.0))
    if strength >= 2.0:
        scores["displacement"] = 1.0
    elif strength >= 1.0:
        scores["displacement"] = 0.5
    else:
        scores["displacement"] = 0.0

    # 9) بوابة الارتباط ------------------------------------------------------
    correlation = context.get("correlation") or {}
    verdict = correlation.get("verdict", "neutral")
    scores["correlation"] = {"aligned": 1.0, "neutral": 0.5}.get(verdict, 0.0)
    if verdict == "conflict":
        notes.append("Correlation Gate: تعارض")

    # 10) الحالة النفسية -----------------------------------------------------
    psychology = context.get("psychology") or {}
    scores["psychology"] = float(psychology.get("score", 0.5))

    breakdown = {key: _clamp(key, value) for key, value in scores.items()}
    raw = sum(breakdown.values())
    normalized = raw / MAX_RAW * 10.0

    result = ACSResult(raw=raw, score=normalized, breakdown=breakdown, notes=notes)
    result.hard_reject = _hard_rejects(normalized, breakdown, context)
    return result


def _hard_rejects(score: float, breakdown: dict[str, float],
                  context: dict[str, Any]) -> str | None:
    """قواعد الرفض التلقائي (البند 7.2 + البند 15)."""
    correlation = context.get("correlation") or {}
    psychology = context.get("psychology") or {}
    news = context.get("news") or {}
    session = context.get("session") or {}

    if session.get("blocked"):
        return f"خارج نافذة التداول المسموحة: {session.get('reason', '')}"
    if correlation.get("verdict") == "conflict":
        return "Correlation Gate متعارض — رفض تلقائي"
    if psychology.get("blocked"):
        return f"Psychology Gate: {psychology.get('reason', 'حالة غير صالحة للتداول')}"
    if breakdown.get("psychology", 1.0) <= 0.0:
        return "الحالة النفسية = 0 — لا تتداول وأنت غاضب"
    if news.get("frozen"):
        return f"تجميد بسبب خبر عاجل: {news.get('title', '')}"
    if score < 4.0:
        return f"ACS {score:.1f}/10 أقل من حد الرفض التلقائي (4)"
    return None
