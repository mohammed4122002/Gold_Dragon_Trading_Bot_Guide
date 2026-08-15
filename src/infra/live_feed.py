"""مصادر بيانات حية عبر HTTP — لتشغيل البوت على Linux/سحابة بلا MetaTrader.

لماذا هذا الملف موجود: مكتبة MetaTrader5 تعمل على Windows فقط وتتطلب طرفية
MT5 مفتوحة على نفس الجهاز. أي نشر على Railway أو أي حاوية Linux لا يستطيع
استخدامها. هذه المصادر تجلب شموع XAU/USD وأصول الارتباط عبر REST، فيعمل
التحليل الكامل والتنفيذ الورقي (forward test) في أي مكان.

⚠️ فرق جوهري يجب وعيه: السعر هنا من مزوّد بيانات، لا من وسيطك. الفروقات
الطبيعية (سبريد، بايس العقود الآجلة، توقيت الإغلاق) تعني أن مستويات
الدخول/الوقف قد تختلف بدولارات عن منصتك. النتائج مؤشر على جودة المنطق،
لا بديل عن اختبار على حساب تجريبي لدى وسيطك.

المزوّدان:
- ``yahoo``      — بلا مفتاح. XAUUSD=X للفوري، مع DXY و US10Y و VIX و SPX.
- ``twelvedata`` — يتطلب ``GD_TWELVEDATA_KEY`` (الطبقة المجانية ~800 طلب/يوم).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .data_feed import REQUIRED_COLUMNS, DataFeed, DataFeedError, validate_frame
from .logger import get_logger

log = get_logger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; GoldDragonBot/1.0)"
HTTP_TIMEOUT = 20

# مهلة صلاحية الذاكرة المؤقتة لكل إطار (ثانية).
# الهدف: عدم استنزاف حصة المزوّد. شمعة H4 لا تتغير كل دقيقة.
DEFAULT_TTL = {"M5": 60, "M15": 90, "M30": 180, "H1": 300, "H4": 900, "D1": 3600}


class LiveFeedError(DataFeedError):
    """فشل جلب بيانات حية."""


def _http_get_json(url: str, params: dict[str, Any] | None = None,
                   retries: int = 2) -> dict[str, Any]:
    """طلب GET يعيد JSON، مع إعادة محاولة متدرجة على الأخطاء العابرة."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 4xx (عدا 429) خطأ دائم — لا فائدة من إعادة المحاولة
            if exc.code != 429 and 400 <= exc.code < 500:
                break
        except Exception as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(2 ** attempt)

    raise LiveFeedError(f"فشل الطلب {url[:80]}: {last_error}")


class _CachedFeed(DataFeed):
    """أساس مشترك: ذاكرة مؤقتة لكل إطار زمني + بيانات ارتباط بمهلة أطول."""

    def __init__(self, ttl: dict[str, int] | None = None) -> None:
        self.ttl = {**DEFAULT_TTL, **(ttl or {})}
        self._bars_cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._corr_cache: tuple[float, dict[str, dict[str, Any]]] | None = None

    def _cached_bars(self, timeframe: str) -> pd.DataFrame | None:
        entry = self._bars_cache.get(timeframe)
        if not entry:
            return None
        fetched_at, frame = entry
        if time.time() - fetched_at > self.ttl.get(timeframe.upper(), 300):
            return None
        return frame

    def _store_bars(self, timeframe: str, frame: pd.DataFrame) -> None:
        self._bars_cache[timeframe] = (time.time(), frame)

    @staticmethod
    def _finalize(frame: pd.DataFrame, count: int, drop_forming: bool = True) -> pd.DataFrame:
        """ترتيب، وتنظيف، وإسقاط الشمعة الجارية غير المكتملة.

        الشمعة الجارية تتغير مع كل تحديث؛ بناء قرار عليها يعني قراراً
        يتبدّل تحت أقدام البوت.
        """
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        frame = frame.sort_values("time").reset_index(drop=True)
        if drop_forming and len(frame) > 1:
            frame = frame.iloc[:-1]
        return frame.tail(count).reset_index(drop=True)


