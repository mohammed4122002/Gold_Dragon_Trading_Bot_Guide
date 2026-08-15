"""اختبارات البوابات: الجلسات، الأخبار، الارتباطات، السيكولوجيا، ACS."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.core.acs import calculate_acs
from src.models import POI
from src.strategies import CorrelationGate, NewsFilter, PsychologyGate, SessionManager


# ═══ الجلسات ═══════════════════════════════════════════════════════
def test_london_killzone_is_open(config):
    session = SessionManager(config).current_session(
        datetime(2026, 3, 2, 8, 30, tzinfo=timezone.utc)
    )
    assert session["inside_killzone"] and session["name"] == "London"


def test_overlap_has_highest_quality(config):
    session = SessionManager(config).current_session(
        datetime(2026, 3, 2, 13, 0, tzinfo=timezone.utc)
    )
    assert session["name"] == "Overlap" and session["quality"] > 1.0


def test_outside_killzone_blocks_when_configured(config):
    session = SessionManager(config).current_session(
        datetime(2026, 3, 2, 4, 0, tzinfo=timezone.utc)
    )
    assert not session["inside_killzone"] and session["blocked"]


def test_us_close_window_is_blocked(config):
    session = SessionManager(config).current_session(
        datetime(2026, 3, 2, 18, 30, tzinfo=timezone.utc)
    )
    assert session["blocked"] and "US Close" in session["reason"]


def test_midnight_crossing_window(config):
    """جلسة طوكيو 23:00→02:00 تعبر منتصف الليل."""
    session = SessionManager(config).current_session(
        datetime(2026, 3, 2, 23, 30, tzinfo=timezone.utc)
    )
    assert session["name"] == "Tokyo"


# ═══ الأخبار ═══════════════════════════════════════════════════════
def _write_calendar(config, events):
    path = config.path("news.calendar_file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"events": events}), encoding="utf-8")


def test_high_impact_news_freezes_entries(config):
    now = datetime.now(timezone.utc)
    _write_calendar(config, [
        {"time": (now + timedelta(minutes=10)).isoformat(),
         "title": "FOMC Statement", "impact": "high"}
    ])
    status = NewsFilter(config).check(now)
    assert status["frozen"] and status["impact"] == "high"


def test_news_outside_window_does_not_freeze(config):
    now = datetime.now(timezone.utc)
    _write_calendar(config, [
        {"time": (now + timedelta(hours=9)).isoformat(),
         "title": "CPI m/m", "impact": "high"}
    ])
    assert not NewsFilter(config).check(now)["frozen"]


def test_missing_calendar_reports_unknown_not_safe(config):
    status = NewsFilter(config).check()
    assert status["impact"] == "unknown" and status["calendar"] == "missing"


def test_stale_calendar_is_flagged(config):
    now = datetime.now(timezone.utc)
    _write_calendar(config, [
        {"time": (now - timedelta(days=30)).isoformat(),
         "title": "Old NFP", "impact": "high"}
    ])
    status = NewsFilter(config).check(now)
    assert status["calendar"] == "stale" and status["impact"] == "unknown"


# ═══ الارتباطات ════════════════════════════════════════════════════
def test_falling_dollar_confirms_bullish_gold(config):
    result = CorrelationGate(config).evaluate("bullish", {
        "DXY": {"change_pct": -0.6, "price": 103.0},
        "US10Y": {"change_pct": -0.8, "price": 4.1},
    })
    assert result["verdict"] == "aligned"


def test_rising_dollar_conflicts_with_bullish_gold(config):
    result = CorrelationGate(config).evaluate("bullish", {
        "DXY": {"change_pct": 0.9, "price": 106.0},
        "US10Y": {"change_pct": 1.2, "price": 4.6},
    })
    assert result["verdict"] == "conflict"


def test_extreme_vix_blocks_regardless_of_alignment(config):
    result = CorrelationGate(config).evaluate("bullish", {
        "DXY": {"change_pct": -0.9, "price": 100.0},
        "US10Y": {"change_pct": -0.9, "price": 3.9},
        "VIX": {"change_pct": 20.0, "price": 42.0},
    })
    assert result["verdict"] == "conflict" and "VIX" in result["reason"]


def test_missing_data_is_neutral_not_aligned(config):
    assert CorrelationGate(config).evaluate("bullish", {})["verdict"] == "neutral"


# ═══ السيكولوجيا ═══════════════════════════════════════════════════
def test_angry_state_blocks(config):
    gate = PsychologyGate(config)
    gate.set_state("angry", "خسارة أمس")
    status = gate.check()
    assert status["blocked"] and status["score"] == 0.0


def test_calm_state_scores_full(config):
    gate = PsychologyGate(config)
    gate.set_state("calm")
    assert gate.check()["score"] == 1.0 and not gate.check()["blocked"]


def test_stale_declaration_falls_back_to_unknown(config):
    gate = PsychologyGate(config)
    gate.set_state("calm")
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    payload = json.loads(gate.path.read_text(encoding="utf-8"))
    payload["updated_at"] = old.isoformat()
    gate.path.write_text(json.dumps(payload), encoding="utf-8")

    status = gate.check()
    assert status["state"] == "unknown" and status["score"] == 0.5


def test_risk_lock_propagates_to_psychology_gate(config):
    status = PsychologyGate(config).check(risk_locked=True, lock_reason="3 خسارات")
    assert status["blocked"] and status["score"] == 0.0


def test_fomo_filter_blocks_late_entry():
    result = PsychologyGate.fomo_check(price=2410.0, poi_level=2400.0, target=2420.0)
    assert result["blocked"] and result["traveled"] == pytest.approx(0.5)

    assert not PsychologyGate.fomo_check(2402.0, 2400.0, 2420.0)["blocked"]


# ═══ ACS ═══════════════════════════════════════════════════════════
def _perfect_context():
    poi = POI(type="OB", direction="bullish", top=2400.0, bottom=2398.0,
              timeframe="H1", bar_index=10, confidence=0.8)
    return {
        "bias": {"direction": "bullish"},
        "ltf_bias": {"direction": "bullish"},
        "poi": poi,
        "sweep": {"status": "confirmed", "type": "SSL_Sweep"},
        "session": {"name": "Overlap", "inside_killzone": True},
        "choch": {"direction": "bullish"},
        "fresh_fvg": True,
        "news": {"impact": "none"},
        "zone": {"zone": "discount"},
        "direction": "bullish",
        "displacement": {"strength": 2.5},
        "correlation": {"verdict": "aligned"},
        "psychology": {"score": 1.0},
    }


def test_perfect_setup_scores_ten():
    result = calculate_acs(_perfect_context())
    assert result.raw == 14.0
    assert result.score == pytest.approx(10.0)
    assert result.grade == "excellent"


def test_empty_context_scores_low():
    result = calculate_acs({})
    assert result.score < 4.0 and result.hard_reject


def test_correlation_conflict_is_hard_reject():
    context = _perfect_context()
    context["correlation"] = {"verdict": "conflict"}
    result = calculate_acs(context)
    assert result.hard_reject and "Correlation" in result.hard_reject


def test_zero_psychology_is_hard_reject():
    context = _perfect_context()
    context["psychology"] = {"score": 0.0}
    assert calculate_acs(context).hard_reject is not None


def test_news_freeze_is_hard_reject():
    context = _perfect_context()
    context["news"] = {"impact": "high", "frozen": True, "title": "NFP"}
    assert "NFP" in (calculate_acs(context).hard_reject or "")


def test_breakdown_covers_all_ten_criteria():
    assert len(calculate_acs(_perfect_context()).breakdown) == 10
