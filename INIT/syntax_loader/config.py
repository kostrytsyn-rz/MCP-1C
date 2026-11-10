# Конфигурация для syntax_loader
import os
from dotenv import load_dotenv

load_dotenv()

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
EMBEDDING_SERVICE_URL = os.getenv("EMBEDDING_SERVICE_URL", "http://localhost:5000")
COLLECTION_SYNTAX = os.getenv("COLLECTION_SYNTAX", "1c_syntax_help")

# Параметры батчинга
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "50"))

