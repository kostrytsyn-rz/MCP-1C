@echo off
chcp 65001 >nul
rem Запуск базового набора: Qdrant, Embedding Service, MCP 1C, MCP SQL (Windows)
cd /d "%~dp0"

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker не запущен. Запустите Docker Desktop и повторите.
    exit /b 1
)

rem GPU-оверлей (embeddings/Dockerfile.gpu) подключается только при наличии NVIDIA GPU
set COMPOSE_FILES=-f docker-compose.yml
where nvidia-smi >nul 2>&1
if %ERRORLEVEL%==0 (
    nvidia-smi >nul 2>&1
    if not errorlevel 1 (
        echo Обнаружена NVIDIA GPU - Embedding Service на GPU ^(embeddings/Dockerfile.gpu^)
        set COMPOSE_FILES=-f docker-compose.yml -f docker-compose.gpu.yml
    )
)
if "%COMPOSE_FILES%"=="-f docker-compose.yml" echo NVIDIA GPU не найдена - Embedding Service на CPU ^(embeddings/Dockerfile^)

docker compose %COMPOSE_FILES% up --build -d --remove-orphans

if errorlevel 1 exit /b 1

echo.
echo Сервисы запущены:
echo - MCP Server 1C:     http://localhost:9000/mcp
echo - MCP SQL Server:    http://localhost:9001/mcp
echo - Qdrant:            http://localhost:6333/dashboard
echo - Embedding Service: http://localhost:5000
echo.
echo Загрузка и обновление данных в Qdrant: INIT\start.bat
