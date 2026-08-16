"""اختبارات تحميل الإعدادات والتحقق منها + Kill Switch."""

from __future__ import annotations

import pytest

from src.config_loader import Config, ConfigError, load_config
from src.infra.broker import PaperBroker, SymbolSpec
from src.kill_switch import KillSwitch


class SilentNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message, level="info"):
        self.messages.append(message)


# ═══ الإعدادات ═════════════════════════════════════════════════════
def test_real_config_loads_and_validates():
    cfg = load_config()
    assert cfg.symbol == "XAUUSD"
    assert cfg.get("risk.risk_per_trade") <= cfg.get("risk.max_risk_per_trade")


def test_dotted_access_and_defaults():
    cfg = load_config()
    assert cfg.get("gold_dragon.min_acs") is not None
    assert cfg.get("does.not.exist", "fallback") == "fallback"


def test_env_override(monkeypatch):
    monkeypatch.setenv("GD_BOT__SCAN_INTERVAL_SECONDS", "17")
    assert load_config().get("bot.scan_interval_seconds") == 17


def test_symbol_outside_allowlist_is_rejected():
    cfg = load_config()
    cfg.data["bot"]["symbol"] = "EURUSD"
    from src.config_loader import _validate
    with pytest.raises(ConfigError, match="خارج القائمة"):
        _validate(cfg)


def test_risk_above_two_percent_is_rejected():
    cfg = load_config()
    cfg.data["risk"]["max_risk_per_trade"] = 0.05
    from src.config_loader import _validate
    with pytest.raises(ConfigError, match="2%"):
        _validate(cfg)


def test_challenge_mode_requires_signed_contract():
    cfg = load_config()
    cfg.data["challenge_mode"]["enabled"] = True
    cfg.data["challenge_mode"]["accepted_contract"] = False
    from src.config_loader import _validate
    with pytest.raises(ConfigError, match="عقد التحدي"):
        _validate(cfg)


def test_secret_required_raises_when_missing(monkeypatch):
    monkeypatch.delenv("GD_MT5_PASSWORD", raising=False)
    with pytest.raises(ConfigError):
        Config().secret("GD_MT5_PASSWORD", required=True)


# ═══ Kill Switch ═══════════════════════════════════════════════════
@pytest.fixture
def kill(config, spec):
    broker = PaperBroker(spec, balance=1000.0)
    broker.set_price(2400.0)
    return KillSwitch(config, broker, SilentNotifier()), broker


def test_trigger_closes_positions_and_blocks(kill):
    switch, broker = kill
    broker.market_order("buy", 0.05, sl=2390.0, tp=2420.0)

    switch.trigger("اختبار", level="pause")
    assert switch.active
    assert broker.positions() == []


def test_state_persists_across_restart(config, spec):
    broker = PaperBroker(spec, balance=1000.0)
    broker.set_price(2400.0)
    first = KillSwitch(config, broker, SilentNotifier())
    first.trigger("drawdown", level="terminate")

    revived = KillSwitch(config, broker, SilentNotifier())
    assert revived.active and revived.state["level"] == "terminate"


def test_manual_reset_clears_state(kill):
    switch, _ = kill
    switch.trigger("اختبار", level="terminate")
    switch.reset("مراجعة يدوية")
    assert not switch.active


def test_auto_check_thresholds(kill):
    switch, _ = kill
    assert not switch.auto_check({"drawdown": 0.05, "daily_pnl_pct": -0.01,
                                  "consecutive_losses": 1})
    assert switch.auto_check({"drawdown": 0.22, "daily_pnl_pct": -0.02,
                              "consecutive_losses": 1})
    assert switch.state["level"] == "pause"


def test_terminate_level_never_expires(kill):
    switch, _ = kill
    switch.trigger("خسارة كارثية", level="terminate")
    assert switch.state["until"] is None
    assert switch.active


def test_no_double_trigger(kill):
    switch, broker = kill
    switch.trigger("أول", level="pause")
    first_reason = switch.reason
    switch.auto_check({"drawdown": 0.9, "daily_pnl_pct": -0.5,
                       "consecutive_losses": 9})
    assert switch.reason == first_reason


# ═══ الوسيط الورقي ضمن نظام الحدود ═════════════════════════════════
def test_spec_from_config_matches_yaml():
    from src.main import build_symbol_spec
    spec = build_symbol_spec(load_config())
    assert isinstance(spec, SymbolSpec)
    assert spec.contract_size == 100.0
    assert spec.min_lot == 0.01


