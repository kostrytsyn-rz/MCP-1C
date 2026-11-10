#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP инструменты для поиска по синтаксису 1С.

Предоставляет 4 инструмента:
- search_1c_global_function - поиск глобальных функций
- search_1c_object_method - поиск методов объектов
- search_1c_property - поиск свойств
- get_1c_syntax_details - детальная информация о синтаксисе
"""

import sys
import os

# Добавляем путь к родительской директории для импорта config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, Prefetch, FusionQuery, Fusion
from typing import List
import requests
import json
import re
import logging

from config import (
    QDRANT_HOST, QDRANT_PORT, EMBEDDING_SERVICE_URL,
    COLLECTION_SYNTAX, DEFAULT_SEARCH_LIMIT
)

# Логирование
logger = logging.getLogger(__name__)

# Клиент Qdrant
qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


def get_query_embedding(query: str) -> List[float]:
    """Получение эмбеддинга для запроса"""
    payload = json.dumps({"texts": [query], "task": "retrieval.query"})
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.post(
            f"{EMBEDDING_SERVICE_URL}/embed",
            headers=headers,
            data=payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        if "embeddings" not in data or not data["embeddings"]:
            raise Exception("Сервис эмбеддингов вернул пустой ответ")
        
        return data["embeddings"][0]
    except requests.RequestException as e:
        raise Exception(f"Ошибка получения эмбеддинга: {str(e)}")


def _format_parameters(parameters: list, bold: bool = False) -> List[str]:
    """
    Параметры из синтакс-помощника. Признак обязательности уже входит в имя
    («<Строка> (обязательный)»), поле required в данных отсутствует - не дописываем его.
    """
    lines = []
    for param in parameters or []:
        name = param.get('name', '')
        param_type = param.get('type', '')
        description = (param.get('description') or '').strip()
        if 'required' in param and '(обязательный)' not in name and '(необязательный)' not in name:
            name += " (обязательный)" if param.get('required') else " (необязательный)"
        # Описание в справке начинается с «Тип: X .» - тип уже выведен отдельно
        description = re.sub(r'^Тип:\s*[^.]*\.\s*', '', description)
        name_md = f"**{name}**" if bold else f"`{name}`"
        line = f"- {name_md}"
        if param_type:
            line += f" — Тип: `{param_type}`"
        if description:
            line += f". {description[:300]}"
        lines.append(line)
    return lines


def search_1c_global_function(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """
    Поиск глобальных функций 1С.
    
    Используйте для поиска встроенных функций платформы 1С:Предприятие.
    
    Args:
        query: Описание или название функции (например: "длина строки", "формат даты", "округление числа")
        limit: Количество результатов (по умолчанию 5)
    
    Returns:
        Список функций с синтаксисом, параметрами и примерами
    
    Examples:
        search_1c_global_function("длина строки")
        search_1c_global_function("округление числа")
        search_1c_global_function("текущая дата")
    """
    try:
        # Проверяем существование коллекции
        try:
            qdrant_client.get_collection(COLLECTION_SYNTAX)
        except Exception as e:
            logger.error(f"Коллекция не найдена: {e}")
            return "❌ Коллекция синтаксиса не найдена. Загрузите документацию через Syntax Loader (http://localhost:8502)"
        
        # Получаем эмбеддинг запроса
        try:
            query_embedding = get_query_embedding(query)
            logger.debug(f"Эмбеддинг получен, размер: {len(query_embedding)}")
        except Exception as e:
            logger.error(f"Ошибка получения эмбеддинга: {e}")
            return f"❌ Ошибка получения эмбеддинга: {str(e)}"
        
        # Фильтр по типу
        query_filter = Filter(
            must=[FieldCondition(key="item_type", match=MatchValue(value="global_function"))]
        )
        
        # Мультивекторный поиск по обоим векторам (name и description)
        try:
            # Сначала пробуем простой поиск по одному вектору для диагностики
            try:
                simple_results = qdrant_client.query_points(
                    collection_name=COLLECTION_SYNTAX,
                    query=query_embedding,
                    using="name",
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True
                )
                if simple_results.points:
                    logger.info(f"Простой поиск по 'name' нашел {len(simple_results.points)} результатов")
                    results = simple_results
                else:
                    # Пробуем мультивекторный поиск
                    results = qdrant_client.query_points(
                        collection_name=COLLECTION_SYNTAX,
                        prefetch=[
                            Prefetch(
                                query=query_embedding,
                                using="name",
                                filter=query_filter,
                                limit=limit * 3
                            ),
                            Prefetch(
                                query=query_embedding,
                                using="description",
                                filter=query_filter,
                                limit=limit * 3
                            )
                        ],
                        query=FusionQuery(fusion=Fusion.RRF),
                        limit=limit,
                        with_payload=True
                    )
                    logger.debug(f"Мультивекторный поиск выполнен, найдено точек: {len(results.points) if results.points else 0}")
            except Exception as e:
                logger.error(f"Ошибка при простом поиске, пробуем мультивекторный: {e}")
                results = qdrant_client.query_points(
                    collection_name=COLLECTION_SYNTAX,
                    prefetch=[
                        Prefetch(
                            query=query_embedding,
                            using="name",
                            filter=query_filter,
                            limit=limit * 3
                        ),
                        Prefetch(
                            query=query_embedding,
                            using="description",
                            filter=query_filter,
                            limit=limit * 3
                        )
                    ],
                    query=FusionQuery(fusion=Fusion.RRF),
                    limit=limit,
                    with_payload=True
                )
            logger.debug(f"Поиск выполнен, найдено точек: {len(results.points) if results.points else 0}")
        except Exception as e:
            logger.error(f"Ошибка поиска в Qdrant: {e}")
            return f"❌ Ошибка поиска в базе данных: {str(e)}"
        
        if not results.points:
            # Диагностика: проверяем, есть ли вообще функции в коллекции
            try:
                test_results = qdrant_client.scroll(
                    collection_name=COLLECTION_SYNTAX,
                    scroll_filter=Filter(
                        must=[FieldCondition(key="item_type", match=MatchValue(value="global_function"))]
                    ),
                    limit=5,
                    with_payload=True
                )
                has_functions = len(test_results[0]) > 0 if test_results else False
                
                if has_functions:
                    sample = test_results[0][0].payload
                    logger.warning(f"В коллекции есть функции, пример: {sample.get('name')}, но поиск не нашел результаты для запроса '{query}'")
                    return f"По запросу '{query}' глобальные функции не найдены (хотя в коллекции есть {len(test_results[0]) if test_results else 0} функций).\n\n💡 Попробуйте:\n- Изменить запрос (например: 'строка', 'дата', 'число', 'СтрДлина')\n- Использовать точное имя функции\n- Проверить логи MCP сервера для деталей"
                else:
                    logger.warning("В коллекции нет элементов типа 'global_function'")
                    return f"❌ В коллекции нет элементов типа 'global_function'.\n\n💡 Возможно:\n- Функции не были найдены/загружены из .hbk файла\n- Переиндексируйте файл через Syntax Loader (http://localhost:8502)\n- Проверьте статистику загрузки (должно быть ~350 функций)"
            except Exception as diag_e:
                logger.error(f"Ошибка диагностики: {diag_e}")
            
            return f"По запросу '{query}' глобальные функции не найдены.\n\n💡 Попробуйте:\n- Изменить запрос (например: 'строка', 'дата', 'число')\n- Использовать другие ключевые слова\n- Проверить, что документация загружена"
        
        output = [f"Найдено глобальных функций: {len(results.points)}\n"]
        
        for i, result in enumerate(results.points, 1):
            payload = result.payload
            score = getattr(result, 'score', 0)
            
            output.append(f"\n{'=' * 60}")
            output.append(f"Результат {i} (релевантность: {score:.3f})")
            output.append(f"{'=' * 60}")
            output.append(f"**Функция:** {payload.get('name', 'Unknown')}")
            
            if payload.get('name_en'):
                output.append(f"**Английское имя:** {payload.get('name_en')}")
            
            if payload.get('category'):
                output.append(f"**Категория:** {payload.get('category')}")
            
            output.append(f"\n**Описание:**")
            output.append(payload.get('description', 'Нет описания'))
            
            output.append(f"\n**Синтаксис:**")
            output.append(f"```bsl\n{payload.get('syntax', 'N/A')}\n```")
            
            # Параметры
            parameters = payload.get('parameters', [])
            if parameters:
                output.append(f"\n**Параметры:**")
                output.extend(_format_parameters(parameters))
            
            # Возвращаемое значение
            if payload.get('return_type'):
                output.append(f"\n**Возвращает:** {payload.get('return_type')}")
            
            # Примеры
            examples = payload.get('examples', [])
            if examples:
                output.append(f"\n**Примеры:**")
                for example in examples[:2]:  # Максимум 2 примера
                    output.append(f"```bsl\n{example}\n```")
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Ошибка поиска глобальных функций: {e}", exc_info=True)
        return f"❌ Ошибка поиска: {str(e)}\n\nДетали:\n{type(e).__name__}: {str(e)}"


def search_1c_object_method(
    object_name: str | None = None,
    method_query: str = "",
    limit: int = DEFAULT_SEARCH_LIMIT
) -> str:
    """
    Поиск методов объектов 1С.
    
    Args:
        object_name: Имя объекта (ТаблицаЗначений, Запрос, СписокЗначений, Файл и т.д.)
        method_query: Описание метода (например: "добавить строку", "выполнить запрос")
        limit: Количество результатов (по умолчанию 5)
    
    Returns:
        Список методов с синтаксисом и примерами
    
    Examples:
        search_1c_object_method("ТаблицаЗначений", "добавить")
        search_1c_object_method("Запрос", "выполнить")
        search_1c_object_method(method_query="получить элемент по индексу")
    """
    try:
        # Проверяем существование коллекции
        try:
            qdrant_client.get_collection(COLLECTION_SYNTAX)
        except Exception:
            return "❌ Коллекция синтаксиса не найдена. Загрузите документацию через Syntax Loader (http://localhost:8502)"
        
        # Формируем запрос
        if object_name and method_query:
            query = f"{object_name} {method_query}"
        elif object_name:
            query = object_name
        elif method_query:
            query = method_query
        else:
            return "❌ Укажите хотя бы object_name или method_query"
        
        # Получаем эмбеддинг запроса
        query_embedding = get_query_embedding(query)
        
        # Фильтр
        filter_conditions = [
            FieldCondition(key="item_type", match=MatchValue(value="object_method"))
        ]
        
        if object_name:
            filter_conditions.append(
                FieldCondition(key="object_name", match=MatchValue(value=object_name))
            )
        
        query_filter = Filter(must=filter_conditions)
        
        # Мультивекторный поиск по обоим векторам
        results = qdrant_client.query_points(
            collection_name=COLLECTION_SYNTAX,
            prefetch=[
                Prefetch(
                    query=query_embedding,
                    using="name",
                    filter=query_filter,
                    limit=limit * 3
                ),
                Prefetch(
                    query=query_embedding,
                    using="description",
                    filter=query_filter,
                    limit=limit * 3
                )
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True
        )
        
        if not results.points:
            return f"По запросу '{query}' методы объектов не найдены.\n\n💡 Попробуйте:\n- Проверить написание объекта\n- Изменить описание метода\n- Искать без указания объекта"
        
        output = [f"Найдено методов объектов: {len(results.points)}\n"]
        
        for i, result in enumerate(results.points, 1):
            payload = result.payload
            score = getattr(result, 'score', 0)
            
            output.append(f"\n{'=' * 60}")
            output.append(f"Результат {i} (релевантность: {score:.3f})")
            output.append(f"{'=' * 60}")
            output.append(f"**Метод:** {payload.get('object_name', 'Unknown')}.{payload.get('name', 'Unknown')}")
            
            if payload.get('name_en'):
                output.append(f"**Английское имя:** {payload.get('name_en')}")
            
            output.append(f"\n**Описание:**")
            output.append(payload.get('description', 'Нет описания'))
            
            output.append(f"\n**Синтаксис:**")
            syntax = payload.get('syntax', 'N/A')
            if not syntax.startswith(payload.get('object_name', '')):
                # Добавляем имя объекта если его нет в синтаксисе
                syntax = f"<{payload.get('object_name', 'Объект')}>.{syntax}"
            output.append(f"```bsl\n{syntax}\n```")
            if payload.get('parameters'):
                output.append(f"\n**Параметры:**")
                output.extend(_format_parameters(payload.get('parameters')))
            
            # Возвращаемое значение
            if payload.get('return_type'):
                output.append(f"\n**Возвращает:** {payload.get('return_type')}")
            
            # Примеры
            examples = payload.get('examples', [])
            if examples:
                output.append(f"\n**Примеры:**")
                for example in examples[:2]:
                    output.append(f"```bsl\n{example}\n```")
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Ошибка поиска методов объектов: {e}")
        return f"❌ Ошибка поиска: {str(e)}"


def search_1c_property(query: str, object_name: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """
    Поиск свойств объектов 1С.
    
    Args:
        query: Описание свойства (например: "колонки", "количество элементов")
        object_name: Имя объекта (опционально, для уточнения поиска)
        limit: Количество результатов (по умолчанию 5)
    
    Returns:
        Список свойств с описанием и типами
    
    Examples:
        search_1c_property("колонки", "ТаблицаЗначений")
        search_1c_property("количество элементов")
        search_1c_property("имя файла", "Файл")
    """
    try:
        # Проверяем существование коллекции
        try:
            qdrant_client.get_collection(COLLECTION_SYNTAX)
        except Exception:
            return "❌ Коллекция синтаксиса не найдена. Загрузите документацию через Syntax Loader (http://localhost:8502)"
        
        # Формируем запрос
        search_query = f"{object_name} {query}" if object_name else query
        
        # Получаем эмбеддинг запроса
        query_embedding = get_query_embedding(search_query)
        
        # Фильтр
        filter_conditions = [
            FieldCondition(key="item_type", match=MatchValue(value="property"))
        ]
        
        if object_name:
            filter_conditions.append(
                FieldCondition(key="object_name", match=MatchValue(value=object_name))
            )
        
        query_filter = Filter(must=filter_conditions)
        
        # Мультивекторный поиск по обоим векторам
        results = qdrant_client.query_points(
            collection_name=COLLECTION_SYNTAX,
            prefetch=[
                Prefetch(
                    query=query_embedding,
                    using="name",
                    filter=query_filter,
                    limit=limit * 3
                ),
                Prefetch(
                    query=query_embedding,
                    using="description",
                    filter=query_filter,
                    limit=limit * 3
                )
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True
        )
        
        if not results.points:
            return f"По запросу '{search_query}' свойства не найдены.\n\n💡 Попробуйте:\n- Изменить описание свойства\n- Искать без указания объекта\n- Проверить написание объекта"
        
        output = [f"Найдено свойств объектов: {len(results.points)}\n"]
        
        for i, result in enumerate(results.points, 1):
            payload = result.payload
            score = getattr(result, 'score', 0)
            
            output.append(f"\n{'=' * 60}")
            output.append(f"Результат {i} (релевантность: {score:.3f})")
            output.append(f"{'=' * 60}")
            output.append(f"**Свойство:** {payload.get('object_name', 'Unknown')}.{payload.get('name', 'Unknown')}")
            
            if payload.get('name_en'):
                output.append(f"**Английское имя:** {payload.get('name_en')}")
            
            # Тип свойства
            prop_type = payload.get('property_type', payload.get('return_type', 'Unknown'))
            output.append(f"**Тип:** {prop_type}")
            
            output.append(f"\n**Описание:**")
            output.append(payload.get('description', 'Нет описания'))
            
            output.append(f"\n**Использование:**")
            output.append(f"```bsl\n{payload.get('syntax', 'N/A')}\n```")
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Ошибка поиска свойств: {e}")
        return f"❌ Ошибка поиска: {str(e)}"


def get_1c_syntax_details(item_name: str, item_type: str = "function", object_name: str | None = None) -> str:
    """
    Получить детальную информацию о синтаксисе.
    
    Используйте когда знаете точное имя функции/метода/свойства (а для методов объектов - и имя объекта).
    
    Args:
        item_name: Точное имя (СтрДлина, Добавить, Колонки и т.д.)
        item_type: Тип элемента:
            - "function" или "global_function" - глобальная функция
            - "method" или "object_method" - метод объекта (опционально object_name)
            - "property" - свойство объекта  (опционально object_name)
        object_name: Имя объекта (например, ТаблицаЗначений) - для точного поиска методов/свойств
    Returns:
        Полное описание с примерами, параметрами, замечаниями
    """
    try:
        # Проверяем существование коллекции
        try:
            qdrant_client.get_collection(COLLECTION_SYNTAX)
        except Exception:
            return "❌ Коллекция синтаксиса не найдена. Загрузите документацию через Syntax Loader (http://localhost:8502)"
        # Нормализация типа
        type_mapping = {
            "function": "global_function",
            "global_function": "global_function",
            "method": "object_method",
            "object_method": "object_method",
            "property": "property"
        }
        normalized_type = type_mapping.get(item_type.lower(), "global_function")
        must_conditions = [
            FieldCondition(key="name", match=MatchValue(value=item_name)),
            FieldCondition(key="item_type", match=MatchValue(value=normalized_type))
        ]
        if object_name:
            must_conditions.insert(0, FieldCondition(key="object_name", match=MatchValue(value=object_name)))
        # Поиск по точному имени
        results = qdrant_client.scroll(
            collection_name=COLLECTION_SYNTAX,
            scroll_filter=Filter(must=must_conditions),
            limit=10,
            with_payload=True
        )
        points = results[0] if results else []
        # Fuzzy fallback
        if not points:
            query_embedding = get_query_embedding(item_name)
            filter_conditions = [
                FieldCondition(key="item_type", match=MatchValue(value=normalized_type))
            ]
            if object_name:
                filter_conditions.insert(0, FieldCondition(key="object_name", match=MatchValue(value=object_name)))
            results = qdrant_client.query_points(
                collection_name=COLLECTION_SYNTAX,
                query=query_embedding,
                using="name",
                query_filter=Filter(must=filter_conditions),
                limit=5,
                with_payload=True
            )
            points = results.points
        if not points:
            obj_note = f" в объекте '{object_name}'" if object_name else ""
            return f"❌ Элемент '{item_name}' типа '{item_type}'{obj_note} не найден.\n\n💡 Попробуйте:\n- Проверить написание\n- Использовать поиск: bslsyntax_search_global_function, bslsyntax_search_method или bslsyntax_search_property"
        output = []
        for point in points[:3]:  # Показываем максимум 3 результата
            payload = point.payload
            output.append(f"\n{'=' * 70}")
            if payload.get('object_name'):
                output.append(f"# {payload.get('object_name')}.{payload.get('name')}")
            else:
                output.append(f"# {payload.get('name')}")
            output.append(f"{'=' * 70}")
            if payload.get('name_en'):
                output.append(f"**Английское имя:** {payload.get('name_en')}")
            if payload.get('category'):
                output.append(f"**Категория:** {payload.get('category')}")
            output.append(f"\n## Описание")
            output.append(payload.get('description', 'Нет описания'))
            output.append(f"\n## Синтаксис")
            output.append(f"```bsl\n{payload.get('syntax', 'N/A')}\n```")
            parameters = payload.get('parameters', [])
            if parameters:
                output.append(f"\n## Параметры")
                output.extend(_format_parameters(parameters, bold=True))
            if payload.get('property_type'):
                output.append(f"\n## Тип свойства")
                output.append(f"`{payload.get('property_type')}`")
            if payload.get('return_type'):
                output.append(f"\n## Возвращаемое значение")
                output.append(f"Тип: `{payload.get('return_type')}`")
            examples = payload.get('examples', [])
            if examples:
                output.append(f"\n## Примеры")
                for idx, example in enumerate(examples, 1):
                    output.append(f"\n**Пример {idx}:**")
                    output.append(f"```bsl\n{example}\n```")
            if payload.get('remarks'):
                output.append(f"\n## Замечания")
                output.append(payload.get('remarks'))
        return "\n".join(output)
    except Exception as e:
        logger.error(f"Ошибка получения деталей синтаксиса: {e}")
        return f"❌ Ошибка: {str(e)}"

