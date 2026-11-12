from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging
import json
import os
import time
import uvicorn
from typing import Dict, List, Optional, Tuple
import numpy as np
import torch
import gc
import requests
from collections import OrderedDict
from starlette.middleware.gzip import GZipMiddleware

app = FastAPI(
    title="Configurable Embeddings Service",
    description="API for generating embeddings with configurable models"
)

# Enable gzip responses for large batches
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKEND_SENTENCE_TRANSFORMERS = "sentence_transformers"
BACKEND_OPENAI = "openai"


# Load configuration
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("Config file not found, using default configuration")
        return {
            "model": {
                "name": "all-MiniLM-L6-v2",
                "trust_remote_code": False,
                "default_task": "retrieval.passage"
            },
            "models_info": {
                "all-MiniLM-L6-v2": {
                    "dimensions": 384,
                    "supports_task": False
                }
            }
        }


def apply_env_overrides(config):
    """
    Переопределение настроек модели переменными окружения (удобно для docker-compose / .env).

    EMBEDDING_BACKEND        - sentence_transformers | openai
    EMBEDDING_MODEL          - имя модели (HuggingFace или модели OpenAI-совместимого API)
    OPENAI_BASE_URL          - адрес OpenAI-совместимого API (OpenAI, Ollama, LM Studio, vLLM, ...)
    OPENAI_API_KEY           - ключ API (для локальных серверов можно не задавать)
    OPENAI_EMBEDDING_DIMENSIONS - размерность (для моделей с поддержкой параметра dimensions)
    """
    model_cfg = config.setdefault("model", {})
    openai_cfg = model_cfg.setdefault("openai", {})

    if os.getenv("EMBEDDING_BACKEND"):
        model_cfg["backend"] = os.getenv("EMBEDDING_BACKEND")
    if os.getenv("EMBEDDING_MODEL"):
        model_cfg["name"] = os.getenv("EMBEDDING_MODEL")
    if os.getenv("OPENAI_BASE_URL"):
        openai_cfg["base_url"] = os.getenv("OPENAI_BASE_URL")
    if os.getenv("OPENAI_EMBEDDING_DIMENSIONS"):
        openai_cfg["dimensions"] = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS"))
    return config


config = apply_env_overrides(load_config())


def resolve_prompt(model_meta: Dict, task: str, use_prompts: bool) -> Tuple[str, Optional[str]]:
    """
    Определить, как передать модели тип задачи (запрос / документ).

    В models_info для модели можно указать:
        "prefixes":     {"retrieval.query": "query: ", "retrieval.passage": "passage: "}  - текстовый префикс (E5)
        "prompt_names": {"retrieval.query": "query"}                                      - prompt_name модели (Qwen3)

    Returns:
        (префикс к тексту, prompt_name для SentenceTransformer.encode или None)
    """
    if not use_prompts:
        return "", None
    prefix = (model_meta.get("prefixes") or {}).get(task, "")
    prompt_name = (model_meta.get("prompt_names") or {}).get(task)
    return prefix, prompt_name


def apply_performance_optimizations(model, config):
    """
    Применить оптимизации производительности без потери точности.

    Args:
        model: SentenceTransformer модель
        config: Конфигурация с секцией performance

    Returns:
        Оптимизированная модель
    """
    perf = config.get("performance", {})

    precision = perf.get("precision", "fp32")
    if precision == "fp16" and torch.cuda.is_available():
        logger.info("🔧 Applying FP16 precision...")
        model = model.half()
        logger.info("✓ FP16 enabled: ~50% memory reduction, 1.5-3x speedup")
    elif precision == "bf16" and torch.cuda.is_available():
        if torch.cuda.get_device_capability(0)[0] >= 8:
            logger.info("🔧 Applying BF16 precision...")
            model = model.to(torch.bfloat16)
            logger.info("✓ BF16 enabled (Ampere+ GPU)")
        else:
            logger.warning("⚠️ BF16 requires Ampere+ GPU, using FP32")

    model.eval()
    for param in model.parameters():
        param.requires_grad = False
    logger.info("✓ Gradients disabled (inference mode)")

    if perf.get("torch_compile", False):
        try:
            if hasattr(torch, 'compile'):
                logger.info("🔧 Compiling model with torch.compile()...")
                model = torch.compile(model, mode="reduce-overhead")
                logger.info("✓ Model compiled: 1.2-2x speedup expected")
            else:
                logger.warning("⚠️ torch.compile not available (PyTorch 2.0+ required)")
        except Exception as e:
            logger.warning(f"⚠️ torch.compile failed: {e}")

    if torch.cuda.is_available():
        if perf.get("cudnn_benchmark", True):
            torch.backends.cudnn.benchmark = True
            logger.info("✓ cuDNN benchmark enabled")

        if perf.get("enable_tf32", True):
            if torch.cuda.get_device_capability(0)[0] >= 8:
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
                logger.info("✓ TF32 enabled (Ampere+ optimization)")

    return model, perf


