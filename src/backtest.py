"""محرك Backtest — إعادة تشغيل البروتوكول على بيانات تاريخية.

يستخدم نفس النواة ونفس مدير المخاطرة ونفس منطق الإدارة المستخدم في التشغيل
الحي، عبر ``PaperBroker``. أي اختلاف بين نتيجة الـ Backtest والتشغيل الحي
مصدره البيانات والتنفيذ، لا اختلاف في المنطق.

قيود يجب الوعي بها قبل تصديق أي رقم:
- التنفيذ يتم عند إغلاق شمعة M15 (لا تنفيذ داخل الشمعة).
- عند ملامسة SL و TP في نفس الشمعة يُفترض ضرب SL أولاً (افتراض متحفظ).
- السبريد ثابت والانزلاق ثابت — الواقع أسوأ عند الأخبار.
- لا يوجد تقويم أخبار تاريخي افتراضياً → فلتر الأخبار محايد في الـ Backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from .core.gold_dragon_core import GoldDragonCore
from .infra.broker import PaperBroker, SymbolSpec
from .infra.logger import get_logger
from .models import Trade, TradeStage
from .risk_manager import RiskManager
from .strategies import CorrelationGate, NewsFilter, PsychologyGate, SessionManager

log = get_logger(__name__)


@dataclass
class BacktestResult:
    initial_balance: float
    final_balance: float
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)

    @property
    def net_pnl(self) -> float:
        return self.final_balance - self.initial_balance

    def stats(self) -> dict[str, Any]:
        closed = [t for t in self.trades if t.get("pnl") is not None]
        if not closed:
            return {"trades": 0, "note": "لم تُنفَّذ أي صفقة — راجع شروط القبول"}

        pnls = [float(t["pnl"]) for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_loss = abs(sum(losses)) or 1e-9

        peak, max_dd = self.initial_balance, 0.0
        for _, equity in self.equity_curve:
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)

        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        return {
            "trades": len(pnls),
            "win_rate": len(wins) / len(pnls),
            "profit_factor": sum(wins) / gross_loss,
            "net_pnl": sum(pnls),
            "return_pct": ((self.final_balance / self.initial_balance - 1)
                           if self.initial_balance else 0),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": sum(pnls) / len(pnls),
            "max_drawdown": max_dd,
            "final_balance": self.final_balance,
        }

    def format(self) -> str:
        stats = self.stats()
        if stats.get("trades", 0) == 0:
            return f"لا صفقات. أسباب الرفض الأكثر تكراراً: {self.top_rejections()}"
        return (
            "═══ نتيجة Backtest ═══\n"
            f"الصفقات: {stats['trades']}\n"
            f"نسبة النجاح: {stats['win_rate']:.1%}\n"
            f"Profit Factor: {stats['profit_factor']:.2f}\n"
            f"صافي الربح: {stats['net_pnl']:+.2f}$ ({stats['return_pct']:+.1%})\n"
            f"التوقع لكل صفقة: {stats['expectancy']:+.2f}$\n"
            f"أقصى Drawdown: {stats['max_drawdown']:.1%}\n"
            f"الرصيد النهائي: {stats['final_balance']:.2f}$\n"
            f"أسباب الرفض: {self.top_rejections()}"
        )

    def top_rejections(self, n: int = 5) -> str:
        top = sorted(self.rejections.items(), key=lambda kv: -kv[1])[:n]
        return " | ".join(f"{reason} ×{count}" for reason, count in top) or "—"


class Backtester:
    def __init__(self, config: Any, balance: float = 1000.0,
                 spread: float = 0.20, slippage: float = 0.05) -> None:
        self.config = config
        self.core = GoldDragonCore(config)
        self.spec = SymbolSpec(
            name=config.symbol,
            digits=int(config.get("symbols.XAUUSD.digits", 2)),
            contract_size=float(config.get("broker.contract_size", 100)),
            min_lot=float(config.get("broker.min_lot", 0.01)),
            max_lot=float(config.get("broker.max_lot", 10.0)),
            lot_step=float(config.get("broker.lot_step", 0.01)),
        )
        self.broker = PaperBroker(self.spec, balance, spread, slippage=slippage)
        self.initial_balance = balance

        self.sessions = SessionManager(config)
        self.news = NewsFilter(config)
        self.correlation = CorrelationGate(config)
        self.psychology = PsychologyGate(config)

        # حالة مخاطرة معزولة حتى لا يلوّث الـ Backtest حالة التشغيل الحي
        self.risk = RiskManager(config, self.spec,
                                state_file=config.path("storage.database").parent
                                / "backtest_risk_state.json")
        self.risk.state = self.risk._default_state()

        mgmt = "risk.management."
        self.partial_1 = float(config.get(mgmt + "partial_1_pct", 0.30))
        self.breakeven_offset = float(config.get(mgmt + "breakeven_offset_r", 0.02))

    def run(self, df_m15: pd.DataFrame, df_h1: pd.DataFrame,
            warmup: int = 250, window: int = 400) -> BacktestResult:
        result = BacktestResult(self.initial_balance, self.initial_balance)
        open_trades: dict[int, Trade] = {}

        h1_times = pd.DatetimeIndex(df_h1["time"])

        for i in range(warmup, len(df_m15)):
            bar = df_m15.iloc[i]
            when = pd.Timestamp(bar["time"]).to_pydatetime()

            # 1) تحديث المراكز القائمة على شمعة هذه الخطوة
            events = self.broker.process_bar(
                float(bar["high"]), float(bar["low"]), float(bar["close"]), when
            )
            for event in events:
                trade = open_trades.pop(event["ticket"], None)
                if trade:
                    trade.pnl += event["pnl"]
                    trade.exit_price = event["price"]
                    trade.exit_reason = event["reason"]
                    trade.stage = TradeStage.CLOSED
                    self.risk.record_result(trade.pnl)
                    result.trades.append(trade.to_dict())

            self._manage(open_trades, float(bar["close"]))
            result.equity_curve.append((when.isoformat(), self.broker.equity()))

            if open_trades:
                continue  # صفقة واحدة في كل مرة داخل الـ Backtest

            # 2) تحليل بنافذة منزلقة
            m15_window = df_m15.iloc[max(0, i - window + 1): i + 1].reset_index(drop=True)
            h1_cut = int(h1_times.searchsorted(pd.Timestamp(bar["time"]), side="right"))
            h1_window = df_h1.iloc[max(0, h1_cut - window): h1_cut].reset_index(drop=True)
            if len(h1_window) < 60:
                continue

            context = self._context(when)
            analysis = self.core.analyze(h1_window, m15_window, None, context)

            if analysis.signal is None:
                for reason in analysis.rejections:
                    key = reason.split("—")[0].strip()[:60]
                    result.rejections[key] = result.rejections.get(key, 0) + 1
                continue

            # 3) بوابات المخاطرة ثم التنفيذ
            balance = self.broker.balance()
            decision = self.risk.can_trade(balance, 0, 0.0)
            if not decision.allowed:
                key = decision.reason[:60]
                result.rejections[key] = result.rejections.get(key, 0) + 1
                continue

            signal = analysis.signal
            sizing = self.risk.calculate_position_size(balance, signal.entry, signal.sl)
            if sizing["lots"] <= 0:
                result.rejections["حجم مركز غير كافٍ"] = (
                    result.rejections.get("حجم مركز غير كافٍ", 0) + 1
                )
                continue

            ticket = self.broker.market_order(
                signal.direction, sizing["lots"], signal.sl, signal.tp3,
                comment=f"BT_{signal.poi_type}",
            )
            position = next(p for p in self.broker.positions() if p.ticket == ticket)
            open_trades[ticket] = Trade(
                ticket=ticket, direction=signal.direction, symbol=self.spec.name,
                entry=position.entry, sl=signal.sl, tp1=signal.tp1, tp2=signal.tp2,
                tp3=signal.tp3, volume=sizing["lots"], initial_volume=sizing["lots"],
                acs=signal.acs, poi_type=signal.poi_type, opened_at=when,
            )

        # إغلاق ما تبقى بسعر آخر شمعة
        for ticket, trade in list(open_trades.items()):
            pnl = self.broker.close(ticket, reason="end_of_data")
            trade.pnl += pnl
            trade.exit_reason = "end_of_data"
            trade.stage = TradeStage.CLOSED
            result.trades.append(trade.to_dict())

        result.final_balance = self.broker.balance()
        return result

    # ═══════════════════════════════════════════════════════════════
    def _manage(self, open_trades: dict[int, Trade], price: float) -> None:
        """إغلاق جزئي عند TP1 ثم نقل الوقف لنقطة التعادل."""
        for trade in list(open_trades.values()):
            position = next(
                (p for p in self.broker.positions() if p.ticket == trade.ticket), None
            )
            if position is None:
                continue

            if trade.stage == TradeStage.OPEN and trade.reached(price, trade.tp1):
                volume = self.spec.normalize_lot(trade.initial_volume * self.partial_1)
                if 0 < volume < position.volume:
                    trade.pnl += self.broker.close(trade.ticket, volume, "tp1_partial")
                    trade.volume = position.volume - volume
                trade.stage = TradeStage.PARTIAL_1_DONE

                offset = trade.initial_risk * self.breakeven_offset
                new_sl = (trade.entry + offset if trade.direction == "buy"
                          else trade.entry - offset)
                self.broker.modify(trade.ticket, sl=new_sl)
                trade.sl = new_sl

    def _context(self, when: datetime) -> dict[str, Any]:
        return {
            "session": self.sessions.current_session(when),
            "news": self.news.check(when),
            # الحالة النفسية والارتباط لا يمكن إعادة بنائهما تاريخياً بصدق،
            # لذا يُثبّتان على "محايد" بدل تلوين النتائج بتفاؤل زائف.
            "psychology": {"score": 0.5, "blocked": False, "state": "backtest"},
            "correlation": {"verdict": "neutral", "score": 0.0},
            "minutes_to_killzone": None,
        }
