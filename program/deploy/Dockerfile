FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DB_PATH=/app/data/oa.db \
    M0_WORKSPACE=/app/workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/workspace /app/logs

EXPOSE 8077

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
