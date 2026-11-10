#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Загрузчик конфигурации 1С в Qdrant

Архитектура:
- STREAMING EMBEDDINGS: Чанки объектов → генерация эмбеддингов → создание точек → загрузка → очистка памяти
- PROCEDURES: хранение полного кода процедур
- MODULES: метаданные модулей (signature, params, start_line, end_line)
- CHUNKS: для чанков типа "procedure" используются эмбеддинги из PROCEDURES
- OBJECTS: метаданные, реквизиты, табличные части, связи
"""

import streamlit as st
import zipfile
import os
import json
import csv
import requests
import re
import numpy as np
import pandas as pd
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue, MatchAny,
    OptimizersConfigDiff
)
from typing import List, Dict, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import gc
import logging
try:
    import psutil  # optional, used for memory metrics only
    PSUTIL_AVAILABLE = True
except Exception:
    PSUTIL_AVAILABLE = False
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Настройка логирования для детальных сообщений (попадут в Docker логи)
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Конфигурация (переменные окружения, см. config.py)
from config import (
    QDRANT_HOST, QDRANT_PORT, EMBEDDING_SERVICE_URL,
    COLLECTION_OBJECTS, COLLECTION_MODULES, COLLECTION_CHUNKS, COLLECTION_PROCEDURES,
    CHUNK_SIZE, MIN_LINES_FOR_CHUNKING
)

# Checkpoint файл
CHECKPOINT_FILE = Path("temp_extracted/.checkpoint.json")
_embed_session = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=16,
    pool_maxsize=64,
    max_retries=Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET"]
    )
)
_embed_session.mount("http://", _adapter)
_embed_session.mount("https://", _adapter)




def save_checkpoint(checkpoint_data: Dict):
    """
    Сохранение текущего состояния загрузки.
    
    Args:
        checkpoint_data: Словарь с данными checkpoint
            - phase: фаза загрузки (1, 2, 3)
            - last_object_idx: индекс последнего обработанного объекта
            - last_object_id: ID последнего созданного объекта
            - last_module_id: ID последнего модуля
            - last_chunk_id: ID последнего чанка
            - last_procedure_id: ID последней процедуры
            - processed_objects: список обработанных объектов
            - timestamp: время сохранения
    """
    try:
        CHECKPOINT_FILE.parent.mkdir(exist_ok=True)
        checkpoint_data['timestamp'] = time.time()
        
        with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
        
        return True
    except Exception as e:
        st.warning(f"⚠️ Не удалось сохранить checkpoint: {e}")
        return False


def load_checkpoint() -> Optional[Dict]:
    """
    Загрузка сохраненного checkpoint.
    
    Returns:
        Словарь с данными checkpoint или None
    """
    try:
        if not CHECKPOINT_FILE.exists():
            return None
        
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)
        
        return checkpoint
    except Exception as e:
        st.warning(f"⚠️ Не удалось загрузить checkpoint: {e}")
        return None


def clear_checkpoint():
    """Удаление checkpoint файла"""
    try:
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
    except Exception as e:
        st.warning(f"⚠️ Не удалось удалить checkpoint: {e}")



def generate_deterministic_id(key: str) -> int:
    """
    Генерация детерминированного ID на основе строкового ключа.
    
    Использует SHA256 хеш и преобразует первые 8 байт в int64.
    Это гарантирует стабильные ID при повторных загрузках.
    
    Args:
        key: Строковый ключ (например, "Справочник.ЕУЗ.МодульОбъекта")
    
    Returns:
        Целое число в диапазоне [0, 2^63-1]
    
    Examples:
        >>> generate_deterministic_id("Справочник.ЕУЗ")
        1234567890123456789
        >>> generate_deterministic_id("Справочник.ЕУЗ")  # Повторный вызов
        1234567890123456789  # Тот же результат
    """
    import hashlib
    
    # Вычисляем SHA256 хеш
    hash_bytes = hashlib.sha256(key.encode('utf-8')).digest()
    
    # Берём первые 8 байт и преобразуем в int64
    # Используем & 0x7FFFFFFFFFFFFFFF чтобы гарантировать положительное число
    id_value = int.from_bytes(hash_bytes[:8], byteorder='big') & 0x7FFFFFFFFFFFFFFF
    
    return id_value


def calculate_content_hash(content: str) -> str:
    """
    Вычисляет SHA256 хеш содержимого.
    
    Args:
        content: Строка с содержимым (код модуля, текст описания и т.д.)
    
    Returns:
        Строка с хешем в hex формате
    
    Examples:
        >>> calculate_content_hash("Процедура Тест() КонецПроцедуры")
        'a3b2c1d4e5f6...'
    """
    import hashlib
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def calculate_object_hash(metadata: Dict) -> str:
    """
    Хеш метаданных объекта для инкрементального обновления - по всем метаданным из парсера
    (реквизиты, ТЧ, измерения, ресурсы, синоним, владельцы, основания, движения, периодичность...).
    Раньше учитывались только реквизиты/ТЧ/измерения/ресурсы/комментарий, и изменение,
    например, владельцев справочника инкрементальная загрузка не замечала.
    """
    return calculate_content_hash(json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str))


def calculate_module_hash(mod: Dict) -> str:
    """
    Хеш модуля для инкрементального обновления: код + результат разбора парсером
    (границы, параметры, вызовы процедур). Так обновление парсера тоже попадает в базу,
    даже если сам код модуля не менялся.
    """
    parsed = mod.get('parsed') or {}
    parsed_items = [
        [item.get('name'), item.get('start_line'), item.get('end_line'), item.get('params'),
         item.get('is_export'), item.get('calls_to')]
        for key in ('procedures', 'functions') for item in parsed.get(key, [])
    ]
    return calculate_content_hash(mod['code'] + json.dumps(parsed_items, ensure_ascii=False, sort_keys=True))


def load_existing_hashes(client: QdrantClient, collection_name: str, scroll_batch_size: int = 1000) -> Dict[int, Dict]:
    """
    Загружает существующие хеши и метаданные из коллекции Qdrant.
    
    Args:
        client: Клиент Qdrant
        collection_name: Имя коллекции
        scroll_batch_size: Размер страницы для Scroll API (100-5000)
    
    Returns:
        Словарь {point_id: {"content_hash": hash, "object_key": key, ...}}
    
    Примечание:
        Для больших коллекций использует scroll API для постраничной загрузки
    """
    existing_data = {}
    
    try:
        # Проверяем существование коллекции
        if not client.collection_exists(collection_name):
            st.info(f"ℹ️ Коллекция {collection_name} не существует, будет создана новая")
            return existing_data
        
        # Получаем информацию о коллекции
        collection_info = client.get_collection(collection_name)
        total_points = collection_info.points_count
        
        if total_points == 0:
            st.info(f"ℹ️ Коллекция {collection_name} пустая")
            return existing_data
        
        st.info(f"📥 Загрузка метаданных из {collection_name} ({total_points} точек, батч: {scroll_batch_size})...")
        
        # Используем scroll API для постраничной загрузки
        offset = None
        batch_size = scroll_batch_size
        loaded = 0
        progress_placeholder = st.empty()
        
        while True:
            # Загружаем батч точек (только payload, без векторов)
            result = client.scroll(
                collection_name=collection_name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False  # Векторы не нужны, только payload
            )
            
            points, next_offset = result
            
            if not points:
                break
            
            # Извлекаем хеши и ключи
            for point in points:
                point_id = point.id
                payload = point.payload or {}
                
                # Извлекаем нужные поля
                existing_data[point_id] = {
                    "content_hash": payload.get("content_hash", ""),
                    "object_type": payload.get("object_type", ""),
                    "object_name": payload.get("object_name", ""),
                    "module_name": payload.get("module_name", ""),
                    "last_updated": payload.get("last_updated", 0)
                }
            
            loaded += len(points)
            progress_placeholder.caption(f"⏳ Загружено {loaded}/{total_points} точек...")
            
            # Если next_offset None, значит больше точек нет
            if next_offset is None:
                break
            
            offset = next_offset
        
        st.success(f"✅ Загружено метаданных: {len(existing_data)} точек")
        
    except Exception as e:
        st.warning(f"⚠️ Ошибка загрузки существующих данных из {collection_name}: {e}")
    
    return existing_data


def detect_changes(
    objects_data: List[Dict],
    modules_data: Dict[str, Dict],
    existing_objects_hashes: Dict[int, Dict],
    existing_modules_hashes: Dict[int, Dict]
) -> Dict:
    """
    Определяет новые, изменённые и удалённые объекты.
    
    Args:
        objects_data: Список новых объектов из CSV
        modules_data: Словарь с данными модулей {obj_key: module_info}
        existing_objects_hashes: Существующие хеши объектов из Qdrant
        existing_modules_hashes: Существующие хеши модулей из Qdrant
    
    Returns:
        Словарь со статистикой изменений:
        {
            "new_objects": [...],           # Новые объекты
            "changed_objects": [...],       # Изменённые объекты (только метаданные изменились)
            "objects_with_module_changes": [...],  # Объекты с изменениями только в модулях
            "unchanged_objects": [...],     # Без изменений
            "deleted_object_ids": [...],    # ID удалённых объектов
            "deleted_objects": [...],       # Удалённые объекты (тип, имя) для отображения
            "deleted_module_ids": [...],    # ID удалённых модулей
            "changed_module_ids": [...],    # ID изменённых модулей (для удаления чанков/процедур)
            "changed_modules": [...],       # Детали изменений модулей (new/changed/deleted)
            "change_reasons": {...}         # obj_key -> что изменилось (для отображения)
        }
    """
    result = {
        "new_objects": [],
        "changed_objects": [],
        "objects_with_module_changes": [],
        "unchanged_objects": [],
        "deleted_object_ids": [],
        "deleted_objects": [],
        "deleted_module_ids": [],
        "changed_module_ids": [],
        "changed_modules": [],
        "change_reasons": {}
    }
    
    # Создаём множество существующих ключей объектов для быстрого поиска
    existing_obj_keys = {}
    for point_id, data in existing_objects_hashes.items():
        key = f"{data['object_type']}.{data['object_name']}"
        existing_obj_keys[key] = {"point_id": point_id, **data}
    
    # Создаём множество существующих ключей модулей + индекс модулей по объекту
    # (чтобы не перебирать все модули конфигурации для каждого объекта)
    existing_mod_keys = {}
    existing_mods_by_object: Dict[str, List[str]] = {}
    for point_id, data in existing_modules_hashes.items():
        obj_key_of_module = f"{data['object_type']}.{data['object_name']}"
        key = f"{obj_key_of_module}.{data['module_name']}"
        existing_mod_keys[key] = {"point_id": point_id, **data}
        existing_mods_by_object.setdefault(obj_key_of_module, []).append(key)
    
    # Множество текущих ключей (для определения удалённых)
    current_obj_keys = set()
    current_mod_keys = set()
    
    # Анализируем каждый объект из нового архива
    for obj_data in objects_data:
        obj_type = obj_data.get('obj_type', obj_data.get('object_type'))
        obj_name = obj_data.get('obj_name', obj_data.get('object_name'))
        obj_key = f"{obj_type}.{obj_name}"
        
        current_obj_keys.add(obj_key)
        
        mod_data = modules_data.get(obj_key, {})
        for mod in mod_data.get('modules', []):
            module_name = mod['module_name']
            module_key = f"{obj_key}.{module_name}"
            current_mod_keys.add(module_key)
        
        # Проверяем существование объекта
        if obj_key not in existing_obj_keys:
            # Новый объект
            result["new_objects"].append(obj_data)
        else:
            # Объект существует - проверяем изменения в метаданных И модулях
            metadata_changed = False
            modules_changed = False
            
            # ИСПРАВЛЕНИЕ: Проверяем изменения в метаданных объекта
            metadata = obj_data.get('metadata', {})
            new_object_hash = calculate_object_hash(metadata)
            old_object_hash = existing_obj_keys[obj_key].get('content_hash', '')
            
            if old_object_hash != new_object_hash:
                metadata_changed = True
            
            # Проверяем изменения в модулях
            for mod in mod_data.get('modules', []):
                module_name = mod['module_name']
                module_key = f"{obj_key}.{module_name}"
                
                # Вычисляем хеш нового модуля
                new_hash = calculate_module_hash(mod)
                
                if module_key not in existing_mod_keys:
                    # Новый модуль в существующем объекте
                    modules_changed = True
                    result["changed_modules"].append({
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "module_name": module_name,
                        "old_hash": "none",
                        "new_hash": new_hash[:8]
                    })
                else:
                    # Модуль существует - сравниваем хеши
                    old_hash = existing_mod_keys[module_key].get('content_hash', '')
                    if old_hash != new_hash:
                        modules_changed = True
                        result["changed_module_ids"].append(existing_mod_keys[module_key]["point_id"])
                        result["changed_modules"].append({
                            "object_type": obj_type,
                            "object_name": obj_name,
                            "module_name": module_name,
                            "old_hash": old_hash[:8] if old_hash else "none",
                            "new_hash": new_hash[:8]
                        })
            
            # Проверяем удалённые модули для ЭТОГО объекта.
            # ID удалённых модулей добавляются один раз в общем цикле ниже,
            # здесь только помечаем объект и собираем детали для отображения.
            for existing_mod_key in existing_mods_by_object.get(obj_key, []):
                if existing_mod_key not in current_mod_keys:
                    modules_changed = True
                    mod_info = existing_mod_keys[existing_mod_key]
                    result["changed_modules"].append({
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "module_name": mod_info.get('module_name', 'Unknown'),
                        "old_hash": mod_info.get('content_hash', '')[:8],
                        "new_hash": "deleted"
                    })
            
            reasons = []
            if metadata_changed:
                reasons.append("метаданные")
            if modules_changed:
                reasons.append("модули")
            if reasons:
                result["change_reasons"][obj_key] = ", ".join(reasons)
            
            # Разделяем объекты с изменениями метаданных и только модулей
            if metadata_changed:
                # Если изменились метаданные - обновляем объект полностью
                result["changed_objects"].append(obj_data)
            elif modules_changed:
                # Если изменились только модули - НЕ обновляем объект, только модули
                result["objects_with_module_changes"].append(obj_data)
            else:
                result["unchanged_objects"].append(obj_data)
    
    # Определяем удалённые объекты
    for obj_key, obj_info in existing_obj_keys.items():
        if obj_key not in current_obj_keys:
            result["deleted_object_ids"].append(obj_info["point_id"])
            result["deleted_objects"].append({
                "Тип": obj_info.get("object_type", ""),
                "Имя": obj_info.get("object_name", "")
            })
    
    # Определяем удалённые модули
    for mod_key, mod_info in existing_mod_keys.items():
        if mod_key not in current_mod_keys:
            result["deleted_module_ids"].append(mod_info["point_id"])
    
    return result


def build_changes_report(changes_info: Dict) -> pd.DataFrame:
    """
    Сводная таблица изменений инкрементальной загрузки (одна строка на объект/модуль).

    Колонки: Действие, Тип, Объект, Что изменилось, Модуль
    """
    rows = []
    reasons = changes_info.get('change_reasons', {})

    def _obj_type_name(obj: Dict) -> Tuple[str, str]:
        return (obj.get('obj_type', obj.get('object_type', '')),
                obj.get('obj_name', obj.get('object_name', '')))

    for obj in changes_info.get('new_objects', []):
        obj_type, obj_name = _obj_type_name(obj)
        rows.append({'Действие': '✅ Новый', 'Тип': obj_type, 'Объект': obj_name,
                     'Что изменилось': 'новый объект', 'Модуль': ''})

    for key, action in (('changed_objects', '🔄 Изменён'), ('objects_with_module_changes', '📝 Модули')):
        for obj in changes_info.get(key, []):
            obj_type, obj_name = _obj_type_name(obj)
            rows.append({'Действие': action, 'Тип': obj_type, 'Объект': obj_name,
                         'Что изменилось': reasons.get(f"{obj_type}.{obj_name}", ''), 'Модуль': ''})

    for obj in changes_info.get('deleted_objects', []):
        rows.append({'Действие': '❌ Удалён', 'Тип': obj.get('Тип', ''), 'Объект': obj.get('Имя', ''),
                     'Что изменилось': 'объект удалён', 'Модуль': ''})

    for mod in changes_info.get('changed_modules', []):
        if mod['new_hash'] == 'deleted':
            action, what = '🗑️ Модуль удалён', 'модуль удалён'
        elif mod['old_hash'] == 'none':
            action, what = '➕ Модуль добавлен', 'новый модуль'
        else:
            action, what = '🔧 Модуль изменён', f"{mod['old_hash']}→{mod['new_hash']}"
        rows.append({'Действие': action, 'Тип': mod['object_type'], 'Объект': mod['object_name'],
                     'Что изменилось': what, 'Модуль': mod['module_name']})

    return pd.DataFrame(rows, columns=['Действие', 'Тип', 'Объект', 'Что изменилось', 'Модуль'])


def render_changes_report(changes_info: Dict, key_prefix: str = "changes"):
    """Таблица изменений с фильтрами, поиском и выгрузкой в CSV (рассчитана на тысячи строк)."""
    df = build_changes_report(changes_info)
    if df.empty:
        return

    col_action, col_type, col_search = st.columns([2, 2, 3])
    with col_action:
        actions = st.multiselect("Действие", sorted(df['Действие'].unique()), key=f"{key_prefix}_actions")
    with col_type:
        types = st.multiselect("Тип объекта", sorted(df['Тип'].unique()), key=f"{key_prefix}_types")
    with col_search:
        search = st.text_input("Поиск по имени объекта/модуля", key=f"{key_prefix}_search")

    filtered = df
    if actions:
        filtered = filtered[filtered['Действие'].isin(actions)]
    if types:
        filtered = filtered[filtered['Тип'].isin(types)]
    if search:
        needle = search.strip().lower()
        filtered = filtered[filtered['Объект'].str.lower().str.contains(needle, regex=False) |
                            filtered['Модуль'].str.lower().str.contains(needle, regex=False)]

    st.caption(f"Показано строк: {len(filtered)} из {len(df)}")
    st.dataframe(filtered, width="stretch", hide_index=True,
                 height=min(400, 35 * (len(filtered) + 1) + 3))
    st.download_button(
        "📥 Скачать отчёт об изменениях (CSV)",
        data=df.to_csv(index=False, sep=';').encode('utf-8-sig'),
        file_name="changes_report.csv",
        mime="text/csv",
        key=f"{key_prefix}_download"
    )


def display_checkpoint_info(checkpoint: Dict):
    """Отображение информации о существующем checkpoint"""
    st.info("📍 Найден сохраненный checkpoint")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.metric("Фаза", f"{checkpoint.get('phase', 0)}/3")
        st.metric("Обработано объектов", checkpoint.get('last_object_idx', 0))
    
    with col2:
        timestamp = checkpoint.get('timestamp', 0)
        time_ago = time.time() - timestamp
        minutes_ago = int(time_ago / 60)
        st.metric("Возраст checkpoint", f"{minutes_ago} мин назад")
        st.metric("Последний объект ID", checkpoint.get('last_object_id', 0))
    
    st.write("**Обработанные объекты:**")
    processed = checkpoint.get('processed_objects', [])
    if processed:
        st.text(", ".join(processed[:10]))
        if len(processed) > 10:
            st.text(f"... и еще {len(processed) - 10}")


def extract_module_name(file_path: str) -> str:
    """
    Извлечение читаемого имени модуля из пути к файлу.
    
    Примеры:
    - modules/Справочник/ЕУЗ/МодульФормы_ФормаСпискаWEB.bsl -> МодульФормы_ФормаСпискаWEB
    - modules/Справочник/ЕУЗ/МодульОбъекта.bsl -> МодульОбъекта
    - modules/Документ/ЗаказПокупателя/МодульМенеджера.bsl -> МодульМенеджера
    """
    path = Path(file_path)
    # Получаем имя файла без расширения
    module_name = path.stem
    return module_name


def get_module_display_name(module_meta: Dict) -> str:
    """
    Получение читаемого имени модуля для отображения.
    
    Приоритет:
    1. Имя из file_path (если есть)
    2. module_type (как fallback)
    """
    file_path = module_meta.get('file_path', '')
    if file_path:
        return extract_module_name(file_path)
    return module_meta.get('module_type', 'UnknownModule')


EMBEDDING_SERVICE_WAIT_SECONDS = int(os.getenv("EMBEDDING_SERVICE_WAIT_SECONDS", "600"))


def wait_for_embedding_service(max_wait: int = EMBEDDING_SERVICE_WAIT_SECONDS) -> bool:
    """
    Ожидание готовности сервиса эмбеддингов (GET /health) после потери соединения.
    
    Returns:
        True - сервис снова доступен, False - не дождались за max_wait секунд
    """
    message = st.empty()
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            resp = requests.get(f"{EMBEDDING_SERVICE_URL}/health", timeout=10)
            if resp.ok and resp.json().get("status") == "healthy":
                message.empty()
                logger.info("Сервис эмбеддингов снова доступен, продолжаем загрузку")
                return True
        except Exception:
            pass
        left = int(deadline - time.time())
        message.warning(f"⏳ Сервис эмбеддингов недоступен (перезапуск?), ждём его готовности... осталось до {left}с")
        logger.warning("Сервис эмбеддингов недоступен, ожидание готовности")
        time.sleep(10)
    message.empty()
    return False


def get_embeddings(texts: List[str], task: str = "retrieval.passage", 
                   max_retries: int = 3, base_timeout: int = 180) -> List[List[float]]:
    """
    Получение эмбеддингов с увеличенным таймаутом и retry логикой
    
    Args:
        texts: Список текстов для эмбеддинга
        task: Тип задачи для эмбеддинга
        max_retries: Максимальное количество повторных попыток (1-10)
        base_timeout: Базовый таймаут в секундах (60-600)
        
    Returns:
        Список векторов эмбеддингов
    """
    headers = {
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip'
    }

    # Динамический таймаут: 10 секунд на текст + базовый таймаут (макс. 600)
    dynamic_timeout = min(base_timeout + len(texts) * 10, 600)

    def _post(text_batch: List[str]) -> List[List[float]]:
        payload = json.dumps({"texts": text_batch, "task": task})
        for attempt in range(max_retries):
            try:
                resp = _embed_session.post(
                    f"{EMBEDDING_SERVICE_URL}/embed",
                    headers=headers,
                    data=payload,
                    timeout=dynamic_timeout
                )
                # Handle 413/422 etc. with adaptive split
                if resp.status_code in (413, 422):
                    raise requests.HTTPError(f"{resp.status_code} Request too large or unprocessable")
                resp.raise_for_status()
                return resp.json()["embeddings"]
            except requests.exceptions.ConnectionError as e:
                # Сервис эмбеддингов перезапускается (рестарт контейнера, загрузка модели ~1-2 мин).
                # Ждём его готовности, а не обрываем многочасовую загрузку.
                if attempt < max_retries - 1 and wait_for_embedding_service():
                    continue
                raise
            except requests.exceptions.ReadTimeout as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 30
                    st.warning(f"⏱️ Таймаут эмбеддингов ({attempt + 1}/{max_retries}). Повтор через {wait_time}с...")
                    time.sleep(wait_time)
                else:
                    raise
            except requests.HTTPError as e:
                # Adaptive split on size/memory errors
                if len(text_batch) > 1:
                    mid = len(text_batch) // 2
                    left = _post(text_batch[:mid])
                    right = _post(text_batch[mid:])
                    return left + right
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                    continue
                raise

    try:
        return _post(texts)
    except Exception as e:
        st.error(f"❌ Ошибка получения эмбеддингов: {str(e)}")
        raise


class EmbeddingBatcher:
    """
    Батчер для массовой обработки эмбеддингов.
    
    Стратегия:
    1. Накапливаем ВСЕ тексты для эмбеддинга
    2. Делаем несколько больших батч-запросов (вместо тысяч мелких)
    3. Распределяем эмбеддинги по соответствующим объектам
    
    Это даёт ускорение в 10-20 раз за счёт:
    - Снижения HTTP overhead
    - Эффективной работы модели с большими батчами
    - Параллельной обработки на стороне сервера
    """
    
    def __init__(self, batch_size: int = 150, task: str = "retrieval.passage",
                 max_retries: int = 3, base_timeout: int = 180):
        """
        Args:
            batch_size: Размер батча для одного запроса (8-500)
            task: Тип задачи для эмбеддинга
            max_retries: Максимальное количество повторных попыток (1-10)
            base_timeout: Базовый таймаут в секундах (60-600)
        """
        self.batch_size = batch_size
        self.task = task
        self.max_retries = max_retries
        self.base_timeout = base_timeout
        self.texts = []
        self.metadata = []  # Метаданные каждого текста (куда относится)
        
    def add(self, text: str, meta: Dict) -> int:
        """
        Добавить текст для эмбеддинга.
        
        Args:
            text: Текст для эмбеддинга
            meta: Метаданные (ID, тип, назначение и т.д.)
        
        Returns:
            Индекс текста в батче
        """
        idx = len(self.texts)
        self.texts.append(text)
        self.metadata.append(meta)
        return idx
    
    def add_multiple(self, texts: List[str], meta_list: List[Dict]) -> List[int]:
        """
        Добавить несколько текстов за раз.
        
        Args:
            texts: Список текстов
            meta_list: Список метаданных (должен быть той же длины)
        
        Returns:
            Список индексов
        """
        if len(texts) != len(meta_list):
            raise ValueError("texts и meta_list должны быть одинаковой длины")
        
        indices = []
        for text, meta in zip(texts, meta_list):
            indices.append(self.add(text, meta))
        return indices
    
    def process_all(self, progress_callback=None, verbose: bool = False) -> List[List[float]]:
        """
        Обработать все накопленные тексты.

        Args:
            progress_callback: Функция для отображения прогресса (опционально)
                              Вызывается с (current, total)
            verbose: Выводить в UI сообщение по каждому батчу (в лог контейнера пишется всегда)

        Returns:
            Список эмбеддингов в том же порядке, что и тексты
        """
        if not self.texts:
            return []
        
        all_embeddings = []
        total_batches = (len(self.texts) + self.batch_size - 1) // self.batch_size
        
        logger.info(f"Обработка {len(self.texts)} текстов в {total_batches} батчах (размер батча: {self.batch_size})")
        if verbose:
            st.info(f"📦 Обработка {len(self.texts)} текстов в {total_batches} батчах (размер батча: {self.batch_size})")

        for batch_idx in range(total_batches):
            start_idx = batch_idx * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(self.texts))
            
            batch_texts = self.texts[start_idx:end_idx]
            batch_start_time = time.time()
            
            if verbose:
                st.write(f"⏳ Обработка батча {batch_idx + 1}/{total_batches} ({len(batch_texts)} текстов)...")

            # Получаем эмбеддинги для батча с настроенными параметрами
            batch_embeddings = get_embeddings(batch_texts, task=self.task, 
                                             max_retries=self.max_retries, 
                                             base_timeout=self.base_timeout)
            all_embeddings.extend(batch_embeddings)
            
            batch_time = time.time() - batch_start_time
            logger.info(f"Батч {batch_idx + 1}/{total_batches} обработан за {batch_time:.1f}с")
            if verbose:
                st.success(f"✅ Батч {batch_idx + 1}/{total_batches} обработан за {batch_time:.1f}с")

            # Прогресс
            if progress_callback:
                progress_callback(end_idx, len(self.texts))
        
        return all_embeddings
    
    def get_embeddings_by_meta(self, embeddings: List[List[float]], 
                               filter_fn) -> Dict[int, List[float]]:
        """
        Получить эмбеддинги, отфильтрованные по метаданным.
        
        Args:
            embeddings: Список всех эмбеддингов (результат process_all)
            filter_fn: Функция-фильтр, принимающая meta, возвращающая bool
        
        Returns:
            Словарь {index: embedding} для прошедших фильтр
        """
        result = {}
        for idx, (emb, meta) in enumerate(zip(embeddings, self.metadata)):
            if filter_fn(meta):
                result[idx] = emb
        return result
    
    def clear(self):
        """Очистить батчер для повторного использования."""
        self.texts = []
        self.metadata = []
    
    def __len__(self):
        """Количество накопленных текстов."""
        return len(self.texts)


def extract_module_context(code: str, max_lines: int = 50) -> str:
    """
    Извлечь контекст модуля: imports, переменные модуля, комментарии в начале.
    
    Args:
        code: Полный код модуля
        max_lines: Максимум строк для контекста
    
    Returns:
        Текст контекста модуля
    """
    lines = code.split('\n')
    context_lines = []
    
    for i, line in enumerate(lines[:max_lines]):
        stripped = line.strip()
        
        # Останавливаемся на первой процедуре/функции
        if stripped.startswith(('Процедура ', 'Procedure ', 'Функция ', 'Function ')):
            break
        
        # Собираем imports, переменные, комментарии
        if stripped.startswith(('&', '//', 'Перем ', 'Var ')):
            context_lines.append(line)
    
    return '\n'.join(context_lines) if context_lines else ''


def chunk_by_procedures(code: str, parsed_items: Dict, max_chunk_lines: int = 500, 
                        overlap_lines: int = 50, max_context_lines: int = 50) -> List[Dict]:
    """
    СЕМАНТИЧЕСКОЕ чанкование кода по процедурам/функциям.
    
    Стратегия:
    1. Каждая процедура/функция = отдельный чанк
    2. Длинные процедуры (>max_chunk_lines) разбиваются с overlap
    3. Код вне процедур = отдельные чанки
    4. К каждому чанку добавляется контекст модуля
    
    Args:
        code: Полный код модуля
        parsed_items: Словарь с ключами 'procedures' и 'functions' из modules_metadata.json
        max_chunk_lines: Максимум строк в чанке процедуры
        overlap_lines: Overlap для длинных процедур
        max_context_lines: Размер контекста модуля (строки из начала)
    
    Returns:
        Список чанков с метаданными
    """
    lines = code.split('\n')
    total_lines = len(lines)
    chunks = []
    chunk_index = 0
    
    # Извлекаем контекст модуля (imports, переменные)
    module_context = extract_module_context(code, max_context_lines)
    
    # Собираем все процедуры и функции в один список с позициями
    all_items = []
    
    for proc in parsed_items.get('procedures', []):
        all_items.append({
            'type': 'Процедура',
            'name': proc['name'],
            'start_line': proc['start_line'],
            'end_line': proc['end_line'],
            'is_export': proc['is_export'],
            'signature': proc['signature'],
            'comments': proc.get('comments', ''),
            'full_code': proc.get('full_code', ''),
            'calls_to': proc.get('calls_to', [])
        })
    
    for func in parsed_items.get('functions', []):
        all_items.append({
            'type': 'Функция',
            'name': func['name'],
            'start_line': func['start_line'],
            'end_line': func['end_line'],
            'is_export': func['is_export'],
            'signature': func['signature'],
            'comments': func.get('comments', ''),
            'full_code': func.get('full_code', ''),
            'calls_to': func.get('calls_to', [])
        })
    
    # Сортируем по start_line
    all_items.sort(key=lambda x: x['start_line'])
    
    # Обрабатываем каждую процедуру/функцию
    for item in all_items:
        proc_lines = item['end_line'] - item['start_line'] + 1
        
        # Если процедура короткая - один чанк
        if proc_lines <= max_chunk_lines:
            # Код БЕЗ контекста модуля (переменные не нужны)
            chunk_code = item['full_code']
            
            chunks.append({
                "chunk_index": chunk_index,
                "chunk_type": "procedure",
                "start_line": item['start_line'],
                "end_line": item['end_line'],
                "code": chunk_code,
                "context": module_context,
                "procedure_name": item['name'],
                "procedure_type": item['type'],
                "procedure_signature": item['signature'],
                "is_export": item['is_export'],
                "comments": item['comments'],
                "calls_to": item['calls_to'],
                "is_partial": False
            })
            chunk_index += 1
        else:
            # Длинная процедура - разбиваем на части с overlap
            proc_start = item['start_line'] - 1  # 0-based
            proc_end = item['end_line']  # 1-based, exclusive при slice
            
            part_index = 0
            current_pos = proc_start
            
            while current_pos < proc_end:
                part_end = min(current_pos + max_chunk_lines, proc_end)
                
                part_code = '\n'.join(lines[current_pos:part_end])
                
                # Контекст модуля НЕ добавляем (переменные не нужны)
                
                chunks.append({
                    "chunk_index": chunk_index,
                    "chunk_type": "procedure_part",
                    "start_line": current_pos + 1,
                    "end_line": part_end,
                    "code": part_code,
                    "context": module_context if part_index == 0 else "",
                    "procedure_name": item['name'],
                    "procedure_type": item['type'],
                    "procedure_signature": item['signature'],
                    "is_export": item['is_export'],
                    "is_partial": True,
                    "part_index": part_index,
                    "total_parts": 0  # Обновим после
                })
                chunk_index += 1
                part_index += 1
                
                # Overlap для следующей части
                current_pos = part_end - overlap_lines if part_end < proc_end else part_end
            
            # Обновляем total_parts
            for i in range(len(chunks) - part_index, len(chunks)):
                chunks[i]["total_parts"] = part_index
    
    # Если нет процедур вообще - весь код одним чанком
    if not all_items:
        chunks.append({
            "chunk_index": 0,
            "chunk_type": "full",
            "start_line": 1,
            "end_line": total_lines,
            "code": code,
            "context": "",
            "procedure_name": None,
            "procedure_type": None,
            "is_export": False
        })
    
    # Обновляем total_chunks
    total_chunks = len(chunks)
    for chunk in chunks:
        chunk["total_chunks"] = total_chunks
    
    return chunks


def chunk_code(code: str, max_lines: int = CHUNK_SIZE) -> List[Dict]:
    """
    УСТАРЕВШАЯ функция механического чанкования.
    Оставлена для обратной совместимости.
    Используйте chunk_by_procedures() для семантического чанкования.
    """
    lines = code.split('\n')
    total_lines = len(lines)
    
    if total_lines <= MIN_LINES_FOR_CHUNKING:
        return [{
            "chunk_index": 0,
            "total_chunks": 1,
            "start_line": 1,
            "end_line": total_lines,
            "code": code
        }]
    
    chunks = []
    current_line = 0
    chunk_index = 0
    
    while current_line < total_lines:
        end_line = min(current_line + max_lines, total_lines)
        
        # Пытаемся найти конец процедуры/функции для более чистого разбиения
        if end_line < total_lines:
            for i in range(end_line, current_line, -1):
                line = lines[i].strip()
                if line.startswith('КонецПроцедуры') or line.startswith('КонецФункции') or \
                   line.startswith('EndProcedure') or line.startswith('EndFunction'):
                    end_line = i + 1
                    break
        
        chunk_code = '\n'.join(lines[current_line:end_line])
        
        chunks.append({
            "chunk_index": chunk_index,
            "total_chunks": 0,  # Обновим позже
            "start_line": current_line + 1,
            "end_line": end_line,
            "code": chunk_code
        })
        
        current_line = end_line
        chunk_index += 1
    
    # Обновляем total_chunks
    total_chunks = len(chunks)
    for chunk in chunks:
        chunk["total_chunks"] = total_chunks
    
    return chunks


def load_object_metadata(obj_type: str, obj_name: str, metadata_dir: Path) -> Dict:
    """
    Загрузка JSON метаданных объекта из папки metadata_json.
    
    Args:
        obj_type: Тип объекта (Справочник, Документ и т.д.)
        obj_name: Имя объекта
        metadata_dir: Корневая директория распакованных данных
    
    Returns:
        Dict с метаданными или пустой dict
    """
    metadata_file = metadata_dir / "metadata" / obj_type / f"{obj_name}.json"
    
    if metadata_file.exists():
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            st.warning(f"Ошибка загрузки метаданных {metadata_file}: {e}")
            return {}
    
    return {}


def create_structure_text(metadata: Dict) -> str:
    """
    Создание текстового представления структуры объекта для эмбеддинга.
    Включает: стандартные реквизиты, реквизиты, табличные части, ресурсы, измерения.
    """
    parts = []
    
    # Стандартные реквизиты
    std_attrs = metadata.get('СтандартныеРеквизиты', metadata.get('standard_attributes', []))
    if std_attrs:
        parts.append("Стандартные реквизиты: " + ", ".join(std_attrs))
    
    # Реквизиты
    attributes = metadata.get('Реквизиты', metadata.get('attributes', []))
    for attr in attributes:
        attr_name = attr.get('Имя', attr.get('name', ''))
        attr_syn = attr.get('Синоним', attr.get('synonym', attr_name))
        attr_type = attr.get('Тип', attr.get('type', ''))
        attr_comment = attr.get('Комментарий', attr.get('comment', ''))
        
        text = f"{attr_name} ({attr_syn}): {attr_type}"
        if attr_comment:
            text += f" - {attr_comment}"
        parts.append(text)
    
    # Табличные части
    tabular_sections = metadata.get('ТабличныеЧасти', metadata.get('tabular_sections', []))
    for ts in tabular_sections:
        ts_name = ts.get('Имя', ts.get('name', ''))
        ts_syn = ts.get('Синоним', ts.get('synonym', ts_name))
        ts_attrs = ts.get('Реквизиты', ts.get('attributes', []))
        
        ts_attrs_text = []
        for tsa in ts_attrs:
            tsa_name = tsa.get('Имя', tsa.get('name', ''))
            tsa_type = tsa.get('Тип', tsa.get('type', ''))
            ts_attrs_text.append(f"{tsa_name} ({tsa_type})")
        
        parts.append(f"ТЧ {ts_name} ({ts_syn}): {', '.join(ts_attrs_text)}")
    
    # Ресурсы (для регистров)
    resources = metadata.get('Ресурсы', metadata.get('resources', []))
    for res in resources:
        res_name = res.get('Имя', res.get('name', ''))
        res_syn = res.get('Синоним', res.get('synonym', res_name))
        res_type = res.get('Тип', res.get('type', ''))
        parts.append(f"Ресурс {res_name} ({res_syn}): {res_type}")
    
    # Измерения (для регистров)
    dimensions = metadata.get('Измерения', metadata.get('dimensions', []))
    for dim in dimensions:
        dim_name = dim.get('Имя', dim.get('name', ''))
        dim_syn = dim.get('Синоним', dim.get('synonym', dim_name))
        dim_type = dim.get('Тип', dim.get('type', ''))
        parts.append(f"Измерение {dim_name} ({dim_syn}): {dim_type}")
    
    return " | ".join(parts) if parts else "Структура не определена"


def create_relationships_text(metadata: Dict) -> str:
    """
    Создание текстового представления связей объекта для эмбеддинга.
    Включает: владельцев, основания, движения регистров.
    """
    parts = []
    
    # Владельцы
    owners = metadata.get('Владельцы', metadata.get('owners', []))
    if owners:
        parts.append("Владельцы: " + ", ".join(owners))
    
    # Основан на
    based_on = metadata.get('ОснованНа', metadata.get('based_on', []))
    if based_on:
        parts.append("Основан на: " + ", ".join(based_on))
    
    # Заполняемые регистры
    register_records = metadata.get('Регистры', metadata.get('register_records', []))
    if register_records:
        parts.append("Движения регистров: " + ", ".join(register_records))
    
    return " | ".join(parts) if parts else "Нет связей"


def extract_all_types(metadata: Dict) -> List[str]:
    """
    Извлечение всех используемых типов данных из метаданных объекта.
    Полезно для поиска объектов, использующих определённые типы.
    """
    types = set()
    
    # Из реквизитов
    attributes = metadata.get('Реквизиты', metadata.get('attributes', []))
    for attr in attributes:
        attr_type = attr.get('Тип', attr.get('type', ''))
        if attr_type:
            types.add(attr_type)
    
    # Из табличных частей
    tabular_sections = metadata.get('ТабличныеЧасти', metadata.get('tabular_sections', []))
    for ts in tabular_sections:
        ts_attrs = ts.get('Реквизиты', ts.get('attributes', []))
        for tsa in ts_attrs:
            tsa_type = tsa.get('Тип', tsa.get('type', ''))
            if tsa_type:
                types.add(tsa_type)
    
    # Из ресурсов
    resources = metadata.get('Ресурсы', metadata.get('resources', []))
    for res in resources:
        res_type = res.get('Тип', res.get('type', ''))
        if res_type:
            types.add(res_type)
    
    # Из измерений
    dimensions = metadata.get('Измерения', metadata.get('dimensions', []))
    for dim in dimensions:
        dim_type = dim.get('Тип', dim.get('type', ''))
        if dim_type:
            types.add(dim_type)
    
    return sorted(list(types))


def count_metadata_elements(metadata: Dict) -> Dict[str, int]:
    """
    Подсчёт количества элементов метаданных для быстрой фильтрации.
    """
    return {
        "attributes_count": len(metadata.get('Реквизиты', metadata.get('attributes', []))),
        "tabular_sections_count": len(metadata.get('ТабличныеЧасти', metadata.get('tabular_sections', []))),
        "resources_count": len(metadata.get('Ресурсы', metadata.get('resources', []))),
        "dimensions_count": len(metadata.get('Измерения', metadata.get('dimensions', []))),
        "standard_attributes_count": len(metadata.get('СтандартныеРеквизиты', metadata.get('standard_attributes', []))),
        "indexed_fields_count": count_indexed_fields(metadata)
    }


INDEXED_VALUES = ("Индексировать", "Индексировать с доп. упорядочиванием", "Index", "IndexWithAdditionalOrder")


def is_indexed_field(field: Dict) -> bool:
    """Реквизит/измерение/ресурс с включённым свойством «Индексирование»."""
    return field.get('Индексирование', field.get('indexing')) in INDEXED_VALUES


def count_indexed_fields(metadata: Dict) -> int:
    """
    Количество индексируемых полей объекта (реквизиты, измерения, ресурсы, реквизиты ТЧ).
    Учитывается только явно заданное свойство «Индексирование» (выгружается парсером).
    """
    fields = []
    for key_ru, key_en in (('Реквизиты', 'attributes'), ('Ресурсы', 'resources'), ('Измерения', 'dimensions')):
        fields.extend(metadata.get(key_ru, metadata.get(key_en, [])) or [])
    for ts in metadata.get('ТабличныеЧасти', metadata.get('tabular_sections', [])) or []:
        fields.extend(ts.get('Реквизиты', ts.get('attributes', [])) or [])
    return sum(1 for f in fields if isinstance(f, dict) and is_indexed_field(f))


def find_vector_size_mismatches(client: QdrantClient, vector_size: int) -> List[Tuple[str, int]]:
    """
    Проверка размерности векторов существующих коллекций.
    
    Returns:
        Список (коллекция[/вектор], размерность) для векторов, размерность которых отличается от vector_size
    """
    mismatches = []
    for collection_name in (COLLECTION_OBJECTS, COLLECTION_MODULES, COLLECTION_CHUNKS, COLLECTION_PROCEDURES):
        if not client.collection_exists(collection_name):
            continue
        vectors = client.get_collection(collection_name).config.params.vectors
        if isinstance(vectors, dict):
            for vector_name, params in vectors.items():
                if params.size != vector_size:
                    mismatches.append((f"{collection_name}/{vector_name}", params.size))
        elif vectors is not None and vectors.size != vector_size:
            mismatches.append((collection_name, vectors.size))
    return mismatches


def create_collections(client: QdrantClient, vector_size: int = 768, incremental: bool = False):
    """
    Создание или проверка коллекций в Qdrant
    
    Args:
        client: Клиент Qdrant
        vector_size: Размер векторов эмбеддингов
        incremental: Если True - не удаляет существующие коллекции (инкрементальный режим)
                    Если False - удаляет и создаёт заново (полная перезагрузка)
    """
    
    if incremental:
        st.info("🔧 Проверка коллекций (инкрементальный режим)...")
    else:
        st.info("🔧 Создание/обновление коллекций (полная перезагрузка)...")
    
    # 1. Коллекция объектов с РАСШИРЕННЫМИ векторами
    if client.collection_exists(COLLECTION_OBJECTS):
        if not incremental:
            client.delete_collection(COLLECTION_OBJECTS)
            client.create_collection(
                collection_name=COLLECTION_OBJECTS,
                vectors_config={
                    "object_name": VectorParams(size=vector_size, distance=Distance.COSINE),
                    "friendly_name": VectorParams(size=vector_size, distance=Distance.COSINE),
                    "description": VectorParams(size=vector_size, distance=Distance.COSINE),
                    "structure": VectorParams(size=vector_size, distance=Distance.COSINE),
                    "relationships": VectorParams(size=vector_size, distance=Distance.COSINE)
                },
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=0  # Отключить индексацию во время загрузки
                )
            )
            st.success(f"✅ Коллекция {COLLECTION_OBJECTS} пересоздана (5 векторов, индексация отключена)")
        else:
            st.success(f"✅ Коллекция {COLLECTION_OBJECTS} существует (обновление)")
    else:
        client.create_collection(
            collection_name=COLLECTION_OBJECTS,
            vectors_config={
                "object_name": VectorParams(size=vector_size, distance=Distance.COSINE),
                "friendly_name": VectorParams(size=vector_size, distance=Distance.COSINE),
                "description": VectorParams(size=vector_size, distance=Distance.COSINE),
                "structure": VectorParams(size=vector_size, distance=Distance.COSINE),
                "relationships": VectorParams(size=vector_size, distance=Distance.COSINE)
            },
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=0  # Отключить индексацию во время загрузки
            )
        )
        st.success(f"✅ Коллекция {COLLECTION_OBJECTS} создана (5 векторов, индексация отключена)")
    
    # 2. Коллекция модулей
    if client.collection_exists(COLLECTION_MODULES):
        if not incremental:
            client.delete_collection(COLLECTION_MODULES)
            client.create_collection(
                collection_name=COLLECTION_MODULES,
                vectors_config={
                    "code": VectorParams(size=vector_size, distance=Distance.COSINE)
                },
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=0  # Отключить индексацию во время загрузки
                )
            )
            st.success(f"✅ Коллекция {COLLECTION_MODULES} пересоздана (индексация отключена)")
        else:
            st.success(f"✅ Коллекция {COLLECTION_MODULES} существует (обновление)")
    else:
        client.create_collection(
            collection_name=COLLECTION_MODULES,
            vectors_config={
                "code": VectorParams(size=vector_size, distance=Distance.COSINE)
            },
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=0  # Отключить индексацию во время загрузки
            )
        )
        st.success(f"✅ Коллекция {COLLECTION_MODULES} создана (индексация отключена)")
    
    # 3. Коллекция чанков кода
    if client.collection_exists(COLLECTION_CHUNKS):
        if not incremental:
            client.delete_collection(COLLECTION_CHUNKS)
            client.create_collection(
                collection_name=COLLECTION_CHUNKS,
                vectors_config={
                    "code": VectorParams(size=vector_size, distance=Distance.COSINE)
                },
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=0  # Отключить индексацию во время загрузки
                )
            )
            st.success(f"✅ Коллекция {COLLECTION_CHUNKS} пересоздана (индексация отключена)")
        else:
            st.success(f"✅ Коллекция {COLLECTION_CHUNKS} существует (обновление)")
    else:
        client.create_collection(
            collection_name=COLLECTION_CHUNKS,
            vectors_config={
                "code": VectorParams(size=vector_size, distance=Distance.COSINE)
            },
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=0  # Отключить индексацию во время загрузки
            )
        )
        st.success(f"✅ Коллекция {COLLECTION_CHUNKS} создана (индексация отключена)")
    
    # 4. НОВАЯ: Коллекция процедур и функций
    if client.collection_exists(COLLECTION_PROCEDURES):
        if not incremental:
            client.delete_collection(COLLECTION_PROCEDURES)
            client.create_collection(
                collection_name=COLLECTION_PROCEDURES,
                vectors_config={
                    "code": VectorParams(size=vector_size, distance=Distance.COSINE)
                },
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=0  # Отключить индексацию во время загрузки
                )
            )
            st.success(f"✅ Коллекция {COLLECTION_PROCEDURES} пересоздана (индексация отключена)")
        else:
            st.success(f"✅ Коллекция {COLLECTION_PROCEDURES} существует (обновление)")
    else:
        client.create_collection(
            collection_name=COLLECTION_PROCEDURES,
            vectors_config={
                "code": VectorParams(size=vector_size, distance=Distance.COSINE)
            },
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=0  # Отключить индексацию во время загрузки
            )
        )
        st.success(f"✅ Коллекция {COLLECTION_PROCEDURES} создана (индексация отключена)")


def load_modules_metadata(metadata_dir: Path) -> Optional[Dict]:
    """
    Загрузка modules_metadata.json для использования данных о процедурах/функциях.
    
    Args:
        metadata_dir: Корневая директория распакованных данных
    
    Returns:
        Dict с индексом модулей: {f"{object_type}.{object_name}.{module_type}": module_data}
        или None если файл не найден
    """
    metadata_file = metadata_dir / "modules_metadata.json"
    
    if not metadata_file.exists():
        return None
    
    try:
        with open(metadata_file, 'r', encoding='utf-8') as f:
            modules_data = json.load(f)
        
        # Создаем индекс для быстрого поиска.
        # Основной ключ - имя файла модуля: module_type у нескольких форм объекта одинаковый
        # (FormModule.Ext), и ключ по нему терял все формы, кроме последней.
        index = {}
        for module in modules_data:
            prefix = f"{module['object_type']}.{module['object_name']}"
            file_stem = Path(module.get('file_path', '').replace('\\', '/')).stem
            if file_stem:
                index[f"{prefix}.{file_stem}"] = module
            index.setdefault(f"{prefix}.{module['module_type']}", module)
        
        return index
    except Exception as e:
        st.warning(f"⚠️ Не удалось загрузить modules_metadata.json: {e}")
        return None


def find_module_in_metadata(
    modules_metadata: Dict,
    obj_type: str,
    obj_name: str,
    module_name: str
) -> Optional[Dict]:
    """
    Найти модуль в modules_metadata.json по object_type, object_name, module_name.
    
    Args:
        modules_metadata: Индекс модулей из load_modules_metadata()
        obj_type: Тип объекта
        obj_name: Имя объекта
        module_name: Имя модуля (например, "МодульМенеджера" или "ManagerModule")
    
    Returns:
        Dict с данными модуля или None если не найден
    """
    # Пробуем разные варианты ключей
    # 1. Прямое соответствие (если module_name = module_type из JSON)
    key = f"{obj_type}.{obj_name}.{module_name}"
    if key in modules_metadata:
        return modules_metadata[key]
    
    # 2. Пробуем найти по file_path (если module_name - это имя файла без расширения)
    # Ищем модули этого объекта
    for key, module in modules_metadata.items():
        if key.startswith(f"{obj_type}.{obj_name}."):
            # Проверяем file_path
            file_path = module.get('file_path', '')
            if file_path.endswith(f"/{module_name}.bsl") or file_path.endswith(f"\\{module_name}.bsl"):
                return module
    
    return None


def process_object_modules(
    obj_type: str, 
    obj_name: str, 
    modules_dir: Path, 
    expected_count: int = None,
    max_chunk_lines: int = 500,
    overlap_lines: int = 50,
    max_context_lines: int = 50,
    modules_metadata: Optional[Dict] = None
) -> Dict:
    """
    Обработка модулей одного объекта (для параллельного выполнения).
    
    Args:
        obj_type: Тип объекта
        obj_name: Имя объекта
        modules_dir: Корневая папка modules
        expected_count: Ожидаемое количество модулей (из CSV)
        max_chunk_lines: Максимальное количество строк в чанке
        overlap_lines: Количество строк перекрытия между чанками
        max_context_lines: Максимальное количество строк контекста
    
    Returns:
        Словарь с информацией о модулях объекта
    """
    result = {
        'obj_type': obj_type,
        'obj_name': obj_name,
        'modules': [],
        'expected_count': expected_count,
        'warning': None,
        'processing_errors': {}
    }
    
    obj_modules_dir = modules_dir / obj_type / obj_name
    
    if not obj_modules_dir.exists() or not obj_modules_dir.is_dir():
        if expected_count and expected_count > 0:
            result['warning'] = f"Ожидалось {expected_count} модулей, но папка не найдена: {obj_modules_dir}"
        return result
    
    all_module_files = list(obj_modules_dir.glob("*.bsl"))
    
    for module_file in all_module_files:
        try:
            module_code = module_file.read_text(encoding='utf-8-sig')
            module_display_name = extract_module_name(str(module_file))
            module_file_stem = module_file.stem  # Например, "МодульМенеджера"
            
            # Используем данные из modules_metadata.json
            if not modules_metadata:
                result['warning'] = f"modules_metadata.json не загружен, пропускаем модуль {module_file_stem}"
                continue
            
            module_meta = find_module_in_metadata(
                modules_metadata, obj_type, obj_name, module_file_stem
            )
            
            if not module_meta:
                result['warning'] = f"Модуль {module_file_stem} не найден в modules_metadata.json, пропускаем"
                continue
            
            # Преобразуем формат из JSON в формат для chunk_by_procedures
            parsed = {
                'procedures': [],
                'functions': []
            }
            
            lines = module_code.split('\n')
            
            # Обрабатываем процедуры
            for proc in module_meta.get('procedures', []):
                start_line = proc['start_line']
                end_line = proc['end_line']
                
                # Извлекаем полный код процедуры (включая комментарии/директивы)
                proc_code_lines = lines[start_line - 1:end_line]  # 1-based -> 0-based
                proc_code = '\n'.join(proc_code_lines)
                
                # Создаем signature из name и params
                params_str = ', '.join(proc.get('params', []))
                signature = f"{proc['name']}({params_str})"
                
                parsed['procedures'].append({
                    'name': proc['name'],
                    'type': 'Процедура',
                    'is_export': proc.get('is_export', False),
                    'params': proc.get('params', []),
                    'start_line': start_line,
                    'end_line': end_line,
                    'signature': signature,
                    'comments': '',  # Комментарии уже включены в start_line
                    'full_code': proc_code,
                    'calls_to': proc.get('calls_to', [])  # Берем из JSON, если есть
                })
            
            # Обрабатываем функции
            for func in module_meta.get('functions', []):
                start_line = func['start_line']
                end_line = func['end_line']
                
                # Извлекаем полный код функции (включая комментарии/директивы)
                func_code_lines = lines[start_line - 1:end_line]  # 1-based -> 0-based
                func_code = '\n'.join(func_code_lines)
                
                # Создаем signature из name и params
                params_str = ', '.join(func.get('params', []))
                signature = f"{func['name']}({params_str})"
                
                parsed['functions'].append({
                    'name': func['name'],
                    'type': 'Функция',
                    'is_export': func.get('is_export', False),
                    'params': func.get('params', []),
                    'start_line': start_line,
                    'end_line': end_line,
                    'signature': signature,
                    'comments': '',  # Комментарии уже включены в start_line
                    'full_code': func_code,
                    'calls_to': func.get('calls_to', [])  # Берем из JSON, если есть
                })
            
            # Семантическое чанкование с настраиваемыми параметрами
            code_chunks = chunk_by_procedures(module_code, parsed, max_chunk_lines, overlap_lines, max_context_lines)
            
            result['modules'].append({
                'file_path': str(module_file),
                'module_name': module_display_name,
                'module_type': module_file_stem,
                'code': module_code,
                'parsed': parsed,
                'chunks': code_chunks,
                'lines_count': len(module_code.split('\n'))
            })
        except Exception as e:
            error_msg = f"⚠️ Ошибка обработки модуля {module_file.name}: {e}"
            import traceback
            error_traceback = traceback.format_exc()
            
            # Сохраняем ошибку для детального вывода
            result['processing_errors'][module_file_stem] = {
                'error': str(e),
                'traceback': error_traceback,
                'file_name': module_file.name
            }
            
            # Продолжаем обработку других модулей, но не добавляем этот модуль в результат
    
    # Проверяем соответствие ожидаемому количеству
    if expected_count is not None and len(result['modules']) != expected_count:
        result['warning'] = f"Ожидалось {expected_count} модулей, найдено {len(result['modules'])}"
        
        # Сохраняем детальную информацию для вывода после обработки
        result['found_files_count'] = len(all_module_files)
        result['found_file_names'] = [f.name for f in all_module_files]
    
    return result


def upsert_with_retry(
    client: QdrantClient,
    collection_name: str,
    points: List[PointStruct],
    max_retries: int = 3,
    retry_delay: int = 5
) -> bool:
    """
    Загрузка точек в Qdrant с retry логикой для обработки timeout'ов.
    
    Args:
        client: Клиент Qdrant
        collection_name: Имя коллекции
        points: Список точек для загрузки
        max_retries: Максимальное количество повторных попыток
        retry_delay: Задержка между попытками (секунды)
    
    Returns:
        True если загрузка успешна, False иначе
    """
    for attempt in range(max_retries):
        try:
            client.upsert(collection_name=collection_name, points=points)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                st.warning(f"⚠️ Попытка {attempt + 1}/{max_retries} не удалась: {e}. Повтор через {retry_delay} сек...")
                time.sleep(retry_delay)
            else:
                st.error(f"❌ Не удалось загрузить {len(points)} точек в {collection_name} после {max_retries} попыток: {e}")
                return False
    return False


def build_object_modules_payload(modules_info: List[Dict], current_timestamp: float) -> Dict:
    """Поля payload точки объекта, описывающие его модули."""
    return {
        "has_modules": len(modules_info) > 0,
        "modules_count": len(modules_info),
        "total_code_lines": sum(m['lines'] for m in modules_info),
        "modules": modules_info,
        "last_updated": current_timestamp
    }


def update_object_modules_payload(
    client: QdrantClient,
    updates: List[Tuple[int, Dict]],
    max_retries: int = 3,
    retry_delay: int = 5
) -> int:
    """
    Обновляет только поля модулей в payload существующих точек объектов
    (без пересоздания точки и векторов).

    Используется в инкрементальном режиме для объектов, у которых изменились
    только модули: метаданные и эмбеддинги объекта остаются прежними,
    но список модулей / количество строк в payload объекта должны быть актуальными.

    Returns:
        Количество успешно обновлённых точек объектов
    """
    updated = 0
    for object_id, payload in updates:
        for attempt in range(max_retries):
            try:
                client.set_payload(
                    collection_name=COLLECTION_OBJECTS,
                    payload=payload,
                    points=[object_id]
                )
                updated += 1
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    st.warning(f"⚠️ Обновление модулей объекта (id={object_id}), попытка {attempt + 1}/{max_retries}: {e}. Повтор через {retry_delay} сек...")
                    time.sleep(retry_delay)
                else:
                    st.error(f"❌ Не удалось обновить модули в payload объекта (id={object_id}): {e}")
    return updated


def process_objects_chunk_streaming(
    client: QdrantClient,
    objects_chunk: List[Dict],
    modules_data: Dict,
    all_embeddings: List,
    object_embedding_indices: Dict,
    module_embedding_indices: Dict,
    chunk_embedding_indices: Dict,
    procedure_embedding_indices: Dict,
    vector_size: int,
    current_timestamp: float,
    skip_object_update_keys: set,
    upload_batch_size: int,
    chunk_start_idx: int
) -> Tuple[int, int, int, int]:
    """
    Обработка чанка объектов с немедленной загрузкой в Qdrant.
    
    Вместо накопления всех точек в памяти, эта функция:
    1. Создает точки для чанка объектов
    2. Сразу загружает их в Qdrant батчами
    3. Освобождает память
    
    Args:
        client: Клиент Qdrant
        objects_chunk: Чанк объектов для обработки
        modules_data: Словарь с данными модулей
        all_embeddings: Список всех эмбеддингов
        *_embedding_indices: Индексы эмбеддингов для разных типов данных
        vector_size: Размер вектора эмбеддинга
        current_timestamp: Текущая метка времени
        skip_object_update_keys: Множество ключей объектов, которые не нужно обновлять
        upload_batch_size: Размер батча для загрузки
        chunk_start_idx: Начальный индекс чанка (для прогресса)
    
    Returns:
        Tuple[objects_count, modules_count, chunks_count, procedures_count]: Количество загруженных точек
    """
    # Локальные списки для чанка (будут очищены после загрузки)
    objects_points = []
    # (object_id, payload) для объектов с изменениями только в модулях
    object_module_payload_updates = []
    modules_points = []
    chunks_points = []
    procedures_points = []
    
    for idx_in_chunk, obj_data in enumerate(objects_chunk):
        obj_type = obj_data['obj_type']
        obj_name = obj_data['obj_name']
        friendly_name = obj_data['friendly_name']
        metadata = obj_data['metadata']
        obj_key = f"{obj_type}.{obj_name}"
        
        # Генерируем детерминированный ID объекта
        object_id = generate_deterministic_id(obj_key)
        
        # Проверяем, нужно ли обновлять точку объекта
        should_skip_object = obj_key in skip_object_update_keys
        
        # Получаем эмбеддинги объекта
        obj_emb_indices = object_embedding_indices.get(obj_key, [])
        if not should_skip_object and len(obj_emb_indices) < 5:
            continue
        
        obj_embeddings = [all_embeddings[i] for i in obj_emb_indices] if not should_skip_object else []
        
        # Обработка модулей
        modules_info = []
        mod_data = modules_data.get(obj_key, {})
        
        for mod in mod_data.get('modules', []):
            module_key = f"{obj_key}.{mod['module_name']}"
            module_id = generate_deterministic_id(module_key)
            module_content_hash = calculate_module_hash(mod)
            
            # Получаем эмбеддинги чанков
            chunk_indices = chunk_embedding_indices.get(module_key, [])
            chunk_embeddings = [all_embeddings[i] for i in chunk_indices if i is not None]
            
            # Эмбеддинг модуля (среднее по чанкам)
            if chunk_embeddings:
                module_embedding = np.mean(chunk_embeddings, axis=0).tolist()
            else:
                module_embedding = [0.0] * vector_size
            
            # Создаем точки для чанков (пропускаем preamble/gap/epilogue)
            skipped_chunk_types = {'preamble', 'gap', 'epilogue'}
            for chunk_idx_in_mod, chunk in enumerate(mod['chunks']):
                chunk_type = chunk.get('chunk_type', 'unknown')
                
                # Пропускаем служебные чанки
                if chunk_type in skipped_chunk_types:
                    continue
                
                # Получаем эмбеддинг для этого чанка
                chunk_emb_idx = chunk_indices[chunk_idx_in_mod] if chunk_idx_in_mod < len(chunk_indices) else None
                if chunk_emb_idx is None:
                    continue  # Пропущенный чанк
                
                chunk_emb = all_embeddings[chunk_emb_idx]
                chunk_key = f"{module_key}.chunk_{chunk_idx_in_mod}"
                chunk_id = generate_deterministic_id(chunk_key)
                
                # Находим процедуры/функции в чанке
                proc_in_chunk = []
                for proc in mod['parsed'].get('procedures', []):
                    if chunk.get('start_line', 0) <= proc.get('start_line', 0) <= chunk.get('end_line', 0):
                        proc_in_chunk.append(proc)
                
                func_in_chunk = []
                for func in mod['parsed'].get('functions', []):
                    if chunk.get('start_line', 0) <= func.get('start_line', 0) <= chunk.get('end_line', 0):
                        func_in_chunk.append(func)
                
                chunk_content_hash = calculate_content_hash(chunk.get('code', ''))
                
                chunks_points.append(PointStruct(
                    id=chunk_id,
                    vector={"code": chunk_emb},
                    payload={
                        "module_id": module_id,
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "module_type": mod['module_type'],
                        "module_name": mod['module_name'],
                        "chunk_index": chunk.get('chunk_index', 0),
                        "chunk_type": chunk.get('chunk_type', 'unknown'),
                        "total_chunks": chunk.get('total_chunks', 1),
                        "start_line": chunk.get('start_line', 1),
                        "end_line": chunk.get('end_line', 1),
                        "code": chunk.get('code', ''),
                        "procedure_name": chunk.get('procedure_name'),
                        "procedures_in_chunk": [
                            {
                                "name": p["name"],
                                "type": p.get("type", "Процедура"),
                                "is_export": p.get("is_export", False)
                            }
                            for p in proc_in_chunk
                        ],
                        "functions_in_chunk": [
                            {
                                "name": f["name"],
                                "type": f.get("type", "Функция"),
                                "is_export": f.get("is_export", False)
                            }
                            for f in func_in_chunk
                        ],
                        "content_hash": chunk_content_hash,
                        "last_updated": current_timestamp
                    }
                ))
            
            # Создаем точки для процедур/функций
            proc_emb_indices = procedure_embedding_indices.get(module_key, [])
            all_procedures = mod['parsed'].get('procedures', []) + mod['parsed'].get('functions', [])
            
            for proc, proc_emb_idx in zip(all_procedures, proc_emb_indices):
                proc_emb = all_embeddings[proc_emb_idx]
                procedure_key = f"{module_key}.{proc['name']}"
                procedure_id = generate_deterministic_id(procedure_key)
                procedure_content_hash = calculate_content_hash(proc.get('full_code', ''))
                
                procedures_points.append(PointStruct(
                    id=procedure_id,
                    vector={"code": proc_emb},
                    payload={
                        "module_id": module_id,
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "module_name": mod['module_name'],
                        "procedure_name": proc['name'],
                        "procedure_type": proc.get('type', 'Процедура'),
                        "signature": proc['signature'],
                        "is_export": proc['is_export'],
                        "params": proc.get('params', []),
                        "start_line": proc['start_line'],
                        "end_line": proc['end_line'],
                        "full_code": proc.get('full_code', ''),
                        "comments": proc.get('comments', ''),
                        "calls_to": proc.get('calls_to', []),
                        "content_hash": procedure_content_hash,
                        "last_updated": current_timestamp
                    }
                ))
            
            # Создаем точку модуля
            modules_points.append(PointStruct(
                id=module_id,
                vector={"code": module_embedding},
                payload={
                    "object_type": obj_type,
                    "object_name": obj_name,
                    "object_id": object_id,
                    "module_type": mod['module_type'],
                    "module_name": mod['module_name'],
                    "file_path": mod['file_path'],
                    "lines_count": mod['lines_count'],
                    "export_methods": [p['name'] for p in mod['parsed'].get('procedures', []) if p.get('is_export')] + 
                                    [f['name'] for f in mod['parsed'].get('functions', []) if f.get('is_export')],
                    "has_server_code": '&НаСервере' in mod['code'] or '&AtServer' in mod['code'],
                    "has_client_code": '&НаКлиенте' in mod['code'] or '&AtClient' in mod['code'],
                    "procedures_count": len(mod['parsed'].get('procedures', [])),
                    "functions_count": len(mod['parsed'].get('functions', [])),
                    "procedures": [
                        {
                            "name": p["name"],
                            "type": p["type"],
                            "signature": p["signature"],
                            "is_export": p["is_export"],
                            "params": p.get("params", []),
                            "start_line": p["start_line"],
                            "end_line": p["end_line"],
                            "calls_to": p.get("calls_to", []),
                            "comments": p.get("comments", "")
                        }
                        for p in mod['parsed'].get('procedures', [])
                    ],
                    "functions": [
                        {
                            "name": f["name"],
                            "type": f["type"],
                            "signature": f["signature"],
                            "is_export": f["is_export"],
                            "params": f.get("params", []),
                            "start_line": f["start_line"],
                            "end_line": f["end_line"],
                            "calls_to": f.get("calls_to", []),
                            "comments": f.get("comments", "")
                        }
                        for f in mod['parsed'].get('functions', [])
                    ],
                    "content_hash": module_content_hash,
                    "last_updated": current_timestamp
                }
            ))
            
            modules_info.append({
                "type": mod['module_type'],
                "name": mod['module_name'],
                "module_id": module_id,
                "lines": mod['lines_count']
            })
        
        # Объект не пересоздаём (изменились только модули), но поля модулей
        # в его payload должны отражать актуальный список модулей
        if should_skip_object:
            object_module_payload_updates.append(
                (object_id, build_object_modules_payload(modules_info, current_timestamp))
            )

        # Создаём точку объекта (если нужно)
        if not should_skip_object:
            try:
                # Нормализация ключей
                attributes = metadata.get('Реквизиты', metadata.get('attributes', []))
                tabular_sections = metadata.get('ТабличныеЧасти', metadata.get('tabular_sections', []))
                standard_attributes = metadata.get('СтандартныеРеквизиты', metadata.get('standard_attributes', []))
                owners = metadata.get('Владельцы', metadata.get('owners', []))
                based_on = metadata.get('ОснованНа', metadata.get('based_on', []))
                register_records = metadata.get('Регистры', metadata.get('register_records', []))
                
                periodicity = metadata.get('Периодичность', metadata.get('periodicity', ''))
                write_mode = metadata.get('РежимЗаписи', metadata.get('write_mode', ''))
                register_type = metadata.get('ТипРегистра', metadata.get('register_type', ''))
                resources = metadata.get('Ресурсы', metadata.get('resources', []))
                dimensions = metadata.get('Измерения', metadata.get('dimensions', []))
                
                counts = count_metadata_elements(metadata)
                all_types = extract_all_types(metadata)
                
                object_content_hash = calculate_object_hash(metadata)
                
                objects_points.append(PointStruct(
                    id=object_id,
                    vector={
                        "object_name": obj_embeddings[0],
                        "friendly_name": obj_embeddings[1],
                        "description": obj_embeddings[2],
                        "structure": obj_embeddings[3],
                        "relationships": obj_embeddings[4]
                    },
                    payload={
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "friendly_name": friendly_name,
                        "comment": metadata.get('Комментарий', metadata.get('comment', '')),
                        "has_modules": len(modules_info) > 0,
                        "modules_count": len(modules_info),
                        "total_code_lines": sum(m['lines'] for m in modules_info),
                        "modules": modules_info,
                        "relationships": {
                            "owners": owners,
                            "based_on": based_on,
                            "register_records": register_records
                        },
                        "standard_attributes": standard_attributes,
                        "attributes": attributes,
                        "tabular_sections": tabular_sections,
                        "register_info": {
                            "periodicity": periodicity,
                            "write_mode": write_mode,
                            "register_type": register_type,
                            "resources": resources,
                            "dimensions": dimensions
                        },
                        "metadata": {
                            "has_attributes": counts['attributes_count'] > 0,
                            "attributes_count": counts['attributes_count'],
                            "has_tabular_sections": counts['tabular_sections_count'] > 0,
                            "tabular_sections_count": counts['tabular_sections_count'],
                            "has_resources": counts['resources_count'] > 0,
                            "resources_count": counts['resources_count'],
                            "has_dimensions": counts['dimensions_count'] > 0,
                            "dimensions_count": counts['dimensions_count'],
                            "standard_attributes_count": counts['standard_attributes_count'],
                            "indexed_fields_count": counts['indexed_fields_count'],
                            "has_relationships": bool(owners or based_on or register_records),
                            "is_hierarchical": "Родитель" in standard_attributes or "Parent" in standard_attributes,
                            "has_owner": "Владелец" in standard_attributes or "Owner" in standard_attributes,
                            "all_types_used": all_types
                        },
                        "content_hash": object_content_hash,
                        "last_updated": current_timestamp
                    }
                ))
            except Exception as e:
                st.warning(f"⚠️ Ошибка создания точки объекта {obj_name}: {e}")
    
    # НЕМЕДЛЕННАЯ ЗАГРУЗКА В QDRANT (streaming upload)
    uploaded_counts = [0, 0, 0, 0]
    
    # Загружаем объекты
    if objects_points:
        for i in range(0, len(objects_points), upload_batch_size):
            batch = objects_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_OBJECTS, batch):
                uploaded_counts[0] += len(batch)
    
    # Обновляем поля модулей в payload объектов, у которых изменились только модули
    if object_module_payload_updates:
        update_object_modules_payload(client, object_module_payload_updates)

    # Загружаем модули
    if modules_points:
        for i in range(0, len(modules_points), upload_batch_size):
            batch = modules_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_MODULES, batch):
                uploaded_counts[1] += len(batch)
    
    # Загружаем чанки
    if chunks_points:
        for i in range(0, len(chunks_points), upload_batch_size):
            batch = chunks_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_CHUNKS, batch):
                uploaded_counts[2] += len(batch)
    
    # Загружаем процедуры
    if procedures_points:
        for i in range(0, len(procedures_points), upload_batch_size):
            batch = procedures_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_PROCEDURES, batch):
                uploaded_counts[3] += len(batch)
    
    # Очищаем память
    objects_points.clear()
    modules_points.clear()
    chunks_points.clear()
    procedures_points.clear()
    
    return tuple(uploaded_counts)


def process_objects_chunk_with_embeddings(
    client: QdrantClient,
    objects_chunk: List[Dict],
    modules_data: Dict,
    vector_size: int,
    current_timestamp: float,
    skip_object_update_keys: set,
    upload_batch_size: int,
    chunk_start_idx: int,
    embedding_batch_size: int,
    max_retries: int,
    base_timeout: int,
    show_progress: bool = True
) -> Tuple[int, int, int, int, int]:
    """
    Обработка чанка объектов с генерацией эмбеддингов и загрузкой в Qdrant.
    
    Объединяет Фазу 2 (генерация эмбеддингов) и Фазу 3 (загрузка) для минимизации использования ОЗУ.
    Вместо накопления всех эмбеддингов в памяти, эта функция:
    1. Создает локальный батчер для чанка объектов
    2. Собирает тексты только для этого чанка
    3. Генерирует эмбеддинги
    4. Создает точки для Qdrant
    5. Загружает в Qdrant
    6. Очищает память
    
    Args:
        client: Клиент Qdrant
        objects_chunk: Чанк объектов для обработки
        modules_data: Словарь с данными модулей
        vector_size: Размер вектора эмбеддинга
        current_timestamp: Текущая метка времени
        skip_object_update_keys: Множество ключей объектов, которые не нужно обновлять
        upload_batch_size: Размер батча для загрузки
        chunk_start_idx: Начальный индекс чанка (для прогресса)
        embedding_batch_size: Размер батча для эмбеддингов
        max_retries: Максимальное количество повторных попыток
        base_timeout: Базовый таймаут для запросов
        show_progress: Показывать ли детальный прогресс
    
    Returns:
        Tuple[objects, modules, chunks, procedures, reused_embeddings]: Количество загруженных точек и переиспользованных эмбеддингов
    """
    # Создаем локальный батчер для этого чанка
    batcher = EmbeddingBatcher(
        batch_size=embedding_batch_size,
        max_retries=max_retries,
        base_timeout=base_timeout
    )
    
    # Словари для индексов эмбеддингов
    object_embedding_indices = {}
    module_embedding_indices = {}
    chunk_embedding_indices = {}
    procedure_embedding_indices = {}
    
    # ШАГ 1: Собираем тексты для объектов чанка
    for obj_data in objects_chunk:
        obj_key = f"{obj_data['obj_type']}.{obj_data['obj_name']}"
        
        # Пропускаем генерацию эмбеддингов для объектов с изменениями только в модулях
        if obj_key in skip_object_update_keys:
            continue
        
        metadata = obj_data['metadata']
        comment = metadata.get('Комментарий', metadata.get('comment', ''))
        structure_text = create_structure_text(metadata)
        relationships_text = create_relationships_text(metadata)
        
        # 5 эмбеддингов для объекта
        texts = [
            obj_data['obj_name'],
            obj_data['friendly_name'] if obj_data['friendly_name'] else obj_data['obj_name'],
            comment[:1000] if comment else f"{obj_data['obj_type']} {obj_data['obj_name']}",
            structure_text,
            relationships_text
        ]
        
        indices = batcher.add_multiple(texts, [{'type': 'object', 'obj_key': obj_key, 'emb_idx': i} for i in range(5)])
        object_embedding_indices[obj_key] = indices
    
    # ШАГ 2: Собираем тексты для модулей, чанков и процедур
    procedure_name_to_emb_idx = {}  # Для переиспользования эмбеддингов
    reused_embeddings_count = 0
    
    for obj_data in objects_chunk:
        obj_key = f"{obj_data['obj_type']}.{obj_data['obj_name']}"
        mod_data = modules_data.get(obj_key, {})
        
        for mod in mod_data.get('modules', []):
            module_key = f"{obj_key}.{mod['module_name']}"
            
            # Сначала собираем эмбеддинги для процедур
            proc_indices = []
            for proc in mod['parsed'].get('procedures', []):
                proc_idx = batcher.add(proc.get('full_code', ''), {'type': 'procedure', 'module_key': module_key, 'proc_name': proc['name']})
                proc_indices.append(proc_idx)
                procedure_name_to_emb_idx[(module_key, proc['name'])] = proc_idx
            
            for func in mod['parsed'].get('functions', []):
                func_idx = batcher.add(func.get('full_code', ''), {'type': 'function', 'module_key': module_key, 'func_name': func['name']})
                proc_indices.append(func_idx)
                procedure_name_to_emb_idx[(module_key, func['name'])] = func_idx
            
            procedure_embedding_indices[module_key] = proc_indices
            
            # Затем собираем эмбеддинги для чанков с переиспользованием
            # ИСКЛЮЧАЕМ: preamble, gap, epilogue (заголовки/области до/между/после процедур)
            chunk_indices = []
            skipped_chunk_types = {'preamble', 'gap', 'epilogue'}
            for chunk in mod['chunks']:
                chunk_type = chunk.get('chunk_type', 'unknown')
                
                # Пропускаем служебные чанки (preamble, gap, epilogue)
                if chunk_type in skipped_chunk_types:
                    chunk_indices.append(None)  # None = пропущен
                    continue
                
                proc_name = chunk.get('procedure_name')
                
                # ОПТИМИЗАЦИЯ: Если это чанк процедуры целиком - переиспользуем эмбеддинг
                if chunk_type == 'procedure' and proc_name:
                    proc_key = (module_key, proc_name)
                    if proc_key in procedure_name_to_emb_idx:
                        chunk_idx = procedure_name_to_emb_idx[proc_key]
                        chunk_indices.append(chunk_idx)
                        reused_embeddings_count += 1
                        continue
                
                # Для остальных чанков генерируем новый эмбеддинг
                chunk_idx = batcher.add(chunk['code'], {'type': 'chunk', 'module_key': module_key})
                chunk_indices.append(chunk_idx)
            
            chunk_embedding_indices[module_key] = chunk_indices
            module_embedding_indices[module_key] = chunk_indices
    
    # ШАГ 3: Генерируем эмбеддинги для чанка
    if show_progress and len(batcher.texts) > 0:
        st.write(f"  🧠 Генерация {len(batcher.texts)} эмбеддингов для чанка...")
    
    # Таймер генерации эмбеддингов
    embedding_start_time = time.time()
    all_embeddings = batcher.process_all(verbose=show_progress) if len(batcher.texts) > 0 else []
    embedding_time = time.time() - embedding_start_time
    
    # ШАГ 4: Создаем точки (логика из process_objects_chunk_streaming)
    objects_points = []
    # (object_id, payload) для объектов с изменениями только в модулях
    object_module_payload_updates = []
    modules_points = []
    chunks_points = []
    procedures_points = []
    
    for obj_data in objects_chunk:
        obj_type = obj_data['obj_type']
        obj_name = obj_data['obj_name']
        friendly_name = obj_data['friendly_name']
        metadata = obj_data['metadata']
        obj_key = f"{obj_type}.{obj_name}"
        
        # Генерируем детерминированный ID объекта
        object_id = generate_deterministic_id(obj_key)
        
        # Проверяем, нужно ли обновлять точку объекта
        should_skip_object = obj_key in skip_object_update_keys
        
        # Получаем эмбеддинги объекта
        obj_emb_indices = object_embedding_indices.get(obj_key, [])
        if not should_skip_object and len(obj_emb_indices) < 5:
            continue
        
        obj_embeddings = [all_embeddings[i] for i in obj_emb_indices] if not should_skip_object else []
        
        # Обработка модулей
        modules_info = []
        mod_data = modules_data.get(obj_key, {})
        
        for mod in mod_data.get('modules', []):
            module_key = f"{obj_key}.{mod['module_name']}"
            module_id = generate_deterministic_id(module_key)
            module_content_hash = calculate_module_hash(mod)
            
            # Получаем эмбеддинги чанков
            chunk_indices = chunk_embedding_indices.get(module_key, [])
            chunk_embeddings = [all_embeddings[i] for i in chunk_indices if i is not None]
            
            # Эмбеддинг модуля (среднее по чанкам)
            if chunk_embeddings:
                module_embedding = np.mean(chunk_embeddings, axis=0).tolist()
            else:
                module_embedding = [0.0] * vector_size
            
            # Создаем точки для чанков (пропускаем preamble/gap/epilogue)
            skipped_chunk_types = {'preamble', 'gap', 'epilogue'}
            for chunk_idx_in_mod, chunk in enumerate(mod['chunks']):
                chunk_type = chunk.get('chunk_type', 'unknown')
                
                # Пропускаем служебные чанки
                if chunk_type in skipped_chunk_types:
                    continue
                
                # Получаем эмбеддинг для этого чанка
                chunk_emb_idx = chunk_indices[chunk_idx_in_mod] if chunk_idx_in_mod < len(chunk_indices) else None
                if chunk_emb_idx is None:
                    continue  # Пропущенный чанк
                
                chunk_emb = all_embeddings[chunk_emb_idx]
                chunk_key = f"{module_key}.chunk_{chunk_idx_in_mod}"
                chunk_id = generate_deterministic_id(chunk_key)
                
                # Находим процедуры/функции в чанке
                proc_in_chunk = []
                for proc in mod['parsed'].get('procedures', []):
                    if chunk.get('start_line', 0) <= proc.get('start_line', 0) <= chunk.get('end_line', 0):
                        proc_in_chunk.append(proc)
                
                func_in_chunk = []
                for func in mod['parsed'].get('functions', []):
                    if chunk.get('start_line', 0) <= func.get('start_line', 0) <= chunk.get('end_line', 0):
                        func_in_chunk.append(func)
                
                chunk_content_hash = calculate_content_hash(chunk.get('code', ''))
                
                chunks_points.append(PointStruct(
                    id=chunk_id,
                    vector={"code": chunk_emb},
                    payload={
                        "module_id": module_id,
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "module_type": mod['module_type'],
                        "module_name": mod['module_name'],
                        "chunk_index": chunk_idx_in_mod,
                        "total_chunks": len(mod['chunks']),
                        "start_line": chunk.get('start_line', 0),
                        "end_line": chunk.get('end_line', 0),
                        "code": chunk.get('code', ''),
                        "chunk_type": chunk.get('chunk_type', 'unknown'),
                        "procedure_name": chunk.get('procedure_name'),
                        "procedures_in_chunk": [
                            {
                                "name": p["name"],
                                "type": p.get("type", "Процедура"),
                                "is_export": p.get("is_export", False)
                            }
                            for p in proc_in_chunk
                        ],
                        "functions_in_chunk": [
                            {
                                "name": f["name"],
                                "type": f.get("type", "Функция"),
                                "is_export": f.get("is_export", False)
                            }
                            for f in func_in_chunk
                        ],
                        "content_hash": chunk_content_hash,
                        "last_updated": current_timestamp
                    }
                ))
            
            # Создаем точки для процедур/функций
            proc_emb_indices = procedure_embedding_indices.get(module_key, [])
            all_procedures = mod['parsed'].get('procedures', []) + mod['parsed'].get('functions', [])
            
            for proc, proc_emb_idx in zip(all_procedures, proc_emb_indices):
                proc_emb = all_embeddings[proc_emb_idx]
                procedure_key = f"{module_key}.{proc['name']}"
                procedure_id = generate_deterministic_id(procedure_key)
                procedure_content_hash = calculate_content_hash(proc.get('full_code', ''))
                
                procedures_points.append(PointStruct(
                    id=procedure_id,
                    vector={"code": proc_emb},
                    payload={
                        "module_id": module_id,
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "module_name": mod['module_name'],
                        "procedure_name": proc['name'],
                        "procedure_type": proc.get('type', 'Процедура'),
                        "signature": proc['signature'],
                        "is_export": proc['is_export'],
                        "params": proc.get('params', []),
                        "start_line": proc['start_line'],
                        "end_line": proc['end_line'],
                        "full_code": proc.get('full_code', ''),
                        "comments": proc.get('comments', ''),
                        "calls_to": proc.get('calls_to', []),
                        "content_hash": procedure_content_hash,
                        "last_updated": current_timestamp
                    }
                ))
            
            # Создаем точку модуля
            modules_points.append(PointStruct(
                id=module_id,
                vector={"code": module_embedding},
                payload={
                    "object_type": obj_type,
                    "object_name": obj_name,
                    "object_id": object_id,
                    "module_type": mod['module_type'],
                    "module_name": mod['module_name'],
                    "file_path": mod['file_path'],
                    "lines_count": mod['lines_count'],
                    "export_methods": [p['name'] for p in mod['parsed'].get('procedures', []) if p.get('is_export')] + 
                                    [f['name'] for f in mod['parsed'].get('functions', []) if f.get('is_export')],
                    "has_server_code": '&НаСервере' in mod['code'] or '&AtServer' in mod['code'],
                    "has_client_code": '&НаКлиенте' in mod['code'] or '&AtClient' in mod['code'],
                    "procedures_count": len(mod['parsed'].get('procedures', [])),
                    "functions_count": len(mod['parsed'].get('functions', [])),
                    "procedures": [
                        {
                            "name": p["name"],
                            "type": p["type"],
                            "signature": p["signature"],
                            "is_export": p["is_export"],
                            "params": p.get("params", []),
                            "start_line": p["start_line"],
                            "end_line": p["end_line"],
                            "calls_to": p.get("calls_to", []),
                            "comments": p.get("comments", "")
                        }
                        for p in mod['parsed'].get('procedures', [])
                    ],
                    "functions": [
                        {
                            "name": f["name"],
                            "type": f["type"],
                            "signature": f["signature"],
                            "is_export": f["is_export"],
                            "params": f.get("params", []),
                            "start_line": f["start_line"],
                            "end_line": f["end_line"],
                            "calls_to": f.get("calls_to", []),
                            "comments": f.get("comments", "")
                        }
                        for f in mod['parsed'].get('functions', [])
                    ],
                    "content_hash": module_content_hash,
                    "last_updated": current_timestamp
                }
            ))
            
            modules_info.append({
                "type": mod['module_type'],
                "name": mod['module_name'],
                "module_id": module_id,
                "lines": mod['lines_count']
            })
        
        # Объект не пересоздаём (изменились только модули), но поля модулей
        # в его payload должны отражать актуальный список модулей
        if should_skip_object:
            object_module_payload_updates.append(
                (object_id, build_object_modules_payload(modules_info, current_timestamp))
            )

        # Создаём точку объекта (если нужно)
        if not should_skip_object:
            try:
                # Нормализация ключей
                attributes = metadata.get('Реквизиты', metadata.get('attributes', []))
                tabular_sections = metadata.get('ТабличныеЧасти', metadata.get('tabular_sections', []))
                standard_attributes = metadata.get('СтандартныеРеквизиты', metadata.get('standard_attributes', []))
                owners = metadata.get('Владельцы', metadata.get('owners', []))
                based_on = metadata.get('ОснованНа', metadata.get('based_on', []))
                register_records = metadata.get('Регистры', metadata.get('register_records', []))
                
                periodicity = metadata.get('Периодичность', metadata.get('periodicity', ''))
                write_mode = metadata.get('РежимЗаписи', metadata.get('write_mode', ''))
                register_type = metadata.get('ТипРегистра', metadata.get('register_type', ''))
                resources = metadata.get('Ресурсы', metadata.get('resources', []))
                dimensions = metadata.get('Измерения', metadata.get('dimensions', []))
                
                counts = count_metadata_elements(metadata)
                all_types = extract_all_types(metadata)
                
                object_content_hash = calculate_object_hash(metadata)
                
                objects_points.append(PointStruct(
                    id=object_id,
                    vector={
                        "object_name": obj_embeddings[0],
                        "friendly_name": obj_embeddings[1],
                        "description": obj_embeddings[2],
                        "structure": obj_embeddings[3],
                        "relationships": obj_embeddings[4]
                    },
                    payload={
                        "object_type": obj_type,
                        "object_name": obj_name,
                        "friendly_name": friendly_name,
                        "comment": metadata.get('Комментарий', metadata.get('comment', '')),
                        "has_modules": len(modules_info) > 0,
                        "modules_count": len(modules_info),
                        "total_code_lines": sum(m['lines'] for m in modules_info),
                        "modules": modules_info,
                        "relationships": {
                            "owners": owners,
                            "based_on": based_on,
                            "register_records": register_records
                        },
                        "standard_attributes": standard_attributes,
                        "attributes": attributes,
                        "tabular_sections": tabular_sections,
                        "register_info": {
                            "periodicity": periodicity,
                            "write_mode": write_mode,
                            "register_type": register_type,
                            "resources": resources,
                            "dimensions": dimensions
                        },
                        "metadata": {
                            "has_attributes": counts['attributes_count'] > 0,
                            "attributes_count": counts['attributes_count'],
                            "has_tabular_sections": counts['tabular_sections_count'] > 0,
                            "tabular_sections_count": counts['tabular_sections_count'],
                            "has_resources": counts['resources_count'] > 0,
                            "resources_count": counts['resources_count'],
                            "has_dimensions": counts['dimensions_count'] > 0,
                            "dimensions_count": counts['dimensions_count'],
                            "standard_attributes_count": counts['standard_attributes_count'],
                            "indexed_fields_count": counts['indexed_fields_count'],
                            "has_relationships": bool(owners or based_on or register_records),
                            "is_hierarchical": "Родитель" in standard_attributes or "Parent" in standard_attributes,
                            "has_owner": "Владелец" in standard_attributes or "Owner" in standard_attributes,
                            "all_types_used": all_types
                        },
                        "content_hash": object_content_hash,
                        "last_updated": current_timestamp
                    }
                ))
            except Exception as e:
                st.warning(f"⚠️ Ошибка создания точки объекта {obj_name}: {e}")
    
    # ШАГ 5: НЕМЕДЛЕННАЯ ЗАГРУЗКА В QDRANT (streaming upload)
    uploaded_counts = [0, 0, 0, 0]
    
    # Таймер загрузки в Qdrant
    qdrant_start_time = time.time()
    
    # Загружаем объекты
    if objects_points:
        for i in range(0, len(objects_points), upload_batch_size):
            batch = objects_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_OBJECTS, batch):
                uploaded_counts[0] += len(batch)
    
    # Обновляем поля модулей в payload объектов, у которых изменились только модули
    if object_module_payload_updates:
        update_object_modules_payload(client, object_module_payload_updates)

    # Загружаем модули
    if modules_points:
        for i in range(0, len(modules_points), upload_batch_size):
            batch = modules_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_MODULES, batch):
                uploaded_counts[1] += len(batch)
    
    # Загружаем чанки
    if chunks_points:
        for i in range(0, len(chunks_points), upload_batch_size):
            batch = chunks_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_CHUNKS, batch):
                uploaded_counts[2] += len(batch)
    
    # Загружаем процедуры
    if procedures_points:
        for i in range(0, len(procedures_points), upload_batch_size):
            batch = procedures_points[i:i + upload_batch_size]
            if upsert_with_retry(client, COLLECTION_PROCEDURES, batch):
                uploaded_counts[3] += len(batch)
    
    qdrant_time = time.time() - qdrant_start_time
    
    # ШАГ 6: Очищаем память
    all_embeddings.clear()
    batcher.clear()
    objects_points.clear()
    modules_points.clear()
    chunks_points.clear()
    procedures_points.clear()
    object_embedding_indices.clear()
    module_embedding_indices.clear()
    chunk_embedding_indices.clear()
    procedure_embedding_indices.clear()
    procedure_name_to_emb_idx.clear()
    
    return tuple(uploaded_counts) + (reused_embeddings_count, embedding_time, qdrant_time)


def process_uploaded_data(
    zip_path: str, 
    force_restart: bool = False, 
    incremental_mode: bool = False,
    # Параметры производительности
    embedding_batch_size: int = 400,
    max_retries: int = 3,
    base_timeout: int = 180,
    upload_batch_size: int = 300,
    scroll_batch_size: int = 1000,
    max_workers: int = 10,
    max_chunk_lines: int = 500,
    overlap_lines: int = 50,
    max_context_lines: int = 50
):
    """
    Обработка загруженного архива и загрузка в Qdrant с поддержкой checkpoint и инкрементального обновления.
    
    Архитектура (3 фазы):
    1. Фаза сбора: Параллельное чтение и парсинг файлов
    2. Фаза эмбеддинга: Батчевая обработка всех текстов
    3. Фаза загрузки: Создание точек и batch upsert в Qdrant
    
    Args:
        zip_path: Путь к ZIP архиву
        force_restart: Игнорировать checkpoint и начать заново
        incremental_mode: Если True - обновлять только изменённые объекты
        embedding_batch_size: Размер батча для обработки эмбеддингов (8-500)
        max_retries: Максимальное количество повторных попыток при ошибках (1-10)
        base_timeout: Базовый таймаут для запросов к embedding service в секундах (60-600)
        upload_batch_size: Размер батча для загрузки точек в Qdrant (10-1000)
        scroll_batch_size: Размер страницы для Scroll API при чтении из Qdrant (100-5000)
        max_workers: Количество потоков для параллельного чтения модулей (1-32)
        max_chunk_lines: Максимальный размер чанка процедуры в строках (200-1000)
        overlap_lines: Размер перекрытия между чанками длинных процедур (10-200)
        max_context_lines: Размер контекста модуля - строки из начала (20-150)
    
    Ускорение: 10-20x по сравнению с последовательной обработкой
    """
    
    # ============================================================================
    # ПРОВЕРКА CHECKPOINT
    # ============================================================================
    # Инициализация session_state для checkpoint
    if 'checkpoint_handled' not in st.session_state:
        st.session_state.checkpoint_handled = False
    if 'checkpoint_action' not in st.session_state:
        st.session_state.checkpoint_action = None
    
    checkpoint = None if force_restart else load_checkpoint()
    
    # Показываем диалог checkpoint только если он еще не был обработан
    if checkpoint and not force_restart and not st.session_state.checkpoint_handled:
        display_checkpoint_info(checkpoint)
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("▶️ Продолжить", key="continue_checkpoint"):
                st.session_state.checkpoint_action = "continue"
                st.session_state.checkpoint_handled = True
                st.rerun()
        
        with col2:
            if st.button("🔄 Начать заново", key="restart_checkpoint"):
                st.session_state.checkpoint_action = "restart"
                st.session_state.checkpoint_handled = True
                st.rerun()
        
        with col3:
            if st.button("❌ Отмена", key="cancel_checkpoint"):
                st.session_state.checkpoint_action = "cancel"
                st.session_state.checkpoint_handled = True
                st.rerun()
        
        # Если действие еще не выбрано, останавливаем выполнение
        st.stop()
    
    # Обрабатываем действие checkpoint
    if st.session_state.checkpoint_handled and st.session_state.checkpoint_action:
        action = st.session_state.checkpoint_action
        
        if action == "cancel":
            st.session_state.checkpoint_action = None
            st.session_state.checkpoint_handled = False
            st.session_state.loading_started = False
            st.session_state.loading_params = None
            st.stop()
        elif action == "restart":
            clear_checkpoint()
            checkpoint = None
            st.session_state.checkpoint_action = None
            st.info("🆕 Начинаем загрузку с начала...")
        elif action == "continue":
            st.session_state.checkpoint_action = None
            st.info("♻️ Продолжение с checkpoint...")
            # Продолжаем с checkpoint
    
    # Проверяем, был ли уже распакован этот архив
    import hashlib
    archive_hash = hashlib.md5(open(zip_path, 'rb').read()).hexdigest()
    
    if 'extracted_archive_hash' not in st.session_state or st.session_state.extracted_archive_hash != archive_hash:
        # Распаковываем заново
        st.info("📦 Распаковка архива...")
        
        # Временная директория
        temp_dir = Path("temp_extracted")
        temp_dir.mkdir(exist_ok=True)
        
        # Распаковка
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        st.success("✅ Архив распакован")
        
        # Сохраняем хеш архива
        st.session_state.extracted_archive_hash = archive_hash
    else:
        # Используем уже распакованный архив
        st.info("📦 Используем уже распакованный архив")
        temp_dir = Path("temp_extracted")
    
    # Автоматическое определение корректной папки (для архивов с вложенной структурой)
    potential_csv = temp_dir / "objects.csv"
    if not potential_csv.exists():
        # Ищем подпапку с objects.csv
        subdirs = [d for d in temp_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1 and (subdirs[0] / "objects.csv").exists():
            temp_dir = subdirs[0]
            st.info(f"📁 Обнаружена вложенная структура: {temp_dir.name}")
        elif len(subdirs) > 1:
            # Пробуем найти папку с objects.csv среди нескольких
            for subdir in subdirs:
                if (subdir / "objects.csv").exists():
                    temp_dir = subdir
                    st.info(f"📁 Обнаружена вложенная структура (среди {len(subdirs)} папок): {temp_dir.name}")
                    break
    
    # Проверка структуры
    csv_file = temp_dir / "objects.csv"
    modules_dir = temp_dir / "modules"
    metadata_dir = temp_dir / "metadata"
    
    if not csv_file.exists():
        st.error("❌ Файл objects.csv не найден")
        return
    
    if not modules_dir.exists():
        st.warning("⚠️ Папка modules не найдена")
    
    if not metadata_dir.exists():
        st.warning("⚠️ Папка metadata не найдена - метаданные будут пустыми")
    
    # Чтение CSV (с кешированием)
    if f'objects_data_{archive_hash}' not in st.session_state:
        st.info("📖 Чтение objects.csv...")
        objects_data = []
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            objects_data = list(reader)
        st.session_state[f'objects_data_{archive_hash}'] = objects_data
        st.success(f"✅ Загружено объектов: {len(objects_data)}")
    else:
        st.info("📖 Используем кэшированные objects.csv...")
        objects_data = st.session_state[f'objects_data_{archive_hash}']
        st.success(f"✅ Загружено объектов: {len(objects_data)}")
    
    # Подключение к Qdrant
    st.info("🔌 Подключение к Qdrant...")
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=600)
    
    # Получаем размер вектора
    test_embedding = get_embeddings(["тест"])[0]
    vector_size = len(test_embedding)
    st.success(f"✅ Размер вектора: {vector_size}")
    
    # При инкрементальной загрузке размерность существующих коллекций должна совпадать с моделью
    if incremental_mode:
        mismatches = find_vector_size_mismatches(client, vector_size)
        if mismatches:
            st.error(
                "❌ Размерность векторов в Qdrant не совпадает с текущей моделью эмбеддингов "
                f"({vector_size}): " + ", ".join(f"{name} = {size}" for name, size in mismatches) +
                ". Скорее всего, модель в embeddings/config.json была изменена. "
                "Отключите «Инкрементальное обновление» и выполните полную перезагрузку."
            )
            st.session_state.loading_started = False
            st.session_state.loading_params = None
            st.stop()
    
    # Создание коллекций (с учётом режима)
    create_collections(client, vector_size, incremental=incremental_mode)
    
    changes_info = None
    existing_objects_hashes = {}
    existing_modules_hashes = {}
    
    # Инициализация session_state для отслеживания подтверждения (ДО загрузки хешей из Qdrant)
    if incremental_mode:
        if 'incremental_confirmed' not in st.session_state:
            st.session_state.incremental_confirmed = False
        if 'incremental_cancelled' not in st.session_state:
            st.session_state.incremental_cancelled = False
    
    # Загружаем хеши из Qdrant ТОЛЬКО если нужно определить изменения (не подтверждено)
    if incremental_mode and not st.session_state.incremental_confirmed:
        st.info("🔍 Анализ изменений...")
        
        # Загружаем существующие хеши из коллекций
        existing_objects_hashes = load_existing_hashes(client, COLLECTION_OBJECTS, scroll_batch_size)
        existing_modules_hashes = load_existing_hashes(client, COLLECTION_MODULES, scroll_batch_size)
        
        st.success("✅ Существующие данные загружены")
        st.info(f"📊 Загружено из Qdrant: объектов={len(existing_objects_hashes)}, модулей={len(existing_modules_hashes)}")
        # Но если изменений нет - пропускаем обработку
        # Поэтому анализ изменений будет после Фазы 1
    
    st.info("📚 Фаза 1/3: Сбор данных...")
    
    modules_metadata = None
    if (temp_dir / "modules_metadata.json").exists():
        st.info("📋 Загрузка modules_metadata.json...")
        modules_metadata = load_modules_metadata(temp_dir)
        if modules_metadata:
            st.success(f"✅ Загружено {len({id(m) for m in modules_metadata.values()})} модулей из modules_metadata.json")
        else:
            st.warning("⚠️ Не удалось загрузить modules_metadata.json, будет использован парсинг")
    else:
        st.info("ℹ️ modules_metadata.json не найден, будет использован парсинг")
    
    phase1_start = time.time()
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Загружаем метаданные всех объектов (с кешированием)
    if f'all_objects_data_{archive_hash}' not in st.session_state:
        status_text.text(f"Загрузка метаданных объектов...")
        all_objects_data = []
        for idx, row in enumerate(objects_data):
            obj_type = row['object_type']
            obj_name = row['object_name']
            friendly_name = row.get('friendly_name', obj_name)
            modules_count = int(row.get('modules_count', 0)) if row.get('modules_count') else None
            
            metadata = load_object_metadata(obj_type, obj_name, temp_dir)
            
            all_objects_data.append({
                'obj_type': obj_type,
                'obj_name': obj_name,
                'friendly_name': friendly_name,
                'metadata': metadata,
                'expected_modules_count': modules_count
            })
            
            progress_bar.progress((idx + 1) / len(objects_data) * 0.3)  # 30% прогресса
        st.session_state[f'all_objects_data_{archive_hash}'] = all_objects_data
    else:
        status_text.text(f"Используем кэшированные метаданные объектов...")
        all_objects_data = st.session_state[f'all_objects_data_{archive_hash}']
    
    # Параллельное чтение и парсинг модулей (с кешированием)
    warnings_count = 0  # Инициализируем перед блоком if
    if f'modules_data_{archive_hash}' not in st.session_state:
        status_text.text(f"Параллельное чтение модулей (потоков: {max_workers})...")
        modules_data = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Создаём задачи для каждого объекта
            future_to_obj = {
                executor.submit(
                    process_object_modules, 
                    obj['obj_type'], 
                    obj['obj_name'], 
                    modules_dir, 
                    obj.get('expected_modules_count'),
                    max_chunk_lines,
                    overlap_lines,
                    max_context_lines,
                    modules_metadata
                ): obj
                for obj in all_objects_data
            }
            
            completed = 0
            total = len(future_to_obj)
            
            for future in as_completed(future_to_obj):
                obj = future_to_obj[future]
                try:
                    result = future.result()
                    key = f"{result['obj_type']}.{result['obj_name']}"
                    modules_data[key] = result
                    
                    # Проверяем наличие предупреждения и сохраняем детали для детального вывода
                    if result.get('warning'):
                        logger.warning(f"{key}: {result['warning']}")
                        warnings_count += 1
                        
                        # Сохраняем детальную информацию для вывода после обработки всех модулей
                        if 'debug_info' not in st.session_state:
                            st.session_state.debug_info = []
                        
                        # Определяем необработанные файлы
                        found_file_names = result.get('found_file_names', [])
                        processed_modules = [m['module_type'] for m in result.get('modules', [])]
                        found_stems = {f.replace('.bsl', '') for f in found_file_names}
                        processed_set = set(processed_modules)
                        missing = found_stems - processed_set
                        
                        # Собираем информацию об ошибках для необработанных файлов
                        missing_errors = {}
                        if missing and 'processing_errors' in result:
                            for missing_name in missing:
                                if missing_name in result.get('processing_errors', {}):
                                    missing_errors[missing_name] = result['processing_errors'][missing_name]
                        
                        st.session_state.debug_info.append({
                            'obj_type': result['obj_type'],
                            'obj_name': result['obj_name'],
                            'expected': result.get('expected_count'),
                            'found_files': result.get('found_files_count', 0),
                            'processed': len(result.get('modules', [])),
                            'found_file_names': found_file_names,
                            'processed_modules': processed_modules,
                            'missing_files': list(missing),
                            'missing_errors': missing_errors
                        })
                    
                    completed += 1
                    progress_bar.progress(0.3 + (completed / total) * 0.2)  # 30-50% прогресса
                    status_text.text(f"Обработано модулей: {completed}/{total}")
                except Exception as e:
                    st.warning(f"⚠️ Ошибка обработки модулей {obj['obj_type']}.{obj['obj_name']}: {e}")
        
        # Сохраняем modules_data в кеш
        st.session_state[f'modules_data_{archive_hash}'] = modules_data
    else:
        status_text.text(f"Используем кэшированные модули...")
        modules_data = st.session_state[f'modules_data_{archive_hash}']
    
    phase1_time = time.time() - phase1_start
    st.success(f"✅ Фаза 1 завершена за {phase1_time:.1f} сек")
    
    # Отладочная информация о найденных данных
    total_modules = sum(len(m.get('modules', [])) for m in modules_data.values())
    st.info(f"📊 Найдено в архиве: объектов={len(all_objects_data)}, модулей={total_modules}")
    
    # Показываем предупреждения о несоответствии с детальной информацией
    if warnings_count > 0:
        st.warning(f"⚠️ Обнаружено {warnings_count} объектов с несоответствием количества модулей")
        
        # Выводим детальную информацию для каждого объекта с несоответствием
        debug_info = st.session_state.get('debug_info', [])
        if debug_info:
            with st.expander(f"🔍 Детальная информация о несоответствиях ({len(debug_info)} объектов)", expanded=False):
                st.dataframe(pd.DataFrame([{
                    'Объект': f"{info['obj_type']}.{info['obj_name']}",
                    'Ожидалось': info['expected'],
                    'Найдено файлов': info['found_files'],
                    'Обработано': info['processed'],
                    'Необработанные файлы': ', '.join(info.get('missing_files') or []),
                } for info in debug_info]), width="stretch", hide_index=True)

                # Ошибки разбора показываем только для объектов с необработанными файлами
                errors_shown = 0
                for info in debug_info:
                    for missing_name, error_info in (info.get('missing_errors') or {}).items():
                        if errors_shown >= 20:
                            break
                        st.error(f"❌ {info['obj_type']}.{info['obj_name']} / {missing_name}: "
                                 f"{error_info.get('error', 'Неизвестная ошибка')}")
                        if error_info.get('traceback'):
                            st.code(error_info['traceback'], language='python')
                        errors_shown += 1
                if errors_shown >= 20:
                    st.caption("Показаны первые 20 ошибок, остальные - в логах контейнера loader")

        # Очищаем debug_info для следующего запуска
        if 'debug_info' in st.session_state:
            del st.session_state.debug_info
    
    if incremental_mode:
        # Если уже подтверждено в предыдущем rerun, используем сохранённые данные
        if st.session_state.incremental_confirmed and 'changes_info' in st.session_state:
            st.success("♻️ Начинаем обновление изменённых объектов...")
            
            # Используем уже вычисленный changes_info из session_state
            changes_info = st.session_state.changes_info
            
            # КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Фильтруем all_objects_data
            # - Новые объекты: создаём полностью (объект + модули)
            # - Изменённые метаданные: обновляем объект + модули
            # - Только изменённые модули: НЕ обновляем объект, только модули/чанки/процедуры
            objects_to_process_full = changes_info['new_objects'] + changes_info['changed_objects']
            objects_with_only_module_changes = changes_info.get('objects_with_module_changes', [])
            
            # Создаём множество ключей объектов, которые НЕ нужно обновлять (только модули)
            skip_object_update_keys = set()
            for obj in objects_with_only_module_changes:
                obj_key = f"{obj['obj_type']}.{obj['obj_name']}"
                skip_object_update_keys.add(obj_key)
            
            # Все объекты для обработки (включая те, у которых изменились только модули)
            # Это нужно для обработки модулей в фазе 2 и 3
            objects_to_process_all = objects_to_process_full + objects_with_only_module_changes
            
            # Заменяем all_objects_data на отфильтрованный список
            st.info(
                f"📝 Будет обработано: полное обновление - {len(objects_to_process_full)}, "
                f"только модули - {len(objects_with_only_module_changes)}, "
                f"удаление - {len(changes_info.get('deleted_object_ids', []))} "
                f"(всего объектов в архиве: {len(all_objects_data)})"
            )
            # Отчёт об изменениях остаётся доступен и после подтверждения
            st.session_state.last_changes_info = changes_info
            if not build_changes_report(changes_info).empty:
                with st.expander("📋 Детали изменений", expanded=False):
                    render_changes_report(changes_info, key_prefix="changes_confirmed")
            
            all_objects_data = objects_to_process_all
            
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Фильтруем modules_data - оставляем только изменённые объекты
            filtered_modules_data = {}
            for obj in objects_to_process_all:
                obj_key = f"{obj['obj_type']}.{obj['obj_name']}"
                if obj_key in modules_data:
                    filtered_modules_data[obj_key] = modules_data[obj_key]
            modules_data = filtered_modules_data
            
            # Сохраняем информацию для пропуска обновления объектов
            # (используется в фазе 3 для пропуска создания точек объектов)
            st.session_state.skip_object_update_keys = skip_object_update_keys
            
            # Сохраняем changes_info в session_state для использования в фазе 2
            st.session_state.changes_info = changes_info
            
            # Сбрасываем флаг для следующего раза
            st.session_state.incremental_confirmed = False
        else:
            # Показываем информацию об изменениях и запрашиваем подтверждение
            st.info("🔍 Определение изменений...")
            
            # Определяем изменения
            changes_info = detect_changes(
                objects_data=all_objects_data,
                modules_data=modules_data,
                existing_objects_hashes=existing_objects_hashes,
                existing_modules_hashes=existing_modules_hashes
            )
            
            # Показываем статистику изменений
            st.markdown("### 📊 Статистика изменений")
            
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            
            with col1:
                st.metric("✅ Новые", len(changes_info['new_objects']))
            with col2:
                st.metric("🔄 Изм. метаданные", len(changes_info['changed_objects']))
            with col3:
                st.metric("📝 Только модули", len(changes_info.get('objects_with_module_changes', [])))
            with col4:
                st.metric("🔧 Изм. модулей", len(changes_info.get('changed_module_ids', [])))
            with col5:
                st.metric("❌ Удалённые", len(changes_info['deleted_object_ids']))
            with col6:
                st.metric("⏭️ Без изменений", len(changes_info['unchanged_objects']))
            
            # Детали изменений: одна таблица с фильтрами, поиском и выгрузкой в CSV
            if not build_changes_report(changes_info).empty:
                with st.expander("📋 Детали изменений", expanded=True):
                    render_changes_report(changes_info, key_prefix="changes_preview")

            # Если нет изменений - предупреждаем и останавливаем
            total_changes = (len(changes_info['new_objects']) + 
                            len(changes_info['changed_objects']) + 
                            len(changes_info.get('objects_with_module_changes', [])) + 
                            len(changes_info['deleted_object_ids']))
            
            if total_changes == 0:
                st.warning("⚠️ Изменений не обнаружено. Загрузка не требуется.")
                st.info("💡 Если вы хотите обновить все данные, отключите инкрементальное обновление.")
                st.stop()
            
            # Подтверждение
            st.warning(f"⚠️ Будет обработано {total_changes} изменений. Продолжить?")
            
            col_yes, col_no = st.columns(2)
            
            with col_yes:
                if st.button("✅ Продолжить обновление", type="primary", key="confirm_update"):
                    st.session_state.incremental_confirmed = True
                    st.session_state.incremental_cancelled = False
                    # Сохраняем changes_info для использования после rerun
                    st.session_state.changes_info = changes_info
                    st.rerun()
            
            with col_no:
                if st.button("❌ Отмена", key="cancel_update"):
                    st.session_state.incremental_cancelled = True
                    st.session_state.incremental_confirmed = False
                    st.rerun()
            
            if st.session_state.incremental_cancelled:
                st.info("Обновление отменено")
                # Сбрасываем все флаги и останавливаем загрузку
                st.session_state.incremental_confirmed = False
                st.session_state.incremental_cancelled = False
                st.session_state.loading_started = False
                st.session_state.loading_params = None
                st.stop()
            
            if not st.session_state.incremental_confirmed:
                st.stop()
    
    # Инициализация skip_object_update_keys для режима полной перезагрузки
    if not incremental_mode:
        st.session_state.skip_object_update_keys = set()
    
    # Получаем changes_info из session_state, если она там есть (после rerun)
    if incremental_mode and 'changes_info' in st.session_state:
        changes_info = st.session_state.changes_info
    
    # =================================================================================
    # =================================================================================
    st.info("🧠📤 Фаза 2/3: Генерация эмбеддингов и загрузка в Qdrant (streaming)...")
    phase23_start = time.time()
    
    st.info(f"⚙️ Параметры: батч эмбеддингов={embedding_batch_size}, батч загрузки={upload_batch_size}, retry={max_retries}, timeout={base_timeout}с")
    
    if incremental_mode and changes_info:
        st.info("🗑️ Удаление устаревших данных...")
        deleted_count = 0
        
        # Удаляем объекты
        if changes_info['deleted_object_ids']:
            st.info(f"🗑️ Удаление {len(changes_info['deleted_object_ids'])} объектов...")
            try:
                client.delete(
                    collection_name=COLLECTION_OBJECTS,
                    points_selector=changes_info['deleted_object_ids']
                )
                deleted_count += len(changes_info['deleted_object_ids'])
                st.success(f"✅ Удалено объектов: {len(changes_info['deleted_object_ids'])}")
            except Exception as e:
                st.warning(f"⚠️ Ошибка удаления объектов: {e}")
        
        # Удаляем модули
        if changes_info['deleted_module_ids']:
            st.info(f"🗑️ Удаление {len(changes_info['deleted_module_ids'])} модулей...")
            try:
                client.delete(
                    collection_name=COLLECTION_MODULES,
                    points_selector=changes_info['deleted_module_ids']
                )
                deleted_count += len(changes_info['deleted_module_ids'])
                st.success(f"✅ Удалено модулей: {len(changes_info['deleted_module_ids'])}")
            except Exception as e:
                st.warning(f"⚠️ Ошибка удаления модулей: {e}")
        
        # Объединяем ID удалённых и изменённых модулей для удаления их чанков/процедур
        modules_to_cleanup = (changes_info.get('deleted_module_ids', []) + 
                             changes_info.get('changed_module_ids', []))
        
        # Удаляем связанные чанки и процедуры для удалённых и изменённых модулей
        if modules_to_cleanup:
            st.info(f"🗑️ Удаление чанков и процедур для {len(modules_to_cleanup)} модулей...")
            try:
                # Удаляем батчами по 100 модулей: один delete-запрос на коллекцию за батч
                batch_size = 100
                cleanup_progress = st.progress(0.0)
                for i in range(0, len(modules_to_cleanup), batch_size):
                    batch_ids = modules_to_cleanup[i:i + batch_size]
                    batch_filter = Filter(
                        must=[FieldCondition(key="module_id", match=MatchAny(any=batch_ids))]
                    )
                    for collection_name in (COLLECTION_CHUNKS, COLLECTION_PROCEDURES):
                        try:
                            client.delete(collection_name=collection_name, points_selector=batch_filter)
                        except Exception as e:
                            st.warning(f"⚠️ Ошибка удаления данных из {collection_name} "
                                       f"(модули {i + 1}-{i + len(batch_ids)}): {e}")
                    cleanup_progress.progress(min(1.0, (i + len(batch_ids)) / len(modules_to_cleanup)))

                st.success(f"✅ Удалены связанные чанки и процедуры для {len(modules_to_cleanup)} модулей")
            except Exception as e:
                st.warning(f"⚠️ Ошибка удаления связанных данных: {e}")
        
        if deleted_count > 0:
            st.success(f"🗑️ Всего удалено точек: {deleted_count}")
        
        # Очищаем changes_info из session_state после использования
        if 'changes_info' in st.session_state:
            del st.session_state.changes_info
    
    # =================================================================================
    # STREAMING PROCESSING: Обработка чанками с генерацией эмбеддингов и загрузкой
    # =================================================================================
    current_timestamp = time.time()
    skip_object_update_keys = getattr(st.session_state, 'skip_object_update_keys', set())
    
    # Определяем размер чанка обработки (250 объектов - оптимизация для снижения overhead)
    PROCESSING_CHUNK_SIZE = 250
    total_chunks = (len(all_objects_data) + PROCESSING_CHUNK_SIZE - 1) // PROCESSING_CHUNK_SIZE
    
    st.info(f"📦 Обработка {len(all_objects_data)} объектов чанками по {PROCESSING_CHUNK_SIZE} (всего {total_chunks} чанков)")
    
    # Строка статистики последнего чанка (перезаписывается, чтобы лог не рос на больших конфигурациях)
    chunk_stats_placeholder = st.empty()

    # Счётчики для статистики
    total_uploaded = [0, 0, 0, 0]  # objects, modules, chunks, procedures
    total_reused_embeddings = 0  # Переиспользованные эмбеддинги
    
    # Получаем процесс для метрик памяти (опционально)
    process = psutil.Process() if PSUTIL_AVAILABLE else None
    
    for chunk_idx in range(total_chunks):
        # Определяем границы текущего чанка
        start_idx = chunk_idx * PROCESSING_CHUNK_SIZE
        end_idx = min(start_idx + PROCESSING_CHUNK_SIZE, len(all_objects_data))
        objects_chunk = all_objects_data[start_idx:end_idx]
        
        # Показываем использование памяти
        if process:
            memory_mb = process.memory_info().rss / 1024 / 1024
            status_text.text(f"📦 Чанк {chunk_idx + 1}/{total_chunks}: объекты {start_idx + 1}-{end_idx} (💾 {memory_mb:.0f} MB)")
        else:
            status_text.text(f"📦 Чанк {chunk_idx + 1}/{total_chunks}: объекты {start_idx + 1}-{end_idx}")
        
        # Обрабатываем чанк: генерируем эмбеддинги + создаем точки + загружаем
        chunk_result = process_objects_chunk_with_embeddings(
            client=client,
            objects_chunk=objects_chunk,
            modules_data=modules_data,
            vector_size=vector_size,
            current_timestamp=current_timestamp,
            skip_object_update_keys=skip_object_update_keys,
            upload_batch_size=upload_batch_size,
            chunk_start_idx=start_idx,
            embedding_batch_size=embedding_batch_size,
            max_retries=max_retries,
            base_timeout=base_timeout,
            show_progress=False
        )
        
        # Обновляем счётчики
        total_uploaded[0] += chunk_result[0]  # objects
        total_uploaded[1] += chunk_result[1]  # modules
        total_uploaded[2] += chunk_result[2]  # chunks
        total_uploaded[3] += chunk_result[3]  # procedures
        total_reused_embeddings += chunk_result[4]  # reused embeddings
        chunk_embedding_time = chunk_result[5]  # embedding time
        chunk_qdrant_time = chunk_result[6]  # qdrant time
        
        # Обновляем прогресс
        progress = 0.6 + ((chunk_idx + 1) / total_chunks) * 0.3  # 60-90%
        progress_bar.progress(progress)
        
        # Сохранение checkpoint (каждые 5 чанков для оптимального баланса)
        if (chunk_idx + 1) % 5 == 0 or (chunk_idx + 1) == total_chunks:
            checkpoint_data = {
                'phase': 3,
                'last_object_idx': end_idx,
                'processed_objects': [f"{o['obj_type']}.{o['obj_name']}" for o in all_objects_data[:end_idx]],
                'total_objects': len(all_objects_data),
                'incremental_mode': incremental_mode
            }
            save_checkpoint(checkpoint_data)
            status_text.text(f"💾 Checkpoint: {end_idx}/{len(all_objects_data)} объектов, загружено: {total_uploaded[0]} obj, {total_uploaded[1]} mod, {total_uploaded[2]} chunks, {total_uploaded[3]} proc")
        
        # КРИТИЧНО: Принудительная очистка памяти после каждого чанка
        gc.collect()
        
        # Показываем статистику чанка с использованием памяти и профилированием
        memory_after_mb = process.memory_info().rss / 1024 / 1024 if process else 0
        
        # Рассчитываем скорости
        total_points = chunk_result[0] + chunk_result[1] + chunk_result[2] + chunk_result[3]
        embeddings_per_sec = len(modules_data) * 40 / chunk_embedding_time if chunk_embedding_time > 0 else 0  # примерно 40 эмб/объект
        points_per_sec = total_points / chunk_qdrant_time if chunk_qdrant_time > 0 else 0
        
        chunk_stats = (f"✓ Чанк {chunk_idx + 1}/{total_chunks}: {chunk_result[0]} obj, {chunk_result[1]} mod, "
                       f"{chunk_result[2]} chunks, {chunk_result[3]} proc (переиспользовано: {chunk_result[4]}, "
                       f"память: {memory_after_mb:.0f} MB) | эмбеддинги: {chunk_embedding_time:.1f}с, "
                       f"Qdrant: {chunk_qdrant_time:.1f}с ({points_per_sec:.0f} точек/сек)")
        logger.info(chunk_stats)
        chunk_stats_placeholder.caption(chunk_stats)
    
    if total_reused_embeddings > 0:
        st.info(f"⚡ ОПТИМИЗАЦИЯ: Переиспользовано {total_reused_embeddings} эмбеддингов процедур для чанков (экономия ~{total_reused_embeddings} GPU запросов)")
    
    st.success(f"✅ Streaming обработка завершена: {total_uploaded[0]} объектов, {total_uploaded[1]} модулей, {total_uploaded[2]} чанков, {total_uploaded[3]} процедур")
    
    # Завершение обработки
    phase23_time = time.time() - phase23_start
    st.success(f"✅ Фазы 2/3 завершены за {phase23_time:.1f} сек")
    
    # Очистка checkpoint после успешного завершения
    clear_checkpoint()
    
    status_text.text("✅ Загрузка завершена!")
    
    # Включаем индексацию обратно после загрузки всех данных
    if not incremental_mode:
        st.info("🔧 Включение индексации и оптимизация коллекций...")
        try:
            client.update_collection(
                collection_name=COLLECTION_OBJECTS,
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=20000  # Включить индексацию обратно
                )
            )
            client.update_collection(
                collection_name=COLLECTION_MODULES,
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=20000
                )
            )
            client.update_collection(
                collection_name=COLLECTION_CHUNKS,
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=20000
                )
            )
            client.update_collection(
                collection_name=COLLECTION_PROCEDURES,
                optimizers_config=OptimizersConfigDiff(
                    indexing_threshold=20000
                )
            )
            st.success("✅ Индексация включена для всех коллекций")
        except Exception as e:
            st.warning(f"⚠️ Ошибка при включении индексации: {e}")
    
    # Статистика
    total_time = time.time() - (phase1_start)
    
    # Расчет метрик производительности
    total_objects = client.get_collection(COLLECTION_OBJECTS).points_count
    total_modules = client.get_collection(COLLECTION_MODULES).points_count
    total_code_chunks = client.get_collection(COLLECTION_CHUNKS).points_count
    total_procedures = client.get_collection(COLLECTION_PROCEDURES).points_count
    total_points = total_objects + total_modules + total_code_chunks + total_procedures
    
    # Скорость обработки
    objects_per_minute = (len(all_objects_data) / total_time) * 60 if total_time > 0 else 0
    run_points = sum(total_uploaded)
    points_per_minute = (run_points / phase23_time) * 60 if phase23_time > 0 else 0
    
    # Показываем текущее использование памяти
    final_memory_mb = process.memory_info().rss / 1024 / 1024 if process else 0
    
    st.success(f"""
    ### 🔄 Этот запуск ({'инкрементальное обновление' if incremental_mode else 'полная загрузка'}):
    - **Загружено:** объектов {total_uploaded[0]}, модулей {total_uploaded[1]}, чанков {total_uploaded[2]}, процедур {total_uploaded[3]}
    - **Обработано объектов из архива:** {len(all_objects_data)}

    ### 📊 Итого в базе:
    - **Объектов:** {total_objects}
    - **Модулей:** {total_modules}
    - **Чанков кода:** {total_code_chunks}
    - **Процедур/Функций:** {total_procedures}
    - **Всего точек:** {total_points}

    ### ⏱️ Время выполнения:
    - Фаза 1 (сбор данных): {phase1_time:.1f} сек
    - Фаза 2/3 (streaming эмбеддинг + загрузка): {phase23_time:.1f} сек
    - **Всего:** {total_time:.1f} сек
    
    ### 🚀 Производительность:
    - Скорость обработки объектов: **{objects_per_minute:.1f} об/мин**
    - Скорость загрузки в Qdrant: **{points_per_minute:.0f} точек/мин**
    - Пакетов обработано: **{total_chunks} по {PROCESSING_CHUNK_SIZE} объектов**
    
    ### 💾 Использование памяти:
    - Текущее использование ОЗУ: **{final_memory_mb:.0f} MB**
    - Оптимизация v6.5: **~25-30x снижение использования ОЗУ**
    
    ### ⚙️ Параметры:
    - Батч эмбеддингов: **{embedding_batch_size}**
    - Батч загрузки: **{upload_batch_size}**
    - Потоков чтения: **{max_workers}**
    - Максимальных попыток: **{max_retries}**
    """)
    
    if incremental_mode and st.session_state.get('last_changes_info'):
        with st.expander("📋 Отчёт об изменениях этого запуска", expanded=False):
            render_changes_report(st.session_state.last_changes_info, key_prefix="changes_final")

    # Очистка
    import shutil
    shutil.rmtree(temp_dir)


def main():
    
    st.set_page_config(page_title="1C → Qdrant", page_icon="🚀", layout="wide")
    
    st.title("🚀 Загрузчик 1С в Qdrant")
    st.write("**Streaming загрузка + Инкрементальное обновление + Retry логика**")
    
    # Инициализация session_state для процесса загрузки
    if 'loading_started' not in st.session_state:
        st.session_state.loading_started = False
    if 'loading_params' not in st.session_state:
        st.session_state.loading_params = None
    
    # Боковая панель с настройками
    st.sidebar.header("⚙️ Настройки")
    st.sidebar.write(f"**Qdrant:** {QDRANT_HOST}:{QDRANT_PORT}")
    st.sidebar.write(f"**Embedding:** {EMBEDDING_SERVICE_URL}")
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔧 Параметры обработки")
    
    # Переключатель режима настроек
    settings_mode = st.sidebar.radio(
        "Режим настроек",
        options=["По умолчанию", "Расширенные"],
        help="""
        **По умолчанию**: Проверенные оптимальные значения для большинства систем
        
        **Расширенные**: Настройка всех параметров производительности вручную
        """
    )
    
    # Дефолтные значения (оптимизированы для средней конфигурации)
    DEFAULT_SETTINGS = {
        'embedding_batch_size': 300,
        'max_retries': 3,
        'base_timeout': 180,
        'upload_batch_size': 100,
        'scroll_batch_size': 1000,
        'max_workers': 10,
        'max_chunk_lines': 500,
        'overlap_lines': 50,
        'max_context_lines': 50
    }
    
    if settings_mode == "По умолчанию":
        # Используем дефолтные настройки
        settings = DEFAULT_SETTINGS.copy()
        st.sidebar.success("✅ Используются оптимальные настройки")
        
        # Показываем только основные параметры
        with st.sidebar.expander("ℹ️ Текущие параметры"):
            st.write(f"• Батч эмбеддингов: **{settings['embedding_batch_size']}**")
            st.write(f"• Потоков чтения: **{settings['max_workers']}**")
            st.write(f"• Батч загрузки Qdrant: **{settings['upload_batch_size']}**")
    
    else:  # Расширенные настройки
        settings = {}
        
        # Пресеты конфигураций
        st.sidebar.markdown("#### 🎯 Быстрые пресеты")
        
        preset_cols = st.sidebar.columns(2)
        
        PRESETS = {
            'Слабый ПК': {
                'embedding_batch_size': 16,
                'max_retries': 3,
                'base_timeout': 240,
                'upload_batch_size': 50,
                'scroll_batch_size': 500,
                'max_workers': 4,
                'max_chunk_lines': 500,
                'overlap_lines': 50,
                'max_context_lines': 50
            },
            'Средний ПК': {
                'embedding_batch_size': 100,
                'max_retries': 3,
                'base_timeout': 180,
                'upload_batch_size': 200,
                'scroll_batch_size': 1000,
                'max_workers': 8,
                'max_chunk_lines': 500,
                'overlap_lines': 50,
                'max_context_lines': 50
            },
            'Мощный ПК': {
                'embedding_batch_size': 300,
                'max_retries': 3,
                'base_timeout': 180,
                'upload_batch_size': 200,
                'scroll_batch_size': 1000,
                'max_workers': 16,
                'max_chunk_lines': 500,
                'overlap_lines': 50,
                'max_context_lines': 50
            },
            'Сервер': {
                'embedding_batch_size': 500,
                'max_retries': 3,
                'base_timeout': 300,
                'upload_batch_size': 500,
                'scroll_batch_size': 2000,
                'max_workers': 24,
                'max_chunk_lines': 500,
                'overlap_lines': 50,
                'max_context_lines': 50
            }
        }
        
        # Инициализация session_state для выбора пресета
        if 'selected_preset' not in st.session_state:
            st.session_state.selected_preset = None
        
        with preset_cols[0]:
            if st.button("💻 Слабый ПК", help="2-4 ядра, 8GB RAM, CPU"):
                st.session_state.selected_preset = 'Слабый ПК'
            if st.button("🏢 Сервер", help="16+ ядер, 64GB+ RAM, GPU"):
                st.session_state.selected_preset = 'Сервер'
        
        with preset_cols[1]:
            if st.button("💼 Средний ПК", help="4-8 ядер, 16GB RAM, CPU/GPU"):
                st.session_state.selected_preset = 'Средний ПК'
            if st.button("🖥️ Мощный ПК", help="8+ ядер, 32GB+ RAM, мощная GPU"):
                st.session_state.selected_preset = 'Мощный ПК'
        
        # Если выбран пресет, применяем его значения
        preset_values = PRESETS.get(st.session_state.selected_preset, PRESETS['Средний ПК'])
        
        if st.session_state.selected_preset:
            st.sidebar.success(f"✅ Выбран пресет: {st.session_state.selected_preset}")
        
        st.sidebar.markdown("---")
        st.sidebar.markdown("#### 🔧 Тонкая настройка")
        
        # === EMBEDDING SETTINGS ===
        with st.sidebar.expander("🧠 Эмбеддинги и сеть", expanded=True):
            settings['embedding_batch_size'] = st.slider(
                "Размер батча эмбеддингов",
                min_value=8,
                max_value=500,
                value=preset_values['embedding_batch_size'],
                step=8,
                help="""
                Количество текстов в одном запросе к embedding service.
                
                **Рекомендации:**
                • CPU (2-4 ядра): 8-16
                • CPU (4-8 ядер): 16-32
                • GPU (4GB VRAM): 50-100
                • GPU (8GB VRAM): 100-200
                • GPU (16GB+ VRAM): 200-500
                
                ⚠️ Большие значения = быстрее, но больше памяти
                """
            )
            
            settings['max_retries'] = st.number_input(
                "Макс. попыток при ошибке",
                min_value=1,
                max_value=10,
                value=preset_values['max_retries'],
                help="""
                Количество повторных попыток при timeout/ошибке сети.
                
                **Рекомендации:**
                • Стабильная сеть: 2-3
                • Нестабильная сеть: 4-5
                • Удаленный сервер: 5-7
                """
            )
            
            settings['base_timeout'] = st.number_input(
                "Базовый таймаут (сек)",
                min_value=60,
                max_value=600,
                value=preset_values['base_timeout'],
                step=30,
                help="""
                Минимальное время ожидания ответа от embedding service.
                
                **Рекомендации:**
                • Локальный сервер: 60-120
                • Сеть LAN: 120-180
                • Удаленный сервер: 180-300
                • Слабый сервер: 240-600
                """
            )
        
        # Категория 2: Загрузка в Qdrant
        with st.sidebar.expander("💾 Загрузка в Qdrant"):
            settings['upload_batch_size'] = st.slider(
                "Размер батча загрузки",
                min_value=10,
                max_value=1000,
                value=preset_values['upload_batch_size'],
                step=10,
                help="""
                Количество точек в одном upsert запросе к Qdrant.
                
                **Рекомендации:**
                • HDD + медленная сеть: 50-100
                • SSD + нормальная сеть: 100-200
                • NVMe + быстрая сеть: 200-500
                • Локальный Qdrant на NVMe: 500-1000
                
                ⚠️ Слишком большие значения могут вызвать timeout
                """
            )
            
            settings['scroll_batch_size'] = st.slider(
                "Размер страницы Scroll API",
                min_value=100,
                max_value=5000,
                value=preset_values['scroll_batch_size'],
                step=100,
                help="""
                Размер страницы при чтении данных из Qdrant.
                
                **Рекомендации:**
                • Медленная система: 500-1000
                • Нормальная система: 1000-2000
                • Мощная система: 2000-5000
                
                Влияет только на скорость инкрементального обновления.
                """
            )
        
        # Категория 3: Параллельная обработка
        with st.sidebar.expander("🔧 Обработка данных"):
            settings['max_workers'] = st.slider(
                "Потоков чтения",
                min_value=1,
                max_value=32,
                value=preset_values['max_workers'],
                help="""
                Количество параллельных потоков для чтения файлов.
                
                **Рекомендации:**
                • 2-4 ядра: 4-6 потоков
                • 4-8 ядер: 8-12 потоков
                • 8+ ядер: 12-24 потоков
                
                ⚠️ Больше потоков = быстрее чтение, но больше RAM
                """
            )
            
            settings['max_chunk_lines'] = st.number_input(
                "Макс. строк в чанке",
                min_value=100,
                max_value=2000,
                value=preset_values['max_chunk_lines'],
                step=100,
                help="""
                Максимальный размер чанка кода.
                
                **Рекомендации:**
                • Средний: 300-500 строк
                • Для мощных систем: 500-1000
                
                Влияет на точность поиска по коду.
                """
            )
            
            settings['overlap_lines'] = st.number_input(
                "Перекрытие чанков",
                min_value=0,
                max_value=200,
                value=preset_values['overlap_lines'],
                step=10,
                help="""
                Количество перекрывающихся строк между чанками.
                
                **Рекомендации:**
                • Без перекрытия: 0
                • Небольшое: 25-50
                • Среднее: 50-100
                
                Помогает избежать потери контекста на границах чанков.
                """
            )
            
            settings['max_context_lines'] = st.number_input(
                "Строк контекста модуля",
                min_value=0,
                max_value=200,
                value=preset_values['max_context_lines'],
                step=10,
                help="""
                Сколько строк из начала модуля добавлять в контекст чанка.
                
                Включает объявления переменных, импорты, комментарии.
                """
            )
    
    # === ЗАГРУЗКА ФАЙЛА ===
    st.markdown("---")
    st.subheader("📁 Выбор файла")
    
    uploaded_file = st.file_uploader(
        "Выберите ZIP архив с выгруженной конфигурацией 1С",
        type=['zip'],
        help="Архив из parser/out_conf/parsed_<имя_конфигурации>/ (merged.zip - для конфигурации с расширениями)"
    )
    
    # === РЕЖИМ ЗАГРУЗКИ ===
    incremental_mode = st.checkbox(
        "🔄 Инкрементальное обновление",
        value=True,
        help="""
        **Инкрементальное обновление** обрабатывает только изменённые объекты:
        
        ✅ Быстрее в 10-20 раз для небольших изменений
        ✅ Экономит ресурсы
        ✅ Автоматически удаляет устаревшие данные
        
        ⚠️ Первая загрузка всегда полная
        """
    )
    
    # === ОПЦИИ ===
    with st.expander("⚙️ Дополнительные опции"):
        force_restart = st.checkbox(
            "🔄 Принудительная перезагрузка",
            value=False,
            help="Игнорировать checkpoint и начать загрузку с начала"
        )
    
    # === КНОПКА ЗАГРУЗКИ ===
    if uploaded_file is not None:
        if st.button("🚀 Запустить загрузку", type="primary", disabled=st.session_state.loading_started):
            # Сохраняем параметры в session_state
            params = settings.copy()
            params['incremental_mode'] = incremental_mode
            params['force_restart'] = force_restart
            st.session_state.loading_params = params
            st.session_state.loading_started = True
            st.rerun()
    
    # === ЗАПУСК ЗАГРУЗКИ ===
    if st.session_state.loading_started and st.session_state.loading_params:
        params = st.session_state.loading_params
        
        try:
            # Сохраняем загруженный файл
            temp_path = f"temp_{uploaded_file.name}"
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getvalue())
            
            # Запускаем загрузку с параметрами
            process_uploaded_data(
                zip_path=temp_path,
                force_restart=params.get('force_restart', False),
                incremental_mode=params.get('incremental_mode', False),
                embedding_batch_size=params.get('embedding_batch_size', 300),
                max_retries=params.get('max_retries', 3),
                base_timeout=params.get('base_timeout', 180),
                upload_batch_size=params.get('upload_batch_size', 100),
                scroll_batch_size=params.get('scroll_batch_size', 1000),
                max_workers=params.get('max_workers', 10),
                max_chunk_lines=params.get('max_chunk_lines', 500),
                overlap_lines=params.get('overlap_lines', 50),
                max_context_lines=params.get('max_context_lines', 50)
            )
            
            # Удаляем временный файл
            import os
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            st.session_state.loading_started = False
            st.session_state.loading_params = None
            
        except Exception as e:
            st.error(f"❌ Ошибка при загрузке: {e}")
            import traceback
            st.code(traceback.format_exc())
            
            st.session_state.loading_started = False
            st.session_state.loading_params = None
    
    # === ИНФОРМАЦИЯ ===
    if not uploaded_file:
        st.info("""
        ### 📝 Инструкция:
        1. Выгрузите конфигурацию (и расширения) в файлы: Конфигуратор → *Конфигурация → Выгрузить конфигурацию в файлы*
        2. Положите выгрузку в `parser/in_conf/` и откройте парсер (контейнер parser-1c, http://localhost:8504)
        3. Возьмите ZIP архив из `parser/out_conf/parsed_<имя_конфигурации>/` (для конфигурации с расширениями - `merged.zip`)
        4. Загрузите его здесь
        
        ### ✨ Возможности:
        - 🔄 **Streaming Upload**: Снижает использование ОЗУ в 3-5 раз
        - ⏱️ **Увеличенные таймауты**: До 600 секунд для Qdrant
        - 🔁 **Retry логика**: Автоматические повторные попытки
        - 🧹 **Управление памятью**: gc.collect() после каждого чанка
        """)


if __name__ == "__main__":
    main()
