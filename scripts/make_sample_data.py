#!/usr/bin/env python3
"""توليد ملفات CSV تجريبية لتشغيل وضع ``feed: csv`` والـ Backtest.

    python scripts/make_sample_data.py --bars 5000

⚠️ البيانات المولَّدة **اصطناعية بالكامل** (مشي عشوائي). تصلح لاختبار أن
الأنابيب تعمل، ولا تصلح إطلاقاً لتقييم أداء استراتيجية. للاختبار الجاد
صدّر بيانات حقيقية من MT5 (سجل الأسعار) بنفس تنسيق الأعمدة:

    time,open,high,low,close,volume
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.infra.data_feed import SyntheticFeed  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="توليد بيانات XAUUSD تجريبية")
    parser.add_argument("--bars", type=int, default=5000, help="عدد شموع M15")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data", help="مجلد الإخراج")
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    feed = SyntheticFeed(seed=args.seed)

    plan = {"M15": args.bars, "H1": args.bars // 4, "H4": args.bars // 16}
    for timeframe, count in plan.items():
        if count < 100:
            continue
        path = out_dir / f"XAUUSD_{timeframe}.csv"
        feed.get_bars(timeframe, count).to_csv(path, index=False)
        print(f"✅ {path.relative_to(ROOT)} — {count} شمعة")

    print("\n⚠️ بيانات اصطناعية للاختبار التقني فقط — لا تستخلص منها أي حكم على الأداء.")
    print("   لتفعيلها: اضبط bot.feed: csv في config/settings.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
