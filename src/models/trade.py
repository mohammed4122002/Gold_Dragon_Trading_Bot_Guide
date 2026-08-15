"""نموذج الصفقة المفتوحة/المغلقة."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Literal


class TradeStage(IntEnum):
    """مراحل إدارة الصفقة بعد الدخول (البند 8.2)."""

    OPEN = 0
    PARTIAL_1_DONE = 1
    BREAKEVEN_DONE = 2
    PARTIAL_2_DONE = 3
    TRAILING = 4
    CLOSED = 5


@dataclass
class Trade:
    ticket: int
    direction: Literal["buy", "sell"]
    symbol: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    volume: float
    initial_volume: float
    acs: float = 0.0
    poi_type: str = ""
    stage: TradeStage = TradeStage.OPEN
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    pnl: float = 0.0
    exit_price: float | None = None
    exit_reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.stage != TradeStage.CLOSED

    @property
    def initial_risk(self) -> float:
        return abs(self.entry - self.sl)

    def r_multiple(self, price: float) -> float:
        """كم R حقق السعر الحالي مقارنة بالمخاطرة الأولية."""
        risk = self.initial_risk
        if risk == 0:
            return 0.0
        move = price - self.entry if self.direction == "buy" else self.entry - price
        return move / risk

    def reached(self, price: float, target: float) -> bool:
        return price >= target if self.direction == "buy" else price <= target

    def is_at_breakeven(self, tolerance: float = 1e-9) -> bool:
        return abs(self.sl - self.entry) <= max(tolerance, self.initial_risk * 0.05)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stage"] = int(self.stage)
        data["opened_at"] = self.opened_at.isoformat()
        data["closed_at"] = self.closed_at.isoformat() if self.closed_at else None
        return data
