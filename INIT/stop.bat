@echo off
chcp 65001 >nul
rem Остановка контейнеров загрузки данных. Базовый набор (Qdrant, Embedding Service, MCP) не затрагивается.
cd /d "%~dp0"
docker compose down --remove-orphans
