# Parser конфигураций 1С (Streamlit UI)
#
# Папки монтируются из docker-compose.yml:
#   ./parser/in_conf  -> /data/in_conf   (выгрузки конфигураций и расширений: ZIP или папки)
#   ./parser/out_conf -> /data/out_conf  (результат разбора: parsed_<имя>/merged.zip и т.д.)
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Код парсера (лишнее отсекается .dockerignore)
COPY . .

ENV PYTHONUNBUFFERED=1 \
    INPUT_CONFIGS_DIR=/data/in_conf \
    OUTPUT_CONFIGS_DIR=/data/out_conf \
    STREAMLIT_SERVER_MAX_UPLOAD_SIZE=2048 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN mkdir -p /data/in_conf /data/out_conf

EXPOSE 8504

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8504/_stcore/health || exit 1

CMD ["streamlit", "run", "parser_ui.py", \
     "--server.port=8504", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
