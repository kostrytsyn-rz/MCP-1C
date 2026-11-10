@echo off
chcp 65001 >nul
rem Запуск контейнеров загрузки данных (Windows). Нужен только Docker.
rem Qdrant и Embedding Service берутся из базового набора: ..\start.bat
cd /d "%~dp0"

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker не запущен. Запустите Docker Desktop и повторите.
    exit /b 1
)

rem Папки парсера монтируются в контейнер parser-1c
if not exist "parser\in_conf" mkdir "parser\in_conf"
if not exist "parser\out_conf" mkdir "parser\out_conf"

set QDRANT_RUNNING=
for /f "delims=" %%i in ('docker inspect -f "{{.State.Running}}" qdrant-1c 2^>nul') do set QDRANT_RUNNING=%%i
set EMBEDDING_RUNNING=
for /f "delims=" %%i in ('docker inspect -f "{{.State.Running}}" embedding-service 2^>nul') do set EMBEDDING_RUNNING=%%i

set BASE_OK=1
if not "%QDRANT_RUNNING%"=="true" set BASE_OK=0
if not "%EMBEDDING_RUNNING%"=="true" set BASE_OK=0

if "%BASE_OK%"=="0" (
    echo Базовый набор ^(qdrant-1c, embedding-service^) не запущен - запускается только Parser.
    echo Для Loader и Syntax Loader сначала выполните: ..\start.bat
    echo.
    docker compose up --build -d --remove-orphans parser
    if errorlevel 1 exit /b 1
    echo.
    echo - Parser: http://localhost:8504   ^(in_conf: parser\in_conf, результат: parser\out_conf^)
    exit /b 0
)

docker compose up --build -d --remove-orphans

if errorlevel 1 exit /b 1

rem Сообщаем, на чём считаются эмбеддинги (образ embedding-service выбирает ..\start.bat)
set EMBEDDING_IMAGE=
for /f "delims=" %%i in ('docker inspect -f "{{.Config.Image}}" embedding-service 2^>nul') do set EMBEDDING_IMAGE=%%i

echo.
echo Сервисы загрузки запущены:
echo - Parser:                        http://localhost:8504
echo - Loader:                        http://localhost:8501
echo - Syntax Loader:                 http://localhost:8502
echo - Embedding Service ^(базовый^):   http://localhost:5000  ^(образ %EMBEDDING_IMAGE%^)
echo - Qdrant ^(базовый^):              http://localhost:6333/dashboard
echo.
echo Порядок работы:
echo 1. Положите выгрузку конфигурации ^(ZIP или папку^) в parser\in_conf
echo 2. Parser ^(8504^) - разбор и объединение с расширениями, результат в parser\out_conf
echo 3. Loader ^(8501^) - загрузка parser\out_conf\parsed_^<имя^>\merged.zip в Qdrant
echo 4. Syntax Loader ^(8502^) - загрузка синтакс-помощника ^(syntax_loader\structure.json^)
echo 5. После загрузки: stop.bat - базовый набор продолжает работать с данными в Qdrant
