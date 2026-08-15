"""اختبارات مدير المخاطرة — أهم ملف في المشروع.

خطأ هنا لا يعني إشارة سيئة، بل حساباً مصفّى.
"""

from __future__ import annotations

import pytest

from src.risk_manager import RiskManager


@pytest.fixture
def risk(config, spec, tmp_path):
    return RiskManager(config, spec, state_file=tmp_path / "risk_state.json")


def test_position_size_matches_intended_risk(risk):
    """1% من 1000$ = 10$؛ وقف 5$ على عقد 100 أونصة → 0.02 لوت بالضبط."""
    result = risk.calculate_position_size(1000.0, 2400.0, 2395.0)
    assert result["lots"] == pytest.approx(0.02)
    assert result["risk_amount"] == pytest.approx(10.0, abs=0.01)


def test_position_size_scales_with_stop_distance(risk):
    """مضاعفة مسافة الوقف تُنصّف الحجم — المخاطرة بالدولار تبقى ثابتة."""
    tight = risk.calculate_position_size(10_000.0, 2400.0, 2395.0)
    wide = risk.calculate_position_size(10_000.0, 2400.0, 2390.0)
    assert wide["lots"] == pytest.approx(tight["lots"] / 2, rel=0.02)
    assert wide["risk_amount"] == pytest.approx(tight["risk_amount"], rel=0.02)


def test_position_size_never_exceeds_max_risk(risk):
    """رصيد صغير + وقف واسع: يجب الرفض لا التقريب لأعلى."""
    result = risk.calculate_position_size(50.0, 2400.0, 2380.0)
    assert result["lots"] == 0.0
    assert "الحد الأدنى" in result["reason"] or "السقف" in result["reason"]


def test_position_size_caps_risk_pct_at_configured_max(risk):
    """طلب مخاطرة 10% يُقصّ إلى السقف (2%)."""
    result = risk.calculate_position_size(100_000.0, 2400.0, 2395.0, risk_pct=0.10)
    assert result["risk_pct"] <= risk.max_risk_per_trade + 1e-6


def test_invalid_inputs_return_zero(risk):
    assert risk.calculate_position_size(1000.0, 2400.0, 2400.0)["lots"] == 0.0
    assert risk.calculate_position_size(0.0, 2400.0, 2395.0)["lots"] == 0.0


def test_three_consecutive_losses_lock_the_bot(risk):
    for _ in range(3):
        risk.record_result(-10.0)
    assert risk.is_locked
    assert not risk.can_trade(1000.0).allowed


def test_lock_survives_process_restart(config, spec, tmp_path):
    """القفل مخزَّن على القرص — إعادة التشغيل لا تُلغيه."""
    path = tmp_path / "risk_state.json"
    first = RiskManager(config, spec, state_file=path)
    for _ in range(3):
        first.record_result(-10.0)
    assert first.is_locked

    revived = RiskManager(config, spec, state_file=path)
    assert revived.is_locked
    assert not revived.can_trade(1000.0).allowed


def test_win_resets_loss_streak(risk):
    risk.record_result(-10.0)
    risk.record_result(-10.0)
    risk.record_result(+30.0)
    assert risk.state["consecutive_losses"] == 0
    assert risk.can_trade(1000.0).allowed


def test_daily_loss_limit_blocks_trading(risk):
    risk.record_result(-60.0)  # 6% من 1000$ > حد 5%
    decision = risk.can_trade(1000.0)
    assert not decision.allowed
    assert "اليومية" in decision.reason


def test_total_open_risk_cap(risk):
    """مخاطرة مفتوحة 4% + 1% جديدة > سقف 4% → رفض."""
    assert not risk.can_trade(1000.0, open_positions=1, open_risk=0.04).allowed
    assert risk.can_trade(1000.0, open_positions=1, open_risk=0.02).allowed


def test_max_concurrent_positions(risk):
    limit = risk.max_positions
    assert not risk.can_trade(1000.0, open_positions=limit).allowed


def test_drawdown_blocks_new_trades(risk):
    risk.can_trade(1000.0)          # يسجّل القمة عند 1000
    decision = risk.can_trade(800.0)  # هبوط 20% > حد 15%
    assert not decision.allowed
    assert "Drawdown" in decision.reason


def test_open_risk_fraction(risk, spec):
    class FakePosition:
        entry, sl, volume = 2400.0, 2395.0, 0.02

    # 0.02 لوت × 5$ × 100 = 10$ = 1% من 1000$
    assert risk.open_risk_fraction([FakePosition()], 1000.0) == pytest.approx(0.01)
