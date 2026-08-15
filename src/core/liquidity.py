"""هندسة السيولة — خريطة BSL/SSL وكشف السويپ (البند 3)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .indicators import swing_highs, swing_lows
from .structure import has_displacement


def _period_levels(df: pd.DataFrame, freq: str) -> tuple[float | None, float | None]:
    """قمة/قاع الفترة السابقة (يوم/أسبوع/شهر) اعتماداً على أختام الوقت."""
    if "time" not in df.columns or df.empty:
        return None, None
    frame = df.set_index(pd.DatetimeIndex(df["time"]))
    grouped = frame.resample(freq).agg({"high": "max", "low": "min"}).dropna()
    if len(grouped) < 2:
        return None, None
    previous = grouped.iloc[-2]
    return float(previous["high"]), float(previous["low"])


def find_equal_levels(levels: list[tuple[int, float]], tolerance: float = 0.5,
                      min_touches: int = 2) -> list[dict[str, Any]]:
    """تجميع القمم/القيعان المتقاربة إلى مجمّعات سيولة (Equal Highs/Lows).

    كل مجمّع يحمل عدد اللمسات — كلما زادت، زادت السيولة المتراكمة خلفه.
    """
    if not levels:
        return []

    ordered = sorted(levels, key=lambda item: item[1])
    clusters: list[list[tuple[int, float]]] = [[ordered[0]]]
    for item in ordered[1:]:
        if abs(item[1] - clusters[-1][-1][1]) <= tolerance:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    out: list[dict[str, Any]] = []
    for cluster in clusters:
        if len(cluster) < min_touches:
            continue
        prices = [price for _, price in cluster]
        out.append(
            {
                "level": sum(prices) / len(prices),
                "touches": len(cluster),
                "last_bar": max(idx for idx, _ in cluster),
                "priority": "high" if len(cluster) >= 3 else "medium",
            }
        )
    return out


def map_liquidity(df_h1: pd.DataFrame, df_m15: pd.DataFrame, df_h4: pd.DataFrame | None = None,
                  tolerance: float = 0.5, lookback: int = 2) -> dict[str, Any]:
    """خريطة السيولة الكاملة: BSL/SSL + PDH/PDL + PWH/PWL + PMH/PML."""
    source = df_h4 if df_h4 is not None and not df_h4.empty else df_h1

    pdh, pdl = _period_levels(df_h1, "1D")
    pwh, pwl = _period_levels(source, "1W")
    pmh, pml = _period_levels(source, "1ME")

    bsl = find_equal_levels(swing_highs(df_m15, lookback), tolerance)
    ssl = find_equal_levels(swing_lows(df_m15, lookback), tolerance)

    # المستويات الزمنية سيولة خارجية (ERL) بأولوية عالية دائماً
    for level, label in ((pdh, "PDH"), (pwh, "PWH"), (pmh, "PMH")):
        if level is not None:
            bsl.append({"level": level, "touches": 1, "last_bar": len(df_m15) - 1,
                        "priority": "high", "source": label})
    for level, label in ((pdl, "PDL"), (pwl, "PWL"), (pml, "PML")):
        if level is not None:
            ssl.append({"level": level, "touches": 1, "last_bar": len(df_m15) - 1,
                        "priority": "high", "source": label})

    return {
        "bsl": sorted(bsl, key=lambda x: x["level"]),
        "ssl": sorted(ssl, key=lambda x: x["level"], reverse=True),
        "pdh": pdh, "pdl": pdl, "pwh": pwh, "pwl": pwl, "pmh": pmh, "pml": pml,
    }


def detect_sweep(df_m15: pd.DataFrame, liquidity: dict[str, Any], lookback_bars: int = 6,
                 atr_period: int = 14, displacement_multiple: float = 1.5) -> dict[str, Any]:
    """كشف سويپ السيولة: اختراق بالظل ثم إغلاق مرتد + إزاحة معاكسة.

    السويپ الحقيقي = **الظل** يتجاوز المستوى بينما **الإغلاق** يعود خلفه.
    الإغلاق خارج المستوى ليس سويپاً بل كسراً هيكلياً (BOS) — والخلط بينهما
    هو الخطأ الأكثر تكلفة في تطبيقات SMC الآلية.
    """
    if len(df_m15) < lookback_bars + 2:
        return {"type": "none", "status": "insufficient_data"}

    recent = df_m15.iloc[-lookback_bars:]
    candidates: list[dict[str, Any]] = []

    for pool in liquidity.get("bsl", []):
        level = pool["level"]
        for pos in range(len(recent)):
            candle = recent.iloc[pos]
            if float(candle["high"]) > level and float(candle["close"]) < level:
                candidates.append(
                    {"type": "BSL_Sweep", "expected_direction": "bearish",
                     "level": level, "priority": pool.get("priority", "medium"),
                     "source": pool.get("source", "EQH"),
                     "bars_ago": len(recent) - 1 - pos,
                     "penetration": float(candle["high"]) - level}
                )
                break

    for pool in liquidity.get("ssl", []):
        level = pool["level"]
        for pos in range(len(recent)):
            candle = recent.iloc[pos]
            if float(candle["low"]) < level and float(candle["close"]) > level:
                candidates.append(
                    {"type": "SSL_Sweep", "expected_direction": "bullish",
                     "level": level, "priority": pool.get("priority", "medium"),
                     "source": pool.get("source", "EQL"),
                     "bars_ago": len(recent) - 1 - pos,
                     "penetration": level - float(candle["low"])}
                )
                break

    if not candidates:
        return {"type": "none", "status": "waiting"}

    # الأحدث أولاً، ثم الأعلى أولوية
    candidates.sort(key=lambda c: (c["bars_ago"], 0 if c["priority"] == "high" else 1))
    sweep = candidates[0]

    displacement = has_displacement(
        df_m15, sweep["expected_direction"], atr_period, displacement_multiple,
        window=max(sweep["bars_ago"] + 1, 2),
    )
    sweep["displacement"] = displacement
    sweep["status"] = "confirmed" if displacement["found"] else "unconfirmed"
    sweep["classification"] = _classify(sweep, displacement)
    return sweep


def _classify(sweep: dict[str, Any], displacement: dict[str, Any]) -> str:
    """تصنيف السويپ: Stop Hunt / Classic / Inducement (البند 3.2)."""
    if not displacement["found"]:
        return "Inducement"  # سويپ بلا رد فعل — غالباً استدراج
    if sweep["priority"] == "high" and displacement["strength"] >= 2.0:
        return "Stop Hunt"
    return "Classic"


def nearest_target(liquidity: dict[str, Any], price: float, direction: str) -> float | None:
    """أقرب مجمّع سيولة في اتجاه الصفقة — هدف طبيعي للربح."""
    upward = direction in {"buy", "bullish"}
    pools = liquidity.get("bsl" if upward else "ssl", [])
    ahead = [p["level"] for p in pools
             if (p["level"] > price if upward else p["level"] < price)]
    if not ahead:
        return None
    return min(ahead) if upward else max(ahead)