# ═══ حراسة تجاوزات البيئة ══════════════════════════════════════════
def test_documented_env_vars_map_to_real_config_keys():
    """كل متغير موثّق في .env.railway.example يجب أن يكون له مفتاح فعلي.

    آلية التجاوز لا تُنشئ مفاتيح جديدة — تُعدّل الموجود فقط. لذلك متغير
    موثّق بلا مفتاح مقابل يُقبل صامتاً ولا يفعل شيئاً، وهو أسوأ من الخطأ
    الصريح: تظن أنك ضبطت شيئاً ولم تضبطه.
    """
    from pathlib import Path

    from src.config_loader import ROOT, load_config

    # هذه أسرار تُقرأ من البيئة مباشرة، لا مفاتيح YAML
    secrets = {
        "GD_MT5_LOGIN", "GD_MT5_PASSWORD", "GD_MT5_SERVER",
        "GD_TELEGRAM_TOKEN", "GD_TELEGRAM_CHAT_ID", "GD_TWELVEDATA_KEY",
        "GD_METAAPI_TOKEN", "GD_METAAPI_ACCOUNT_ID",
    }
    cfg = load_config()
    missing: list[str] = []

    for file in (".env.railway.example", ".env.example"):
        path = Path(ROOT) / file
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip().lstrip("# ").strip()
            if not line.startswith("GD_") or "=" not in line:
                continue
            name = line.split("=", 1)[0].strip()
            if name in secrets or "__" not in name:
                continue

            node = cfg.data
            for part in name[len("GD_"):].lower().split("__"):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    missing.append(f"{name} ({file})")
                    break

    assert not missing, f"متغيرات موثّقة بلا مفاتيح مقابلة: {missing}"


# ═══ تشغيل الاثنين معاً (run-all) ═══════════════════════════════════
def test_build_run_all_bots_shares_feed_and_data_logger(config, tmp_path):
    """أهم ضمانة في run-all: مصدر بيانات وقاعدة بيانات مشتركان، لكن وسيطان
    (وبالتالي رصيدان) مستقلان تماماً — هذا هو معنى "حسابين" هنا."""
    from src.main import build_run_all_bots

    config.data["bot"]["feed"] = "synthetic"
    config.data["grid"]["enabled"] = True
    config.data["grid"]["risk_state_file"] = str(tmp_path / "grid_risk.json")
    config.data["grid"]["kill_switch_state_file"] = str(tmp_path / "grid_kill.json")

    bot, grid_bot = build_run_all_bots(config, live=False)

    assert grid_bot is not None
    assert bot.analyzer.feed is grid_bot.feed          # مصدر بيانات مشترك
    assert bot.data_logger is grid_bot.data_logger      # قاعدة بيانات مشتركة
    assert bot.broker is not grid_bot.broker            # وسيطان مستقلان تماماً
    assert bot.broker.balance() == grid_bot.broker.balance() == pytest.approx(1000.0)
    assert bot.grid_bot is grid_bot                     # للمراقبة فقط


def test_build_run_all_bots_without_grid_enabled(config):
    from src.main import build_run_all_bots

    config.data["bot"]["feed"] = "synthetic"
    config.data["grid"]["enabled"] = False

    bot, grid_bot = build_run_all_bots(config, live=False)
    assert grid_bot is None
    assert bot.grid_bot is None


def test_preflight_storage_includes_grid_paths_when_requested(config, tmp_path):
    from src.main import preflight_storage

    config.data["grid"]["risk_state_file"] = str(tmp_path / "nested" / "grid_risk.json")
    config.data["grid"]["kill_switch_state_file"] = str(tmp_path / "nested" / "grid_kill.json")

    assert preflight_storage(config, include_grid=False) == []
    assert preflight_storage(config, include_grid=True) == []
    assert (tmp_path / "nested").exists()


# ═══ تقرير المقارنة ══════════════════════════════════════════════
def test_report_compares_both_strategies_when_data_exists(config, tmp_path, capsys):
    from datetime import datetime, timezone

    from src.infra.data_logger import DataLogger
    from src.main import cmd_report
    from src.models import Trade, TradeStage

    db_path = tmp_path / "trades.db"
    config.data["storage"]["database"] = str(db_path)
    config.data["grid"]["enabled"] = True

    logger = DataLogger(db_path)
    trade = Trade(ticket=1, direction="buy", symbol="XAUUSD", entry=2400.0, sl=2395.0,
                  tp1=2410.0, tp2=2415.0, tp3=2420.0, volume=0.01, initial_volume=0.01,
                  stage=TradeStage.CLOSED, closed_at=datetime.now(timezone.utc),
                  pnl=10.0, exit_price=2410.0)
    logger.log_trade_open(trade)
    logger.log_trade_close(trade)
    logger.log_grid_basket("basket_tp", pnl=3.0, positions=2, volume=0.02)

    assert cmd_report(config, None) == 0
    output = capsys.readouterr().out
    assert "Gold Dragon SMC" in output
    assert "شبكة تحوّط" in output
    assert "المقارنة" in output
    assert "SMC" in output and "الشبكة" in output


def test_report_skips_comparison_with_no_grid_data(config, tmp_path, capsys):
    from src.main import cmd_report

    config.data["storage"]["database"] = str(tmp_path / "trades.db")
    config.data["grid"]["enabled"] = False

    assert cmd_report(config, None) == 0
    output = capsys.readouterr().out
    assert "شبكة تحوّط" not in output
