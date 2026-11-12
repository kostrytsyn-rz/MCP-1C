#!/bin/bash
# Запуск базового набора: Qdrant, Embedding Service, MCP 1C, MCP SQL (Linux/Mac)
cd "$(dirname "$0")"

if ! docker info >/dev/null 2>&1; then
    echo "Docker не запущен. Запустите Docker и повторите."
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

# GPU-оверлей (embeddings/Dockerfile.gpu) подключается только при наличии NVIDIA GPU
COMPOSE_FILES="-f docker-compose.yml"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    echo "Обнаружена NVIDIA GPU - Embedding Service на GPU (embeddings/Dockerfile.gpu)"
    COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.gpu.yml"
else
    echo "NVIDIA GPU не найдена - Embedding Service на CPU (embeddings/Dockerfile)"
fi

$COMPOSE $COMPOSE_FILES up --build -d --remove-orphans || exit 1

echo ""
echo "Сервисы запущены:"
echo "- MCP Server 1C:     http://localhost:9000/mcp"
echo "- MCP SQL Server:    http://localhost:9001/mcp"
echo "- Qdrant:            http://localhost:6333/dashboard"
echo "- Embedding Service: http://localhost:5000"
echo ""
echo "Загрузка и обновление данных в Qdrant: INIT/start.sh"