def calculate_optimal_batch_size(perf_config, gpu_available):
    """
    Рассчитать оптимальный размер батча.

    Args:
        perf_config: Конфигурация performance
        gpu_available: Доступен ли GPU

    Returns:
        Оптимальный batch_size
    """
    if not gpu_available:
        return 32  # CPU

    # Базовый размер для GPU
    base_size = 128

    precision = perf_config.get("precision", "fp32")
    if precision == "fp16":
        base_size = 256
    elif precision == "int8":
        base_size = 512

    # Dynamic sizing
    if perf_config.get("dynamic_batch_sizing", False):
        try:
            # Проверить доступную память
            total_memory = torch.cuda.get_device_properties(0).total_memory
            # Оставить 2GB для системы
            available = (total_memory - 2 * 1024**3) / (1024**3)

            # Эвристика: ~3 GB на 128 текстов длиной до 512 токенов (FP16, модели large).
            # Проверено на RTX 3060 Ti 8 GB: батч 512 переполнял VRAM, Windows уводила память в RAM
            # и батч замедлялся с ~10 до ~80 секунд.
            memory_per_128 = 3.0 if precision == "fp16" else 6.0
            optimal = int((available / memory_per_128) * 128)

            return min(max(optimal, 32), 512)
        except:
            pass

    return base_size


def warmup_model(model, perf_config, logger):
    """
    Прогрев модели для стабильной производительности.

    Args:
        model: Модель для прогрева
        perf_config: Конфигурация performance
        logger: Logger
    """
    warmup_iters = perf_config.get("warmup_iterations", 3)
    if warmup_iters > 0:
        logger.info(f"🔥 Warming up model ({warmup_iters} iterations)...")

        test_texts = [
            "SELECT * FROM Таблица WHERE Поле = Значение",
            "Процедура ПриЗаписи(Отказ) // Комментарий",
            "Функция ПолучитьДанные() Возврат Данные; КонецФункции"
        ] * 10  # 30 текстов

        with torch.no_grad():
            for i in range(warmup_iters):
                _ = model.encode(
                    test_texts,
                    show_progress_bar=False,
                    normalize_embeddings=True
                )
                if torch.cuda.is_available():
                    torch.cuda.synchronize()

        logger.info("✓ Model warmed up")

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()


