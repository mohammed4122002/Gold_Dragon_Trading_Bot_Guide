"""نقطة الدخول — واجهة سطر الأوامر.

    python -m src.main doctor                  # فحص الإعدادات والبيئة
    python -m src.main analyze                 # تحليل واحد وطباعة التقرير
    python -m src.main run                     # تشغيل الحلقة (dry-run افتراضياً)
    python -m src.main run --live              # تشغيل حقيقي — يتطلب تأكيداً
    python -m src.main grid --once             # دورة واحدة من شبكة Buy Stop/Sell Stop
    python -m src.main grid                    # تشغيل حلقة الشبكة (dry-run افتراضياً)
    python -m src.main backtest --bars 3000
    python -m src.main psych --state calm
    python -m src.main kill --reset
    python -m src.main report
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .bot_engine import GoldDragonBot
from .config_loader import ConfigError, load_config
from .grid_engine import GridHedgeBot
from .infra.broker import Broker, MT5Broker, PaperBroker, SymbolSpec
from .infra.data_feed import DataFeedError, build_feed
from .infra.data_logger import DataLogger
from .infra.logger import get_logger, setup_logging
from .infra.notification_service import NotificationService
from .kill_switch import KillSwitch
from .market_analyzer import MarketAnalyzer
from .risk_manager import RiskManager
from .strategies import PsychologyGate

log = get_logger(__name__)

BANNER = r"""
   ____       _     _   ____
  / ___| ___ | | __| | |  _ \ _ __ __ _  __ _  ___  _ __
 | |  _ / _ \| |/ _` | | | | | '__/ _` |/ _` |/ _ \| '_ \
 | |_| | (_) | | (_| | | |_| | | | (_| | (_| | (_) | | | |
  \____|\___/|_|\__,_| |____/|_|  \__,_|\__, |\___/|_| |_|
                                        |___/  XAU/USD Bot
"""

DISCLAIMER = (
    "⚠️  أداة تعليمية/تقنية. التداول الآلي قد يفقدك رأس مالك بالكامل.\n"
    "    لا تشغّل على حساب حقيقي قبل 3 أشهر Demo واختبار Kill Switch يدوياً."
)


def preflight_storage(config: Any) -> list[str]:
    """التأكد من أن مسارات الحالة قابلة للكتابة قبل بدء التداول.

    على منصات الحاويات، قرص دائم غير قابل للكتابة يعني أن قفل الـ48 ساعة
    وقاعدة الصفقات ستُفقد مع كل إعادة نشر. الفشل هنا يجب أن يكون صريحاً
    ومبكراً، لا أن يُكتشف بعد ثلاث خسارات متتالية.
    """
    problems: list[str] = []
    targets = {
        "قاعدة البيانات": config.path("storage.database", "data/trades.db"),
        "ملف السجل": config.path("storage.log_file", "logs/bot.log"),
        "حالة Kill Switch": config.path("kill_switch.state_file", "state/kill_switch.json"),
        "الحالة النفسية": config.path(
            "gold_dragon.psychology_gate.state_file", "state/psychology.json"
        ),
    }
    for label, path in targets.items():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            probe = path.parent / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"{label}: {path.parent} غير قابل للكتابة ({exc})")
    return problems


def build_symbol_spec(config: Any) -> SymbolSpec:
    symbol = config.symbol
    return SymbolSpec(
        name=symbol,
        digits=int(config.get(f"symbols.{symbol}.digits", 2)),
        contract_size=float(config.get("broker.contract_size", 100)),
        min_lot=float(config.get("broker.min_lot", 0.01)),
        max_lot=float(config.get("broker.max_lot", 10.0)),
        lot_step=float(config.get("broker.lot_step", 0.01)),
        point=float(config.get("broker.point", 0.01)),
    )


def build_broker(config: Any, force_paper: bool = False) -> Broker:
    spec = build_symbol_spec(config)
    provider = str(config.get("broker.provider", "auto"))

    if force_paper or provider == "paper" or (provider == "auto" and config.dry_run):
        return PaperBroker(
            spec,
            balance=float(config.get("broker.paper_balance", 1000.0)),
            spread=float(config.get("broker.paper_spread", 0.20)),
        )

    if provider == "metaapi":
        from .infra.metaapi import MetaApiBroker, build_client
        return MetaApiBroker(
            spec, build_client(config),
            magic=int(config.get("broker.magic", 234000)),
            slippage_points=int(config.get("broker.slippage_points", 20)),
        )

    return MT5Broker(
        spec,
        login=int(config.secret("GD_MT5_LOGIN", required=True) or 0),
        password=str(config.secret("GD_MT5_PASSWORD", required=True)),
        server=str(config.secret("GD_MT5_SERVER", required=True)),
        terminal_path=str(config.get("broker.mt5_terminal_path", "")),
        magic=int(config.get("broker.magic", 234000)),
        deviation=int(config.get("broker.slippage_points", 20)),
    )


# ═══════════════════════════════════════════════════════════════════
def cmd_doctor(config: Any, args: argparse.Namespace) -> int:
    print(BANNER)
    checks: list[tuple[str, bool, str]] = []
    # تحذيرات لا تمنع التشغيل لكنها تُعرض بوضوح
    warnings: list[tuple[str, bool, str]] = []

    checks.append(("الإعدادات محمّلة", True, f"الرمز {config.symbol}"))
    checks.append((
        "وضع التشغيل",
        True,
        "dry-run (آمن)" if config.dry_run else "🔴 حقيقي — تأكد من الإعدادات",
    ))

    feed = build_feed(config)
    try:
        bars = feed.get_bars("M15", 120)
        checks.append(("مصدر البيانات", True,
                       f"{type(feed).__name__} — {len(bars)} شمعة، آخر سعر "
                       f"{bars['close'].iloc[-1]:.2f}"))
    except DataFeedError as exc:
        checks.append(("مصدر البيانات", False, str(exc)))

    from .strategies import NewsFilter
    news_status = NewsFilter(config).check()
    calendar_state = news_status.get("calendar", "?")
    warnings.append(
        ("تقويم الأخبار", calendar_state == "ok",
         {"ok": "محدّث", "missing": "غير موجود — انسخ config/news_calendar.example.json",
          "stale": "قديم — كل أحداثه في الماضي",
          "disabled": "الفلتر معطّل"}.get(calendar_state, calendar_state))
    )

    storage_problems = preflight_storage(config)
    checks.append(("التخزين الدائم", not storage_problems,
                   " | ".join(storage_problems) if storage_problems
                   else f"قابل للكتابة: {config.path('storage.database').parent}"))

    notifier = NotificationService(config)
    checks.append(("الإشعارات", True, f"المزوّد: {notifier.provider}"))

    psychology = PsychologyGate(config).check()
    checks.append(("البوابة النفسية", not psychology["blocked"], psychology["reason"]))

    risk = RiskManager(config, build_symbol_spec(config))
    snapshot = risk.snapshot(1000.0)
    checks.append(("مدير المخاطرة", not snapshot["locked"],
                   f"مخاطرة {risk.risk_per_trade:.1%}/صفقة، "
                   f"قفل: {snapshot['lock_reason'] or 'لا يوجد'}"))

    kill = KillSwitch(config, build_broker(config, force_paper=True), notifier)
    checks.append(("Kill Switch", not kill.active,
                   kill.reason or "غير مفعّل"))

    # مثال حساب حجم — يكشف أخطاء مواصفات العقد مبكراً
    sizing = risk.calculate_position_size(1000.0, 2400.0, 2395.0)
    checks.append(("حساب حجم المركز", sizing["lots"] > 0,
                   f"رصيد 1000$ ووقف 5$ → {sizing['lots']} لوت "
                   f"(مخاطرة {sizing['risk_amount']:.2f}$)"))

    print()
    for name, ok, detail in checks:
        print(f"  {'✅' if ok else '❌'} {name:<22} {detail}")
    for name, ok, detail in warnings:
        print(f"  {'✅' if ok else '⚠️ '} {name:<22} {detail}")
    print(f"\n{DISCLAIMER}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def cmd_analyze(config: Any, args: argparse.Namespace) -> int:
    analyzer = MarketAnalyzer(config, build_feed(config))
    result = analyzer.analyze()
    if result is None:
        print("❌ تعذّر جلب بيانات صالحة للتحليل")
        return 1

    print(analyzer.format_report(result, analyzer.last_context))
    if args.json:
        import json
        print("\n" + json.dumps(result.to_dict(), ensure_ascii=False,
                                indent=2, default=str))
    return 0


def cmd_run(config: Any, args: argparse.Namespace) -> int:
    print(BANNER)
    print(DISCLAIMER)

    if args.live:
        if config.dry_run:
            print("\n⚠️  --live يتطلب bot.dry_run: false في settings.yaml")
            return 2
        confirm = input("\n🔴 تشغيل على حساب حقيقي. اكتب LIVE للتأكيد: ").strip()
        if confirm != "LIVE":
            print("أُلغي التشغيل.")
            return 3
    elif not config.dry_run:
        print("\n⚠️  الإعدادات على وضع حقيقي — استخدم --live للتأكيد الصريح.")
        return 2

    problems = preflight_storage(config)
    if problems:
        print("\n❌ التخزين غير قابل للكتابة — أوقفت التشغيل:")
        for problem in problems:
            print(f"   • {problem}")
        print("\n   على Railway: تأكد من تركيب Volume على /data ومن أن "
              "متغيرات GD_STORAGE__* تشير إليه.")
        return 4

    broker = build_broker(config, force_paper=not args.live)
    feed = build_feed(config)
    bot = GoldDragonBot(config, broker, feed)

    if args.once:
        outcome = bot.tick()
        print(f"\nالنتيجة: {outcome['status']} — {outcome['detail']}")
        return 0

    bot.start()
    return 0


def cmd_grid(config: Any, args: argparse.Namespace) -> int:
    """شبكة تحوّط Buy Stop/Sell Stop — استراتيجية منفصلة، راجع config/grid.yaml."""
    print(BANNER)
    print(DISCLAIMER)
    print(
        "\n🕸️  شبكة تحوّط: تربح من التذبذب العرضي وتخسر بعنف في اختراق اتجاهي بلا فلتر.\n"
        "    اقرأ config/grid.yaml قبل التشغيل الحي — كل حد حماية فيه إلزامي."
    )

    if args.reset_kill:
        notifier = NotificationService(config)
        kill = KillSwitch(
            config, build_broker(config, force_paper=True), notifier,
            state_file=config.path("grid.kill_switch_state_file", "state/grid_kill_switch.json"),
        )
        kill.reset(args.note or "reset from CLI")
        print("✅ أُعيد تعيين Kill Switch الخاص بالشبكة")
        return 0

    if not config.get("grid.enabled", False):
        print("\n⚠️  grid.enabled: false في config/grid.yaml — فعّله أولاً قبل التشغيل")
        return 2

    if args.live:
        if config.dry_run:
            print("\n⚠️  --live يتطلب bot.dry_run: false في settings.yaml")
            return 2
        confirm = input("\n🔴 تشغيل شبكة التحوّط على حساب حقيقي. اكتب LIVE للتأكيد: ").strip()
        if confirm != "LIVE":
            print("أُلغي التشغيل.")
            return 3
    elif not config.dry_run:
        print("\n⚠️  الإعدادات على وضع حقيقي — استخدم --live للتأكيد الصريح.")
        return 2

    problems = preflight_storage(config)
    if problems:
        print("\n❌ التخزين غير قابل للكتابة — أوقفت التشغيل:")
        for problem in problems:
            print(f"   • {problem}")
        return 4

    broker = build_broker(config, force_paper=not args.live)
    feed = build_feed(config)
    bot = GridHedgeBot(config, broker, feed)

    if args.once:
        outcome = bot.tick()
        print(f"\nالنتيجة: {outcome['status']} — {outcome['detail']}")
        return 0

    bot.start()
    return 0


def cmd_backtest(config: Any, args: argparse.Namespace) -> int:
    from .backtest import Backtester

    feed = build_feed(config)
    try:
        df_m15 = feed.get_bars("M15", args.bars)
        df_h1 = feed.get_bars("H1", max(args.bars // 4, 400))
    except DataFeedError as exc:
        print(f"❌ {exc}")
        return 1

    tester = Backtester(config, balance=args.balance)
    result = tester.run(df_m15, df_h1, warmup=args.warmup)
    print(result.format())

    if str(config.get("bot.feed")) == "synthetic":
        print("\n⚠️  البيانات اصطناعية — النتائج بلا أي دلالة على الأداء الحقيقي.")
    return 0


def cmd_psych(config: Any, args: argparse.Namespace) -> int:
    gate = PsychologyGate(config)
    if args.state:
        gate.set_state(args.state, args.note or "")
    status = gate.check()
    print(f"الحالة: {status['state']} | الدرجة: {status['score']} | "
          f"{'⛔ محظور' if status['blocked'] else '✅ مسموح'}")
    print(status["reason"])
    return 0


def cmd_kill(config: Any, args: argparse.Namespace) -> int:
    notifier = NotificationService(config)
    kill = KillSwitch(config, build_broker(config, force_paper=True), notifier)

    if args.reset:
        kill.reset(args.note or "reset from CLI")
        print("✅ أُعيد تعيين Kill Switch")
    elif args.trigger:
        kill.trigger(args.trigger, level=args.level)
        print(f"🚨 فُعّل Kill Switch ({args.level})")
    else:
        print(f"الحالة: {'نشط' if kill.active else 'غير نشط'}")
        if kill.active:
            print(f"المستوى: {kill.state.get('level')} | السبب: {kill.reason}")
    return 0


def cmd_metaapi(config: Any, args: argparse.Namespace) -> int:
    """فحص اتصال MetaApi قبل أي تشغيل حقيقي.

    الغرض: كشف أي اختلاف بين ما يتوقعه الكود وما تعيده الخدمة فعلاً،
    قبل أن يُكتشف الاختلاف بأمر تنفيذ فاشل أو حجم مركز خاطئ.
    """
    from .infra.metaapi import MetaApiFeed, build_client

    print(BANNER)
    client = build_client(config)
    symbol = config.symbol
    failures = 0

    def step(label: str, action: Any) -> Any:
        nonlocal failures
        try:
            value = action()
        except Exception as exc:
            failures += 1
            print(f"  ❌ {label}\n     {exc}")
            return None
        print(f"  ✅ {label}")
        return value

    print(f"\nالمنطقة: {client.region} | الحساب: {client.account_id[:8]}…\n")

    state = step("حالة الحساب (provisioning)", client.account_state)
    if state:
        print(f"     state={state.get('state')} | "
              f"connection={state.get('connectionStatus')} | "
              f"platform={state.get('platform')}")

    info = step("بيانات الحساب", client.account_information)
    if info:
        print(f"     نوع={info.get('type')} | رصيد={info.get('balance')} "
              f"{info.get('currency')} | رافعة={info.get('leverage')} | "
              f"وسيط={info.get('broker')}")
        if str(info.get("type", "")).upper().find("DEMO") < 0:
            print("     ⚠️  هذا لا يبدو حساباً تجريبياً — تأكد قبل المتابعة")

    spec = step(f"مواصفات {symbol}", lambda: client.symbol_specification(symbol))
    if spec:
        print(f"     contractSize={spec.get('contractSize')} | "
              f"minVolume={spec.get('minVolume')} | "
              f"volumeStep={spec.get('volumeStep')} | digits={spec.get('digits')}")
        expected = float(config.get("broker.contract_size", 100))
        actual = spec.get("contractSize")
        if actual is not None and float(actual) != expected:
            print(f"     ⚠️  contract_size في الإعدادات {expected} ويعطيك الوسيط "
                  f"{actual} — سيُعتمد رقم الوسيط، لكن راجع settings.yaml")

    price = step("السعر اللحظي", lambda: client.current_price(symbol))
    if price:
        bid, ask = price.get("bid"), price.get("ask")
        print(f"     bid={bid} ask={ask} | سبريد={float(ask or 0) - float(bid or 0):.2f}$")

    bars = step("الشموع التاريخية (M15)",
                lambda: MetaApiFeed(client, symbol).get_bars("M15", 120))
    if bars is not None:
        print(f"     {len(bars)} شمعة | آخر إغلاق {bars['close'].iloc[-1]:.2f} "
              f"@ {bars['time'].iloc[-1]}")

    step("المراكز المفتوحة", client.positions)

    correlation = {k: v for k, v in (config.get("metaapi_correlation") or {}).items() if v}
    if correlation:
        data = MetaApiFeed(client, symbol, correlation).get_correlation_data()
        print(f"  {'✅' if data else '⚠️ '} أصول الارتباط المتاحة: "
              f"{list(data) or 'لا شيء — البوابة ستُحيّد'}")

    if failures:
        print(f"\n❌ فشل {failures} فحص — لا تُشغّل قبل حلّها.")
        print("   راجع docs/DEPLOY_RAILWAY.md قسم MetaApi، وتحقق من التوثيق:")
        print("   https://metaapi.cloud/docs/client/restApi/")
        return 1

    print("\n✅ كل الفحوص نجحت. الخطوة التالية:")
    print("   GD_BOT__DRY_RUN=false GD_BOT__FEED=metaapi "
          "GD_BROKER__PROVIDER=metaapi python -m src.main run --live")
    print(f"\n{DISCLAIMER}")
    return 0


def cmd_report(config: Any, args: argparse.Namespace) -> int:
    logger = DataLogger(config.path("storage.database"))
    stats = logger.summary()
    print("═══ ملخص الأداء ═══")
    for key, value in stats.items():
        print(f"  {key:<16} {value:.4f}" if isinstance(value, float) else
              f"  {key:<16} {value}")

    memory = logger.performance_by_poi(
        int(config.get("gold_dragon.adaptive_memory.min_trades_for_adjustment", 20))
    )
    if memory:
        print("\n═══ الذاكرة التكيفية (حسب نوع POI) ═══")
        for poi_type, data in memory.items():
            flag = "" if data["significant"] else "  (عينة صغيرة — غير معتد بها)"
            print(f"  {poi_type:<14} صفقات {int(data['trades']):>3} | "
                  f"نجاح {data['win_rate']:.0%} | متوسط R {data['avg_r']:+.2f}{flag}")
    return 0


# ═══════════════════════════════════════════════════════════════════
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gold-dragon", description="Gold Dragon Trading Bot — XAU/USD"
    )
    parser.add_argument("--config-dir", default=None, help="مجلد ملفات الإعدادات")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="فحص الإعدادات والبيئة")

    analyze = sub.add_parser("analyze", help="تحليل واحد وطباعة التقرير")
    analyze.add_argument("--json", action="store_true", help="طباعة النتيجة الكاملة JSON")

    run = sub.add_parser("run", help="تشغيل حلقة البوت")
    run.add_argument("--live", action="store_true", help="تشغيل حقيقي (يتطلب تأكيداً)")
    run.add_argument("--once", action="store_true", help="دورة واحدة فقط ثم الخروج")

    grid = sub.add_parser("grid", help="تشغيل شبكة تحوّط Buy Stop/Sell Stop (راجع config/grid.yaml)")
    grid.add_argument("--live", action="store_true", help="تشغيل حقيقي (يتطلب تأكيداً)")
    grid.add_argument("--once", action="store_true", help="دورة واحدة فقط ثم الخروج")
    grid.add_argument("--reset-kill", action="store_true",
                      help="فكّ Kill Switch الخاص بالشبكة يدوياً")
    grid.add_argument("--note", default="")

    backtest = sub.add_parser("backtest", help="اختبار تاريخي")
    backtest.add_argument("--bars", type=int, default=3000, help="عدد شموع M15")
    backtest.add_argument("--warmup", type=int, default=250, help="شموع الإحماء")
    backtest.add_argument("--balance", type=float, default=1000.0, help="الرصيد الافتتاحي")

    psych = sub.add_parser("psych", help="إعلان/عرض الحالة النفسية")
    psych.add_argument("--state", choices=["calm", "tense", "angry", "tired", "unknown"])
    psych.add_argument("--note", default="")

    kill = sub.add_parser("kill", help="إدارة Kill Switch")
    kill.add_argument("--reset", action="store_true", help="فكّ يدوي")
    kill.add_argument("--trigger", metavar="REASON", help="تفعيل فوري بسبب محدد")
    kill.add_argument("--level", choices=["pause", "terminate"], default="pause")
    kill.add_argument("--note", default="")

    sub.add_parser("report", help="ملخص الأداء والذاكرة التكيفية")

    metaapi = sub.add_parser("metaapi", help="فحص اتصال MetaApi.cloud")
    metaapi.add_argument("--check", action="store_true", default=True,
                         help="تشغيل كل الفحوص (افتراضي)")
    return parser


COMMANDS = {
    "doctor": cmd_doctor,
    "analyze": cmd_analyze,
    "run": cmd_run,
    "grid": cmd_grid,
    "backtest": cmd_backtest,
    "psych": cmd_psych,
    "kill": cmd_kill,
    "report": cmd_report,
    "metaapi": cmd_metaapi,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config_dir)
    except ConfigError as exc:
        print(f"❌ خطأ في الإعدادات: {exc}")
        return 2

    setup_logging(config.path("storage.log_file", "logs/bot.log"),
                  str(config.get("storage.log_level", "INFO")))
    try:
        return COMMANDS[args.command](config, args)
    except KeyboardInterrupt:
        print("\nتم الإيقاف.")
        return 130
    except Exception as exc:
        log.exception("فشل تنفيذ الأمر %s", args.command)
        print(f"❌ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
