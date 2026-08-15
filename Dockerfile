# ═══════════════════════════════════════════════════════════════
#  صورة التشغيل — وضع forward test: بيانات سوق حقيقية + تنفيذ ورقي.
#
#  ملاحظة جوهرية: MetaTrader5 لا تعمل داخل Linux container إطلاقاً
#  (المكتبة ويندوزية وتتطلب طرفية MT5 مفتوحة). للتداول الحقيقي على
#  حساب تجريبي أو حي، شغّل البوت على خادم Windows بجوار طرفية MT5.
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
COPY scripts/ ./scripts/

# /data مخصص لتركيب قرص دائم (Volume): قاعدة الصفقات وحالة الأقفال.
# بدونه تُمسح حالة القفل مع كل إعادة نشر — وهو ما يُبطل حماية 48 ساعة.
RUN mkdir -p /app/logs /app/state /data \
 && useradd --create-home --shell /bin/bash dragon \
 && chown -R dragon:dragon /app /data
USER dragon

# فحص صحي عبر نقطة HTTP المحلية بدل إعادة تحميل الإعدادات كل مرة
HEALTHCHECK --interval=2m --timeout=10s --start-period=45s --retries=3 \
  CMD python -c "import os,urllib.request as u; \
u.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8080') + '/health', timeout=5)" \
  || exit 1

CMD ["python", "-m", "src.main", "run"]