class SentenceTransformersBackend:
    """Локальная модель Sentence Transformers (CPU/GPU)."""

    def __init__(self, config):
        from sentence_transformers import SentenceTransformer

        self.model_name = config["model"]["name"]
        trust_remote_code = config["model"].get("trust_remote_code", False)

        logger.info(f"Loading model: {self.model_name}")

        # Determine device and log GPU info
        self.use_cuda = torch.cuda.is_available()
        device = "cuda" if self.use_cuda else "cpu"

        compute_capability = None
        if self.use_cuda:
            logger.info("=" * 60)
            logger.info("🚀 GPU ACCELERATION ENABLED")
            logger.info("=" * 60)
            logger.info(f"✓ GPU Name: {torch.cuda.get_device_name(0)}")
            logger.info(f"✓ CUDA Version: {torch.version.cuda}")
            logger.info(f"✓ PyTorch Version: {torch.__version__}")

            # GPU memory info
            gpu_memory_total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            logger.info(f"✓ GPU Total Memory: {gpu_memory_total:.2f} GB")

            # Compute capability
            compute_capability = torch.cuda.get_device_capability(0)
            logger.info(f"✓ Compute Capability: {compute_capability[0]}.{compute_capability[1]}")
            logger.info("=" * 60)
        else:
            logger.warning("⚠️ CUDA not available, using CPU")
            logger.warning("For GPU acceleration, ensure:")
            logger.warning("  1. NVIDIA drivers are installed")
            logger.warning("  2. NVIDIA Container Toolkit is configured")
            logger.warning("  3. docker-compose.gpu.yml is used (start.sh / start.bat do it automatically)")

        self.model = SentenceTransformer(
            self.model_name,
            trust_remote_code=trust_remote_code,
            device=device
        )

        # Apply performance optimizations
        logger.info("\n" + "=" * 60)
        logger.info("⚡ APPLYING PERFORMANCE OPTIMIZATIONS")
        logger.info("=" * 60)

        self.model, perf_config = apply_performance_optimizations(self.model, config)

        # Calculate optimal batch size
        self.batch_size = calculate_optimal_batch_size(perf_config, self.use_cuda)
        logger.info(f"✓ Optimal batch size: {self.batch_size}")

        # Warmup
        warmup_model(self.model, perf_config, logger)

        self.device = str(self.model.device)
        self.gpu_info = {
            "available": self.use_cuda,
            "device": self.device,
            "performance": perf_config
        }
        if self.use_cuda:
            self.gpu_info.update({
                "name": torch.cuda.get_device_name(0),
                "cuda_version": torch.version.cuda,
                "pytorch_version": torch.__version__,
                "total_memory_gb": round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2),
                "compute_capability": f"{compute_capability[0]}.{compute_capability[1]}"
            })

    def encode(self, texts: List[str], task: Optional[str] = None,
               prompt_name: Optional[str] = None) -> List[List[float]]:
        kwargs = dict(
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
            batch_size=self.batch_size
        )
        if task:
            # Модели с task-адаптерами (jina-embeddings-v3)
            kwargs["task"] = task
            kwargs["prompt_name"] = task
        elif prompt_name:
            kwargs["prompt_name"] = prompt_name
        with torch.no_grad():
            return self.model.encode(texts, **kwargs).tolist()

    def extra_health(self) -> Dict:
        info = dict(self.gpu_info)
        if self.use_cuda:
            try:
                info["memory_allocated_gb"] = round(torch.cuda.memory_allocated(0) / (1024**3), 2)
                info["memory_reserved_gb"] = round(torch.cuda.memory_reserved(0) / (1024**3), 2)
            except Exception:
                pass
        return info


class OpenAICompatibleBackend:
    """
    Любой OpenAI-совместимый endpoint /v1/embeddings:
    OpenAI (text-embedding-3-small/large), Ollama, LM Studio, vLLM, Infinity, TEI и т.п.
    """

    def __init__(self, config):
        model_cfg = config["model"]
        openai_cfg = model_cfg.get("openai", {})

        self.model_name = model_cfg["name"]
        self.base_url = openai_cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.dimensions = openai_cfg.get("dimensions")
        self.batch_size = int(openai_cfg.get("batch_size", 128))
        self.timeout = int(openai_cfg.get("timeout", 120))
        self.max_retries = int(openai_cfg.get("max_retries", 5))
        api_key = os.getenv("OPENAI_API_KEY", "")

        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

        self.device = f"remote: {self.base_url}"
        self.gpu_info = {"available": False, "device": self.device}
        logger.info(f"Using OpenAI-compatible embeddings API: {self.base_url}, model: {self.model_name}")

    def _request(self, texts: List[str]) -> List[List[float]]:
        payload = {"model": self.model_name, "input": texts}
        if self.dimensions:
            payload["dimensions"] = self.dimensions

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(f"{self.base_url}/embeddings", json=payload, timeout=self.timeout)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    delay = min(2 ** attempt, 30)
                    logger.warning(f"Embeddings API HTTP {resp.status_code}, повтор через {delay}с")
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda item: item.get("index", 0))
                return [item["embedding"] for item in data]
            except requests.RequestException as e:
                if attempt >= self.max_retries:
                    raise
                delay = min(2 ** attempt, 30)
                logger.warning(f"Embeddings API error: {e}, повтор через {delay}с")
                time.sleep(delay)
        raise RuntimeError("Embeddings API: исчерпаны попытки")

    def encode(self, texts: List[str], task: Optional[str] = None,
               prompt_name: Optional[str] = None) -> List[List[float]]:
        vectors = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self._request(texts[i:i + self.batch_size]))
        # Нормализуем: Qdrant коллекции используют косинус, но не все серверы отдают нормализованные векторы
        arr = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (arr / norms).tolist()

    def extra_health(self) -> Dict:
        return dict(self.gpu_info)


