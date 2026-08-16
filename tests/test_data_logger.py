"""اختبارات سجل شبكة التحوّط في DataLogger.

هذا الجزء أُضيف ليكون المصدر الوحيد للحكم على "أي استراتيجية نتائجها أقوى"
بعد شهر تشغيل — خطأ في التجميع هنا يعني مقارنة خاطئة بين البوتين، لا مجرد
عرضاً تجميلياً.
"""

from __future__ import annotations

import pytest

from src.infra.data_logger import DataLogger


@pytest.fixture
def logger(tmp_path):
    return DataLogger(tmp_path / "trades.db")


def test_empty_grid_summary_is_zeroed(logger):
    summary = logger.grid_summary()
    assert summary == {"baskets": 0, "win_rate": 0.0, "profit_factor": 0.0,
                       "net_pnl": 0.0, "avg_pnl": 0.0, "max_drawdown": 0.0}


def test_log_and_summarize_grid_baskets(logger):
    logger.log_grid_basket("basket_tp", pnl=5.0, positions=2, volume=0.02)
    logger.log_grid_basket("basket_loss_cutoff", pnl=-3.0, positions=3, volume=0.03)
    logger.log_grid_basket("basket_tp", pnl=4.0, positions=2, volume=0.02)

    summary = logger.grid_summary()
    assert summary["baskets"] == 3
    assert summary["win_rate"] == pytest.approx(2 / 3)
    assert summary["net_pnl"] == pytest.approx(6.0)
    assert summary["avg_pnl"] == pytest.approx(2.0)
    assert summary["profit_factor"] == pytest.approx(9.0 / 3.0)


def test_grid_summary_all_wins_has_infinite_profit_factor(logger):
    logger.log_grid_basket("basket_tp", pnl=5.0, positions=2, volume=0.02)
    assert logger.grid_summary()["profit_factor"] == float("inf")


def test_closed_grid_baskets_ordered_newest_first(logger):
    logger.log_grid_basket("basket_tp", pnl=1.0, positions=1, volume=0.01)
    logger.log_grid_basket("basket_tp", pnl=2.0, positions=1, volume=0.01)

    rows = logger.closed_grid_baskets()
    assert len(rows) == 2
    assert rows[0]["pnl"] == pytest.approx(2.0)  # الأحدث أولاً


def test_grid_and_smc_summaries_are_independent(logger):
    """التأكد من أن جدول grid_baskets منفصل تماماً عن جدول trades — لا تلوّث بينهما."""
    logger.log_grid_basket("basket_tp", pnl=10.0, positions=1, volume=0.01)
    assert logger.summary()["trades"] == 0
    assert logger.grid_summary()["baskets"] == 1


def test_payload_round_trips_through_json(logger):
    logger.log_grid_basket("basket_tp", pnl=1.0, positions=1, volume=0.01,
                           payload={"note": "اختبار"})
    row = logger.closed_grid_baskets()[0]
    assert "اختبار" in row["payload"]
