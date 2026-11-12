# 🔍 MCP 1C RAG Server + MCP SQL

Рабочий проект: **MCP-сервер 1С** (семантический поиск по конфигурации 1С:Предприятие через Qdrant,
синтакс-помощник, проверка кода BSL) и **MCP-сервер MS SQL** (чтение данных базы, только SELECT).

Загрузка данных в Qdrant (parser, loader, syntax-loader) лежит в папке **[INIT/](INIT/README.md)**:
эти контейнеры поднимаются поверх базового набора только при первичной загрузке и обновлении данных
и используют тот же Qdrant и Embedding Service.

---

## 📋 Содержание

- [Архитектура](#-архитектура)
- [Структура проекта](#-структура-проекта)
- [Быстрый старт](#-быстрый-старт)
- [Подключение к AI-агенту](#-подключение-к-ai-агенту)
- [Инструменты (MCP Tools)](#-инструменты-mcp-tools)
- [Примеры использования](#-примеры-использования)
- [Решение проблем](#-решение-проблем)
- [Модели эмбеддингов](#-модели-эмбеддингов)
- [Сравнение с другими MCP для 1С](#-сравнение-с-другими-mcp-для-1с)
- [FAQ](#-faq)

---

## 🏗️ Архитектура

```
Базовый набор (docker-compose.yml)                      INIT/ (по необходимости)
──────────────────────────────────                      ────────────────────────
AI Agent (Claude Code, Cursor, n8n ...)                 parser → loader / syntax-loader
   │ http://localhost:9000/mcp   http://localhost:9001/mcp          │ запись
   ▼                             ▼                                  │
mcp-server-1c ──► embedding-service ◄────── mssql-mcp ──► SQL Server│
   │              (CPU или GPU) ◄────────────────── векторы ────────┤
   ▼                                                                │
qdrant-1c  (./qdrant_storage) ◄──────────── сеть mcp-1c-network ────┘
```

| Контейнер | Порт | Назначение |
|---|---|---|
| `qdrant-1c` | `127.0.0.1:6333` | Векторная БД с загруженной конфигурацией (`./qdrant_storage`) |
| `embedding-service` | `127.0.0.1:5000` | Эмбеддинги: поисковые запросы MCP и массовая загрузка из INIT. CPU — `embeddings/Dockerfile`, при NVIDIA GPU — `embeddings/Dockerfile.gpu` |
| `mcp-server-1c` | `9000` | MCP 1C (`/mcp`), `/health` |
| `mssql-mcp` | `127.0.0.1:9001` | MCP SQL (`/mcp`), только чтение |

`embedding-service` нужен MCP 1C постоянно: инструменты семантического поиска (`search_1c_*`,
`search_procedures_direct`, `bslsyntax_*`) превращают текст запроса в вектор. Остальные инструменты
читают готовые данные из Qdrant и работают без него.

### Коллекции Qdrant

| Коллекция | Содержимое |
|---|---|
| `1c_objects_meta` | Метаданные объектов, мультивекторный поиск по 5 векторам |
| `1c_modules_full` | Метаданные модулей, код восстанавливается из `1c_procedures` |
| `1c_code_chunks` | Фрагменты кода для детального поиска |
| `1c_procedures` | Полный код процедур/функций, сигнатуры, вызовы |
| `1c_syntax_help` | Синтакс-помощник платформы 1С |

---

## 📂 Структура проекта

| Каталог / файл | Назначение |
|---|---|
| [mcp/](mcp/) | MCP-сервер 1С (FastMCP, BSL Language Server). 📖 [README](mcp/README.md) |
| [mssql-mcp/](mssql-mcp/) | MCP-сервер MS SQL. Настройки подключения — `mssql-mcp/.env`. 📖 [README](mssql-mcp/README.md) |
| [embeddings/](embeddings/) | Сервис эмбеддингов, оба варианта в одной папке: **CPU** — `Dockerfile` + `config.json` (`python:3.11-slim` + PyTorch CPU, ~1.4 GB); **GPU** — `Dockerfile.gpu` + `config.gpu.json` (`pytorch/pytorch` + CUDA, FP16, ~8 GB), подключается `docker-compose.gpu.yml` при наличии NVIDIA GPU. Кэш модели — `embeddings/models/`, общий для обоих. 📖 [README](embeddings/README.md) |
| [INIT/](INIT/README.md) | Контейнеры загрузки данных: parser, loader, syntax-loader |
| `qdrant_storage/` | Данные Qdrant (переносимы между машинами) |
| [standards/](standards/) | Стандарты команды для `get_dev_standards` |
| [projects/1c/](projects/1c/) | Папка с `.bsl`-файлами для `bslcheck_analyze` / `bslcheck_format` (монтируется как `/workspaces`) |
| [standards/1C_DEVELOPMENT_RULES.md](standards/1C_DEVELOPMENT_RULES.md) | Правила разработки (отдаются через `get_dev_standards`, идут первыми). Лежат в `standards/`, отдельно не монтируются |
| `docker-compose.yml`, `docker-compose.gpu.yml` | Базовый набор и GPU-оверлей для embedding-service |
| `start.bat`/`stop.bat` (`start.sh`/`stop.sh`) | Запуск базового набора / остановка всего, включая INIT |

---

## 🚀 Быстрый старт

### Требования

- Docker Desktop (Windows) или Docker Engine (Linux)
- 4 GB RAM для контейнеров (embedding-service ~0.7 GB, Qdrant зависит от объёма конфигурации)
- Загруженные данные в `qdrant_storage/` (иначе — загрузите через [INIT](INIT/README.md))
- NVIDIA GPU — опционально, ускоряет массовую загрузку (нужен NVIDIA Container Toolkit / Docker Desktop с WSL2)

### Настройка

1. **MCP SQL** — заполните `mssql-mcp/.env` (хост, база, SQL-логин с правами `db_datareader`),
   см. [mssql-mcp/README.md](mssql-mcp/README.md).
2. **Папка проектов для проверки BSL** (опционально) — в `docker-compose.yml`, сервис `mcp-server`,
   замените `./projects/1c:/workspaces:ro` на свой путь, например `d:/Projects/1C:/workspaces:ro`.

### Запуск

```bash
start.bat
```

Скрипт сам определяет NVIDIA GPU (`nvidia-smi`): если GPU есть, подключается `docker-compose.gpu.yml`
и embedding-service собирается из `embeddings/Dockerfile.gpu`, иначе — CPU-образ из `embeddings/Dockerfile`.

Ручной запуск и проверка:

```bash
docker compose up -d --build
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
docker compose ps
```

> BSL Language Server скачивается при сборке `mcp-server` (версия — `BSL_VERSION`, по умолчанию 0.29.0).
> Для сборки без интернета положите `bsl-language-server.jar` в `mcp/bsl_syntax_check/`.

### Загрузка и обновление данных

Первичная загрузка конфигурации, инкрементальное обновление и загрузка синтакс-помощника выполняются
контейнерами **[INIT](INIT/README.md)**: после `start.bat` выполните `INIT\start.bat`, загрузите данные,
затем `INIT\stop.bat`. Базовый набор и MCP-серверы при этом не перезапускаются. Для больших конфигураций
загрузку лучше выполнять на машине с GPU.

---

## 🤖 Подключение к AI-агенту

Оба сервера работают по MCP **Streamable HTTP**:

| Сервер | URL |
|---|---|
| MCP 1C | `http://localhost:9000/mcp` |
| MCP SQL | `http://localhost:9001/mcp`|

**Claude Code**:

```bash
claude mcp add --transport http 1c-rag http://localhost:9000/mcp
```

```bash
claude mcp add --transport http --scope user mssql http://localhost:9001/mcp
```

**Cursor** — `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "1c-rag": { "url": "http://localhost:9000/mcp" },
    "mssql": { "url": "http://localhost:9001/mcp" }
  }
}
```

**Windsurf / Kilo Code / Cline / Continue** — сервер с типом `streamableHttp` (или `http`) и URL выше.
**n8n и собственные агенты** — MCP-клиент (например, *MCP Client Tool*) с теми же URL.

**Сервер для команды** — разверните стек на одной машине и укажите в клиентах
`http://<адрес-сервера>:9000/mcp`. MCP SQL и Qdrant привязаны к `127.0.0.1`; чтобы открыть их по сети,
измените `ports` в `docker-compose.yml`.

**Правила для агента** — содержимое [1C_DEVELOPMENT_RULES.md](standards/1C_DEVELOPMENT_RULES.md) и
[1C_DEVELOPMENT_PROMPT.md](../old/1C_DEVELOPMENT_PROMPT.md) добавьте в правила проекта (`.cursor/rules`, `CLAUDE.md` и т.п.).

---

## 🔧 Инструменты (MCP Tools)

### MCP 1C — метаданные

- **`diagnose_database`** - Состояние коллекций, статистика по объектам и модулям
- **`list_metadata_objects`** - Список объектов: сводка по типам или перечень объектов типа
- **`get_object_metadata`** - Полная структура объекта (реквизиты, ТЧ, измерения, ресурсы, индексирование)
- **`search_1c_objects`** - Семантический поиск объектов по описанию
- **`search_by_structure`** - Поиск по реквизитам/типам/ТЧ, в т.ч. по индексируемым полям (`indexed=True`)
- **`search_by_relationship`** - Поиск по связям (владельцы, основания, движения)
- **`find_object_references`** - Где используется объект: метаданные + обращения в коде

### MCP 1C — код

- **`search_1c_code`** - Поиск в коде модулей
- **`search_procedures_direct`** - Прямой поиск процедур
- **`search_1c_procedures`** - Поиск процедур и функций
- **`find_procedure_code`** - Код процедуры
- **`get_module_full_code`** - Полный код модуля
- **`list_object_modules`** - Список модулей объекта
- **`analyze_procedure_calls`** - Анализ зависимостей процедуры
- **`find_similar_procedures`** - Поиск похожих процедур

### MCP 1C — проверка кода и стандарты

- **`bslcheck_code`** - Проверка кода (текстом): BSL Language Server + проверки по стандартам
- **`bslcheck_analyze`** - То же для `.bsl` файла из `projects/1c` (`/workspaces`), файл не изменяется
- **`bslcheck_format`** - Форматирование кода, результат возвращается текстом
- **`get_dev_standards`** - Стандарты разработки: [1C_DEVELOPMENT_RULES.md](standards/1C_DEVELOPMENT_RULES.md) + [standards/](standards/)
- MCP-ресурс `standards://<файл>` и MCP-промпт `develop_1c`

### MCP 1C — синтакс-помощник

- **`bslsyntax_search_global_function`**, **`bslsyntax_search_method`**, **`bslsyntax_search_property`**, **`bslsyntax_get_details`**

### MCP SQL

- **`list_tables`** - Список таблиц базы
- **`query_sql`** - Выполнение SELECT (`execute_sql` доступен только при `MCP_READ_ONLY=false`)

---

## 📖 Примеры использования

```
👤: Где используется справочник ВидыЦен?
🤖: find_object_references("Справочник", "ВидыЦен")
   Метаданные: РегистрСведений.ЦеныНоменклатуры — Измерение ВидЦены, ...
   Код: Документ.УстановкаЦен / МодульОбъекта: ОбработкаПроверкиЗаполнения, ...
```

```
👤: Проанализируй количество проиндексированных полей в регистрах сведений, выведи топ 5
🤖: search_by_structure(object_type="РегистрСведений", indexed=True, limit=5)
```

Агент вызывает `bslcheck_code` с написанным кодом, получает замечания построчно, сам исправляет код и
повторяет проверку.

---

## 🐛 Решение проблем

```bash
curl http://localhost:9000/health                       # MCP 1C: Qdrant, эмбеддинги, коллекции
curl http://localhost:6333/collections                  # Qdrant
curl http://localhost:5000/health                       # Embedding Service (device: cpu / cuda)
docker inspect --format "{{.State.Health.Status}}" mssql-mcp
docker compose logs -f mcp-server
```

- **`/health` показывает `embedding_service: ERROR`** — embedding-service ещё загружает модель
  (первый запуск без кэша `embeddings/models/` скачивает ~2 GB).
- **Поиск возвращает нерелевантные результаты / ошибку размерности** — модель в `embeddings/config.json`
  не совпадает с моделью, которой загружались данные. `embeddings/config.json` и `embeddings/config.gpu.json`
  должны указывать одну и ту же модель.
- **`could not select device driver "nvidia"`** — GPU найден, но не установлен NVIDIA Container Toolkit
  (на Windows — Docker Desktop с WSL2 и свежий драйвер). Запустите без GPU: `docker compose up -d --build`.
- **Ошибка сборки mcp-server (BSL Language Server)** — задайте `BSL_VERSION` в `.env` или положите jar в
  `mcp/bsl_syntax_check/`.
- **MCP SQL** — см. раздел Troubleshooting в [mssql-mcp/README.md](mssql-mcp/README.md).
- **`network mcp-1c-network has active endpoints`** при `docker compose down` вручную — сначала остановите
  INIT (`INIT\stop.bat`); `stop.bat` делает это сам.

---

## 🧠 Модели эмбеддингов

Модель задаётся в [embeddings/config.json](embeddings/config.json) и [embeddings/config.gpu.json](embeddings/config.gpu.json)
(`model.name`, держите одинаковой) или переменными в `.env` (`EMBEDDING_BACKEND`, `EMBEDDING_MODEL`,
`OPENAI_BASE_URL`, `OPENAI_API_KEY`). После смены модели нужна полная перезагрузка данных через INIT.


| Модель | Backend | Размерность | Комментарий |
|---|---|---|---|
| `intfloat/multilingual-e5-large` | sentence_transformers | 1024 | По умолчанию |
| `Qwen/Qwen3-Embedding-0.6B` | sentence_transformers | 1024 | Лучше E5 при том же размере |
| `Qwen/Qwen3-Embedding-4B` / `8B` | sentence_transformers | 2560 / 4096 | Для запросов на CPU медленно |
| `text-embedding-3-small` / `large` | openai | 1536 / 3072 | Платно, без GPU |
| модель Ollama / LM Studio / vLLM | openai | — | `OPENAI_BASE_URL=http://host.docker.internal:11434/v1` |

Код и запросы уходят во внешний API только при `backend: openai` с внешним `OPENAI_BASE_URL`.

---

## ❓ FAQ

**Нужны ли loader/parser/syntax_loader для работы MCP?** Нет. Они только записывают данные в Qdrant и
лежат в [INIT/](INIT/README.md) и запускаются только на время загрузки. Для работы нужны Qdrant с данными
и embedding-service (векторизация запросов).

**Нужен ли GPU?** Нет. GPU ускоряет только массовую загрузку через INIT (полная загрузка ERP на CPU
занимает ночь); один поисковый запрос на CPU векторизуется за доли секунды. При наличии NVIDIA GPU
`start.bat` сам собирает GPU-образ (`embeddings/Dockerfile.gpu`).

**Как перенести на другую машину?** Скопируйте этот проект вместе с `qdrant_storage/` (и `embeddings/models/`,
чтобы не скачивать модель), заполните `mssql-mcp/.env` и выполните `start.bat`.

**Как обновить данные после изменения конфигурации?** Инкрементальная загрузка через INIT — обновляются
только изменившиеся объекты и модули (около 10 минут на GPU).