class EmbeddingRequest(BaseModel):
    texts: List[str]
    task: str = config["model"].get("default_task", "retrieval.passage")
    dimensions: Optional[int] = None


# Load the configured backend/model
try:
    backend_name = config["model"].get("backend", BACKEND_SENTENCE_TRANSFORMERS)
    if backend_name == BACKEND_OPENAI:
        backend = OpenAICompatibleBackend(config)
    elif backend_name == BACKEND_SENTENCE_TRANSFORMERS:
        backend = SentenceTransformersBackend(config)
    else:
        raise ValueError(f"Неизвестный backend эмбеддингов: {backend_name} "
                         f"(допустимо: {BACKEND_SENTENCE_TRANSFORMERS}, {BACKEND_OPENAI})")

    model_name = backend.model_name
    model_meta = config.get("models_info", {}).get(model_name, {})
    supports_task = bool(model_meta.get("supports_task", False)) and backend_name == BACKEND_SENTENCE_TRANSFORMERS
    use_prompts = bool(config["model"].get("use_prompts", False))

    # Реальная размерность - по тестовому эмбеддингу, а не из config.json
    model_dimensions = len(backend.encode(["test"])[0])
    optimal_batch_size = backend.batch_size

    logger.info("=" * 60)
    logger.info("✅ MODEL READY FOR INFERENCE")
    logger.info("=" * 60)
    logger.info(f"✓ Backend: {backend_name}")
    logger.info(f"✓ Model loaded: {model_name}")
    logger.info(f"✓ Dimensions: {model_dimensions}")
    logger.info(f"✓ Supports task parameter: {supports_task}")
    logger.info(f"✓ Query/passage prompts: {'on' if use_prompts else 'off'}")
    logger.info(f"✓ Device: {backend.device}")

except Exception as e:
    logger.error(f"Failed to load model: {e}")
    raise e


# LRU-кэш эмбеддингов: ключ учитывает задачу, т.к. запрос и документ кодируются по-разному
_embedding_cache: "OrderedDict[Tuple[str, str], List[float]]" = OrderedDict()
MAX_CACHE_ITEMS = 20000


