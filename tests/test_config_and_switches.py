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
