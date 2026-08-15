"""نموذج إشارة التداول."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Side = Literal["buy", "sell"]


@dataclass
class Signal:
    """إشارة جاهزة للتنفيذ — مخرج نهائي لـ GoldDragonCore."""

    direction: Side
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    acs: float
    acs_breakdown: dict[str, float]
    poi_type: str
    invalidation: float
    setup: Literal["with_trend", "counter_trend", "range"] = "with_trend"
    lot_size: float = 0.0
    risk_amount: float = 0.0
    scenario: Literal["primary", "secondary"] = "primary"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    notes: list[str] = field(default_factory=list)

    @property
    def sl_distance(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def rr(self) -> float:
        """نسبة المكافأة/المخاطرة المعتمدة للقبول — تُقاس على TP2.

        TP1 هدف الإغلاق الجزئي فقط، أما بوابة الحد الأدنى لـ RR (البند 8.3)
        فتُطبَّق على TP2 وهو الهدف الأساسي للصفقة.
        """
        if self.sl_distance == 0:
            return 0.0
        return abs(self.tp2 - self.entry) / self.sl_distance

    @property
    def rr_tp1(self) -> float:
        if self.sl_distance == 0:
            return 0.0
        return abs(self.tp1 - self.entry) / self.sl_distance

    def is_consistent(self) -> bool:
        """فحص منطقي: SL و TP في الجهة الصحيحة من الدخول."""
        if self.entry <= 0 or self.sl <= 0:
            return False
        if self.direction == "buy":
            return self.sl < self.entry < self.tp1 <= self.tp2 <= self.tp3
        return self.sl > self.entry > self.tp1 >= self.tp2 >= self.tp3

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["rr"] = round(self.rr, 2)
        return data
