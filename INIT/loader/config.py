# Конфигурация для loader (значения переопределяются переменными окружения из docker-compose.yml)
import os
from dotenv import load_dotenv

load_dotenv()

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
EMBEDDING_SERVICE_URL = os.getenv("EMBEDDING_SERVICE_URL", "http://localhost:5000")

# Имена коллекций (должны соответствовать mcp/config.py)
COLLECTION_OBJECTS = os.getenv("COLLECTION_OBJECTS", "1c_objects_meta")
COLLECTION_MODULES = os.getenv("COLLECTION_MODULES", "1c_modules_full")
COLLECTION_CHUNKS = os.getenv("COLLECTION_CHUNKS", "1c_code_chunks")
COLLECTION_PROCEDURES = os.getenv("COLLECTION_PROCEDURES", "1c_procedures")

# Параметры чанкования
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))  # Строк кода в одном чанке
MIN_LINES_FOR_CHUNKING = int(os.getenv("MIN_LINES_FOR_CHUNKING", "800"))  # Минимум строк для разбиения на чанки
