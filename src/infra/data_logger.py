"""مسجّل البيانات — سجل الأداء المؤسساتي + الذاكرة التكيفية (البند 10).

قاعدة SQLite واحدة تحمل: الإشارات، الصفقات، اللقطات التحليلية، ونتائج التدقيق.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    direction    TEXT NOT NULL,
    entry        REAL NOT NULL,
    sl           REAL NOT NULL,
    tp1          REAL, tp2 REAL, tp3 REAL,
    rr           REAL,
    acs          REAL,
    poi_type     TEXT,
    setup        TEXT,
    scenario     TEXT,
    executed     INTEGER DEFAULT 0,
    reject_reason TEXT,
    payload      TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    ticket       INTEGER PRIMARY KEY,
    opened_at    TEXT NOT NULL,
    closed_at    TEXT,
    direction    TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    entry        REAL NOT NULL,
    sl           REAL NOT NULL,
    tp1          REAL, tp2 REAL, tp3 REAL,
    volume       REAL NOT NULL,
    acs          REAL,
    poi_type     TEXT,
    exit_price   REAL,
    exit_reason  TEXT,
    pnl          REAL DEFAULT 0,
    r_multiple   REAL,
    payload      TEXT
);

CREATE TABLE IF NOT EXISTS analysis_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    bias         TEXT,
    acs          REAL,
    sweep        TEXT,
    verdict      TEXT,
    payload      TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    message      TEXT,
    payload      TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at);
CREATE INDEX IF NOT EXISTS idx_trades_poi ON trades(poi_type);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DataLogger:
    def __init__(self, db_path: Path | str = "data/trades.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # كتابة -------------------------------------------------------------------
    def log_signal(
        self, signal: Any, executed: bool = False, reject_reason: str = ""
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO signals
                   (created_at, direction, entry, sl, tp1, tp2, tp3, rr, acs,
                    poi_type, setup, scenario, executed, reject_reason, payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _now(), signal.direction, signal.entry, signal.sl,
                    signal.tp1, signal.tp2, signal.tp3, round(signal.rr, 3),
                    signal.acs, signal.poi_type, signal.setup, signal.scenario,
                    int(executed), reject_reason,
                    json.dumps(signal.to_dict(), ensure_ascii=False, default=str),
                ),
            )
            return int(cur.lastrowid or 0)

    def log_trade_open(self, trade: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trades
                   (ticket, opened_at, direction, symbol, entry, sl, tp1, tp2, tp3,
                    volume, acs, poi_type, payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade.ticket, trade.opened_at.isoformat(), trade.direction,
                    trade.symbol, trade.entry, trade.sl, trade.tp1, trade.tp2,
                    trade.tp3, trade.initial_volume, trade.acs, trade.poi_type,
                    json.dumps(trade.to_dict(), ensure_ascii=False, default=str),
                ),
            )

    def log_trade_close(self, trade: Any) -> None:
        r_multiple = trade.r_multiple(trade.exit_price) if trade.exit_price else 0.0
        with self._conn() as conn:
            conn.execute(
                """UPDATE trades SET closed_at=?, exit_price=?, exit_reason=?,
                   pnl=?, r_multiple=?, payload=? WHERE ticket=?""",
                (
                    (trade.closed_at or datetime.now(timezone.utc)).isoformat(),
                    trade.exit_price, trade.exit_reason, trade.pnl, round(r_multiple, 3),
                    json.dumps(trade.to_dict(), ensure_ascii=False, default=str),
                    trade.ticket,
                ),
            )

    def log_analysis(self, analysis: dict[str, Any]) -> None:
        bias = analysis.get("bias") or {}
        sweep = analysis.get("sweep") or {}
        acs = analysis.get("acs")
        # acs يصل كقاموس مفصّل من AnalysisResult، أو كرقم من مستدعٍ مبسّط
        score = float(acs.get("score", 0)) if isinstance(acs, dict) else float(acs or 0)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO analysis_log (created_at, bias, acs, sweep, verdict, payload)"
                " VALUES (?,?,?,?,?,?)",
                (
                    _now(), str(bias.get("direction")), score,
                    str(sweep.get("type", "none")), str(analysis.get("verdict", "")),
                    json.dumps(analysis, ensure_ascii=False, default=str),
                ),
            )

    def log_event(self, kind: str, message: str, payload: dict | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO events (created_at, kind, message, payload) VALUES (?,?,?,?)",
                (_now(), kind, message,
                 json.dumps(payload or {}, ensure_ascii=False, default=str)),
            )

    # قراءة -------------------------------------------------------------------
    def closed_trades(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE closed_at IS NOT NULL"
                " ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def daily_pnl(self, day: str | None = None) -> float:
        day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades"
                " WHERE closed_at LIKE ?",
                (f"{day}%",),
            ).fetchone()
        return float(row["total"])

    def performance_by_poi(self, min_trades: int = 20) -> dict[str, dict[str, float]]:
        """الذاكرة التكيفية: نسبة نجاح كل نوع POI (البند 10.2)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT poi_type,
                          COUNT(*) AS trades,
                          SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                          AVG(r_multiple) AS avg_r
                   FROM trades WHERE closed_at IS NOT NULL AND poi_type IS NOT NULL
                   GROUP BY poi_type""",
            ).fetchall()

        stats: dict[str, dict[str, float]] = {}
        for row in rows:
            trades = int(row["trades"])
            stats[str(row["poi_type"])] = {
                "trades": float(trades),
                "win_rate": (int(row["wins"] or 0) / trades) if trades else 0.0,
                "avg_r": float(row["avg_r"] or 0.0),
                # لا يُعتد بالإحصاء قبل بلوغ الحد الأدنى من الصفقات
                "significant": float(trades >= min_trades),
            }
        return stats

    def summary(self, limit: int = 500) -> dict[str, Any]:
        trades = self.closed_trades(limit)
        if not trades:
            return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                    "net_pnl": 0.0, "avg_r": 0.0, "max_drawdown": 0.0}

        pnls = [float(t["pnl"] or 0) for t in trades][::-1]  # ترتيب زمني تصاعدي
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_loss = abs(sum(losses))

        equity, peak, max_dd = 0.0, 0.0, 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

        rs = [float(t["r_multiple"] or 0) for t in trades]
        return {
            "trades": len(pnls),
            "win_rate": len(wins) / len(pnls),
            "profit_factor": (sum(wins) / gross_loss) if gross_loss else float("inf"),
            "net_pnl": sum(pnls),
            "avg_r": sum(rs) / len(rs) if rs else 0.0,
            "max_drawdown": max_dd,
        }