@app.post("/embed")
async def generate_embeddings(request: EmbeddingRequest):
    """
    Generate embeddings for input texts.

    Args:
        texts: List of texts to embed
        task: Task type: retrieval.query (поисковый запрос) или retrieval.passage (документ)
        dimensions: Output dimensions (currently not used)

    Returns:
        Dictionary with embeddings and metadata
    """
    try:
        texts = request.texts
        task = request.task

        if not texts:
            raise HTTPException(status_code=400, detail="No texts provided")

        if len(texts) > 100:
            logger.warning(f"Large batch: {len(texts)} texts")

        prefix, prompt_name = resolve_prompt(model_meta, task, use_prompts)
        cache_scope = f"{task}|{prefix}|{prompt_name}" if (supports_task or prefix or prompt_name) else ""

        index_map = {}
        unique_texts: List[str] = []
        for i, t in enumerate(texts):
            if t not in index_map:
                index_map[t] = []
                unique_texts.append(t)
            index_map[t].append(i)

        cached_vectors = {}
        to_encode: List[str] = []
        for t in unique_texts:
            key = (cache_scope, t)
            if key in _embedding_cache:
                vec = _embedding_cache.pop(key)
                _embedding_cache[key] = vec
                cached_vectors[t] = vec
            else:
                to_encode.append(t)

        logger.info(
            f"Generating embeddings: total={len(texts)}, unique={len(unique_texts)}, "
            f"cache_hits={len(cached_vectors)}, to_encode={len(to_encode)}"
        )

        encoded_map = {}
        if to_encode:
            enc = backend.encode(
                [prefix + t for t in to_encode] if prefix else to_encode,
                task=task if supports_task else None,
                prompt_name=prompt_name
            )
            for t, vec in zip(to_encode, enc):
                if len(_embedding_cache) >= MAX_CACHE_ITEMS:
                    _embedding_cache.popitem(last=False)
                _embedding_cache[(cache_scope, t)] = vec
                encoded_map[t] = vec

        final_map = {**cached_vectors, **encoded_map}
        embeddings_list = [None] * len(texts)
        for t, positions in index_map.items():
            v = final_map[t]
            for pos in positions:
                embeddings_list[pos] = v

        actual_dimensions = len(embeddings_list[0]) if embeddings_list else 0

        logger.info(f"✓ Generated {len(embeddings_list)} embeddings of {actual_dimensions}D")

        if supports_task:
            task_info = task
        elif prefix or prompt_name:
            task_info = f"{task} (prefix={prefix!r}, prompt_name={prompt_name})"
        else:
            task_info = "default (task parameter ignored)"

        return {
            "embeddings": embeddings_list,
            "dimensions": actual_dimensions,
            "model": model_name,
            "task": task_info,
            "count": len(embeddings_list)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating embeddings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint for service monitoring"""
    try:
        # Для удалённого API не тратим запрос на каждую проверку - модель проверена при старте
        if backend_name == BACKEND_SENTENCE_TRANSFORMERS:
            dimensions = len(backend.encode(["test"])[0])
        else:
            dimensions = model_dimensions

        return {
            "status": "healthy",
            "service": "embedding_service",
            "backend": backend_name,
            "model": model_name,
            "dimensions": dimensions,
            "device": backend.device,
            "gpu": backend.extra_health()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@app.get("/model-info")
async def get_model_info():
    """Get information about the currently loaded model"""
    return {
        "backend": backend_name,
        "model_name": model_name,
        "dimensions": model_dimensions,
        "supports_task": supports_task,
        "use_prompts": use_prompts,
        "prompts": {
            "query": resolve_prompt(model_meta, "retrieval.query", use_prompts),
            "passage": resolve_prompt(model_meta, "retrieval.passage", use_prompts)
        },
        "trust_remote_code": config["model"].get("trust_remote_code", False),
        "device": backend.device,
        "gpu": backend.gpu_info,
        "available_models": list(config.get("models_info", {}).keys()),
        "optimal_batch_size": optimal_batch_size,
        "note": "task parameter is ignored for models with supports_task=false"
    }


@app.get("/performance-info")
async def get_performance_info():
    """Получить информацию о применённых оптимизациях"""
    perf = config.get("performance", {})

    info = {
        "backend": backend_name,
        "precision": perf.get("precision", "fp32"),
        "optimizations": {
            "torch_compile": perf.get("torch_compile", False),
            "use_fast_tokenizer": perf.get("use_fast_tokenizer", True),
            "cudnn_benchmark": perf.get("cudnn_benchmark", True),
            "tf32_enabled": perf.get("enable_tf32", True),
            "pin_memory": perf.get("pin_memory", True),
            "dynamic_batching": perf.get("dynamic_batch_sizing", False)
        },
        "batch_size": optimal_batch_size,
        "warmup_iterations": perf.get("warmup_iterations", 3),
        "memory_savings": "~50%" if perf.get("precision") == "fp16" else "0%",
        "expected_speedup": "1.5-3x" if perf.get("precision") == "fp16" and backend.gpu_info["available"] else "1x"
    }

    return info


@app.get("/")
async def root():
    """Root endpoint with service information"""
    return {
        "service": "Embedding Service",
        "backend": backend_name,
        "model": model_name,
        "endpoints": {
            "embed": "POST /embed - Generate embeddings",
            "health": "GET /health - Health check",
            "model_info": "GET /model-info - Model information",
            "performance_info": "GET /performance-info - Performance optimizations info"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
        log_level="info"
    )
