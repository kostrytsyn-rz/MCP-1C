#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Веб-интерфейс для парсинга конфигураций 1С
Поддержка основной конфигурации + множественные расширения с изменяемым порядком
"""

import streamlit as st
import os
import zipfile
import tempfile
import shutil
import time
import sys
import io
from pathlib import Path
from typing import List, Dict, Optional
import json

# Настройка страницы
st.set_page_config(
    page_title="Парсер конфигураций 1С",
    page_icon="🔧",
    layout="wide"
)



class StreamlitOutput:
    """Перехватывает stdout и отображает в Streamlit"""
    def __init__(self, status_widget):
        self.status_widget = status_widget
        self.buffer = []
        
    def write(self, text):
        if text and text.strip():  # Пропускаем пустые строки
            # Отображаем в streamlit
            self.status_widget.write(text)
            self.buffer.append(text)
    
    def flush(self):
        pass  # Streamlit не требует flush

# Папки для работы с файлами (из переменных окружения или дефолтные)
INPUT_CONFIGS_DIR = Path(os.getenv("INPUT_CONFIGS_DIR", "./in_conf"))
OUTPUT_CONFIGS_DIR = Path(os.getenv("OUTPUT_CONFIGS_DIR", "./out_conf"))

# Инициализация session state
if 'extensions' not in st.session_state:
    st.session_state.extensions = []  # Список загруженных ZIP расширений

if 'main_config_file' not in st.session_state:
    st.session_state.main_config_file = None

if 'temp_dir' not in st.session_state:
    st.session_state.temp_dir = None

if 'uploader_key' not in st.session_state:
    st.session_state.uploader_key = 0

if 'input_mode' not in st.session_state:
    st.session_state.input_mode = "upload"  # upload или folder


def extract_zip(zip_file, target_dir: Path) -> Path:
    """Распаковка ZIP архива"""
    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
        zip_ref.extractall(target_dir)
    
    # Ищем папку с Configuration.xml
    for item in target_dir.iterdir():
        if item.is_dir():
            config_xml = item / "Configuration.xml"
            if config_xml.exists():
                return item
    
    # Если Configuration.xml в корне
    config_xml = target_dir / "Configuration.xml"
    if config_xml.exists():
        return target_dir
    
    raise ValueError("Configuration.xml не найден в архиве")


def validate_config_zip(zip_file) -> tuple[bool, str]:
    """
    Проверка, что ZIP содержит конфигурацию 1С
    
    Returns:
        (is_valid, message)
    """
    try:
        with zipfile.ZipFile(zip_file, 'r') as zip_ref:
            # Ищем Configuration.xml в архиве
            files = zip_ref.namelist()
            has_config = any('Configuration.xml' in f for f in files)
            
            if not has_config:
                return False, "❌ Configuration.xml не найден в архиве"
            
            return True, "✅ Конфигурация 1С обнаружена"
    except Exception as e:
        return False, f"❌ Ошибка чтения архива: {str(e)}"


def get_config_info(path: str) -> Dict:
    """Получение информации о конфигурации"""
    p = Path(path)
    config_name = p.name
    
    # Подсчитываем объекты
    object_types = {
        'Catalogs': 'Справочники',
        'Documents': 'Документы',
        'InformationRegisters': 'Регистры сведений',
        'AccumulationRegisters': 'Регистры накопления',
        'CommonModules': 'Общие модули',
        'DataProcessors': 'Обработки',
        'Reports': 'Отчёты'
    }
    
    stats = {}
    for eng_name, rus_name in object_types.items():
        type_dir = p / eng_name
        if type_dir.exists():
            # Считаем папки (каждая папка = объект)
            count = sum(1 for item in type_dir.iterdir() if item.is_dir())
            if count > 0:
                stats[rus_name] = count
    
    return {
        'name': config_name,
        'path': str(p),
        'stats': stats
    }


def move_extension_up(index: int):
    """Переместить расширение вверх"""
    if index > 0:
        st.session_state.extensions[index], st.session_state.extensions[index - 1] = \
            st.session_state.extensions[index - 1], st.session_state.extensions[index]


def move_extension_down(index: int):
    """Переместить расширение вниз"""
    if index < len(st.session_state.extensions) - 1:
        st.session_state.extensions[index], st.session_state.extensions[index + 1] = \
            st.session_state.extensions[index + 1], st.session_state.extensions[index]


def remove_extension(index: int):
    """Удалить расширение"""
    if 0 <= index < len(st.session_state.extensions):
        st.session_state.extensions.pop(index)


def add_extension(file_info: Dict):
    """Добавить расширение"""
    # Проверяем, нет ли уже такого файла
    for ext in st.session_state.extensions:
        if ext['name'] == file_info['name']:
            return False
    st.session_state.extensions.append(file_info)
    return True


def scan_folder_for_config_dirs(folder_path: Path, max_depth: int = 2) -> List[Path]:
    """
    Сканирование папки на наличие папок с конфигурациями (содержащих Configuration.xml)
    СТРОГО 2 УРОВНЯ ВЛОЖЕННОСТИ МАКСИМУМ!
    
    Args:
        folder_path: Путь к папке для сканирования (уровень 0)
        max_depth: Максимальная глубина поиска (1 или 2)
    """
    if not folder_path.exists():
        return []
    
    config_dirs = []
    try:
        # УРОВЕНЬ 1: Проверяем папки в корне (например: in_conf/conf/)
        for level1_item in folder_path.iterdir():
            if not level1_item.is_dir():
                continue
            
            # Проверяем наличие Configuration.xml на уровне 1
            if (level1_item / "Configuration.xml").exists():
                config_dirs.append(level1_item)
                continue  # не идём глубже если нашли Configuration.xml
            
            # УРОВЕНЬ 2: Проверяем подпапки (например: in_conf/test/conf/)
            if max_depth >= 2:
                try:
                    for level2_item in level1_item.iterdir():
                        if level2_item.is_dir() and (level2_item / "Configuration.xml").exists():
                            config_dirs.append(level2_item)
                        # НЕ ИДЁМ ГЛУБЖЕ 2 УРОВНЯ!
                except (PermissionError, OSError):
                    continue  # Пропускаем недоступные папки
        
        # Сортируем по имени
        config_dirs.sort(key=lambda x: x.name.lower())
    except Exception as e:
        st.error(f"Ошибка сканирования папок: {str(e)}")
    
    return config_dirs


def scan_folder_for_zips(folder_path: Path, max_depth: int = 2) -> List[Path]:
    """
    Сканирование папки на наличие ZIP файлов конфигураций
    
    Args:
        folder_path: Путь к папке для сканирования
        max_depth: Максимальная глубина поиска (0 = только в корне, 1 = корень + 1 уровень подпапок)
    """
    if not folder_path.exists():
        return []
    
    zip_files = []
    try:
        # Ищем ZIP файлы в корне папки (БЕЗ фильтра по размеру)
        for item in folder_path.glob("*.zip"):
            if item.is_file():
                zip_files.append(item)
        
        # Сканируем подпапки (максимум на 1 уровень вниз)
        # Для подпапок применяем фильтр по размеру чтобы исключить служебные файлы
        if max_depth > 0:
            for subfolder in folder_path.iterdir():
                if subfolder.is_dir():
                    for item in subfolder.glob("*.zip"):
                        if item.is_file() and item.stat().st_size > 1024 * 100:  # Минимум 100 KB
                            zip_files.append(item)
        
        # Сортируем по имени
        zip_files.sort(key=lambda x: x.name.lower())
    except Exception as e:
        st.error(f"Ошибка сканирования папки: {str(e)}")
    
    return zip_files


def scan_folder_for_configs(folder_path: Path, max_depth: int = 2) -> List[Dict]:
    """
    Сканирование папки на наличие конфигураций (ZIP и распакованные папки)
    
    Returns:
        List of dicts with 'path', 'name', 'type' ('zip' or 'dir'), 'size'
    """
    if not folder_path.exists():
        return []
    
    configs = []
    
    # Собираем ZIP файлы
    zip_files = scan_folder_for_zips(folder_path, max_depth)
    for zip_path in zip_files:
        configs.append({
            'path': zip_path,
            'name': zip_path.name,
            'type': 'zip',
            'size': 0,  # Не считаем размер
            'display_name': f"📦 {zip_path.name}"
        })
    
    # Собираем папки с конфигурациями
    config_dirs = scan_folder_for_config_dirs(folder_path, max_depth)
    for dir_path in config_dirs:
        # НЕ считаем размер - для больших конфигураций это долго!
        # Просто ставим 0 для папок
        configs.append({
            'path': dir_path,
            'name': dir_path.name,
            'type': 'dir',
            'size': 0,  # Не считаем размер для скорости
            'display_name': f"📁 {dir_path.name}/"
        })
    
    # Сортируем по имени
    configs.sort(key=lambda x: x['name'].lower())
    
    return configs


def get_file_info_from_path(file_path: Path, source: str = "folder", config_type: str = "zip") -> Dict:
    """
    Получение информации о конфигурации из пути
    
    Args:
        file_path: Путь к ZIP файлу или папке
        source: 'upload' или 'folder'
        config_type: 'zip' или 'dir'
    """
    return {
        'name': file_path.name,
        'file': file_path,  # Путь вместо BytesIO
        'size': 0,  # Не считаем размер - не нужен
        'source': source,  # 'upload' или 'folder'
        'path': str(file_path),
        'type': config_type  # 'zip' или 'dir'
    }



st.title("🔧 Парсер конфигураций 1С")
st.markdown("### Загрузите ZIP-архивы для парсинга и объединения")

# Инфо-панель сверху
info_col1, info_col2, info_col3 = st.columns(3)
with info_col1:
    if st.session_state.main_config_file:
        st.metric("Основная конфигурация", "✅ Загружена")
    else:
        st.metric("Основная конфигурация", "⏳ Не загружена")
with info_col2:
    extensions_count = len(st.session_state.extensions)
    if extensions_count > 0:
        st.metric("Расширения", f"{extensions_count} шт.")
    else:
        st.metric("Расширения", "0 шт.")
with info_col3:
    total_items = 0
    if st.session_state.main_config_file:
        total_items += 1
    total_items += len(st.session_state.extensions)
    
    if total_items > 0:
        st.metric("Итого", f"{total_items} шт.")
    else:
        st.metric("Итого", "Не выбрано")

st.markdown("---")

# Две колонки: настройки и превью
col_left, col_right = st.columns([2, 1])

with col_left:
    st.header("📦 Шаг 1: Выбор конфигурации")
    
    # ========== ОСНОВНАЯ КОНФИГУРАЦИЯ ==========
    st.subheader("Основная конфигурация")
    
    # Показываем путь к папке
    if INPUT_CONFIGS_DIR.exists():
        st.info(f"📂 Папка: `{INPUT_CONFIGS_DIR}`")
        
        # Сканируем папку на наличие конфигураций (ZIP и папки)
        available_configs = scan_folder_for_configs(INPUT_CONFIGS_DIR)
        
        if available_configs:
            st.success(f"✅ Найдено конфигураций: **{len(available_configs)}**")
            
            # Создаём список для отображения
            config_display_names = []
            config_map = {}
            
            for config in available_configs:
                display_name = config['display_name']
                config_display_names.append(display_name)
                config_map[display_name] = config
            
            # Выбор основной конфигурации
            selected_main = st.selectbox(
                "Выберите основную конфигурацию:",
                options=[""] + config_display_names,
                index=0,
                key="folder_main_select"
            )
            
            if selected_main:
                selected_config = config_map[selected_main]
                selected_path = selected_config['path']
                config_type = selected_config['type']
                
                # Валидация только для ZIP
                if config_type == 'zip':
                    is_valid, message = validate_config_zip(str(selected_path))
                else:
                    # Для папки проверяем наличие Configuration.xml
                    if (selected_path / "Configuration.xml").exists():
                        is_valid = True
                        message = "✅ Конфигурация валидна (папка с Configuration.xml)"
                    else:
                        is_valid = False
                        message = "❌ Не найден файл Configuration.xml"
                
                if is_valid:
                    st.success(message)
                    type_label = "📦 ZIP" if config_type == 'zip' else "📁 Папка"
                    st.info(f"{type_label} **{selected_path.name}**")
                    
                    # Сохраняем в session state
                    st.session_state.main_config_file = get_file_info_from_path(selected_path, "folder", config_type)
                    st.session_state.input_mode = 'folder'
                else:
                    st.error(message)
                    if st.session_state.main_config_file:
                        st.session_state.main_config_file = None
        else:
            st.warning(f"📭 Конфигурации не найдены в папке")
            st.markdown("""
            **💡 Как добавить файлы:**
            1. Скопируйте ZIP архивы конфигураций в папку `in_conf/` на вашем компьютере
            2. Обновите страницу (F5)
            """)
    else:
        st.error(f"❌ Папка не найдена: `{INPUT_CONFIGS_DIR}`")
        st.markdown("""
        **⚠️ Возможные причины:**
        - Docker контейнер не запущен
        - Папка не примонтирована в docker-compose.yml
        - Папка не существует на хосте
        """)
    
    st.markdown("---")
    
    # ========== РАСШИРЕНИЯ ==========
    st.subheader("Расширения (опционально)")
    
    # Используем ту же папку что и для основной конфигурации
    st.caption("Выберите расширения из папки in_conf")
    
    # Получаем список доступных конфигураций (исключая уже добавленные и основную)
    available_for_extensions = []
    if INPUT_CONFIGS_DIR.exists():
        all_configs = scan_folder_for_configs(INPUT_CONFIGS_DIR, max_depth=2)
        
        # Исключаем уже добавленные расширения и основную конфигурацию
        added_names = [ext['name'] for ext in st.session_state.extensions]
        if st.session_state.main_config_file:
            added_names.append(st.session_state.main_config_file['name'])
        
        available_for_extensions = [c for c in all_configs if c['name'] not in added_names]
    
    if available_for_extensions:
        # Создаём список для отображения
        ext_display_names = []
        ext_config_map = {}
        
        for config in available_for_extensions:
            display_name = config['display_name']
            ext_display_names.append(display_name)
            ext_config_map[display_name] = config
        
        # Функция callback для автоматического добавления
        def on_extension_select():
            selected = st.session_state.extension_folder_select
            if selected and selected != "":
                selected_config = ext_config_map[selected]
                selected_path = selected_config['path']
                config_type = selected_config['type']
                
                # Валидация
                if config_type == 'zip':
                    is_valid, message = validate_config_zip(str(selected_path))
                else:
                    # Для папки проверяем Configuration.xml
                    if (selected_path / "Configuration.xml").exists():
                        is_valid = True
                        message = "✅ Конфигурация валидна"
                    else:
                        is_valid = False
                        message = "❌ Не найден Configuration.xml"
                
                if is_valid:
                    # Добавляем в список
                    file_info = get_file_info_from_path(selected_path, "folder", config_type)
                    add_extension(file_info)
                    # Сохраняем статус для отображения
                    st.session_state.last_extension_status = f"✅ Добавлено: {selected_path.name}"
                else:
                    st.session_state.last_extension_status = f"❌ {message}"
        
        # Выбор расширения для добавления (автоматическое добавление при выборе)
        selected_extension = st.selectbox(
            "Выберите расширение для добавления:",
            options=[""] + ext_display_names,
            index=0,
            key="extension_folder_select",
            help="Расширение будет автоматически добавлено при выборе",
            on_change=on_extension_select
        )
        
        # Показываем статус последнего добавления
        if hasattr(st.session_state, 'last_extension_status') and st.session_state.last_extension_status:
            if st.session_state.last_extension_status.startswith("✅"):
                st.success(st.session_state.last_extension_status)
            else:
                st.error(st.session_state.last_extension_status)
            # Очищаем статус после показа
            st.session_state.last_extension_status = None
    else:
        st.info("📭 Нет доступных расширений для добавления")
    
    # Список расширений с возможностью управления порядком
    if st.session_state.extensions:
        st.markdown("#### 📝 Порядок применения расширений")
        st.caption("⚡ Расширения применяются сверху вниз")
        
        for idx, ext_info in enumerate(st.session_state.extensions):
            col1, col2, col3, col4 = st.columns([0.5, 3, 0.6, 0.6])
            
            with col1:
                st.markdown(f"**{idx + 1}**")
            
            with col2:
                ext_type = ext_info.get('type', 'zip')
                type_icon = "📦" if ext_type == 'zip' else "📁"
                st.markdown(f"{type_icon} **{ext_info['name']}**")
            
            with col3:
                # Стрелки вверх/вниз
                up_disabled = (idx == 0)
                down_disabled = (idx == len(st.session_state.extensions) - 1)
                
                if st.button("⬆️", key=f"up_{idx}", disabled=up_disabled, use_container_width=True):
                    move_extension_up(idx)
                    st.rerun()
                
                if st.button("⬇️", key=f"down_{idx}", disabled=down_disabled, use_container_width=True):
                    move_extension_down(idx)
                    st.rerun()
            
            with col4:
                st.write("")  # Отступ для выравнивания
                if st.button("🗑️", key=f"del_{idx}", use_container_width=True, type="secondary"):
                    remove_extension(idx)
                    st.rerun()
            
            st.markdown("---")
    else:
        st.info("📭 Расширения не добавлены\n\nЗагрузите ZIP-архив выше, чтобы добавить расширение")
    
st.markdown("---")
st.header("🚀 Шаг 2: Запуск")

# Проверка готовности к запуску
can_start = st.session_state.main_config_file is not None

# Настройки парсинга
merge_configs = st.checkbox(
    "🔀 Объединить конфигурации", 
    value=True,
    help="Создать единую конфигурацию из основной + все расширения"
)

# Кнопка запуска - ВСЕГДА показываем
if can_start:
    # Если уже есть результаты, показываем как "Парсить заново"
    button_text = "🔄 Парсить заново" if st.session_state.get('parsing_completed', False) else "🚀 Начать парсинг"
    
    if st.button(button_text, type="primary", use_container_width=True):
        # Сбрасываем предыдущие результаты
        st.session_state.parsing_started = True
        st.session_state.parsing_completed = False
        st.session_state.merge_configs = merge_configs
        # Очищаем старые пути к файлам
        if 'merged_zip_path' in st.session_state:
            del st.session_state.merged_zip_path
        if 'main_zip_path' in st.session_state:
            del st.session_state.main_zip_path
        if 'extension_zip_paths' in st.session_state:
            del st.session_state.extension_zip_paths
        st.rerun()
else:
    st.error("❌ Загрузите основную конфигурацию для продолжения")
    st.button("🚀 Начать парсинг", type="primary", disabled=True, use_container_width=True)

# ========== ПРАВАЯ КОЛОНКА - ПРЕВЬЮ ==========
with col_right:
    st.header("💡 Подсказки")
    
    # Показываем что будет обработано
    with st.container():
        st.markdown("##### Что будет обработано:")
        
        if st.session_state.main_config_file:
            st.success(f"📦 {st.session_state.main_config_file['name']}")
        else:
            st.error("❌ Основная конфигурация не загружена")
        
        if st.session_state.extensions:
            st.markdown("**➕ Расширения:**")
            for idx, ext_info in enumerate(st.session_state.extensions):
                st.caption(f"  {idx + 1}. {ext_info['name']}")
        else:
            st.caption("_Без расширений_")
    
    st.markdown("---")
    
    # Инструкция
    st.markdown("##### 📖 Инструкция:")
    st.markdown("""
    1. 📂 Выберите основную конфигурацию из папки `in_conf`
    2. ➕ Добавьте расширения (если есть)
    3. 🔄 Измените порядок расширений (если нужно)
    4. 🚀 Запустите парсинг
    5. 💾 Скачайте результаты или сохраните в папку Docker
    """)
    
    st.markdown("---")
    
    # Что получим
    st.markdown("##### 📦 Результаты парсинга:")
    st.success("""
    **После парсинга:**
    - `merged.zip` - объединённая конфигурация
    - `main_*.zip` - основная конфигурация
    - Скачивание или сохранение в `out_conf/`
    """)
    
    st.markdown("---")
    
    # Быстрые действия
    if st.button("🗑️ Очистить всё", use_container_width=True, type="secondary"):
        # Очищаем загруженные файлы
        st.session_state.main_config_file = None
        st.session_state.extensions = []
        
        # Очищаем результаты парсинга
        if 'parsing_started' in st.session_state:
            del st.session_state.parsing_started
        if 'parsing_completed' in st.session_state:
            del st.session_state.parsing_completed
        if 'merged_zip_path' in st.session_state:
            del st.session_state.merged_zip_path
        if 'main_zip_path' in st.session_state:
            del st.session_state.main_zip_path
        if 'extension_zip_paths' in st.session_state:
            del st.session_state.extension_zip_paths
        if 'temp_dir' in st.session_state:
            del st.session_state.temp_dir
        
        # Увеличиваем счетчик для сброса file_uploader
        st.session_state.uploader_key += 1
        
        st.rerun()

# ============================================================================
# ОБЛАСТЬ ВЫПОЛНЕНИЯ ПАРСИНГА
# ============================================================================

# Показываем процесс парсинга только если он запущен и не завершён
if st.session_state.get('parsing_started', False) and not st.session_state.get('parsing_completed', False):
    st.markdown("---")
    st.header("🚀 Выполнение парсинга")
    
    progress_col1, progress_col2 = st.columns([2, 1])
    
    with progress_col1:
        st.markdown("**Выполнение:**")
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Реальный парсинг конфигураций
        status_text.write("**Распаковка загруженных архивов...**")
        
        # Импортируем парсер
        try:
            from parse_1c_conf import Config1CParser
        except ImportError:
            status_text.error("❌ Ошибка: модуль parse_1c_conf не найден")
            st.session_state.parsing_completed = False
            st.stop()
        
        # Создаём папку для результатов на основе имени основной конфигурации
        main_config_name = st.session_state.main_config_file['name'].replace('.zip', '')
        output_folder = OUTPUT_CONFIGS_DIR / f"parsed_{main_config_name}"
        
        # Если папка уже существует, добавляем счётчик
        counter = 1
        original_output_folder = output_folder
        while output_folder.exists():
            output_folder = OUTPUT_CONFIGS_DIR / f"parsed_{main_config_name}_{counter}"
            counter += 1
        
        output_folder.mkdir(parents=True, exist_ok=True)
        
        # Временная папка для распаковки архивов
        temp_dir = Path(tempfile.mkdtemp())
        
        # Обрабатываем основную конфигурацию
        main_config_type = st.session_state.main_config_file.get('type', 'zip')
        
        if main_config_type == 'dir':
            # Для папки используем напрямую
            status_text.write(f"📁 Использование папки: {st.session_state.main_config_file['name']}")
            main_config_dir = Path(st.session_state.main_config_file['path'])
        else:
            # Для ZIP - распаковываем
            main_extracted = temp_dir / "main_extracted"
            main_extracted.mkdir()
            
            status_text.write(f"📦 Распаковка ZIP: {st.session_state.main_config_file['name']}")
            
            # Обработка в зависимости от источника
            if st.session_state.main_config_file['source'] == 'upload':
                # Файл загружен через браузер - нужно получить байты
                main_config_data = st.session_state.main_config_file['file'].getvalue()
                main_zip_temp = temp_dir / "main.zip"
                main_zip_temp.write_bytes(main_config_data)
            else:
                # Файл из папки - используем путь напрямую
                main_zip_temp = Path(st.session_state.main_config_file['path'])
            
            with zipfile.ZipFile(main_zip_temp, 'r') as zip_ref:
                zip_ref.extractall(main_extracted)
            
            # Находим папку с Configuration.xml
            main_config_dir = None
            for item in main_extracted.iterdir():
                if item.is_dir() and (item / "Configuration.xml").exists():
                    main_config_dir = item
                    break
            if not main_config_dir:
                if (main_extracted / "Configuration.xml").exists():
                    main_config_dir = main_extracted
        
        # Парсим основную конфигурацию напрямую в output_folder
        # Папка называется по имени входного архива (без .zip)
        main_config_name_clean = st.session_state.main_config_file['name'].replace('.zip', '')
        main_output = output_folder / main_config_name_clean
        
        # Перехватываем вывод парсера и показываем в UI
        old_stdout = sys.stdout
        sys.stdout = StreamlitOutput(status_text)
        try:
            parser_main = Config1CParser(str(main_config_dir), str(main_output))
            parser_main.parse()
        finally:
            sys.stdout = old_stdout
        
        status_text.write(f"✅ Основная конфигурация: {len(parser_main.objects)} объектов")
        
        # Создаём архив основной внутри папки, называем по имени конфигурации
        main_zip_path = output_folder / st.session_state.main_config_file['name']
        shutil.make_archive(str(main_zip_path.with_suffix('')), 'zip', main_output)
        
        st.session_state.main_zip_path = str(main_zip_path)
        st.session_state.output_folder = str(output_folder)
        st.session_state.temp_dir = str(temp_dir)
        
        # Парсим и сохраняем расширения (если есть)
        extension_outputs = []
        extension_zip_paths = []
        
        if st.session_state.extensions:
            for idx, ext_info in enumerate(st.session_state.extensions):
                ext_config_type = ext_info.get('type', 'zip')
                
                if ext_config_type == 'dir':
                    # Для папки используем напрямую
                    status_text.write(f"📁 Использование папки расширения {idx+1}: {ext_info['name']}")
                    ext_config_dir = Path(ext_info['path'])
                else:
                    # Для ZIP - распаковываем
                    status_text.write(f"📦 Распаковка расширения {idx+1}: {ext_info['name']}")
                    
                    ext_extracted = temp_dir / f"ext_{idx}_extracted"
                    ext_extracted.mkdir()
                    
                    # Обработка в зависимости от источника
                    if ext_info.get('source') == 'upload':
                        # Файл загружен через браузер
                        ext_data = ext_info['file'].getvalue()
                        ext_zip_temp = temp_dir / f"ext_{idx}.zip"
                        ext_zip_temp.write_bytes(ext_data)
                    else:
                        # Файл из папки
                        ext_zip_temp = Path(ext_info['path'])
                    
                    with zipfile.ZipFile(ext_zip_temp, 'r') as zip_ref:
                        zip_ref.extractall(ext_extracted)
                    
                    # Находим папку с Configuration.xml
                    ext_config_dir = None
                    for item in ext_extracted.iterdir():
                        if item.is_dir() and (item / "Configuration.xml").exists():
                            ext_config_dir = item
                            break
                    if not ext_config_dir:
                        if (ext_extracted / "Configuration.xml").exists():
                            ext_config_dir = ext_extracted
                
                # Парсим расширение напрямую в output_folder
                # Папка называется по имени входного архива расширения (без .zip)
                ext_name_clean = ext_info['name'].replace('.zip', '')
                ext_output = output_folder / ext_name_clean
                
                # Перехватываем вывод парсера и показываем в UI
                old_stdout = sys.stdout
                sys.stdout = StreamlitOutput(status_text)
                try:
                    parser_ext = Config1CParser(str(ext_config_dir), str(ext_output))
                    parser_ext.parse()
                finally:
                    sys.stdout = old_stdout
                
                status_text.write(f"✅ Расширение {idx+1}: {len(parser_ext.objects)} объектов")
                
                extension_outputs.append(ext_output)
                
                # Создаём архив расширения внутри папки, называем по имени расширения
                ext_zip_path = output_folder / ext_info['name']
                shutil.make_archive(str(ext_zip_path.with_suffix('')), 'zip', ext_output)
                
                extension_zip_paths.append(str(ext_zip_path))
            
            # Сохраняем пути к архивам расширений
            st.session_state.extension_zip_paths = extension_zip_paths
            
            # Если нужно объединение - создаём merged
            if st.session_state.get('merge_configs', True):
                status_text.write("🔀 Объединение конфигураций...")
                
                try:
                    from merge_config_with_extension import IntelligentConfigMerger
                    
                    # Начинаем с основной конфигурации
                    current_merged = main_output
                    
                    # Применяем расширения последовательно
                    for idx, ext_output in enumerate(extension_outputs):
                        status_text.write(f"   🔀 Применение расширения {idx+1}/{len(extension_outputs)}...")
                        
                        # Создаем временную папку ТОЛЬКО для результата
                        step_merged = temp_dir / f"merge_step_{idx}"
                        step_merged.mkdir(exist_ok=True)
                        
                        # Используем IntelligentConfigMerger напрямую с уже распарсенными данными
                        # Merger читает из current_merged и ext_output, создаёт результат в step_merged
                        # Исходные данные НЕ изменяются!
                        merger = IntelligentConfigMerger(
                            str(current_merged), 
                            str(ext_output), 
                            str(temp_dir / f"merge_step_{idx}_parent")  # не используется
                        )
                        merger.main_output = current_merged
                        merger.ext_output = ext_output
                        merger.merged_output = step_merged
                        merger.intelligent_merge()
                        
                        status_text.write(f"   ✅ Расширение {idx+1} применено")
                        
                        # Обновляем current_merged для следующего расширения
                        current_merged = step_merged
                    
                    # Финальный merged сохраняем в output_folder
                    merged_output = output_folder / "merged"
                    if merged_output.exists():
                        shutil.rmtree(merged_output)
                    shutil.copytree(current_merged, merged_output)
                    
                    # ВСЕГДА перепарсиваем modules_metadata.json для финальной папки merged
                    # Это гарантирует что файл будет создан с актуальными данными
                    status_text.write("   📝 Создание modules_metadata.json для финального merged...")
                    merger_final = IntelligentConfigMerger(
                        str(main_output),
                        str(extension_outputs[-1] if extension_outputs else main_output),
                        str(temp_dir / "reparse_final_parent")
                    )
                    merger_final.merged_output = merged_output
                    merger_final.reparse_merged_modules_metadata()
                    
                    status_text.write("   ✅ Объединение завершено")
                    
                except Exception as e:
                    status_text.warning(f"⚠️ Ошибка объединения: {str(e)}")
                    status_text.write("   Используем основную конфигурацию как merged")
                    # Fallback: копируем основную
                    merged_output = output_folder / "merged"
                    if merged_output.exists():
                        shutil.rmtree(merged_output)
                    shutil.copytree(main_output, merged_output)
                
                # Создаём ФИНАЛЬНЫЙ архив merged.zip (после всех объединений)
                merged_zip_path = output_folder / "merged.zip"
                shutil.make_archive(str(merged_zip_path.with_suffix('')), 'zip', merged_output)
                
                st.session_state.merged_zip_path = str(merged_zip_path)
        else:
            # Нет расширений - очищаем старые данные о расширениях
            if 'extension_zip_paths' in st.session_state:
                del st.session_state.extension_zip_paths
            if 'merged_zip_path' in st.session_state:
                del st.session_state.merged_zip_path
            
        status_text.write("✅ **Парсинг завершён!**")
        st.session_state.parsing_completed = True
        
        # Задержка для отображения сообщения
        time.sleep(1)
        
        # Перерисовываем страницу чтобы показать результаты
        st.rerun()
    
    with progress_col2:
        st.markdown("**Результаты:**")
        if st.session_state.get('merge_configs', True):
            st.success("📥 **merged.zip**\nОбъединённая конфигурация")
        st.info(f"📥 Основная конфигурация\n📥 {len(st.session_state.extensions)} расширений")

# ============================================================================
# ОБЛАСТЬ РЕЗУЛЬТАТОВ
# ============================================================================

# Показываем результаты если парсинг завершён
if st.session_state.get('parsing_completed', False):
    st.markdown("---")
    
    # ========== РЕЗУЛЬТАТЫ ПАРСИНГА ==========
    st.header("📥 Результаты парсинга")
    
    # Статистика результата
    result_info_col1, result_info_col2, result_info_col3 = st.columns(3)
    
    with result_info_col1:
        st.metric("✅ Статус", "Готово", "Завершено")
    
    with result_info_col2:
        files_count = 0
        if 'merged_zip_path' in st.session_state:
            files_count += 1
        if 'main_zip_path' in st.session_state:
            files_count += 1
        if 'extension_zip_paths' in st.session_state:
            files_count += len(st.session_state.extension_zip_paths)
        st.metric("📦 Файлов создано", f"{files_count} шт.")
    
    with result_info_col3:
        # Размер результатов
        result_size = 0
        if 'merged_zip_path' in st.session_state:
            merged_path = Path(st.session_state.merged_zip_path)
            if merged_path.exists():
                result_size += merged_path.stat().st_size
        if 'main_zip_path' in st.session_state:
            main_path = Path(st.session_state.main_zip_path)
            if main_path.exists():
                result_size += main_path.stat().st_size
        if 'extension_zip_paths' in st.session_state:
            for ext_zip_path in st.session_state.extension_zip_paths:
                ext_path = Path(ext_zip_path)
                if ext_path.exists():
                    result_size += ext_path.stat().st_size
        st.metric("💾 Размер", f"{result_size / 1024 / 1024:.1f} MB")
    
    st.markdown("---")
    
    # Показываем где находятся результаты
    st.subheader("💾 Результаты сохранены")
    
    if 'output_folder' in st.session_state:
        output_path = Path(st.session_state.output_folder)
        folder_name = output_path.name
        
        st.success(f"""
        ### 🎉 Файлы готовы!
        
        **📂 Папка:** `out_conf/{folder_name}/`
        
        **💡 На вашем компьютере:** `<путь_к_проекту>/out_conf/{folder_name}/`
        """)
        
        # Формируем список файлов
        files_list = []
        if 'merged_zip_path' in st.session_state:
            files_list.append("- `merged/` - папка с объединённой конфигурацией (исходники)")
            files_list.append("- `merged.zip` - архив объединённой конфигурации")
        
        if 'main_config_file' in st.session_state:
            main_name = st.session_state.main_config_file['name']
            main_name_clean = main_name.replace('.zip', '')
            files_list.append(f"- `{main_name_clean}/` - папка с основной конфигурацией (исходники)")
            files_list.append(f"- `{main_name}` - архив основной конфигурации")
        
        if st.session_state.extensions:
            for ext in st.session_state.extensions:
                ext_name = ext['name']
                ext_name_clean = ext_name.replace('.zip', '')
                files_list.append(f"- `{ext_name_clean}/` - папка с расширением (исходники)")
                files_list.append(f"- `{ext_name}` - архив расширения")
        
        st.info("**📦 В папке находятся:**\n" + "\n".join(files_list))
    
    st.markdown("---")
    
    if 'output_folder' in st.session_state:
        folder_name = Path(st.session_state.output_folder).name
        st.success(f"""
        ### ✅ Что дальше:
        
        1. **Откройте Loader** по адресу http://localhost:8501
        2. **Загрузите merged.zip** из папки `out_conf/{folder_name}/` в Qdrant
        3. **Используйте MCP Server** для RAG-запросов через AI агентов
        
        💡 **Хотите парсить снова?** Прокрутите вверх и нажмите "🔄 Парсить заново"
        """)
    else:
        st.success("""
        ### ✅ Что дальше:
        
        1. **Откройте Loader** по адресу http://localhost:8501
        2. **Загрузите merged.zip** из папки `out_conf/parsed_*/` в Qdrant
        3. **Используйте MCP Server** для RAG-запросов через AI агентов
        
        💡 **Хотите парсить снова?** Прокрутите вверх и нажмите "🔄 Парсить заново"
        """)
    

# ============================================================================
# FOOTER
# ============================================================================

st.markdown("---")
st.caption("⚡ Парсер конфигураций 1С v3.2 · Локальный запуск")

