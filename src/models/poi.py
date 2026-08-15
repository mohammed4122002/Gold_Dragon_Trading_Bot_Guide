"""نموذج منطقة الاهتمام (Point of Interest)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PoiType = Literal["OB", "FVG", "Breaker", "Mitigation", "Rejection", "VolumeImbalance"]
Direction = Literal["bullish", "bearish"]


@dataclass
class POI:
    """منطقة اهتمام معتمدة حسب البند 5 من Gold Dragon Pro v4.

    ``top`` و ``bottom`` هما حدّا المنطقة السعرية. الدخول يكون من الحافة
    القريبة من السعر عند الاقتراب (Proximal)، والـ SL خلف الحافة البعيدة (Distal).
    """

    type: PoiType
    direction: Direction
    top: float
    bottom: float
    timeframe: str
    bar_index: int
    confidence: float = 0.5
    mitigations: int = 0
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.bottom > self.top:
            self.top, self.bottom = self.bottom, self.top

    @property
    def height(self) -> float:
        return self.top - self.bottom

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def proximal(self) -> float:
        """الحافة التي يلمسها السعر أولاً — نقطة الدخول المرشحة."""
        return self.top if self.direction == "bullish" else self.bottom

    @property
    def distal(self) -> float:
        """الحافة البعيدة — خلفها يوضع وقف الخسارة."""
        return self.bottom if self.direction == "bullish" else self.top

    @property
    def is_fresh(self) -> bool:
        return self.mitigations == 0

    @property
    def is_exhausted(self) -> bool:
        """مُختبرة مرتين أو أكثر → مرفوضة (البند 5.1/4)."""
        return self.mitigations >= 2

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def distance_from(self, price: float) -> float:
        """المسافة السعرية بين السعر الحالي والحافة القريبة (0 إذا داخل المنطقة)."""
        if self.contains(price):
            return 0.0
        return self.bottom - price if price < self.bottom else price - self.top

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
