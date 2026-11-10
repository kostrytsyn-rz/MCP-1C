FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем все файлы из папки loader
COPY . .

# Создаём конфигурацию Streamlit с максимальным размером загрузки 2 ГБ
RUN mkdir -p .streamlit && \
    echo "[server]" > .streamlit/config.toml && \
    echo "maxUploadSize = 2048" >> .streamlit/config.toml

# Порт Streamlit
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3     CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Запуск Streamlit UI для загрузки данных
CMD ["streamlit", "run", "loader.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]