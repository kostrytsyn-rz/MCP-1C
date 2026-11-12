# 🛠️ INIT — загрузка и обновление данных MCP 1C

Контейнеры для подготовки и загрузки данных в Qdrant: разбор конфигурации 1С (**parser**),
загрузка конфигурации (**loader**) и синтакс-помощника (**syntax-loader**).

Они поднимаются **поверх базового набора** MCP-1C (`..\start.bat`) и используют его
**Qdrant** и **Embedding Service** — своих экземпляров не запускают. После загрузки контейнеры INIT
останавливаются, базовый набор и MCP-серверы продолжают работать.

**Требуется только Docker** (Docker Desktop на Windows). 

---

## 🏗️ Как это связано

```
MCP-1C/ (базовый набор, ..\start.bat)          MCP-1C/INIT/ (этот каталог, INIT\start.bat)
────────────────────────────────────           ───────────────────────────────────────────
qdrant-1c          127.0.0.1:6333  ◄─── запись ── loader          :8501
embedding-service  127.0.0.1:5000  ◄─ векторы ─── syntax-loader   :8502
  CPU: ../embeddings/Dockerfile                   parser          :8504  (in_conf → out_conf)
  GPU: ../embeddings/Dockerfile.gpu (при NVIDIA GPU)
mcp-server-1c :9000, mssql-mcp :9001
                 └──────────── общая сеть mcp-1c-network ───────────┘
```

- **CPU или GPU** выбирает `..\start.bat`: при найденной NVIDIA GPU (`nvidia-smi`) embedding-service
  собирается из `../embeddings/Dockerfile.gpu` (FP16, CUDA), иначе — из `../embeddings/Dockerfile` (CPU). Для массовой
  загрузки большой конфигурации запускайте базовый набор на машине с GPU.
- **parser** не зависит от Qdrant и эмбеддингов. Если базовый набор не запущен, `start.bat` поднимает
  только его — разбор можно сделать заранее.
- Данные попадают в `../qdrant_storage/`.

---

## 📂 Структура

| Каталог / файл | Назначение |
|---|---|
| [parser/](parser/) | Разбор выгрузки конфигурации и объединение с расширениями (Streamlit UI, контейнер `parser-1c`). Входные выгрузки — `parser/in_conf/`, результат — `parser/out_conf/`. Код встроен в образ — после правок `.py` нужна пересборка. 📖 [README](parser/README.md) |
| [loader/](loader/) | Загрузка конфигурации в Qdrant (коллекции `1c_objects_meta`, `1c_modules_full`, `1c_code_chunks`, `1c_procedures`). Код монтируется томом. 📖 [README](loader/README.md) |
| [syntax_loader/](syntax_loader/) | Загрузка синтакс-помощника (`1c_syntax_help`), готовый `structure.json` в комплекте. Код монтируется томом. 📖 [README](syntax_loader/README.md) |
| `docker-compose.yml` | parser, loader, syntax-loader (сеть `mcp-1c-network` — внешняя, из базового набора) |
| `start.bat` / `stop.bat` (`start.sh` / `stop.sh`) | Запуск / остановка контейнеров INIT |

---

## 🚀 Порядок работы

### 1. Запустите базовый набор

```bash
..\start.bat
```

Поднимает Qdrant, Embedding Service (CPU/GPU), MCP 1C, MCP SQL и создаёт сеть `mcp-1c-network`.

### 2. Запустите контейнеры INIT

```bash
start.bat
```

Скрипт проверяет, что `qdrant-1c` и `embedding-service` запущены, и показывает образ embedding-service
(`embedding-service-cpu` или `embedding-service-gpu`).

Ручной запуск:

```bash
docker compose up -d --build           # всё
docker compose up -d --build parser    # только parser
```

| Сервис | Адрес |
|---|---|
| Parser | http://localhost:8504 |
| Loader | http://localhost:8501 |
| Syntax Loader | http://localhost:8502 |
| Embedding Service (базовый) | http://localhost:5000 |
| Qdrant (базовый) | http://localhost:6333/dashboard |

