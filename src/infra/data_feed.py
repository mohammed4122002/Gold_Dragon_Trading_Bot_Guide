"""مصادر بيانات الشموع — MT5 / CSV / اصطناعية.

كل مصدر يعيد ``pandas.DataFrame`` بأعمدة موحّدة:
``time, open, high, low, close, volume`` مع فهرس رقمي وترتيب زمني تصاعدي.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .logger import get_logger

log = get_logger(__name__)

REQUIRED_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# دقائق كل إطار زمني
TIMEFRAME_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080, "MN1": 43200,
}


class DataFeedError(RuntimeError):
    """تعذّر جلب بيانات صالحة."""


def validate_frame(df: pd.DataFrame, min_bars: int = 50) -> pd.DataFrame:
    """بوابة استقبال البيانات (البند 1) — لا تحليل على بيانات ناقصة."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataFeedError(f"أعمدة ناقصة في البيانات: {missing}")
    if len(df) < min_bars:
        raise DataFeedError(f"عدد الشموع {len(df)} أقل من الحد الأدنى {min_bars}")
    if df[["open", "high", "low", "close"]].isna().any().any():
        raise DataFeedError("قيم سعرية مفقودة (NaN) في البيانات")
    bad = df[(df["high"] < df["low"]) | (df["high"] < df["close"]) | (df["low"] > df["close"])]
    if not bad.empty:
        raise DataFeedError(f"شموع غير متسقة (high/low/close) عند الفهارس {list(bad.index[:3])}")
    return df


class DataFeed(ABC):
    @abstractmethod
    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame: ...

    def get_correlation_data(self) -> dict[str, dict[str, Any]]:
        """بيانات الأصول المرتبطة. الافتراضي: لا شيء (البوابة تُحيّد)."""
        return {}

    def close(self) -> None:  # اختياري
        return None


class CsvFeed(DataFeed):
    """قراءة من ملفات CSV: ``<dir>/<SYMBOL>_<TF>.csv``.

    مفيدة للـ Backtest ولإعادة إنتاج حالة سوق بعينها أثناء التطوير.
    """

    def __init__(self, directory: Path | str, symbol: str = "XAUUSD") -> None:
        self.directory = Path(directory)
        self.symbol = symbol
        self._cache: dict[str, pd.DataFrame] = {}

    def _load(self, timeframe: str) -> pd.DataFrame:
        if timeframe in self._cache:
            return self._cache[timeframe]
        path = self.directory / f"{self.symbol}_{timeframe}.csv"
        if not path.exists():
            raise DataFeedError(f"ملف بيانات مفقود: {path}")
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.sort_values("time").reset_index(drop=True)
        self._cache[timeframe] = df
        return df

    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame:
        df = self._load(timeframe).tail(count).reset_index(drop=True)
        return validate_frame(df, min_bars=min(count, 50))


class SyntheticFeed(DataFeed):
    """بيانات اصطناعية قابلة لإعادة الإنتاج (seed ثابت).

    للاختبار والتشغيل التجريبي فقط — **لا تعكس سوقاً حقيقياً**، ولا يجوز
    استخلاص أي حكم على أداء الاستراتيجية منها.
    """

    def __init__(self, symbol: str = "XAUUSD", seed: int = 42,
                 start_price: float = 2400.0) -> None:
        self.symbol = symbol
        self.seed = seed
        self.start_price = start_price

    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame:
        minutes = TIMEFRAME_MINUTES.get(timeframe.upper(), 15)
        rng = np.random.default_rng(self.seed + minutes)

        # مشي عشوائي مع انجراف طفيف وتقلب متناسب مع طول الإطار
        vol = 0.35 * np.sqrt(minutes)
        steps = rng.normal(loc=0.01 * np.sqrt(minutes), scale=vol, size=count)
        close = self.start_price + np.cumsum(steps)
        open_ = np.concatenate([[self.start_price], close[:-1]])
        wick = np.abs(rng.normal(scale=vol * 0.8, size=count))
        high = np.maximum(open_, close) + wick
        low = np.minimum(open_, close) - np.abs(rng.normal(scale=vol * 0.8, size=count))
        volume = rng.integers(500, 5000, size=count).astype(float)

        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        times = [end - timedelta(minutes=minutes * (count - i - 1)) for i in range(count)]

        df = pd.DataFrame(
            {"time": times, "open": open_, "high": high, "low": low,
             "close": close, "volume": volume}
        )
        return validate_frame(df, min_bars=min(count, 50))

    def get_correlation_data(self) -> dict[str, dict[str, Any]]:
        rng = np.random.default_rng(self.seed)
        return {
            "DXY": {"price": 104.2, "change_pct": float(rng.normal(0, 0.25)), "trend": "neutral"},
            "US10Y": {"price": 4.25, "change_pct": float(rng.normal(0, 0.4)), "trend": "neutral"},
            "VIX": {"price": 16.5, "change_pct": float(rng.normal(0, 2.0)), "trend": "neutral"},
            "SPX500": {"price": 5200.0, "change_pct": float(rng.normal(0, 0.3)),
                       "trend": "neutral"},
        }


