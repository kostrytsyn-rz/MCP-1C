#!/bin/bash
# Запуск контейнеров загрузки данных (Linux/Mac). Нужен только Docker.
# Qdrant и Embedding Service берутся из базового набора: ../start.sh
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

# Папки парсера монтируются в контейнер parser-1c
mkdir -p parser/in_conf parser/out_conf

QDRANT_RUNNING=$(docker inspect -f '{{.State.Running}}' qdrant-1c 2>/dev/null)
EMBEDDING_RUNNING=$(docker inspect -f '{{.State.Running}}' embedding-service 2>/dev/null)

if [ "$QDRANT_RUNNING" != "true" ] || [ "$EMBEDDING_RUNNING" != "true" ]; then
    echo "Базовый набор (qdrant-1c, embedding-service) не запущен - запускается только Parser."
    echo "Для Loader и Syntax Loader сначала выполните: ../start.sh"
    echo ""
    $COMPOSE up --build -d --remove-orphans parser || exit 1
    echo ""
    echo "- Parser: http://localhost:8504   (in_conf: parser/in_conf, результат: parser/out_conf)"
    exit 0
fi

$COMPOSE up --build -d --remove-orphans || exit 1

# Сообщаем, на чём считаются эмбеддинги (образ embedding-service выбирает ../start.sh)
EMBEDDING_IMAGE=$(docker inspect -f '{{.Config.Image}}' embedding-service 2>/dev/null)

echo ""
echo "Сервисы загрузки запущены:"
echo "- Parser:                        http://localhost:8504"
echo "- Loader:                        http://localhost:8501"
echo "- Syntax Loader:                 http://localhost:8502"
echo "- Embedding Service (базовый):   http://localhost:5000  (образ $EMBEDDING_IMAGE)"
echo "- Qdrant (базовый):              http://localhost:6333/dashboard"
echo ""
echo "Порядок работы:"
echo "1. Положите выгрузку конфигурации (ZIP или папку) в parser/in_conf"
echo "2. Parser (8504) - разбор и объединение с расширениями, результат в parser/out_conf"
echo "3. Loader (8501) - загрузка parser/out_conf/parsed_<имя>/merged.zip в Qdrant"
echo "4. Syntax Loader (8502) - загрузка синтакс-помощника (syntax_loader/structure.json)"
echo "5. После загрузки: stop.sh - базовый набор продолжает работать с данными в Qdrant"
