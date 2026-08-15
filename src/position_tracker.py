"""متابعة الصفقات المفتوحة وتنفيذ خطة الإدارة (البند 8.2).

التسلسل الإلزامي: إغلاق جزئي 1 عند TP1 → نقل الوقف لنقطة التعادل →
إغلاق جزئي 2 عند TP2 → وقف متحرّك خلف آخر Swing → إغلاق نهائي.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .core.indicators import swing_highs, swing_lows
from .infra.logger import get_logger
from .models import Trade, TradeStage
from .order_manager import OrderManager

log = get_logger(__name__)


class PositionTracker:
    def __init__(self, config: Any, order_manager: OrderManager, notifier: Any,
                 data_logger: Any = None) -> None:
        self.config = config
        self.orders = order_manager
        self.broker = order_manager.broker
        self.notifier = notifier
        self.data_logger = data_logger

        mgmt = "risk.management."
        self.partial_1 = float(config.get(mgmt + "partial_1_pct", 0.30))
        self.partial_2 = float(config.get(mgmt + "partial_2_pct", 0.30))
        self.breakeven_at_r = float(config.get(mgmt + "breakeven_at_r", 1.0))
        self.breakeven_offset_r = float(config.get(mgmt + "breakeven_offset_r", 0.02))
        self.trail_after_r = float(config.get(mgmt + "trail_after_r", 3.0))
        self.trail_lookback = int(config.get(mgmt + "trail_swing_lookback", 5))

        self.trades: dict[int, Trade] = {}

    # التسجيل ----------------------------------------------------------------
    def register(self, trade: Trade) -> None:
        self.trades[trade.ticket] = trade
        if self.data_logger:
            self.data_logger.log_trade_open(trade)

    @property
    def open_trades(self) -> list[Trade]:
        return [t for t in self.trades.values() if t.is_open]

    def sync_with_broker(self) -> list[Trade]:
        """اكتشاف الصفقات التي أغلقها الوسيط (SL/TP) وتسجيل نتيجتها.

        بدون هذه المزامنة يبقى البوت يظن أن صفقة مغلقة ما زالت مفتوحة،
        فيحسب مخاطرة وهمية ويمنع صفقات جديدة بلا سبب.
        """
        live = {p.ticket for p in self.broker.positions()}
        closed: list[Trade] = []

        for trade in list(self.open_trades):
            if trade.ticket in live:
                continue
            trade.stage = TradeStage.CLOSED
            trade.closed_at = datetime.now(timezone.utc)
            trade.exit_reason = trade.exit_reason or "broker_close"

            realized = self.broker.realized_pnl(trade.ticket)
            if realized is not None:
                trade.pnl = realized["pnl"]
                trade.exit_price = realized["exit"]
                trade.exit_reason = realized["reason"] or trade.exit_reason
            closed.append(trade)

            if self.data_logger:
                self.data_logger.log_trade_close(trade)
            self.notifier.send(
                f"📕 أُغلقت #{trade.ticket} ({trade.exit_reason}) — النتيجة {trade.pnl:+.2f}$",
                level="warning",
            )
        return closed

    # الإدارة ----------------------------------------------------------------
    def manage(self, df_m15: pd.DataFrame | None = None) -> list[dict[str, Any]]:
        """جولة إدارة واحدة على كل الصفقات المفتوحة."""
        actions: list[dict[str, Any]] = []
        self.sync_with_broker()
        if not self.open_trades:
            return actions  # لا داعي لسؤال الوسيط عن السعر بلا صفقات

        try:
            bid, ask = self.broker.tick()
        except Exception as exc:
            log.error("تعذّرت قراءة السعر أثناء إدارة الصفقات: %s", exc)
            return actions

        for trade in self.open_trades:
            price = bid if trade.direction == "buy" else ask
            r_now = trade.r_multiple(price)

            if trade.stage == TradeStage.OPEN and trade.reached(price, trade.tp1):
                pnl = self.orders.partial_close(trade, self.partial_1, "tp1_partial")
                trade.stage = TradeStage.PARTIAL_1_DONE
                actions.append({"ticket": trade.ticket, "action": "partial_1", "pnl": pnl})
                self.notifier.send(
                    f"📊 إغلاق جزئي 1 (30%) #{trade.ticket} @ {price:.2f} → {pnl:+.2f}$"
                )

            ready_for_breakeven = (
                trade.stage == TradeStage.PARTIAL_1_DONE
                and r_now >= self.breakeven_at_r
            )
            if ready_for_breakeven and self.orders.move_to_breakeven(
                trade, self.breakeven_offset_r
            ):
                trade.stage = TradeStage.BREAKEVEN_DONE
                actions.append({"ticket": trade.ticket, "action": "breakeven"})
                self.notifier.send(f"🛡️ نقل الوقف لنقطة التعادل #{trade.ticket}")

            if trade.stage == TradeStage.BREAKEVEN_DONE and trade.reached(price, trade.tp2):
                pnl = self.orders.partial_close(trade, self.partial_2, "tp2_partial")
                trade.stage = TradeStage.PARTIAL_2_DONE
                actions.append({"ticket": trade.ticket, "action": "partial_2", "pnl": pnl})
                self.notifier.send(
                    f"📊 إغلاق جزئي 2 (30%) #{trade.ticket} @ {price:.2f} → {pnl:+.2f}$"
                )

            if trade.stage >= TradeStage.PARTIAL_2_DONE and r_now >= self.trail_after_r:
                new_sl = self._trailing_stop(trade, df_m15, price)
                if new_sl is not None and self.orders.update_stop(trade, new_sl):
                    trade.stage = TradeStage.TRAILING
                    actions.append({"ticket": trade.ticket, "action": "trail", "sl": new_sl})

        return actions

    def _trailing_stop(self, trade: Trade, df_m15: pd.DataFrame | None,
                       price: float) -> float | None:
        """الوقف خلف آخر Swing على M15، وإلا خلف السعر بمسافة 1R."""
        if df_m15 is not None and len(df_m15) > self.trail_lookback * 2:
            if trade.direction == "buy":
                lows = swing_lows(df_m15, self.trail_lookback)
                if lows:
                    return lows[-1][1]
            else:
                highs = swing_highs(df_m15, self.trail_lookback)
                if highs:
                    return highs[-1][1]

        offset = trade.initial_risk
        return price - offset if trade.direction == "buy" else price + offset

    # التقارير ---------------------------------------------------------------
    def daily_report(self, balance: float, risk_snapshot: dict[str, Any]) -> str:
        closed_today = [
            t for t in self.trades.values()
            if t.closed_at and t.closed_at.date() == datetime.now(timezone.utc).date()
        ]
        wins = [t for t in closed_today if t.pnl > 0]
        total = sum(t.pnl for t in closed_today)
        win_rate = (len(wins) / len(closed_today) * 100) if closed_today else 0.0
        lock_state = (
            f"مقفل — {risk_snapshot.get('lock_reason', '')}"
            if risk_snapshot.get("locked") else "مفتوح"
        )

        return (
            "📋 *التقرير اليومي*\n"
            f"الرصيد: `{balance:.2f}$`\n"
            f"صفقات اليوم: `{len(closed_today)}` | مفتوحة: `{len(self.open_trades)}`\n"
            f"نسبة النجاح: `{win_rate:.0f}%`\n"
            f"صافي اليوم: `{total:+.2f}$`\n"
            f"خسارات متتالية: `{risk_snapshot.get('consecutive_losses', 0)}`\n"
            f"Drawdown: `{risk_snapshot.get('drawdown', 0):.2%}`\n"
            f"حالة القفل: `{lock_state}`"
        )
