#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборщик иерархической структуры документации 1С.

Собирает структуру:
- Корневые объекты (Global context, InfoBaseUser и т.д.)
  - Свойства (с детальной информацией и шаблонами кода)
  - Методы
  - События
"""

import os
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging

from parsers.categories_parser import parse_categories_file
from parsers.hierarchical_html_parser import HierarchicalHTMLParser
from parsers.st_template_parser import parse_st_file

logger = logging.getLogger(__name__)


class HierarchicalCollector:
    """Сборщик иерархической структуры"""
    
    def __init__(self, root_dir: str):
        """
        Args:
            root_dir: Корневая директория с распакованными файлами
        """
        # Используем абсолютный путь для правильной обработки кириллицы
        root_dir = os.path.abspath(root_dir)
        self.root_dir = Path(root_dir)
        self.html_parser = HierarchicalHTMLParser()
        logger.info(f"Инициализация коллектора, root_dir: {self.root_dir} (exists: {self.root_dir.exists()})")
        self.stats = {
            'objects_processed': 0,
            'properties_processed': 0,
            'methods_processed': 0,
            'events_processed': 0,
            'st_files_processed': 0
        }
    
    def collect_all(self) -> Dict[str, Any]:
        """
        Сбор всей иерархической структуры.
        
        Returns:
            Словарь со структурой: {"objects": {...}, "tables": {...}}
        """
        logger.info(f"Начало сбора иерархической структуры из {self.root_dir}")
        
        result = {
            'objects': {},
            'tables': {}
        }
        
        # Обрабатываем objects и tables
        for section_name in ['objects', 'tables']:
            section_dir = self.root_dir / section_name
            if not section_dir.exists():
                logger.warning(f"Директория {section_dir} не найдена")
                logger.debug(f"Проверяем существование: {section_dir.absolute()}")
                continue
            
            # Парсим корневой __categories__ и рекурсивно обрабатываем вложенные категории
            categories_file = section_dir / '__categories__'
            if categories_file.exists():
                logger.debug(f"Обработка {section_name} из {categories_file}")
                self._process_categories_recursive(
                    section_dir,
                    categories_file,
                    section_name,
                    result[section_name],
                    section_name
                )
            else:
                logger.warning(f"Файл категорий не найден: {categories_file}")
        
        logger.info("Сбор структуры завершен")
        logger.info(f"Статистика: {self.stats}")
        
        return result
    
    def _process_categories_recursive(self, base_dir: Path, categories_file: Path,
                                     section_name: str, result_dict: Dict[str, Any],
                                     relative_path: str):
        """
        Рекурсивная обработка категорий.
        
        Args:
            base_dir: Базовая директория (objects или tables)
            categories_file: Путь к файлу __categories__
            section_name: 'objects' или 'tables'
            result_dict: Словарь для записи результатов
            relative_path: Относительный путь для логирования
        """
        # Проверяем существование через os.path (лучше работает с кириллицей в Windows)
        categories_file_str = str(categories_file)
        if not os.path.exists(categories_file_str):
            logger.warning(f"Файл категорий не найден: {categories_file_str}")
            return
        
        logger.debug(f"Парсинг категорий из: {categories_file_str}")
        category_items = parse_categories_file(categories_file_str)
        logger.debug(f"Найдено элементов в категории: {len(category_items)}")
        if len(category_items) > 0:
            logger.debug(f"Первые 3 элемента: {[item.filename for item in category_items[:3]]}")
        categories_dir = categories_file.parent
        
        for item in category_items:
            if not item.is_html:
                continue
            
            # Формируем путь к HTML файлу
            html_path = categories_dir / item.filename
            html_path_str = str(html_path)
            
            logger.debug(f"Проверка файла: {html_path_str}")
            
            if os.path.exists(html_path_str):
                # Проверяем, является ли это корневым объектом (есть ли списки properties/methods/events)
                try:
                    with open(html_path_str, 'r', encoding='utf-8', errors='ignore') as f:
                        html_content = f.read()
                    
                    # Парсим корневую страницу
                    obj_data = self.html_parser.parse_root_page(html_content, html_path_str)
                    
                    props_count = len(obj_data.get('properties', []))
                    methods_count = len(obj_data.get('methods', []))
                    events_count = len(obj_data.get('events', []))
                    
                    # Если есть свойства, методы или события - это корневой объект
                    has_children = (props_count > 0 or methods_count > 0 or events_count > 0)
                    
                    if has_children:
                        logger.debug(f"Обработка корневого объекта {html_path}")
                        # Обрабатываем как корневой объект
                        obj_data = self._process_root_object(
                            html_path_str,
                            section_name,
                            item.filename
                        )
                        
                        if obj_data:
                            obj_name = obj_data.get('title', item.filename.replace('.html', ''))
                            logger.info(f"    -> Успешно обработан: {obj_name}")
                            result_dict[obj_name] = obj_data
                            self.stats['objects_processed'] = self.stats.get('objects_processed', 0) + 1
                        else:
                            logger.warning(f"    -> Ошибка обработки: {item.filename}")
                    
                    # Также проверяем наличие подкаталога с __categories__ для вложенных объектов
                    item_dir = html_path.parent / item.filename.replace('.html', '')
                    if item_dir.exists() and item_dir.is_dir():
                        # Проверяем подкаталоги properties, methods, events
                        for subdir_name in ['properties', 'methods', 'events']:
                            subdir = item_dir / subdir_name
                            if subdir.exists():
                                sub_categories = subdir / '__categories__'
                                if sub_categories.exists():
                                    # Рекурсивно обрабатываем вложенные категории
                                    pass  # Пока пропускаем, так как ссылки уже есть в HTML
                    
                    # Обрабатываем вложенные __categories__ если есть
                    # Например, objects/catalog63/catalog732/__categories__ содержит InfoBaseUser.html
                    html_name_without_ext = item.filename.replace('.html', '')
                    potential_subdir = categories_dir / html_name_without_ext
                    if potential_subdir.exists() and potential_subdir.is_dir():
                        sub_categories_file = potential_subdir / '__categories__'
                        if sub_categories_file.exists():
                            # Рекурсивно обрабатываем вложенные категории
                            logger.debug(f"Найден вложенный каталог категорий: {sub_categories_file}")
                            self._process_categories_recursive(
                                base_dir,
                                sub_categories_file,
                                section_name,
                                result_dict,
                                f"{relative_path}/{html_name_without_ext}"
                            )
                
                except Exception as e:
                    logger.warning(f"Ошибка обработки {html_path}: {e}")
    
    def _process_root_object(self, html_path: str, section_name: str, filename: str) -> Optional[Dict[str, Any]]:
        """
        Обработка корневой страницы объекта.
        
        Args:
            html_path: Путь к HTML файлу корневого объекта
            section_name: 'objects' или 'tables'
            filename: Имя файла (например, 'Global context.html')
        
        Returns:
            Словарь с данными объекта
        """
        try:
            # Читаем HTML
            with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
                html_content = f.read()
            
            # Парсим корневую страницу
            obj_data = self.html_parser.parse_root_page(html_content, html_path)
            
            # Обрабатываем свойства
            properties = []
            for prop_link in obj_data.get('properties', []):
                prop_detail = self._process_detail_item(
                    html_path,
                    prop_link,
                    'properties',
                    section_name
                )
                if prop_detail:
                    properties.append(prop_detail)
                    self.stats['properties_processed'] += 1
            
            obj_data['properties'] = properties
            
            # Обрабатываем методы
            methods = []
            for method_link in obj_data.get('methods', []):
                method_detail = self._process_detail_item(
                    html_path,
                    method_link,
                    'methods',
                    section_name
                )
                if method_detail:
                    methods.append(method_detail)
                    self.stats['methods_processed'] += 1
            
            obj_data['methods'] = methods
            
            # Обрабатываем события
            events = []
            for event_link in obj_data.get('events', []):
                event_detail = self._process_detail_item(
                    html_path,
                    event_link,
                    'events',
                    section_name
                )
                if event_detail:
                    events.append(event_detail)
                    self.stats['events_processed'] += 1
            
            obj_data['events'] = events
            
            return obj_data
            
        except Exception as e:
            logger.error(f"Ошибка обработки {html_path}: {e}", exc_info=True)
            return None
    
    def _process_detail_item(self, parent_html_path: str, link_info: Dict[str, str],
                            item_type: str, section_name: str) -> Optional[Dict[str, Any]]:
        """
        Обработка детальной страницы (свойство, метод, событие).
        
        Args:
            parent_html_path: Путь к HTML файлу родительского объекта
            link_info: Информация о ссылке из корневой страницы
            item_type: 'properties', 'methods' или 'events'
            section_name: 'objects' или 'tables'
        
        Returns:
            Словарь с детальной информацией
        """
        try:
            # Разрешаем путь к HTML файлу
            link_path = link_info.get('link', link_info.get('path', ''))
            if not link_path:
                return None
            
            html_file_path = self._resolve_link_path(parent_html_path, link_path, section_name)
            if not html_file_path or not os.path.exists(html_file_path):
                logger.debug(f"HTML файл не найден: {html_file_path} (из ссылки {link_path})")
                return None
            
            # Читаем и парсим HTML
            with open(html_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                html_content = f.read()
            
            detail_data = self.html_parser.parse_detail_page(html_content, html_file_path)
            
            # Ищем соответствующий .st файл
            st_file_path = html_file_path.replace('.html', '.st')
            if os.path.exists(st_file_path):
                templates = parse_st_file(st_file_path)
                if templates:
                    detail_data['code_template'] = templates
                    self.stats['st_files_processed'] += 1
                    # Если синтаксис пустой в HTML, пробуем взять из .st файла
                    if not detail_data.get('syntax') and templates:
                        # Берем русский шаблон, если есть
                        if 'ru' in templates:
                            detail_data['syntax'] = templates['ru']
                        elif 'en' in templates:
                            detail_data['syntax'] = templates['en']
            else:
                detail_data['code_template'] = {}
            
            # Добавляем информацию о ссылке
            detail_data['link'] = link_path
            detail_data['name'] = link_info.get('name', detail_data.get('name', ''))
            detail_data['name_en'] = link_info.get('name_en', detail_data.get('name_en', ''))
            
            return detail_data
            
        except Exception as e:
            logger.warning(f"Ошибка обработки детальной страницы {link_info}: {e}")
            return None
    
    def _resolve_link_path(self, parent_html_path: str, link: str, section_name: str) -> Optional[str]:
        """
        Разрешение пути к файлу из ссылки.
        
        Args:
            parent_html_path: Путь к родительскому HTML файлу
            link: Относительная или абсолютная ссылка
            section_name: 'objects' или 'tables'
        
        Returns:
            Абсолютный путь к файлу или None
        """
        # Если ссылка уже абсолютная (начинается с objects/ или tables/)
        if link.startswith('objects/') or link.startswith('tables/'):
            full_path = self.root_dir / link
            if full_path.exists():
                return str(full_path)
        
        # Если относительная ссылка (например, "Global context/properties/prop5975.html")
        parent_dir = os.path.dirname(parent_html_path)
        resolved_path = os.path.normpath(os.path.join(parent_dir, link))
        
        if os.path.exists(resolved_path):
            return resolved_path
        
        # Пробуем найти в директории родителя
        parent_path_obj = Path(parent_html_path)
        parent_dir_obj = parent_path_obj.parent
        
        # Если ссылка указывает на поддиректорию (например, "properties/prop5975.html")
        if '/' in link or '\\' in link:
            link_path = parent_dir_obj / link.replace('\\', '/')
            if link_path.exists():
                return str(link_path)
        
        return None

