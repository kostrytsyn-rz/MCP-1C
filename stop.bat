@echo off
chcp 65001 >nul
rem Остановка всех контейнеров: сначала INIT (он использует сеть mcp-1c-network), затем базовый набор.
cd /d "%~dp0"
docker compose -f INIT\docker-compose.yml down --remove-orphans
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down --remove-orphans
