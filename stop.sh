#!/bin/bash
# Остановка всех контейнеров: сначала INIT (он использует сеть mcp-1c-network), затем базовый набор.
cd "$(dirname "$0")"

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

$COMPOSE -f INIT/docker-compose.yml down --remove-orphans
$COMPOSE -f docker-compose.yml -f docker-compose.gpu.yml down --remove-orphans