### 3. Разбор конфигурации (parser)

1. Выгрузите конфигурацию (и расширения) в файлы: Конфигуратор → «Выгрузить конфигурацию в файлы».
2. Положите ZIP или папку в `parser/in_conf/` (расширения — туда же). В контейнере это
   `/data/in_conf` (только чтение); новые файлы появляются в UI без перезапуска.
3. Откройте http://localhost:8504, выберите основную конфигурацию и расширения, нажмите «Начать парсинг».
4. Результат — в `parser/out_conf/parsed_<имя>/` (в контейнере `/data/out_conf`).

> Утилиты парсера (`parse_1c_conf.py`, `merge_config_with_extension.py`, `check_annotations.py`)
> запускаются внутри контейнера: `docker exec -it parser-1c python check_annotations.py`.

### 4. Загрузка конфигурации (loader)

1. Откройте http://localhost:8501.
2. Загрузите `merged.zip` из `parser/out_conf/parsed_<имя>/`
   (без расширений — `conf_<имя>.zip`; если нет `merged.zip`, заархивируйте папку `merged/`).
3. Нажмите «🚀 Запустить загрузку».

Для **дополнения/обновления** используйте инкрементальный режим: обновляются только объекты и модули
с изменившимся SHA256, удалённые — удаляются.

### 5. Загрузка синтакс-помощника (syntax-loader)

Нужна один раз (и после смены модели эмбеддингов): http://localhost:8502 → выберите
`syntax_loader/structure.json` → «🚀 Начать загрузку».

`structure.json` для другой версии платформы собирается без Python в образе syntax-loader
(путь к `bin` платформы замените на свой; образ появляется после первого `start.bat`, команда — для cmd):

```bash
docker run --rm -v "C:/Program Files/1cv8/8.3.27.2214/bin:/hbk:ro" -v "%cd%/syntax_loader:/app" syntax-loader-1c sh -c "7z x /hbk/shcntx_ru.hbk -o/tmp/shcntx -y >/dev/null; python parse_structure.py /tmp/shcntx --output /app/structure.json"
```

### 6. Остановите контейнеры INIT

```bash
stop.bat
```

Базовый набор (Qdrant, Embedding Service, MCP 1C, MCP SQL) продолжает работать. `..\stop.bat`
останавливает всё, включая INIT.

---

## ⚠️ Важно

- **Модель эмбеддингов** задаётся в `../embeddings/config.json` (CPU) и `../embeddings/config.gpu.json` (GPU) —
  держите её одинаковой. После смены модели нужна **полная** загрузка конфигурации и повторная загрузка
  синтакс-помощника.
- Ориентировочное время полной (первоначальной) загрузки: УТ на RTX 3060 Ti — 2-3 часа, ERP + 2 расширения — около
  4 часов; Без GPU полная загрузка ERP занимает 10-12 часов. Инкрементальное обновление — 10-30 минут.
- Во время загрузки не перезапускайте базовый набор. Если embedding-service всё же перезапустился,
  Loader подождёт его готовности (до 10 минут, `EMBEDDING_SERVICE_WAIT_SECONDS`) и продолжит.
- Переключение CPU ↔ GPU: остановите INIT, выполните `..\stop.bat` и `..\start.bat` на нужной машине.

## 🐛 Проверка

```bash
docker compose ps
curl http://localhost:8504/_stcore/health  # parser
curl http://localhost:5000/health          # embedding-service (поле device: cpu / cuda)
curl http://localhost:6333/collections     # коллекции Qdrant
docker compose logs -f loader
```

- `network mcp-1c-network declared as external, but could not be found` — не запущен базовый набор (`..\start.bat`).
- Loader пишет об ошибке подключения к `embedding-service` — базовый набор запущен не полностью или
  embedding-service ещё загружает модель (`docker logs embedding-service`).