class YahooFeed(_CachedFeed):
    """مصدر Yahoo Finance — بلا مفتاح ولا تسجيل.

    الرمز الافتراضي ``XAUUSD=X`` (الذهب الفوري). يمكن استخدام ``GC=F``
    (العقود الآجلة) لكن سعرها يختلف عن الفوري ببضعة دولارات (Basis).
    """

    BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

    INTERVALS = {"M5": ("5m", "1mo"), "M15": ("15m", "1mo"), "M30": ("30m", "1mo"),
                 "H1": ("1h", "3mo"), "H4": ("1h", "6mo"), "D1": ("1d", "2y")}

    CORRELATION_SYMBOLS = {
        "DXY": "DX-Y.NYB",
        "US10Y": "^TNX",
        "VIX": "^VIX",
        "SPX500": "^GSPC",
        "XAGUSD": "XAGUSD=X",
    }

    def __init__(self, symbol: str = "XAUUSD=X", ttl: dict[str, int] | None = None,
                 correlation_ttl: int = 900) -> None:
        super().__init__(ttl)
        self.symbol = symbol
        self.correlation_ttl = correlation_ttl

    def _fetch_chart(self, symbol: str, interval: str, range_: str) -> pd.DataFrame:
        payload = _http_get_json(
            self.BASE + urllib.parse.quote(symbol),
            {"interval": interval, "range": range_, "includePrePost": "false"},
        )
        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise LiveFeedError(f"Yahoo رفض {symbol}: {chart['error']}")

        results = chart.get("result") or []
        if not results:
            raise LiveFeedError(f"لا نتائج من Yahoo للرمز {symbol}")

        result = results[0]
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not stamps or not quote.get("close"):
            raise LiveFeedError(f"استجابة Yahoo بلا شموع للرمز {symbol}")

        frame = pd.DataFrame(
            {
                "time": [datetime.fromtimestamp(s, tz=timezone.utc) for s in stamps],
                "open": quote.get("open"),
                "high": quote.get("high"),
                "low": quote.get("low"),
                "close": quote.get("close"),
                "volume": quote.get("volume") or [0] * len(stamps),
            }
        )
        frame["volume"] = frame["volume"].fillna(0.0)
        return frame[REQUIRED_COLUMNS]

    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame:
        timeframe = timeframe.upper()
        cached = self._cached_bars(timeframe)
        if cached is not None and len(cached) >= min(count, len(cached)):
            return cached.tail(count).reset_index(drop=True)

        if timeframe not in self.INTERVALS:
            raise LiveFeedError(f"إطار زمني غير مدعوم لدى Yahoo: {timeframe}")

        interval, range_ = self.INTERVALS[timeframe]
        frame = self._fetch_chart(self.symbol, interval, range_)

        # Yahoo لا يوفّر H4 — نُجمّعه من H1 (أربع شمعات لكل واحدة)
        if timeframe == "H4":
            frame = resample(frame, "4h")

        frame = self._finalize(frame, count)
        validate_frame(frame, min_bars=min(count, 60))
        self._store_bars(timeframe, frame)
        return frame

    def get_correlation_data(self) -> dict[str, dict[str, Any]]:
        if self._corr_cache and time.time() - self._corr_cache[0] < self.correlation_ttl:
            return self._corr_cache[1]

        out: dict[str, dict[str, Any]] = {}
        for key, symbol in self.CORRELATION_SYMBOLS.items():
            try:
                frame = self._fetch_chart(symbol, "1h", "5d")
            except LiveFeedError as exc:
                log.warning("تعذّر جلب أصل الارتباط %s: %s", key, exc)
                continue
            if len(frame) < 2:
                continue
            price = float(frame["close"].iloc[-1])
            # نسبة التغير خلال آخر ~24 ساعة تداول
            reference = float(frame["close"].iloc[max(0, len(frame) - 25)])
            change = ((price - reference) / reference * 100) if reference else 0.0
            out[key] = {
                "price": price,
                "change_pct": change,
                "trend": "up" if change > 0.05 else "down" if change < -0.05 else "neutral",
            }

        if out:
            self._corr_cache = (time.time(), out)
        return out


