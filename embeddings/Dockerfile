# Легкий CPU-образ embedding-service для робочого режиму MCP
# (векторизація одиночних пошукових запитів, без масового імпорту).
# Замість pytorch/pytorch:*-cuda (~8 GB) - python-slim + CPU-збірка PyTorch.
# GPU-варіант - Dockerfile.gpu + config.gpu.json у цій самій папці (docker-compose.gpu.yml).
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# CPU-збірка PyTorch (без CUDA-бібліотек) - тієї ж версії, що в GPU-образі
ARG TORCH_VERSION=2.7.0
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "torch==${TORCH_VERSION}" --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
# --extra-index-url: щоб залежності не підтягнули CUDA-версію torch з PyPI
RUN pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

COPY config.json .
COPY embedding_service.py .

ENV PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=4

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

CMD ["python", "embedding_service.py"]
