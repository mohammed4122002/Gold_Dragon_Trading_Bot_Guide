"""أدوات مشتركة للاختبارات — بناء شموع مُصمَّمة يدوياً بدل الاعتماد على العشوائية."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config  # noqa: E402
from src.infra.broker import SymbolSpec  # noqa: E402

START = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)  # اثنين، داخل London KZ


def make_bars(rows: list[tuple[float, float, float, float]], minutes: int = 15,
              start: datetime = START, volume: float = 1000.0) -> pd.DataFrame:
    """بناء DataFrame من صفوف (open, high, low, close)."""
    times = [start + timedelta(minutes=minutes * i) for i in range(len(rows))]
    return pd.DataFrame(
        {
            "time": times,
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [volume] * len(rows),
        }
    )


def trend_bars(count: int, start_price: float = 2400.0, step: float = 1.0,
               wick: float = 0.4, minutes: int = 60, leg: int = 4,
               retrace: float = 0.6, start: datetime = START) -> pd.DataFrame:
    """اتجاه متعرّج: ساق دافعة من ``leg`` شمعة ثم ساق تصحيحية أقصر.

    الاتجاه المستقيم لا يُنتج فراكتلات أصلاً (القمة تحتاج جارين أدنى منها
    على كل جانب)، وهو لا يوجد في السوق الحقيقي. التعرّج هو ما يولّد تسلسل
    HH/HL الذي يقرأه محرك الهيكل. ``step`` سالب يعطي اتجاهاً هابطاً.
    """
    rows: list[tuple[float, float, float, float]] = []
    price = start_price
    for i in range(count):
        impulse = (i // leg) % 2 == 0
        move = step if impulse else -step * retrace
        open_, close = price, price + move
        rows.append((open_, max(open_, close) + wick, min(open_, close) - wick, close))
        price = close
    return make_bars(rows, minutes=minutes, start=start)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """إعدادات حقيقية من config/ مع تحويل كل الكتابة إلى مجلد مؤقت."""
    cfg = load_config()
    cfg.data["storage"]["database"] = str(tmp_path / "trades.db")
    cfg.data["storage"]["log_file"] = str(tmp_path / "bot.log")
    cfg.data["kill_switch"]["state_file"] = str(tmp_path / "kill_switch.json")
    cfg.data["gold_dragon"]["psychology_gate"]["state_file"] = str(tmp_path / "psy.json")
    cfg.data["news"]["calendar_file"] = str(tmp_path / "news.json")
    cfg.root = tmp_path
    return cfg


@pytest.fixture
def spec():
    return SymbolSpec(name="XAUUSD", digits=2, contract_size=100.0,
                      min_lot=0.01, max_lot=10.0, lot_step=0.01)


def legs_bars(points: list[float], bars_per_leg: int = 12, wick: float = 0.6,
              minutes: int = 60, start: datetime = START) -> pd.DataFrame:
    """بناء شموع من نقاط انعطاف: يُولّد سيقاناً خطية بين كل نقطتين.

    أوضح من ضبط معادلة عشوائية: تكتب مسار السعر الذي تريد اختباره حرفياً.
    """
    rows: list[tuple[float, float, float, float]] = []
    for start_price, end_price in zip(points, points[1:], strict=False):
        step = (end_price - start_price) / bars_per_leg
        price = start_price
        for _ in range(bars_per_leg):
            open_, close = price, price + step
            rows.append((open_, max(open_, close) + wick, min(open_, close) - wick, close))
            price = close
    return make_bars(rows, minutes=minutes, start=start)


def bullish_sweep_scenario() -> tuple[pd.DataFrame, pd.DataFrame]:
    """سيناريو شراء مكتمل الأركان، مبني يدوياً لا عشوائياً.

    التسلسل: قاعان متساويان (SSL) → سويپ بالظل تحتهما مع إغلاق فوقهما →
    شمعة إزاحة صعودية قوية تكسر آخر قمة (CHoCH) → OB صعودي خلف الحركة.
    """
    rows: list[tuple[float, float, float, float]] = []

    def leg(from_price: float, to_price: float, bars: int) -> None:
        step = (to_price - from_price) / bars
        price = from_price
        for _ in range(bars):
            open_, close = price, price + step
            rows.append((open_, max(open_, close) + 0.4,
                         min(open_, close) - 0.4, close))
            price = close

    leg(2420.0, 2384.0, 14)                       # هبوط نحو منطقة السيولة
    rows.append((2384.0, 2384.4, 2380.0, 2383.0))  # القاع الأول عند 2380.0
    leg(2383.0, 2392.0, 5)                        # ارتداد
    leg(2392.0, 2383.0, 5)                        # هبوط ثانٍ
    rows.append((2383.0, 2383.4, 2380.2, 2382.0))  # قاع مساوٍ (ضمن التسامح)
    leg(2382.0, 2389.0, 4)                        # ارتداد يصنع القمة المرجعية
    rows.append((2389.0, 2389.2, 2380.6, 2381.0))  # شمعة OB الهابطة
    rows.append((2381.0, 2382.2, 2376.8, 2382.0))  # السويپ: ظل تحت 2380
    rows.append((2382.0, 2394.5, 2381.5, 2394.0))  # إزاحة تكسر القمة → CHoCH
    rows.append((2394.0, 2395.0, 2392.5, 2393.5))  # تثبيت فوق الكسر

    df_m15 = make_bars(rows, minutes=15, start=START)

    # H1 بنطاق واسع ينتهي قرب سعر M15، فيقع السعر و POI في النصف السفلي
    df_h1 = legs_bars([2400.0, 2460.0, 2350.0, 2445.0, 2360.0, 2395.0],
                      bars_per_leg=14, minutes=60, start=START - timedelta(days=6))
    return df_m15, df_h1