class TwelveDataFeed(_CachedFeed):
    """مصدر Twelve Data — يتطلب مفتاحاً مجانياً من twelvedata.com.

    أدق من Yahoo لرمز XAU/USD وأوضح شروطاً، لكن الطبقة المجانية محدودة
    (~800 طلب/يوم و8 طلبات/دقيقة) — لذلك الذاكرة المؤقتة إلزامية هنا.
    """

    BASE = "https://api.twelvedata.com/time_series"

    INTERVALS = {"M5": "5min", "M15": "15min", "M30": "30min",
                 "H1": "1h", "H4": "4h", "D1": "1day"}

    CORRELATION_SYMBOLS = {"DXY": "DXY", "US10Y": "TNX", "VIX": "VIX",
                           "SPX500": "SPX", "XAGUSD": "XAG/USD"}

    def __init__(self, api_key: str, symbol: str = "XAU/USD",
                 ttl: dict[str, int] | None = None, correlation_ttl: int = 900) -> None:
        super().__init__(ttl)
        if not api_key:
            raise LiveFeedError("مفتاح Twelve Data مفقود — اضبط GD_TWELVEDATA_KEY")
        self.api_key = api_key
        self.symbol = symbol
        self.correlation_ttl = correlation_ttl

    def _fetch_series(self, symbol: str, interval: str, size: int) -> pd.DataFrame:
        payload = _http_get_json(
            self.BASE,
            {"symbol": symbol, "interval": interval, "outputsize": min(size, 5000),
             "format": "JSON", "apikey": self.api_key},
        )
        if str(payload.get("status", "ok")).lower() == "error":
            raise LiveFeedError(f"Twelve Data: {payload.get('message', 'خطأ غير معروف')}")

        values = payload.get("values") or []
        if not values:
            raise LiveFeedError(f"لا بيانات من Twelve Data للرمز {symbol}")

        frame = pd.DataFrame(values)
        frame["time"] = pd.to_datetime(frame["datetime"], utc=True)
        for column in ("open", "high", "low", "close"):
            frame[column] = frame[column].astype(float)
        frame["volume"] = (
            frame["volume"].astype(float) if "volume" in frame.columns else 0.0
        )
        return frame[REQUIRED_COLUMNS]

    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame:
        timeframe = timeframe.upper()
        cached = self._cached_bars(timeframe)
        if cached is not None:
            return cached.tail(count).reset_index(drop=True)

        if timeframe not in self.INTERVALS:
            raise LiveFeedError(f"إطار زمني غير مدعوم: {timeframe}")

        frame = self._fetch_series(self.symbol, self.INTERVALS[timeframe], count + 5)
        frame = self._finalize(frame, count)
        validate_frame(frame, min_bars=min(count, 60))
        self._store_bars(timeframe, frame)
        return frame

    def get_correlation_data(self) -> dict[str, dict[str, Any]]:
        if self._corr_cache and time.time() - self._corr_cache[0] < self.correlation_ttl:
            return self._corr_cache[1]

        out: dict[str, dict[str, Any]] = {}
        for key, symbol in self.CORRELATION_SYMBOLS.items():
            try:
                frame = self._fetch_series(symbol, "1h", 25)
            except LiveFeedError as exc:
                log.warning("تعذّر جلب أصل الارتباط %s: %s", key, exc)
                continue
            if len(frame) < 2:
                continue
            price = float(frame["close"].iloc[-1])
            reference = float(frame["close"].iloc[0])
            change = ((price - reference) / reference * 100) if reference else 0.0
            out[key] = {
                "price": price,
                "change_pct": change,
                "trend": "up" if change > 0.05 else "down" if change < -0.05 else "neutral",
            }

        if out:
            self._corr_cache = (time.time(), out)
        return out


def resample(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    """تجميع شموع إلى إطار أعلى (مثلاً H1 → H4)."""
    indexed = frame.set_index(pd.DatetimeIndex(frame["time"]))
    aggregated = indexed.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    aggregated = aggregated.reset_index().rename(columns={"index": "time"})
    aggregated.columns = [str(c).lower() for c in aggregated.columns]
    return aggregated[REQUIRED_COLUMNS]
