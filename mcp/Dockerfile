FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей + Java для BSL Language Server
RUN apt-get update && apt-get install -y \
    curl \
    openjdk-21-jre-headless \
    wget \
    && rm -rf /var/lib/apt/lists/*

# BSL Language Server кладём в /opt/bsl - ВНЕ /app: docker-compose монтирует ./mcp:/app
# и перекрывает всё, что лежит в /app внутри образа (jar «пропадал» при запуске).
ARG BSL_VERSION=0.29.0
# Папка bsl_syntax_check: в поставке есть .gitkeep; положите туда bsl-language-server.jar для офлайн-сборки
COPY bsl_syntax_check/ /opt/bsl/
# Если локального файла нет, скачиваем из GitHub
RUN if [ ! -f /opt/bsl/bsl-language-server.jar ]; then \
        echo "Локальный BSL Language Server не найден, скачиваем версию ${BSL_VERSION}..." && \
        wget -q "https://github.com/1c-syntax/bsl-language-server/releases/download/v${BSL_VERSION}/bsl-language-server-${BSL_VERSION}-exec.jar" \
        -O /opt/bsl/bsl-language-server.jar; \
    else \
        echo "Используется локальный BSL Language Server"; \
    fi && \
    chmod +r /opt/bsl/bsl-language-server.jar && \
    ls -lh /opt/bsl/bsl-language-server.jar

# Копируем requirements
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY mcp_server.py .
COPY config.py .
COPY .bsl-language-server.json .
COPY tools/ tools/

# Порт
EXPOSE 9000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# Переменные окружения для BSL Language Server
ENV BSL_LANGUAGE_SERVER_PATH=/opt/bsl/bsl-language-server.jar
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# Запуск
CMD ["python", "mcp_server.py"]