FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/londres_market_gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY services /app/services
COPY tradingagents /app/tradingagents

EXPOSE 8080

CMD ["uvicorn", "services.londres_market_gateway.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
