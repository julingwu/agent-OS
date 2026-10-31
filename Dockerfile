FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    DEBIAN_FRONTEND=noninteractive \
    DB_PATH=/app/data/oa.db \
    M0_WORKSPACE=/app/workspace

# 安装系统级基础依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制代码与构建产物
COPY . .

# 创建持久化目录
RUN mkdir -p /app/data /app/workspace /app/logs /app/outputs

EXPOSE 8077

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
