#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Индексатор синтаксиса 1С в Qdrant.

Создаёт коллекцию и загружает данные с векторизацией.
"""

import requests
import json
from typing import Dict, List, Any
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
try:
    import streamlit as st
except ImportError:
    st = None  # Для использования вне Streamlit


def get_embeddings(texts: List[str], embedding_service_url: str, task: str = "retrieval.passage") -> List[List[float]]:
    """
    Получение эмбеддингов через сервис.
    
    Args:
        texts: Список текстов для векторизации
        embedding_service_url: URL сервиса эмбеддингов
        task: Тип задачи
    
    Returns:
        Список векторов
    """
    payload = json.dumps({"texts": texts, "task": task})
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.post(
            f"{embedding_service_url}/embed",
            headers=headers,
            data=payload,
            timeout=300
        )
        response.raise_for_status()
        return response.json()["embeddings"]
    except Exception as e:
        error_msg = f"Ошибка получения эмбеддингов: {e}"
        if st:
            st.error(error_msg)
        raise Exception(error_msg)


def create_syntax_collection(
    qdrant_client: QdrantClient,
    collection_name: str,
    vector_size: int = 768
):
    """
    Создание коллекции для синтаксиса 1С.
    
    Args:
        qdrant_client: Клиент Qdrant
        collection_name: Имя коллекции
        vector_size: Размер вектора
    """
    # Удаляем коллекцию если существует (как в основном loader)
    if qdrant_client.collection_exists(collection_name):
        if st:
            st.info(f"Удаляем существующую коллекцию {collection_name}...")
        qdrant_client.delete_collection(collection_name)
    
    # Создаём коллекцию с двумя векторами
    if st:
        st.info(f"Создание коллекции {collection_name}...")
    qdrant_client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "name": VectorParams(size=vector_size, distance=Distance.COSINE),
            "description": VectorParams(size=vector_size, distance=Distance.COSINE)
        }
    )
    
    if st:
        st.success(f"✅ Коллекция {collection_name} создана")


def _syntax_items(data: Dict[str, Any]):
    """
    Элементы синтаксиса для индексации: (тип для статистики, текст имени, текст описания, payload).
    """
    for func in data.get("global_functions", []):
        name = func.get("name", "")
        description = func.get("description", "")
        yield "global_functions", name, description, {
            "item_type": "global_function",
            "name": name,
            "name_en": func.get("name_en", ""),
            "object_name": None,
            "category": func.get("category", ""),
            "description": description,
            "syntax": func.get("syntax", ""),
            "parameters": func.get("parameters", []),
            "return_type": func.get("return_type", ""),
            "examples": func.get("examples", []),
            "remarks": func.get("remarks", "")
        }

    for method in data.get("object_methods", []):
        method_name = method.get("name", "")
        object_name = method.get("object_name", "")
        description = method.get("description", "")
        yield "object_methods", f"{object_name}.{method_name}", description, {
            "item_type": "object_method",
            "name": method_name,
            "name_en": method.get("name_en", ""),
            "object_name": object_name,
            "category": "Методы объектов",
            "description": description,
            "syntax": method.get("syntax", ""),
            "parameters": method.get("parameters", []),
            "return_type": method.get("return_type", ""),
            "examples": method.get("examples", []),
            "remarks": method.get("remarks", "")
        }

    for prop in data.get("object_properties", []):
        prop_name = prop.get("name", "")
        object_name = prop.get("object_name", "")
        description = prop.get("description", "")
        yield "object_properties", f"{object_name}.{prop_name}", description, {
            "item_type": "property",
            "name": prop_name,
            "name_en": prop.get("name_en", ""),
            "object_name": object_name,
            "category": "Свойства объектов",
            "description": description,
            "syntax": f"{object_name}.{prop_name}",
            "property_type": prop.get("type", ""),
            "remarks": prop.get("remarks", "")
        }


def index_syntax_data(
    data: Dict[str, Any],
    qdrant_client: QdrantClient,
    collection_name: str,
    embedding_service_url: str,
    batch_size: int = 100
) -> Dict[str, int]:
    """
    Индексация данных синтаксиса в Qdrant в формате с плоскими списками.
    Эмбеддинги запрашиваются пакетно: один запрос на batch_size элементов
    (раньше - отдельный HTTP-запрос на каждый элемент, загрузка шла в разы дольше).
    Args:
        data: подготовленный dict со списками:
            - global_functions
            - object_methods
            - object_properties
        qdrant_client: Клиент Qdrant
        collection_name: Имя коллекции
        embedding_service_url: URL сервиса эмбеддингов
        batch_size: Размер батча для эмбеддингов и загрузки
    Returns:
        Статистика загрузки
    """
    stats = {
        "global_functions": 0,
        "object_methods": 0,
        "object_properties": 0
    }

    total_items = (
        len(data.get("global_functions", [])) +
        len(data.get("object_methods", [])) +
        len(data.get("object_properties", []))
    )

    progress_bar = st.progress(0) if st else None
    status_text = st.empty() if st else None
    processed = 0
    point_id = 1

    def flush(batch):
        nonlocal processed, point_id
        texts = []
        for _, name_text, description, _ in batch:
            texts.extend([name_text, description])
        embeddings = get_embeddings(texts, embedding_service_url, task="retrieval.passage")
        points = []
        for idx, (kind, _, _, payload) in enumerate(batch):
            points.append(PointStruct(
                id=point_id,
                vector={"name": embeddings[2 * idx], "description": embeddings[2 * idx + 1]},
                payload=payload
            ))
            point_id += 1
            stats[kind] += 1
        qdrant_client.upsert(collection_name=collection_name, points=points)
        processed += len(batch)
        if progress_bar:
            progress_bar.progress(processed / total_items)
        if status_text:
            status_text.text(f"Загружено: {processed}/{total_items}")

    batch = []
    for item in _syntax_items(data):
        batch.append(item)
        if len(batch) >= batch_size:
            flush(batch)
            batch = []
    if batch:
        flush(batch)

    if progress_bar:
        progress_bar.progress(1.0)
    if status_text:
        status_text.text(f"✅ Загружено: {processed}/{total_items}")
    return stats
