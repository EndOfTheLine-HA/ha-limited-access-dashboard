FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN addgroup --system dashboard && adduser --system --ingroup dashboard dashboard

COPY pyproject.toml README.md ./
COPY limited_access_dashboard ./limited_access_dashboard
RUN chmod -R a+rX /app/limited_access_dashboard
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data && chown -R dashboard:dashboard /app/data
USER dashboard

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=2)"

CMD ["uvicorn", "limited_access_dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787", "--no-server-header"]

