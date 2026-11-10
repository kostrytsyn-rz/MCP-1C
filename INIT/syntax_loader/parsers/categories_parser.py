#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер файлов __categories__ для извлечения структуры навигации.

Формат файла __categories__:
{count, "file1.html", flags, {conditions}, version, "", "", "file2.html", ...}

Пример:
{10,"Global context.html", 9, {"CONF_ENTERPRISE",...}, "8.0.4", "", "", "catalog56.html", ...}
"""

import re
import os
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class CategoryItem:
    """Элемент категории"""
    def __init__(self, filename: str, flags: int = 0, conditions: List[str] = None, version: str = ""):
        self.filename = filename
        self.flags = flags
        self.conditions = conditions or []
        self.version = version
        self.is_html = filename.endswith('.html')
        self.is_st = filename.endswith('.st')
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь"""
        return {
            'filename': self.filename,
            'flags': self.flags,
            'conditions': self.conditions,
            'version': self.version,
            'is_html': self.is_html,
            'is_st': self.is_st
        }


def parse_categories_file(file_path: str) -> List[CategoryItem]:
    """
    Парсинг файла __categories__.
    
    Args:
        file_path: Путь к файлу __categories__
    
    Returns:
        Список элементов категории
    """
    try:
        # Преобразуем путь в правильный формат для Windows с кириллицей
        if isinstance(file_path, str):
            # Если это строка с обратными слешами, конвертируем
            file_path = file_path.replace('\\', os.sep)
        
        with open(file_path, 'r', encoding='utf-8-sig', errors='ignore') as f:  # utf-8-sig убирает BOM
            content = f.read().strip()
    except Exception as e:
        logger.warning(f"Ошибка чтения {file_path}: {e}")
        logger.debug(f"Тип пути: {type(file_path)}, значение: {repr(file_path)}")
        return []
    
    # Убираем BOM, если он есть
    if content.startswith('\ufeff'):
        content = content[1:].strip()
    
    if not content or not content.startswith('{'):
        logger.debug(f"Файл пуст или не начинается с '{{': первые 100 символов: {content[:100]}")
        return []
    
    # Убираем внешние фигурные скобки
    content = content.strip()
    if content.startswith('{') and content.endswith('}'):
        content = content[1:-1]
    
    logger.debug(f"Длина контента после обработки: {len(content)}, первые 200 символов: {content[:200]}")
    
    items = []
    i = 0
    content_len = len(content)
    
    EXPECTED_COUNT_POS = 0
    
    # Пропускаем количество элементов в начале
    if content_len > 0 and content[0].isdigit():
        count_start = i
        while i < content_len and (content[i].isdigit() or content[i] == ','):
            i += 1
        if i > count_start:
            try:
                count_str = content[count_start:i].replace(',', '')
                expected_count = int(count_str)
                EXPECTED_COUNT_POS = expected_count
                logger.debug(f"Ожидается элементов: {expected_count}")
            except ValueError:
                pass
    
    # Парсим элементы
    element_num = 0
    while i < content_len:
        i = skip_whitespace(content, i)
        if i >= content_len:
            break
        
        # Ищем начало строки с кавычками (имя файла)
        if content[i] == '"':
            element_num += 1
            if element_num == 1:
                logger.debug(f"Начало парсинга первого элемента на позиции {i}")
            # Извлекаем имя файла
            filename_start = i + 1
            filename_end = find_string_end(content, filename_start)
            if filename_end == -1:
                break
            
            filename = content[filename_start:filename_end]
            i = filename_end + 1
            
            # Пропускаем запятую
            i = skip_whitespace_and_comma(content, i)
            if i >= content_len:
                break
            
            # Парсим flags (число)
            flags = 0
            flags_start = i
            while i < content_len and content[i].isdigit():
                i += 1
            if i > flags_start:
                try:
                    flags = int(content[flags_start:i])
                except ValueError:
                    pass
            
            # Пропускаем запятую
            i = skip_whitespace_and_comma(content, i)
            if i >= content_len:
                break
            
            # Парсим conditions {список строк, включая version в конце}
            conditions = []
            version = ""
            if content[i] == '{':
                conditions_end = find_brace_end(content, i)
                if conditions_end != -1:
                    conditions_str = content[i+1:conditions_end]
                    # Парсим список строк вида "CONF_ENTERPRISE","CONF_SRV_ENTERPRISE",..., "8.0.4", "", ""
                    all_strings = parse_string_list(conditions_str)
                    # Разделяем: последний непустой элемент - это версия, остальные - условия
                    if all_strings:
                        # Фильтруем пустые строки и берем последний непустой как версию
                        non_empty = [s for s in all_strings if s]
                        if non_empty:
                            version = non_empty[-1]
                            conditions = non_empty[:-1]  # Все кроме последнего - условия
                    i = conditions_end + 1
            
            # Пропускаем запятую после закрывающей скобки conditions (если следующая строка - это следующий элемент)
            # Но это уже не нужно, так как мы обрабатываем только элементы, начинающиеся с '"'
            
            items.append(CategoryItem(filename, flags, conditions, version))
            logger.debug(f"  Элемент #{element_num}: {filename} (flags={flags}, conditions={len(conditions)}, version={version})")
        else:
            # Пропускаем неизвестный символ
            if i < content_len and content[i] not in ' \t\n\r,':
                logger.debug(f"Неожиданный символ на позиции {i}: '{content[i]}' (код {ord(content[i])}), контекст: '{content[max(0,i-20):i+20]}'")
            i += 1
    
    logger.debug(f"Парсинг завершен: найдено {len(items)} элементов (ожидалось {EXPECTED_COUNT_POS if EXPECTED_COUNT_POS > 0 else 'неизвестно'})")
    return items


