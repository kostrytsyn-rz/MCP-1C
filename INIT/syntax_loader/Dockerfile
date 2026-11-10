FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    curl \
    p7zip-full \
    libchm-bin \
    libchm-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "syntax_loader.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]

