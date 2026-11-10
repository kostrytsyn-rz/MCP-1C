# Конфигурация для MCP сервера 1С RAG

import os
from dotenv import load_dotenv

load_dotenv()

# ========================================
# Qdrant settings
# ========================================
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

# Имена коллекций
COLLECTION_OBJECTS = os.getenv("COLLECTION_OBJECTS", "1c_objects_meta")
COLLECTION_MODULES = os.getenv("COLLECTION_MODULES", "1c_modules_full")
COLLECTION_CHUNKS = os.getenv("COLLECTION_CHUNKS", "1c_code_chunks")
COLLECTION_PROCEDURES = os.getenv("COLLECTION_PROCEDURES", "1c_procedures")

# Старая переменная (deprecated, оставлена для обратной совместимости)
COLLECTION_NAME = os.getenv("COLLECTION_NAME", COLLECTION_OBJECTS)

# ========================================
# Embedding service settings
# ========================================
EMBEDDING_SERVICE_URL = os.getenv(
    "EMBEDDING_SERVICE_URL", "http://localhost:5000"
)

# ========================================
# Server settings
# ========================================
SERVER_NAME = os.getenv("SERVER_NAME", "MCP 1C RAG Server")
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "9000"))

# ========================================
# RAG settings
# ========================================
DEFAULT_SEARCH_LIMIT = int(os.getenv("DEFAULT_SEARCH_LIMIT", "5"))
MAX_SEARCH_LIMIT = int(os.getenv("MAX_SEARCH_LIMIT", "20"))
MIN_SEARCH_LIMIT = int(os.getenv("MIN_SEARCH_LIMIT", "1"))

# ========================================
# Request timeout settings
# ========================================
EMBEDDING_REQUEST_TIMEOUT = int(os.getenv("EMBEDDING_REQUEST_TIMEOUT", "30"))
HEALTH_CHECK_TIMEOUT = int(os.getenv("HEALTH_CHECK_TIMEOUT", "5"))
SSE_PING_INTERVAL = float(os.getenv("SSE_PING_INTERVAL", "30.0"))

# ========================================
# Vector search settings
# ========================================
# Имена векторов в коллекции COLLECTION_OBJECTS
VECTOR_OBJECT_NAME = "object_name"
VECTOR_FRIENDLY_NAME = "friendly_name"
VECTOR_DESCRIPTION = "description"

# Имя вектора в коллекциях COLLECTION_MODULES и COLLECTION_CHUNKS
VECTOR_CODE = "code"

# Множитель для prefetch лимита в мультивекторном поиске
PREFETCH_LIMIT_MULTIPLIER = int(os.getenv("PREFETCH_LIMIT_MULTIPLIER", "3"))

# ========================================
# Code chunking settings
# ========================================
# Размер чанка кода (строк)
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))

# Минимальное количество строк для разбиения на чанки
MIN_LINES_FOR_CHUNKING = int(os.getenv("MIN_LINES_FOR_CHUNKING", "800"))

# ========================================
# Module types (типы модулей 1С)
# ========================================
MODULE_TYPES = [
    "ObjectModule",           # Модуль объекта
    "ManagerModule",          # Модуль менеджера
    "RecordSetModule",        # Модуль набора записей
    "ValueManagerModule",     # Модуль менеджера значения
    "FormModule",             # Модуль формы
    "CommandModule",          # Модуль команды
    "CommonModule",           # Общий модуль
    "SessionModule",          # Модуль сеанса
    "ExternalConnectionModule", # Модуль внешнего соединения
    "ManagedApplicationModule", # Модуль управляемого приложения
    "OrdinaryApplicationModule" # Модуль обычного приложения
]

# ========================================
# Object types (типы объектов 1С)
# ========================================
OBJECT_TYPES = [
    "Справочник",
    "Документ",
    "РегистрСведений",
    "РегистрНакопления",
    "РегистрБухгалтерии",
    "Константа",
    "Перечисление",
    "ПланВидовХарактеристик",
    "ПланСчетов",
    "ПланВидовРасчета",
    "ПланОбмена",
    "БизнесПроцесс",
    "Задача",
    "Отчет",
    "Обработка",
    "ОбщийМодуль",
    "ОбщаяФорма",
    "ОбщаяКоманда",
    "ГруппаКоманд",
    "ПараметрСеанса",
    "Роль",
    "ОбщийРеквизит",
    "ОбщаяКартинка",
    "Подписка",
    "ПланировщикРегламентныхЗаданий",
    "ФункциональнаяОпция",
    "ПараметрФункциональныхОпций",
    "ОпределяемыйТип",
    "ХранилищеНастроек",
    "WebСервис",
    "HTTPСервис",
    "WSСсылка",
    "Интерфейс",
    "СтильЭлемента",
    "Язык"
]

# ========================================
# Logging settings
# ========================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ========================================
# Feature flags
# ========================================
# Включить детальное логирование поиска
ENABLE_SEARCH_LOGGING = os.getenv("ENABLE_SEARCH_LOGGING", "false").lower() == "true"

# Включить кэширование результатов поиска
ENABLE_SEARCH_CACHE = os.getenv("ENABLE_SEARCH_CACHE", "false").lower() == "true"

# Максимальный размер полного кода модуля для возврата (символов)
MAX_FULL_CODE_SIZE = int(os.getenv("MAX_FULL_CODE_SIZE", "100000"))

# ========================================
# Синтакс-помощник 1С
# ========================================
COLLECTION_SYNTAX = os.getenv("COLLECTION_SYNTAX", "1c_syntax_help")

# ========================================
# BSL Language Server (проверка синтаксиса)
# ========================================
# Версия BSL Language Server для скачивания
BSL_LANGUAGE_SERVER_VERSION = os.getenv("BSL_LANGUAGE_SERVER_VERSION", "0.29.0")

# Путь к JAR файлу BSL Language Server
BSL_LANGUAGE_SERVER_PATH = os.getenv(
    "BSL_LANGUAGE_SERVER_PATH",
    "/opt/bsl/bsl-language-server.jar"
)

# Файл настроек BSL Language Server (язык сообщений, параметры диагностик)
BSL_CONFIGURATION_PATH = os.getenv(
    "BSL_CONFIGURATION_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bsl-language-server.json")
)

# Таймаут выполнения команд BSL Language Server (в секундах)
BSL_COMMAND_TIMEOUT = int(os.getenv("BSL_COMMAND_TIMEOUT", "120"))  # 2 минуты

# ========================================
# Workspaces mount paths (для конвертации путей в Docker)
# ========================================
# Пути монтирования проектов 1С (из docker-compose.yml)
# Формат: "host_path1:container_path1,host_path2:container_path2"
# Пример: "./projects/1c:/workspaces,d:/Projects:/workspaces"
# Если не задано, будет использоваться автоматическое определение
WORKSPACES_MOUNT_PATHS = os.getenv("WORKSPACES_MOUNT_PATHS", "")

# ========================================
# Стандарты разработки (инструмент get_dev_standards)
# ========================================
# Правила проекта (лежат в папке standards/, в выдаче идут первыми)
DEV_RULES_PATH = os.getenv("DEV_RULES_PATH", "/standards/1C_DEVELOPMENT_RULES.md")
# Папка с собственными стандартами команды (*.md)
STANDARDS_DIR = os.getenv("STANDARDS_DIR", "/standards")
