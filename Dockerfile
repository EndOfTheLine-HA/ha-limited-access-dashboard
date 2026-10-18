FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN addgroup --system portal && adduser --system --ingroup portal portal

COPY pyproject.toml README.md ./
COPY guest_portal ./guest_portal
RUN chmod -R a+rX /app/guest_portal
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data && chown -R portal:portal /app/data
USER portal

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=2)"

CMD ["uvicorn", "guest_portal.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787", "--no-server-header"]

