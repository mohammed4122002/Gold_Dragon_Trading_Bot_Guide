# ═══════════════════════════════════════════════════════════════
#  صورة التشغيل — للوضع التجريبي والتحليل والـ Backtest.
#  ملاحظة: MetaTrader5 لا تعمل داخل Linux container. للتداول
#  الحقيقي شغّل البوت على خادم Windows بجوار طرفية MT5.
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
RUN mkdir -p logs data state

# مستخدم غير جذر
RUN useradd --create-home --shell /bin/bash dragon && chown -R dragon:dragon /app
USER dragon

HEALTHCHECK --interval=5m --timeout=30s --start-period=30s \
  CMD python -m src.main doctor || exit 1

CMD ["python", "-m", "src.main", "run"]
