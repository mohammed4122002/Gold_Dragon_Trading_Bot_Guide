"""بوابة الارتباطات متعددة الأصول (البند 4).

الفكرة: الذهب لا يتحرك في فراغ. لكل أصل مرجعي ارتباط معروف الإشارة ووزن.
نحسب "درجة توافق" مرجّحة بين -1 و +1 ثم نحكم: aligned / neutral / conflict.
"""

from __future__ import annotations

from typing import Any

DEFAULT_ASSETS: dict[str, dict[str, Any]] = {
    "DXY": {"weight": 0.30, "correlation": -0.80, "required": True},
    "US10Y": {"weight": 0.20, "correlation": -0.55, "required": True},
    "XAGUSD": {"weight": 0.15, "correlation": 0.75, "required": False},
    "VIX": {"weight": 0.10, "correlation": 0.30, "required": False},
    "SPX500": {"weight": 0.10, "correlation": -0.35, "required": False},
}

# تغيّر أصغر من هذا (%) يُعتبر ضجيجاً لا اتجاهاً
NOISE_THRESHOLD = 0.05


class CorrelationGate:
    def __init__(self, config: Any) -> None:
        self.enabled = bool(config.get("gold_dragon.correlation_gate.enabled", True))
        self.assets = config.get("gold_dragon.correlation_gate.assets") or DEFAULT_ASSETS
        self.vix_limit = float(
            config.get("gold_dragon.correlation_gate.vix_no_trade_above", 30.0)
        )
        self.conflict_threshold = float(
            config.get("gold_dragon.correlation_gate.conflict_threshold", -0.20)
        )

    def evaluate(self, direction: str, market: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """``direction``: bullish/bearish للذهب. ``market``: {ASSET: {change_pct,...}}."""
        if not self.enabled:
            return {"verdict": "neutral", "score": 0.0, "details": {},
                    "reason": "البوابة معطّلة"}
        if not market:
            return {"verdict": "neutral", "score": 0.0, "details": {},
                    "reason": "لا تتوفر بيانات ارتباط — البوابة محايدة"}

        gold_sign = 1.0 if direction in {"bullish", "buy"} else -1.0
        details: dict[str, Any] = {}
        weighted_sum, total_weight = 0.0, 0.0
        missing_required: list[str] = []

        for asset, spec in self.assets.items():
            weight = float(spec.get("weight", 0))
            corr = float(spec.get("correlation", 0))
            data = market.get(asset)

            if not data:
                if spec.get("required"):
                    missing_required.append(asset)
                details[asset] = {"status": "missing"}
                continue

            change = float(data.get("change_pct", 0.0))
            if abs(change) < NOISE_THRESHOLD:
                details[asset] = {"change_pct": change, "effect": "neutral", "score": 0.0}
                total_weight += weight
                continue

            # الأصل يدعم الاتجاه إذا كان تحركه × الارتباط في نفس اتجاه الذهب
            implied = change * corr
            agreement = 1.0 if (implied * gold_sign) > 0 else -1.0
            magnitude = min(abs(change) / 0.5, 1.0)  # تشبع عند 0.5%
            contribution = agreement * magnitude

            details[asset] = {
                "change_pct": round(change, 3),
                "effect": "confirm" if agreement > 0 else "reject",
                "score": round(contribution, 3),
            }
            weighted_sum += contribution * weight
            total_weight += weight

        score = weighted_sum / total_weight if total_weight else 0.0

        vix = market.get("VIX", {}).get("price")
        vix_block = vix is not None and float(vix) > self.vix_limit

        if vix_block:
            verdict, reason = "conflict", f"VIX {vix} فوق الحد {self.vix_limit} — لا صفقات جديدة"
        elif missing_required:
            verdict, reason = "neutral", f"بيانات مطلوبة ناقصة: {missing_required}"
        elif score <= self.conflict_threshold:
            verdict, reason = "conflict", f"الأصول المرجعية تعارض الاتجاه (score={score:.2f})"
        elif score >= 0.25:
            verdict, reason = "aligned", f"الأصول المرجعية تؤكد الاتجاه (score={score:.2f})"
        else:
            verdict, reason = "neutral", f"إشارات مختلطة (score={score:.2f})"

        return {
            "verdict": verdict,
            "score": round(score, 3),
            "details": details,
            "reason": reason,
            "aligned": verdict == "aligned",
        }

    @staticmethod
    def format_report(result: dict[str, Any]) -> str:
        """تنسيق مخرج البوابة حسب البند 4.3."""
        lines = ["📊 Correlation Gate:"]
        for asset, info in result.get("details", {}).items():
            if info.get("status") == "missing":
                lines.append(f"   {asset}: — (لا بيانات)")
            else:
                lines.append(
                    f"   {asset}: {info['change_pct']:+.2f}% → {info['effect']}"
                )
        lines.append(f"   الحكم النهائي: {result.get('verdict')} — {result.get('reason')}")
        return "\n".join(lines)
