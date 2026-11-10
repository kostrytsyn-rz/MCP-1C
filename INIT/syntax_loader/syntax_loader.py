#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Streamlit UI для загрузки синтаксиса 1С в Qdrant из готового JSON.

Позволяет:
- Загрузить structure.json
- Проиндексировать в Qdrant
- Просмотреть статистику
"""

import streamlit as st
from qdrant_client import QdrantClient
from parsers.syntax_indexer import create_syntax_collection, index_syntax_data, get_embeddings
import config
import logging
import re
import html
from pathlib import Path
import os


class StreamlitLogHandler(logging.Handler):
    """Handler для вывода прогресса парсинга в Streamlit"""
    def __init__(self, text_container, progress_bar=None, status_text=None):
        super().__init__()
        self.text_container = text_container
        self.progress_bar = progress_bar
        self.status_text = status_text
        self.buffer = []
        self.current_progress = 0
        self.max_progress = 0
        
    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
            
            # Обновляем прогресс-бар если находим информацию о прогрессе
            if self.progress_bar and "[{}/" in msg:
                match = re.search(r'\[(\d+)/(\d+)\]', msg)
                if match:
                    current = int(match.group(1))
                    total = int(match.group(2))
                    self.current_progress = current
                    self.max_progress = total
                    if total > 0:
                        progress_value = min(current / total, 1.0)
                        self.progress_bar.progress(progress_value)
            
            # Извлекаем информацию о текущем файле из сообщения
            current_file = ""
            if "Обработка файла объекта" in msg:
                match = re.search(r'Обработка файла объекта[^:]*:\s*(.+)', msg)
                if match:
                    current_file = match.group(1).strip()
            elif "Обработка глобального метода" in msg:
                match = re.search(r'Обработка глобального метода:\s*(.+)', msg)
                if match:
                    current_file = match.group(1).strip()
            
            # Обновляем статус текста с информацией о прогрессе и текущем файле
            if self.status_text:
                if self.max_progress > 0:
                    file_info = f" - {current_file}" if current_file else ""
                    status_msg = f"Обрабатывается: {self.current_progress} из {self.max_progress}{file_info}"
                else:
                    # Если еще нет информации о прогрессе, показываем просто текущее сообщение
                    status_msg = msg[:200]  # Ограничиваем длину
                self.status_text.text(status_msg)
            
        except Exception:
            pass  # Игнорируем ошибки при выводе


def setup_streamlit_logging(text_container, progress_bar=None, status_text=None):
    """Настройка логирования для Streamlit"""
    # Получаем root logger - он поймает все логи из всех модулей
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Удаляем существующие handlers у root logger (чтобы избежать дублирования)
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    
    # Удаляем handlers у logger'ов парсеров, если они есть
    parsers_logger = logging.getLogger('parsers')
    for h in list(parsers_logger.handlers):
        parsers_logger.removeHandler(h)
    
    # Добавляем наш handler только к root logger - он поймает все логи
    # (логи из parsers будут пропагировать к root logger автоматически)
    handler = StreamlitLogHandler(text_container, progress_bar, status_text)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    return handler


def flatten_structure_for_indexing(structure):
    """
    Преобразует structure.json с objects -> [(item_type,...)] для индексации
    """
    objects = structure.get('objects', {})
    # Глобальные функции (можно дооснастить поиск по другим объектам позже)
    global_functions = []
    if "Глобальный контекст" in objects:
        for method in objects["Глобальный контекст"].get("methods", []):
            method = method.copy()
            method["item_type"] = "global_function"
            global_functions.append(method)
    # Методы и свойства объектов
    object_methods = []
    object_properties = []
    for obj_name, obj in objects.items():
        for method in obj.get("methods", []):
            m = method.copy()
            m["item_type"] = "object_method"
            m["object_name"] = obj_name
            object_methods.append(m)
        for prop in obj.get("properties", []):
            p = prop.copy()
            p["item_type"] = "property"
            p["object_name"] = obj_name
            object_properties.append(p)
    return {
        'global_functions': global_functions,
        'object_methods': object_methods,
        'object_properties': object_properties
    }


def main():
    st.set_page_config(
        page_title="Загрузчик синтаксиса 1С",
        page_icon="🔍",
        layout="wide"
    )
    
    st.title("🔍 Загрузчик синтаксиса 1С в Qdrant")
    st.write("Загрузка готового structure.json для MCP синтакс-помощника")
    
    # Боковая панель с настройками
    st.sidebar.header("⚙️ Настройки")
    st.sidebar.write(f"**Qdrant:** {config.QDRANT_HOST}:{config.QDRANT_PORT}")
    st.sidebar.write(f"**Embedding:** {config.EMBEDDING_SERVICE_URL}")
    st.sidebar.write(f"**Коллекция:** {config.COLLECTION_SYNTAX}")
    
    st.sidebar.markdown("---")
    st.sidebar.info("""
    **Где взять structure.json?**
    
    1) Распакуйте `shcntx_ru.hbk` из папки `bin` платформы (не `1cv8_ru.hbk`) через 7-Zip — должна появиться папка `objects/`.
    2) Постройте JSON через CLI: `python parse_structure.py <папка> --output structure.json`.
    3) Загрузите полученный structure.json сюда.
    """)
    
    # Основной интерфейс
    st.header("📁 Загрузка structure.json")

    uploaded_file = st.file_uploader(
        "Выберите готовый structure.json",
        type=["json"],
        help="Файл должен содержать структуру синтаксиса 1С (objects → methods/properties)"
    )

    if st.button("🚀 Начать загрузку", type="primary", use_container_width=True):
        if not uploaded_file:
            st.error("Загрузите structure.json")
        else:
            process_json(uploaded_file)
    
    st.markdown("---")
    st.header("📊 Статистика")
    
    if st.button("🔄 Обновить статистику", use_container_width=False):
        show_statistics()


def process_json(uploaded_file, force_reindex: bool = False):
    try:
        import json

        # Подключение к Qdrant
        st.info("🔌 Подключение к Qdrant...")
        qdrant_client = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT)

        # Получаем размер вектора
        st.info("🔍 Проверка сервиса эмбеддингов...")
        test_emb = get_embeddings(["тест"], config.EMBEDDING_SERVICE_URL)[0]
        vector_size = len(test_emb)
        st.success(f"✅ Размер вектора: {vector_size}")

        # Создание коллекции
        st.info("🗃️ Создание коллекции...")
        create_syntax_collection(qdrant_client, config.COLLECTION_SYNTAX, vector_size)

        # Загрузка готового JSON
        st.info("📖 Загрузка structure.json ...")
        data = json.load(uploaded_file)

        # Преобразуем структуру к плоскому виду перед индексацией
        prepared = flatten_structure_for_indexing(data)
        st.success(f"✅ Распарсено:")
        st.write(f"- Глобальных функций: {len(prepared['global_functions'])}")
        st.write(f"- Методов объектов: {len(prepared['object_methods'])}")
        st.write(f"- Свойств объектов: {len(prepared['object_properties'])}")

        # Индексация
        st.info("🚀 Индексация в Qdrant...")
        stats = index_syntax_data(
            prepared,
            qdrant_client,
            config.COLLECTION_SYNTAX,
            config.EMBEDDING_SERVICE_URL,
            batch_size=config.BATCH_SIZE
        )
        st.success("✅ Загрузка завершена!")
        st.balloons()
        # Итоговая статистика
        st.markdown("---")
        st.subheader("📊 Итоговая статистика")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Глобальные функции", stats['global_functions'])
        with col2:
            st.metric("Методы объектов", stats['object_methods'])
        with col3:
            st.metric("Свойства объектов", stats['object_properties'])
        total_items = sum(stats.values())
        st.metric("Всего элементов", total_items)
        st.info("""
        ✅ **Синтаксис успешно загружен!**

        Теперь MCP сервер может использовать инструменты:
        - `bslsyntax_search_global_function()` - поиск глобальных функций
        - `bslsyntax_search_method()` - поиск методов объектов
        - `bslsyntax_search_property()` - поиск свойств
        - `bslsyntax_get_details()` - детальная информация
        """)
    except Exception as e:
        st.error(f"❌ Ошибка загрузки: {str(e)}")
        import traceback
        with st.expander("🐛 Детали ошибки"):
            st.code(traceback.format_exc())


def show_statistics():
    """Отображение статистики коллекции"""
    try:
        qdrant_client = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT)
        
        if qdrant_client.collection_exists(config.COLLECTION_SYNTAX):
            info = qdrant_client.get_collection(config.COLLECTION_SYNTAX)
            
            st.success("✅ Коллекция существует")
            st.metric("Всего точек", f"{info.points_count:,}")
            st.metric("Векторов на точку", len(info.config.params.vectors))
            
            # Пробуем получить примеры данных
            try:
                samples = qdrant_client.scroll(
                    collection_name=config.COLLECTION_SYNTAX,
                    limit=5,
                    with_payload=True,
                    with_vectors=False
                )
                
                if samples[0]:
                    st.subheader("📝 Примеры данных:")
                    for point in samples[0]:
                        payload = point.payload
                        item_type = payload.get('item_type', 'unknown')
                        name = payload.get('name', 'Unknown')
                        obj_name = payload.get('object_name')
                        
                        if obj_name:
                            st.write(f"- **{item_type}**: {obj_name}.{name}")
                        else:
                            st.write(f"- **{item_type}**: {name}")
            except Exception as e:
                st.warning(f"Не удалось получить примеры: {e}")
        else:
            st.warning("⚠️ Коллекция не существует. Загрузите structure.json.")
    
    except Exception as e:
        st.error(f"❌ Ошибка подключения к Qdrant: {str(e)}")


if __name__ == "__main__":
    main()

