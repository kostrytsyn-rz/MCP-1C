# 🧠 Сервис генерации эмбеддингов

FastAPI сервис для генерации векторных представлений текста с поддержкой GPU и оптимизаций производительности.

---

## 📋 Содержание

- [Обзор](#обзор)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [API](#api)
- [Оптимизации производительности](#оптимизации-производительности)
- [Docker](#docker)
- [Структура проекта](#структура-проекта)
- [Требования](#требования)
- [Доступные модели](#доступные-модели)
- [Настройка под ваше железо](#настройка-под-ваше-железо)
- [Мониторинг](#мониторинг)
- [Советы по использованию](#советы-по-использованию)
- [Примеры использования](#примеры-использования)
- [Решение проблем](#решение-проблем)
- [Интеграция](#интеграция)
- [Дополнительная документация](#дополнительная-документация)
- [Формат эмбеддингов](#формат-эмбеддингов)
- [Переменные окружения](#переменные-окружения)
- [Безопасность](#безопасность)

---

## 🎯 Обзор

Сервис предоставляет REST API для генерации эмбеддингов текста с использованием моделей Sentence Transformers:

- 🚀 **GPU ускорение** - автоматическое использование CUDA при наличии
- ⚡ **Оптимизации производительности** - FP16, torch.compile, cuDNN benchmark
- 💾 **LRU кэш** - переиспользование эмбеддингов для повторяющихся текстов (до 20k элементов)
- 🔄 **Дедупликация** - автоматическое удаление дубликатов в запросах
- 📊 **Мониторинг** - health check и метрики производительности

---

## 🚀 Быстрый старт

### Локальный запуск

```bash
cd embeddings
pip install -r requirements.txt
python embedding_service.py
```

Сервис будет доступен на http://localhost:5000

### Docker

```bash
cd embeddings
docker-compose up -d
```

Или через основной `docker-compose.yml` из корня проекта:

```bash
docker-compose up -d embedding-service
```

---

## ⚙️ Конфигурация

Конфигурация задается в файле `config.json`:

```json
{
  "model": {
    "backend": "sentence_transformers",
    "name": "intfloat/multilingual-e5-large",
    "trust_remote_code": false,
    "default_task": "retrieval.passage",
    "use_prompts": false,
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "dimensions": null,
      "batch_size": 128
    }
  },
  "performance": {
    "precision": "fp16",
    "torch_compile": true,
    "use_fast_tokenizer": true,
    "enable_tf32": true,
    "cudnn_benchmark": true,
    "pin_memory": true,
    "dynamic_batch_sizing": true,
    "warmup_iterations": 2
  }
}
```

### Параметры модели

- `backend` - источник эмбеддингов:
  - `sentence_transformers` - локальная модель HuggingFace (CPU/GPU), по умолчанию;
  - `openai` - любой OpenAI-совместимый `/v1/embeddings`: OpenAI, Ollama, LM Studio, vLLM, Infinity, TEI.
    Контракт `/embed` не меняется, loader / mcp / syntax_loader работают без изменений.
- `name` - модель HuggingFace (`intfloat/multilingual-e5-large`, `Qwen/Qwen3-Embedding-0.6B`, ...) или
  имя модели API (`text-embedding-3-small`, `bge-m3` в Ollama, ...)
- `trust_remote_code` - разрешить выполнение пользовательского кода модели
- `default_task` - тип задачи по умолчанию (`retrieval.passage`)
- `use_prompts` - использовать подсказки «запрос/документ» из `models_info` модели:
  `prefixes` (E5: `query: ` / `passage: `) или `prompt_names` (Qwen3: `query`). Улучшает поиск;
  после включения нужна полная перезагрузка данных
- `openai.base_url`, `openai.dimensions`, `openai.batch_size` - параметры OpenAI-совместимого API
  (ключ - переменная окружения `OPENAI_API_KEY`)

### Переопределение через переменные окружения (`.env` в корне проекта)

```bash
EMBEDDING_BACKEND=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
# OPENAI_EMBEDDING_DIMENSIONS=1024   # для моделей с поддержкой параметра dimensions
```

Ollama на хосте: `EMBEDDING_BACKEND=openai`, `EMBEDDING_MODEL=bge-m3`,
`OPENAI_BASE_URL=http://host.docker.internal:11434/v1`.

> ⚠️ После смены модели или размерности нужна **полная** перезагрузка конфигурации в Loader и
> перезагрузка синтакс-помощника. Инкрементальная загрузка проверяет размерность и остановится с ошибкой.

### Параметры производительности

- `precision` - точность вычислений: `fp32`, `fp16`, `bf16` (FP16 рекомендуется для GPU)
- `torch_compile` - использовать torch.compile() для ускорения (PyTorch 2.0+)
- `use_fast_tokenizer` - использовать быстрый токенизатор
- `enable_tf32` - включить TF32 для GPU Ampere+ (RTX 30xx, A100)
- `cudnn_benchmark` - включить cuDNN benchmark
- `pin_memory` - закреплять память для ускорения передачи данных
- `dynamic_batch_sizing` - автоматически рассчитывать оптимальный размер батча на основе доступной GPU памяти
- `warmup_iterations` - количество итераций прогрева модели при запуске

---

## 📡 API

### POST `/embed`

Генерация эмбеддингов для списка текстов.

**Запрос:**
```json
{
  "texts": ["текст 1", "текст 2", "текст 3"],
  "task": "retrieval.passage",
  "dimensions": null
}
```

**Ответ:**
```json
{
  "embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...]],
  "dimensions": 768,
  "model": "intfloat/multilingual-e5-base",
  "task": "default (task parameter ignored)",
  "count": 2
}
```

**Особенности:**
- Автоматическая дедупликация текстов в запросе
- Использование LRU кэша для повторяющихся текстов
- Поддержка больших батчей (автоматическое разбиение)

### GET `/health`

Проверка состояния сервиса.

**Ответ:**
```json
{
  "status": "healthy",
  "service": "embedding_service",
  "model": "intfloat/multilingual-e5-base",
  "dimensions": 768,
  "device": "cuda:0",
  "gpu": {
    "available": true,
    "name": "NVIDIA GeForce RTX 3060 Ti",
    "memory_allocated_gb": 2.1,
    "memory_reserved_gb": 2.5
  }
}
```

### GET `/model-info`

Информация о загруженной модели.

**Ответ:**
```json
{
  "model_name": "intfloat/multilingual-e5-base",
  "dimensions": 768,
  "supports_task": false,
  "device": "cuda:0",
  "gpu": {...},
  "available_models": [...],
  "optimal_batch_size": 512
}
```

### GET `/performance-info`

Информация о применённых оптимизациях.

**Ответ:**
```json
{
  "precision": "fp16",
  "optimizations": {
    "torch_compile": true,
    "use_fast_tokenizer": true,
    "cudnn_benchmark": true,
    "tf32_enabled": true,
    "pin_memory": true,
    "dynamic_batching": true
  },
  "batch_size": 512,
  "memory_savings": "~50%",
  "expected_speedup": "1.5-3x"
}
```

---

## ⚡ Оптимизации производительности

### FP16 (Half Precision)

**Преимущества:**
- ~50% экономия GPU памяти
- 1.5-3x ускорение на GPU
- Минимальная потеря точности

**Требования:**
- GPU с поддержкой FP16 (все современные GPU)

**Рекомендуется для:**
- RTX 3060 Ti, RTX 3070, RTX 3080 и выше
- Серверные GPU (A100, V100)

### torch.compile()

**Преимущества:**
- 1.2-2x ускорение первого запуска
- Стабильная производительность

**Требования:**
- PyTorch 2.0+

### Dynamic Batch Sizing

Автоматически рассчитывает оптимальный размер батча на основе:
- Доступной GPU памяти
- Выбранной точности (FP16/FP32)
- Размера модели

### LRU Кэш

- Переиспользование эмбеддингов для повторяющихся текстов
- Максимум 20,000 элементов в кэше
- Автоматическое удаление старых элементов

### Дедупликация

Автоматическое удаление дубликатов текстов в запросе:
- Одинаковые тексты обрабатываются один раз
- Результаты копируются для всех позиций

---

## 🐳 Docker

### Запуск через docker-compose

```bash
cd embeddings
docker-compose up -d
```

### С GPU поддержкой

GPU подключается оверлеем `docker-compose.gpu.yml` из корня проекта (`start.sh` / `start.bat` делают это
автоматически при наличии `nvidia-smi`):

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Без оверлея сервис работает на CPU.

### Проверка GPU в контейнере

```bash
docker exec embedding-service python -c "import torch; print(torch.cuda.is_available())"
```

---

## 📂 Структура проекта

```
embeddings/
├── README.md              # Этот файл
├── embedding_service.py   # Основной сервис
├── config.json            # Конфигурация модели и оптимизаций
├── requirements.txt       # Зависимости Python
├── Dockerfile             # Docker образ
├── docker-compose.yml     # Docker Compose конфигурация
└── models/                # Кэш моделей HuggingFace
```

---

## 📦 Требования

### Зависимости

- Python 3.7+
- PyTorch 2.0+ (с CUDA для GPU)
- sentence-transformers
- fastapi
- uvicorn
- numpy

**Установка:**
```bash
pip install -r requirements.txt
```

### Системные требования

**CPU режим:**
- RAM: 4GB+
- Рекомендуется для небольших моделей или тестирования

**GPU режим (рекомендуется):**
- GPU: NVIDIA с поддержкой CUDA
- VRAM: 4GB+ (для base моделей), 8GB+ (для large моделей)
- CUDA Toolkit 11.8+
- NVIDIA Container Toolkit (для Docker)

---

## 🎛️ Доступные модели

### Рекомендуемые для русского языка и кода

**intfloat/multilingual-e5-base**
- Размерность: 768
- Скорость: ⭐⭐⭐⭐
- Качество: ⭐⭐⭐⭐
- Поддержка: 100+ языков, включая русский
- **Рекомендуется для большинства случаев**

**intfloat/multilingual-e5-large**
- Размерность: 1024
- Скорость: ⭐⭐⭐
- Качество: ⭐⭐⭐⭐⭐
- Поддержка: 100+ языков, включая русский
- **Для максимального качества**

### Другие модели

**all-MiniLM-L6-v2**
- Размерность: 384
- Скорость: ⭐⭐⭐⭐⭐
- Качество: ⭐⭐⭐
- Поддержка: только английский

**jinaai/jina-embeddings-v3**
- Размерность: 1024
- Поддержка task parameter: ✅
- Поддержка: универсальная

**Qwen/Qwen3-Embedding-0.6B**
- Размерность: 1024
- Скорость: ⭐⭐⭐⭐
- Качество: ⭐⭐⭐⭐
- Поддержка: китайский, английский

---

## 🔧 Настройка под ваше железо

### RTX 3060 Ti / RTX 3070 (8GB VRAM)

```json
{
  "model": {
    "name": "intfloat/multilingual-e5-base"
  },
  "performance": {
    "precision": "fp16",
    "torch_compile": true,
    "dynamic_batch_sizing": true
  }
}
```

**Ожидаемая производительность:**
- Batch size: ~512
- Скорость: ~100-200 текстов/сек

### RTX 3080 / RTX 3090 (10GB+ VRAM)

```json
{
  "model": {
    "name": "intfloat/multilingual-e5-large"
  },
  "performance": {
    "precision": "fp16",
    "torch_compile": true,
    "dynamic_batch_sizing": true
  }
}
```

**Ожидаемая производительность:**
- Batch size: ~256-512
- Скорость: ~50-100 текстов/сек

### CPU режим

```json
{
  "model": {
    "name": "intfloat/multilingual-e5-base"
  },
  "performance": {
    "precision": "fp32",
    "torch_compile": false,
    "dynamic_batch_sizing": false
  }
}
```

**Ожидаемая производительность:**
- Batch size: 32
- Скорость: ~5-10 текстов/сек

---

## 📊 Мониторинг

### Health Check

```bash
curl http://localhost:5000/health
```

### Информация о модели

```bash
curl http://localhost:5000/model-info
```

### Информация о производительности

```bash
curl http://localhost:5000/performance-info
```

---

## 💡 Советы по использованию

### Оптимизация батчей

- Используйте размер батча, кратный 8 или 16 (для оптимизации GPU)
- При `dynamic_batch_sizing` размер рассчитывается автоматически
- Для больших батчей увеличьте `base_timeout` в loader

### Управление памятью

- При нехватке VRAM используйте `fp16` вместо `fp32`
- Уменьшите `optimal_batch_size` если возникают OOM ошибки
- Закрывайте неиспользуемые сессии к сервису

### Кэширование

- LRU кэш работает автоматически
- Повторяющиеся тексты обрабатываются мгновенно из кэша
- Кэш очищается при перезапуске сервиса

---

## 🔍 Примеры использования

### Python

```python
import requests

response = requests.post(
    "http://localhost:5000/embed",
    json={
        "texts": [
            "Процедура ПриЗаписи(Отказ)",
            "Функция ПолучитьДанные() Возврат Данные; КонецФункции"
        ]
    }
)

embeddings = response.json()["embeddings"]
```

### cURL

```bash
curl -X POST http://localhost:5000/embed \
  -H "Content-Type: application/json" \
  -d '{
    "texts": ["текст для векторизации"]
  }'
```

---

## 🐛 Решение проблем

### GPU не используется

**Проблема:** В логах видно "⚠️ CUDA not available, using CPU"

**Решение:**
1. Проверьте установку CUDA: `nvidia-smi`
2. Проверьте версию PyTorch: `python -c "import torch; print(torch.cuda.is_available())"`
3. Для Docker: убедитесь что установлен NVIDIA Container Toolkit

### Out of Memory (OOM)

**Проблема:** Ошибка нехватки памяти GPU

**Решение:**
1. Используйте модель меньшего размера (base вместо large)
2. Включите `fp16` вместо `fp32`
3. Уменьшите `optimal_batch_size` в конфиге
4. Включите `dynamic_batch_sizing`

### Медленная работа

**Проблема:** Низкая скорость генерации эмбеддингов

**Решение:**
1. Используйте GPU (CPU значительно медленнее)
2. Включите `torch_compile`
3. Используйте `fp16` вместо `fp32`
4. Увеличьте размер батча (если достаточно VRAM)

---

## 🔗 Интеграция

### С Loader

Loader автоматически использует embedding service для генерации эмбеддингов перед загрузкой в Qdrant.

**Конфигурация через переменные окружения:**
```bash
EMBEDDING_SERVICE_URL=http://localhost:5000
```

### С MCP Server

MCP Server использует embedding service для генерации эмбеддингов при семантическом поиске.

---

## 📚 Дополнительная документация

- [Sentence Transformers](https://www.sbert.net/)
- [PyTorch Optimizations](https://pytorch.org/tutorials/recipes/recipes/tuning_guide.html)
- [HuggingFace Models](https://huggingface.co/models)

---

## 📝 Формат эмбеддингов

Все эмбеддинги нормализуются (L2 норма = 1) для использования с косинусным расстоянием:

```python
# В Qdrant используется Distance.COSINE
# Это работает с нормализованными векторами
```

---

## ⚙️ Переменные окружения

Основные настройки - в `config.json`. Переменные окружения переопределяют модель:
`EMBEDDING_BACKEND`, `EMBEDDING_MODEL`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_EMBEDDING_DIMENSIONS`
(пустые значения игнорируются).

Для Docker можно переопределить переменные окружения для PyTorch:
```bash
PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
CUDA_VISIBLE_DEVICES=0
```

---

## 🔐 Безопасность

- Сервис работает локально и не требует авторизации
- Для продакшена рекомендуется добавить аутентификацию
- Не экспортируйте порт 5000 наружу без необходимости