def skip_whitespace(s: str, i: int) -> int:
    """Пропуск пробелов"""
    while i < len(s) and s[i] in ' \t\n\r':
        i += 1
    return i


def skip_whitespace_and_comma(s: str, i: int) -> int:
    """Пропуск пробелов и запятой"""
    i = skip_whitespace(s, i)
    if i < len(s) and s[i] == ',':
        i += 1
    return skip_whitespace(s, i)


def find_string_end(s: str, start: int) -> int:
    """Поиск конца строки в кавычках"""
    i = start
    while i < len(s):
        if s[i] == '"' and (i == start or s[i-1] != '\\'):
            return i
        i += 1
    return -1


def find_brace_end(s: str, start: int) -> int:
    """Поиск конца фигурных скобок"""
    depth = 0
    i = start
    while i < len(s):
        if s[i] == '{':
            depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def parse_string_list(s: str) -> List[str]:
    """Парсинг списка строк вида "item1","item2",..."""
    items = []
    i = 0
    s = s.strip()
    
    while i < len(s):
        i = skip_whitespace(s, i)
        if i >= len(s):
            break
        
        if s[i] == '"':
            end = find_string_end(s, i + 1)
            if end == -1:
                break
            items.append(s[i+1:end])
            i = end + 1
            i = skip_whitespace_and_comma(s, i)
        else:
            i += 1
    
    return items


def build_navigation_tree(
    root_dir: str,
    current_path: str = "",
    categories_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Построение дерева навигации из __categories__ файлов.
    
    Args:
        root_dir: Корневая директория с распакованными файлами
        current_path: Текущий путь относительно root_dir
        categories_file: Путь к файлу __categories__ (если None, ищем в current_path)
    
    Returns:
        Словарь с деревом навигации
    """
    if categories_file is None:
        categories_file = os.path.join(root_dir, current_path, '__categories__')
    
    if not os.path.exists(categories_file):
        return {}
    
    # Парсим __categories__ файл
    items = parse_categories_file(categories_file)
    
    # Получаем путь директории
    dir_path = os.path.dirname(categories_file)
    relative_dir = os.path.relpath(dir_path, root_dir).replace('\\', '/')
    
    tree = {
        'path': relative_dir,
        'categories_file': os.path.relpath(categories_file, root_dir).replace('\\', '/'),
        'children': []
    }
    
    # Обрабатываем каждый элемент
    for item in items:
        if item.is_html:
            # Это HTML файл - создаем узел дерева
            html_path = os.path.join(dir_path, item.filename)
            relative_html = os.path.relpath(html_path, root_dir).replace('\\', '/')
            
            # Проверяем, есть ли подкатегории
            html_dir_base = html_path[:-5]  # Убираем .html
            sub_categories = os.path.join(html_dir_base, '__categories__')
            
            child = {
                'title': item.filename[:-5],  # Без .html
                'filename': item.filename,
                'path': relative_html,
                'flags': item.flags,
                'conditions': item.conditions,
                'version': item.version
            }
            
            # Если есть подкатегории, рекурсивно парсим
            if os.path.exists(sub_categories):
                sub_tree = build_navigation_tree(root_dir, os.path.dirname(relative_html), sub_categories)
                if sub_tree.get('children'):
                    child['children'] = sub_tree['children']
            
            tree['children'].append(child)
    
    return tree

