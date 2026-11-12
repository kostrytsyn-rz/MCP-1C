#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер .st файлов для извлечения шаблонов кода.

Формат .st файла - это вложенные структуры в фигурных скобках:
{1,
 {2,
  {"",1,0,"",""},
  {0,
   {"ru",0,0,"","код на русском"}
  },
  {0,
   {"en",0,0,"","code in English"}
  }
 }
}
"""

import re
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def skip_whitespace(content: str, i: int) -> int:
    """Пропуск пробельных символов"""
    while i < len(content) and content[i] in ' \t\n\r':
        i += 1
    return i


def find_string_end(content: str, start: int) -> int:
    """Найти конец строки в кавычках"""
    if start >= len(content) or content[start] != '"':
        return -1
    
    i = start + 1
    escape = False
    while i < len(content):
        if escape:
            escape = False
        elif content[i] == '\\':
            escape = True
        elif content[i] == '"':
            return i
        i += 1
    return -1


def parse_st_file(file_path: str) -> Optional[Dict[str, str]]:
    """
    Парсинг .st файла для извлечения шаблонов кода по языкам.
    
    Args:
        file_path: Путь к .st файлу
    
    Returns:
        Словарь с шаблонами: {"ru": "...", "en": "..."} или None при ошибке
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read().strip()
    except Exception as e:
        logger.warning(f"Ошибка чтения {file_path}: {e}")
        return None
    
    if not content or not content.startswith('{'):
        logger.debug(f"Неверный формат .st файла: {file_path}")
        return None
    
    templates = {}
    
    # Ищем все вхождения структур с языковыми кодами
    # Формат: {"ru",0,0,"","код"} или {"ru",0,0,"","ACos(<?>)"}
    # Паттерн должен учитывать все символы в строке кода, включая <, >, ?, (, )
    pattern = r'\{"(ru|en)"\s*,\s*\d+\s*,\s*\d+\s*,\s*"[^"]*"\s*,\s*"((?:[^"\\]|\\.|\\n|\\t)*?)"\s*\}'
    
    matches = re.finditer(pattern, content, re.DOTALL)
    for match in matches:
        lang_code = match.group(1)
        code = match.group(2)
        
        # Раскодируем экранированные символы
        code = code.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t').replace('\\\\', '\\')
        # Заменяем плейсхолдеры
        code = code.replace('&lt;', '<').replace('&gt;', '>').replace('<?>', '<Число>')
        
        if lang_code in ('ru', 'en') and code:
            templates[lang_code] = code.strip()
    
    # Если не нашли через regex, пробуем ручной парсинг
    if not templates:
        templates = _manual_parse_st(content)
    
    return templates if templates else None


def _manual_parse_st(content: str) -> Dict[str, str]:
    """Ручной парсинг .st файла"""
    templates = {}
    i = 0
    content_len = len(content)
    
    while i < content_len:
        i = skip_whitespace(content, i)
        if i >= content_len:
            break
        
        # Ищем начало строки с языковым кодом
        if content[i] == '{':
            # Пытаемся найти языковой код
            code_start = content.find('"ru"', i)
            if code_start == -1:
                code_start = content.find('"en"', i)
            
            if code_start != -1:
                # Нашли языковой код, ищем строку с кодом
                # Ожидаем формат: {"ru",0,0,"","код"}
                lang_match = re.match(r'\{(["\'])(ru|en)\1', content[code_start:code_start+10])
                if lang_match:
                    lang = lang_match.group(2)
                    
                    # Ищем последнюю строку в этой структуре
                    # Ищем все строки в кавычках после языкового кода
                    rest = content[code_start:]
                    string_pattern = r'["\']([^"\']*)["\']'
                    strings = re.findall(string_pattern, rest)
                    
                    # Последняя непустая строка обычно содержит код
                    # Формат: {"ru",0,0,"","код"} - последняя строка после 3 пустых/цифр
                    for s in reversed(strings):
                        if s and s != lang and s != "" and len(s) > 0:
                            # Заменяем плейсхолдеры и HTML сущности
                            code = s.replace('&lt;', '<').replace('&gt;', '>').replace('<?>', '<Число>')
                            templates[lang] = code
                            break
        
        i += 1
    
    return templates


def extract_code_from_st(content: str) -> Dict[str, str]:
    """
    Более надежный метод извлечения кода из .st файла.
    Парсит вложенные структуры рекурсивно.
    """
    templates = {}
    
    def parse_value(content: str, pos: int) -> Tuple[Optional[Dict], int]:
        """Рекурсивный парсинг значения"""
        pos = skip_whitespace(content, pos)
        if pos >= len(content):
            return None, pos
        
        if content[pos] == '{':
            # Начинается структура
            pos += 1
            result = {}
            while pos < len(content):
                pos = skip_whitespace(content, pos)
                if content[pos] == '}':
                    pos += 1
                    break
                
                # Парсим элемент структуры
                elem, pos = parse_value(content, pos)
                if elem is None:
                    pos += 1
                    continue
                
                # Если это словарь с языковым кодом
                if isinstance(elem, dict):
                    if 'lang' in elem and 'code' in elem:
                        templates[elem['lang']] = elem['code']
                
                pos = skip_whitespace(content, pos)
                if pos < len(content) and content[pos] == ',':
                    pos += 1
        
        elif content[pos] == '"':
            # Строка
            end = find_string_end(content, pos)
            if end != -1:
                value = content[pos+1:end]
                # Экранирование
                value = value.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
                pos = end + 1
                
                # Проверяем, является ли это языковым кодом
                if value in ('ru', 'en'):
                    # Следующие значения могут быть кодом
                    return {'lang': value}, pos
                else:
                    return {'code': value}, pos
        
        else:
            # Число или другое
            start = pos
            while pos < len(content) and content[pos] not in ',} \t\n\r':
                pos += 1
            return None, pos
        
        return None, pos
    
    parse_value(content, 0)
    return templates

