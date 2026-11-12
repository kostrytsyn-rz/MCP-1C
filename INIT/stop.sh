#!/bin/bash
# Остановка контейнеров загрузки данных. Базовый набор (Qdrant, Embedding Service, MCP) не затрагивается.
cd "$(dirname "$0")"

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

$COMPOSE down --remove-orphans