class MT5Feed(DataFeed):
    """جلب الشموع من MetaTrader 5 (يتطلب اتصالاً مُهيّأ عبر MT5Broker)."""

    def __init__(self, symbol: str = "XAUUSD",
                 correlation_symbols: dict[str, list[str]] | None = None) -> None:
        self.symbol = symbol
        self.correlation_symbols = correlation_symbols or {}
        self._resolved: dict[str, str | None] = {}

    def _api(self) -> Any:
        try:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DataFeedError(
                "مكتبة MetaTrader5 غير متاحة — استخدم feed: csv أو synthetic"
            ) from exc
        return mt5

    def _tf_const(self, timeframe: str) -> Any:
        mt5 = self._api()
        const = getattr(mt5, f"TIMEFRAME_{timeframe.upper()}", None)
        if const is None:
            raise DataFeedError(f"إطار زمني غير مدعوم في MT5: {timeframe}")
        return const

    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame:
        mt5 = self._api()
        rates = mt5.copy_rates_from_pos(self.symbol, self._tf_const(timeframe), 0, count)
        if rates is None or len(rates) == 0:
            raise DataFeedError(f"لا توجد بيانات لـ {self.symbol} {timeframe}: {mt5.last_error()}")

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        if "tick_volume" in df.columns:
            df["volume"] = df["tick_volume"].astype(float)
        df = df[REQUIRED_COLUMNS]
        # الشمعة الأخيرة غير مكتملة — نستبعدها حتى لا يُبنى قرار على شمعة تتغيّر
        df = df.iloc[:-1].reset_index(drop=True)
        return validate_frame(df, min_bars=min(count - 1, 50))

    def _resolve(self, key: str, aliases: list[str]) -> str | None:
        """إيجاد اسم الرمز الفعلي لدى الوسيط من بين الأسماء البديلة."""
        if key in self._resolved:
            return self._resolved[key]
        mt5 = self._api()
        for alias in aliases:
            if mt5.symbol_info(alias) is not None:
                mt5.symbol_select(alias, True)
                self._resolved[key] = alias
                return alias
        log.warning("تعذّر إيجاد رمز الارتباط %s لدى الوسيط (جُرّبت: %s)", key, aliases)
        self._resolved[key] = None
        return None

    def get_correlation_data(self) -> dict[str, dict[str, Any]]:
        mt5 = self._api()
        out: dict[str, dict[str, Any]] = {}
        for key, aliases in self.correlation_symbols.items():
            name = self._resolve(key, aliases)
            if not name:
                continue
            rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 0, 25)
            if rates is None or len(rates) < 2:
                continue
            df = pd.DataFrame(rates)
            price = float(df["close"].iloc[-1])
            ref = float(df["close"].iloc[0])
            change = ((price - ref) / ref * 100) if ref else 0.0
            out[key] = {
                "price": price,
                "change_pct": change,
                "trend": "up" if change > 0.05 else "down" if change < -0.05 else "neutral",
            }
        return out


def build_feed(config: Any) -> DataFeed:
    """مصنع المصدر حسب ``bot.feed``."""
    kind = str(config.get("bot.feed", "synthetic")).lower()
    symbol = config.symbol

    if kind == "csv":
        return CsvFeed(config.path("bot.csv_dir", "data"), symbol)
    if kind == "mt5":
        feeds = config.get("correlation_feeds", {}) or {}
        aliases = {k: v.get("aliases", [k]) for k, v in feeds.items()}
        return MT5Feed(symbol, aliases)
    return SyntheticFeed(symbol)
