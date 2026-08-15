"""محوّل MetaApi.cloud — تشغيل حساب MT5 تجريبي/حقيقي من أي حاوية Linux.

MetaApi خدمة تستضيف طرفية MT4/MT5 في السحابة وتفتح عليها REST/WebSocket،
فتحل العقبة الأساسية: مكتبة MetaTrader5 ويندوزية ولا تعمل على Railway.

البنية هنا ثلاث طبقات:
- ``MetaApiClient``  — النقل: مصادقة، مضيفات المناطق، إعادة المحاولة، الأخطاء
- ``MetaApiBroker``  — تنفيذ واجهة ``Broker`` (أوامر، مراكز، رصيد، إغلاق)
- ``MetaApiFeed``    — تنفيذ واجهة ``DataFeed`` (شموع تاريخية + أصول الارتباط)

⚠️ تحذير صريح ومهم: أشكال هذه النهايات (endpoints) مكتوبة من توثيق MetaApi،
ولم يجرِ التحقق منها مقابل الخدمة الحية أثناء كتابة هذا الملف (بيئة البناء
بلا وصول شبكي إليها). لذلك:

1. شغّل ``python -m src.main metaapi --check`` **قبل** أي تشغيل حقيقي.
   يفحص الاتصال والحساب ومواصفات الرمز والسعر اللحظي ويطبع ما تلقّاه.
2. كل خطأ في الاستجابة يرفع ``MetaApiError`` برسالة تحوي الرد الخام —
   لا تخمين ولا قيم افتراضية صامتة في مسار التنفيذ.
3. ابدأ على حساب **تجريبي** حصراً حتى تتأكد أن الأرقام تطابق منصتك.

التوثيق: https://metaapi.cloud/docs/client/restApi/
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .broker import Broker, BrokerError, BrokerPosition, Side, SymbolSpec
from .data_feed import REQUIRED_COLUMNS, DataFeed, validate_frame
from .logger import get_logger

log = get_logger(__name__)

HTTP_TIMEOUT = 30

# مضيفات MetaApi حسب المنطقة (region) المختارة عند إنشاء الحساب
CLIENT_HOST = "https://mt-client-api-v1.{region}.agiliumtrade.ai"
MARKET_DATA_HOST = "https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai"
PROVISIONING_HOST = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"

# إطاراتنا الزمنية ← ترميز MetaApi
TIMEFRAMES = {"M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h", "D1": "1d"}
TIMEFRAME_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


class MetaApiError(BrokerError):
    """فشل في التعامل مع MetaApi — يحمل الرد الخام لتسهيل التشخيص."""


class MetaApiClient:
    """طبقة النقل. مبنية على المكتبة القياسية فقط (لا تبعية إضافية)."""

    def __init__(self, token: str, account_id: str, region: str = "new-york",
                 timeout: int = HTTP_TIMEOUT) -> None:
        if not token or not account_id:
            raise MetaApiError(
                "بيانات MetaApi ناقصة — اضبط GD_METAAPI_TOKEN و GD_METAAPI_ACCOUNT_ID"
            )
        self.token = token
        self.account_id = account_id
        self.region = region
        self.timeout = timeout
        self.client_host = CLIENT_HOST.format(region=region)
        self.market_host = MARKET_DATA_HOST.format(region=region)

    # النقل ------------------------------------------------------------------
    def request(self, method: str, path: str, host: str | None = None,
                params: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None,
                retries: int = 2) -> Any:
        url = (host or self.client_host) + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"auth-token": self.token, "Accept": "application/json"}
        if data:
            headers["Content-Type"] = "application/json"

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            request = urllib.request.Request(url, data=data, headers=headers,
                                             method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                last_error = MetaApiError(
                    f"{method} {path} → HTTP {exc.code}: {detail}"
                )
                # 4xx خطأ دائم (عدا الاختناق) — إعادة المحاولة لن تنفع
                if exc.code != 429 and 400 <= exc.code < 500:
                    raise last_error from exc
            except Exception as exc:
                last_error = MetaApiError(f"{method} {path} فشل: {exc}")
            if attempt < retries:
                time.sleep(2 ** attempt)

        raise last_error or MetaApiError(f"{method} {path} فشل بلا تفصيل")

    # نهايات جاهزة -----------------------------------------------------------
    def _account_path(self, suffix: str = "") -> str:
        return f"/users/current/accounts/{self.account_id}{suffix}"

    def account_information(self) -> dict[str, Any]:
        return self.request("GET", self._account_path("/account-information"))

    def positions(self) -> list[dict[str, Any]]:
        return self.request("GET", self._account_path("/positions")) or []

    def symbol_specification(self, symbol: str) -> dict[str, Any]:
        return self.request(
            "GET", self._account_path(f"/symbols/{urllib.parse.quote(symbol)}/specification")
        )

    def current_price(self, symbol: str) -> dict[str, Any]:
        return self.request(
            "GET", self._account_path(f"/symbols/{urllib.parse.quote(symbol)}/current-price"),
            params={"keepSubscription": "true"},
        )

    def trade(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", self._account_path("/trade"), body=payload)

    def deals_by_position(self, position_id: str) -> list[dict[str, Any]]:
        response = self.request(
            "GET", self._account_path(f"/history-deals/position/{position_id}")
        )
        return response if isinstance(response, list) else response.get("deals", [])

    def candles(self, symbol: str, timeframe: str, limit: int,
                start_time: datetime | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": min(limit, 1000)}
        if start_time:
            params["startTime"] = start_time.astimezone(timezone.utc).isoformat()
        path = self._account_path(
            f"/historical-market-data/symbols/{urllib.parse.quote(symbol)}"
            f"/timeframes/{timeframe}/candles"
        )
        response = self.request("GET", path, host=self.market_host, params=params)
        return response if isinstance(response, list) else response.get("candles", [])

    def account_state(self) -> dict[str, Any]:
        """حالة النشر والاتصال من واجهة التزويد (provisioning)."""
        return self.request("GET", self._account_path(), host=PROVISIONING_HOST)


class MetaApiBroker(Broker):
    """تنفيذ الأوامر عبر MetaApi. يطابق واجهة ``Broker`` تماماً."""

    def __init__(self, spec: SymbolSpec, client: MetaApiClient,
                 magic: int = 234000, slippage_points: int = 20) -> None:
        self.spec = spec
        self.client = client
        self.magic = magic
        self.slippage_points = slippage_points
        self._connected = False

    # دورة الحياة ------------------------------------------------------------
    def connect(self) -> None:
        state = self.client.account_state()
        deployed = str(state.get("state", "")).upper()
        connection = str(state.get("connectionStatus", "")).upper()
        if deployed != "DEPLOYED":
            raise MetaApiError(
                f"حساب MetaApi ليس منشوراً (state={deployed or 'غير معروف'}) — "
                "انشره من لوحة MetaApi أولاً"
            )
        if connection not in {"CONNECTED", ""}:
            log.warning("حالة اتصال MetaApi بالوسيط: %s", connection)

        info = self.client.account_information()
        self._apply_symbol_spec()
        self._connected = True
        log.info(
            "MetaApi متصل | %s | نوع الحساب: %s | الرصيد %.2f %s",
            self.spec.name, info.get("type", "?"), float(info.get("balance", 0)),
            info.get("currency", ""),
        )

    def _apply_symbol_spec(self) -> None:
        """مواصفات الرمز من الوسيط تتقدم دائماً على ملف الإعدادات."""
        try:
            spec = self.client.symbol_specification(self.spec.name)
        except MetaApiError as exc:
            log.warning("تعذّرت قراءة مواصفات %s (%s) — سنستخدم قيم الإعدادات",
                        self.spec.name, exc)
            return

        mapping = {
            "digits": "digits", "contract_size": "contractSize",
            "min_lot": "minVolume", "max_lot": "maxVolume", "lot_step": "volumeStep",
        }
        for attribute, key in mapping.items():
            value = spec.get(key)
            if value is not None:
                setattr(self.spec, attribute, type(getattr(self.spec, attribute))(value))
        log.info("مواصفات %s من الوسيط: contract_size=%s min_lot=%s step=%s digits=%s",
                 self.spec.name, self.spec.contract_size, self.spec.min_lot,
                 self.spec.lot_step, self.spec.digits)

    def shutdown(self) -> None:
        self._connected = False

    # الحساب -----------------------------------------------------------------
    def balance(self) -> float:
        return float(self.client.account_information().get("balance", 0.0))

    def equity(self) -> float:
        return float(self.client.account_information().get("equity", 0.0))

    def tick(self) -> tuple[float, float]:
        price = self.client.current_price(self.spec.name)
        bid, ask = price.get("bid"), price.get("ask")
        if bid is None or ask is None:
            raise MetaApiError(f"سعر غير مكتمل من MetaApi: {price}")
        return float(bid), float(ask)

    # الأوامر ----------------------------------------------------------------
    def market_order(self, direction: Side, volume: float, sl: float, tp: float,
                     comment: str = "") -> int:
        volume = self.spec.normalize_lot(volume)
        if volume <= 0:
            raise MetaApiError("حجم غير صالح بعد التقريب")

        payload = {
            "actionType": "ORDER_TYPE_BUY" if direction == "buy" else "ORDER_TYPE_SELL",
            "symbol": self.spec.name,
            "volume": volume,
            "stopLoss": self.spec.normalize_price(sl),
            "takeProfit": self.spec.normalize_price(tp),
            "magic": self.magic,
            "slippage": self.slippage_points,
            "comment": comment[:31],
        }
        result = self.client.trade(payload)
        if str(result.get("stringCode", "")).upper() not in {"TRADE_RETCODE_DONE", "OK", ""}:
            raise MetaApiError(f"رفض الأمر: {result}")

        ticket = result.get("positionId") or result.get("orderId")
        if ticket is None:
            raise MetaApiError(f"استجابة تنفيذ بلا معرّف مركز: {result}")
        return int(ticket)

    def modify(self, ticket: int, sl: float | None = None,
               tp: float | None = None) -> bool:
        current = next((p for p in self.positions() if p.ticket == ticket), None)
        if current is None:
            return False

        payload = {
            "actionType": "POSITION_MODIFY",
            "positionId": str(ticket),
            "stopLoss": self.spec.normalize_price(sl if sl is not None else current.sl),
            "takeProfit": self.spec.normalize_price(tp if tp is not None else current.tp),
        }
        try:
            self.client.trade(payload)
            return True
        except MetaApiError as exc:
            log.error("فشل تعديل المركز #%s: %s", ticket, exc)
            return False

    def close(self, ticket: int, volume: float | None = None, reason: str = "") -> float:
        position = next((p for p in self.positions() if p.ticket == ticket), None)
        if position is None:
            return 0.0

        before = position.profit
        if volume is None or volume >= position.volume:
            payload = {"actionType": "POSITION_CLOSE_ID", "positionId": str(ticket),
                       "comment": f"close:{reason}"[:31]}
        else:
            payload = {"actionType": "POSITION_PARTIAL", "positionId": str(ticket),
                       "volume": self.spec.normalize_lot(volume),
                       "comment": f"partial:{reason}"[:31]}

        result = self.client.trade(payload)
        if str(result.get("stringCode", "")).upper() not in {"TRADE_RETCODE_DONE", "OK", ""}:
            raise MetaApiError(f"فشل الإغلاق #{ticket}: {result}")

        realized = self.realized_pnl(ticket)
        return realized["pnl"] if realized else before

    def positions(self) -> list[BrokerPosition]:
        out: list[BrokerPosition] = []
        for raw in self.client.positions():
            if raw.get("symbol") != self.spec.name:
                continue
            if self.magic and int(raw.get("magic", 0)) not in {0, self.magic}:
                continue  # لا نلمس صفقات لم يفتحها هذا البوت
            out.append(
                BrokerPosition(
                    ticket=int(raw["id"]),
                    symbol=raw["symbol"],
                    direction="buy" if raw.get("type") == "POSITION_TYPE_BUY" else "sell",
                    volume=float(raw.get("volume", 0)),
                    entry=float(raw.get("openPrice", 0)),
                    sl=float(raw.get("stopLoss") or 0),
                    tp=float(raw.get("takeProfit") or 0),
                    opened_at=_parse_time(raw.get("time")),
                    comment=str(raw.get("comment", "")),
                    profit=float(raw.get("profit", 0)),
                    meta={"unrealizedProfit": raw.get("unrealizedProfit")},
                )
            )
        return out

    def realized_pnl(self, ticket: int) -> dict[str, Any] | None:
        try:
            deals = self.client.deals_by_position(str(ticket))
        except MetaApiError as exc:
            log.warning("تعذّرت قراءة صفقات المركز #%s: %s", ticket, exc)
            return None
        if not deals:
            return None

        closing = [d for d in deals if str(d.get("entryType", "")).upper() != "DEAL_ENTRY_IN"]
        if not closing:
            return None

        total = sum(
            float(d.get("profit", 0)) + float(d.get("swap", 0))
            + float(d.get("commission", 0))
            for d in closing
        )
        return {"pnl": total, "exit": float(closing[-1].get("price", 0)),
                "reason": "broker_close"}


class MetaApiFeed(DataFeed):
    """شموع من نفس الحساب الذي ننفّذ عليه — لا فجوة بين التحليل والتنفيذ."""

    def __init__(self, client: MetaApiClient, symbol: str = "XAUUSD",
                 correlation_symbols: dict[str, str] | None = None,
                 correlation_ttl: int = 900) -> None:
        self.client = client
        self.symbol = symbol
        self.correlation_symbols = correlation_symbols or {}
        self.correlation_ttl = correlation_ttl
        self._corr_cache: tuple[float, dict[str, dict[str, Any]]] | None = None

    def _to_frame(self, candles: list[dict[str, Any]]) -> pd.DataFrame:
        if not candles:
            raise MetaApiError("MetaApi أعاد قائمة شموع فارغة")
        frame = pd.DataFrame(candles)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        if "tickVolume" in frame.columns:
            frame["volume"] = frame["tickVolume"].astype(float)
        elif "volume" not in frame.columns:
            frame["volume"] = 0.0
        for column in ("open", "high", "low", "close"):
            frame[column] = frame[column].astype(float)
        return frame[REQUIRED_COLUMNS].sort_values("time").reset_index(drop=True)

    def get_bars(self, timeframe: str, count: int) -> pd.DataFrame:
        timeframe = timeframe.upper()
        if timeframe not in TIMEFRAMES:
            raise MetaApiError(f"إطار زمني غير مدعوم: {timeframe}")

        # MetaApi يعيد الشموع حتى startTime للخلف — نطلب من الآن
        candles = self.client.candles(self.symbol, TIMEFRAMES[timeframe], count)
        frame = self._to_frame(candles)

        # إسقاط الشمعة الجارية: قرار مبني على شمعة تتغيّر قرار متبدّل
        minutes = TIMEFRAME_MINUTES[timeframe]
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        closed = frame[frame["time"] <= cutoff]
        if len(closed) >= 50:
            frame = closed

        frame = frame.tail(count).reset_index(drop=True)
        return validate_frame(frame, min_bars=min(count, 60))

    def get_correlation_data(self) -> dict[str, dict[str, Any]]:
        if self._corr_cache and time.time() - self._corr_cache[0] < self.correlation_ttl:
            return self._corr_cache[1]

        out: dict[str, dict[str, Any]] = {}
        for key, symbol in self.correlation_symbols.items():
            try:
                frame = self._to_frame(self.client.candles(symbol, "1h", 25))
            except Exception as exc:
                log.warning("تعذّر جلب أصل الارتباط %s (%s) — قد لا يوفره وسيطك",
                            key, exc)
                continue
            if len(frame) < 2:
                continue
            price = float(frame["close"].iloc[-1])
            reference = float(frame["close"].iloc[0])
            change = ((price - reference) / reference * 100) if reference else 0.0
            out[key] = {
                "price": price, "change_pct": change,
                "trend": "up" if change > 0.05 else "down" if change < -0.05 else "neutral",
            }

        if out:
            self._corr_cache = (time.time(), out)
        return out


def _parse_time(value: Any) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def build_client(config: Any) -> MetaApiClient:
    return MetaApiClient(
        token=str(config.secret("GD_METAAPI_TOKEN", required=True)),
        account_id=str(config.secret("GD_METAAPI_ACCOUNT_ID", required=True)),
        region=str(config.get("broker.metaapi_region", "new-york")),
    )
