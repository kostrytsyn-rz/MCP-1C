#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер HTML для иерархической структуры документации 1С.

Извлекает:
- Для корневых страниц: списки свойств/методов/событий
- Для детальных страниц: полную информацию (версия, использование, описание, доступность)
"""

import re
from typing import Dict, List, Any, Optional
import logging

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    class BeautifulSoupStub:
        def __init__(self, *args, **kwargs):
            raise ImportError("BeautifulSoup4 не установлен. Установите: pip install beautifulsoup4 html5lib")
    BeautifulSoup = BeautifulSoupStub

logger = logging.getLogger(__name__)


class HierarchicalHTMLParser:
    """Парсер HTML для иерархической структуры"""
    
    def parse_root_page(self, html_content: str, file_path: str = "") -> Dict[str, Any]:
        """
        Парсинг корневой страницы объекта (например, Global context.html).
        
        Извлекает:
        - Название и версию
        - Описание
        - Списки ссылок на свойства/методы/события
        - Доступность
        - Примеры использования
        """
        if not HAS_BS4:
            raise ImportError("BeautifulSoup4 не установлен")
        
        soup = BeautifulSoup(html_content, 'html5lib')
        
        result = {
            'path': file_path,
            'title': self._extract_title(soup),
            'title_en': self._extract_title_en(soup),
            'version': self._extract_version(soup),
            'description': self._extract_description(soup),
            'availability': self._extract_availability(soup),
            'properties': self._extract_section_links(soup, 'Свойства:', 'properties'),
            'methods': self._extract_section_links(soup, 'Методы:', 'methods'),
            'events': self._extract_section_links(soup, 'События:', 'events'),
            'examples': self._extract_examples(soup)
        }
        
        return result
    
    def parse_detail_page(self, html_content: str, file_path: str = "") -> Dict[str, Any]:
        """
        Парсинг детальной страницы (свойство, метод, событие).
        
        Извлекает:
        - Название (RU и EN)
        - Версию
        - Использование (чтение/запись)
        - Описание
        - Тип (для свойств)
        - Параметры (для методов)
        - Доступность
        """
        if not HAS_BS4:
            raise ImportError("BeautifulSoup4 не установлен")
        
        soup = BeautifulSoup(html_content, 'html5lib')
        
        result = {
            'path': file_path,
            'name': self._extract_name(soup),
            'name_en': self._extract_name_en(soup),
            'version': self._extract_version(soup),
            'usage': self._extract_usage(soup),
            'description': self._extract_description(soup),
            'type': self._extract_type(soup),
            'syntax': self._extract_syntax(soup),
            'parameters': self._extract_parameters(soup),
            'availability': self._extract_availability(soup),
            'version_info': self._extract_version_info(soup),
            'examples': self._extract_examples(soup)
        }
        
        return result
    
    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Извлечение заголовка"""
        title_tag = soup.find('h1', class_='V8SH_pagetitle')
        if title_tag:
            title = title_tag.get_text(strip=True)
            # Убираем английское название в скобках
            title = re.sub(r'\s*\([^)]+\)\s*$', '', title).strip()
            return title
        
        title_tag = soup.find('p', class_='V8SH_title')
        if title_tag:
            title = title_tag.get_text(strip=True)
            title = re.sub(r'\s*\([^)]+\)\s*$', '', title).strip()
            return title
        
        return ""
    
    def _extract_title_en(self, soup: BeautifulSoup) -> str:
        """Извлечение английского названия"""
        title_tag = soup.find('h1', class_='V8SH_pagetitle')
        if title_tag:
            title = title_tag.get_text(strip=True)
            match = re.search(r'\(([^)]+)\)', title)
            if match:
                return match.group(1).strip()
        
        return ""
    
    def _extract_name(self, soup: BeautifulSoup) -> str:
        """Извлечение названия элемента"""
        heading = soup.find('p', class_='V8SH_heading')
        if heading:
            name = heading.get_text(strip=True)
            name = re.sub(r'\s*\([^)]+\)\s*$', '', name).strip()
            return name
        return ""
    
    def _extract_name_en(self, soup: BeautifulSoup) -> str:
        """Извлечение английского названия элемента"""
        heading = soup.find('p', class_='V8SH_heading')
        if heading:
            name = heading.get_text(strip=True)
            match = re.search(r'\(([^)]+)\)', name)
            if match:
                return match.group(1).strip()
        return ""
    
    def _extract_version(self, soup: BeautifulSoup) -> str:
        """Извлечение версии из блока __SINCE_SHOW_STYLE__"""
        version_div = soup.find('div', class_='__SINCE_SHOW_STYLE__')
        if version_div:
            version_text = version_div.get_text(strip=True)
            # Ищем паттерн "Доступен, начиная с версии X.X"
            match = re.search(r'(\d+\.\d+(?:\.\d+)?)', version_text)
            if match:
                return match.group(1)
        return ""
    
    def _extract_usage(self, soup: BeautifulSoup) -> str:
        """Извлечение использования (чтение/запись)"""
        chapters = soup.find_all('p', class_='V8SH_chapter')
        for chapter in chapters:
            if 'Использование:' in chapter.get_text():
                next_elem = chapter.next_sibling
                while next_elem:
                    if hasattr(next_elem, 'get_text'):
                        text = next_elem.get_text(strip=True)
                        if text:
                            return text
                    next_elem = next_elem.next_sibling
        return ""
    
    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Извлечение описания"""
        chapters = soup.find_all('p', class_='V8SH_chapter')
        for chapter in chapters:
            if 'Описание:' in chapter.get_text():
                desc_parts = []
                next_elem = chapter.next_sibling
                while next_elem:
                    # Останавливаемся на следующем заголовке раздела
                    if (hasattr(next_elem, 'get') and hasattr(next_elem, 'name') and
                        next_elem.name == 'p' and next_elem.get('class') == ['V8SH_chapter']):
                        break
                    
                    if hasattr(next_elem, 'get_text'):
                        text = next_elem.get_text(strip=True)
                        if text:
                            desc_parts.append(text)
                    next_elem = next_elem.next_sibling
                
                return ' '.join(desc_parts)
        return ""
    
    def _extract_type(self, soup: BeautifulSoup) -> str:
        """Извлечение типа (для свойств)"""
        desc_text = self._extract_description(soup)
        # Ищем паттерн "Тип: <тип>"
        match = re.search(r'Тип:\s*([^.]+)', desc_text)
        if match:
            return match.group(1).strip()
        return ""
    
    def _extract_syntax(self, soup: BeautifulSoup) -> str:
        """Извлечение синтаксиса"""
        chapters = soup.find_all('p', class_='V8SH_chapter')
        for chapter in chapters:
            if 'Синтаксис:' in chapter.get_text():
                next_elem = chapter.next_sibling
                while next_elem:
                    # Проверяем, не дошли ли до следующего раздела
                    if (hasattr(next_elem, 'name') and hasattr(next_elem, 'get') and
                        next_elem.name == 'p' and next_elem.get('class') == ['V8SH_chapter']):
                        break
                    
                    # Синтаксис может быть текстовым узлом (NavigableString) сразу после заголовка
                    # Или внутри элемента <p>
                    # Сначала проверяем, является ли это текстовым узлом
                    if hasattr(next_elem, 'strip') and not hasattr(next_elem, 'get'):
                        # Это NavigableString (текстовый узел)
                        text = str(next_elem).strip()
                        if text:
                            # Заменяем HTML сущности
                            text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
                            return text
                    elif hasattr(next_elem, 'get_text'):
                        # Это элемент с текстом
                        text = next_elem.get_text(strip=True)
                        if text:
                            text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
                            return text
                    elif isinstance(next_elem, str):
                        # Просто строка
                        text = next_elem.strip()
                        if text:
                            text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
                            return text
                    
                    next_elem = next_elem.next_sibling
        return ""
    
    def _extract_parameters(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Извлечение параметров"""
        parameters = []
        
        # Находим раздел "Параметры:"
        chapters = soup.find_all('p', class_='V8SH_chapter')
        params_chapter = None
        
        for chapter in chapters:
            if 'Параметры:' in chapter.get_text():
                params_chapter = chapter
                break
        
        if not params_chapter:
            return parameters
        
        # Находим все V8SH_rubric после раздела параметров
        rubrics = []
        current = params_chapter.find_next_sibling()
        
        while current:
            # Останавливаемся на следующем разделе
            if (hasattr(current, 'name') and current.name == 'p' and 
                hasattr(current, 'get') and current.get('class') == ['V8SH_chapter']):
                break
            
            # Собираем все V8SH_rubric
            if (hasattr(current, 'name') and current.name == 'div' and
                hasattr(current, 'get') and current.get('class') == ['V8SH_rubric']):
                rubrics.append(current)
            
            current = current.find_next_sibling() if hasattr(current, 'find_next_sibling') else None
        
        # Обрабатываем каждый rubric
        for rubric in rubrics:
            # Имя параметра
            param_name_elem = rubric.find('p')
            if not param_name_elem:
                continue
            
            param_name = param_name_elem.get_text(strip=True)
            
            # Описание - всё между текущим rubric и следующим (или концом раздела)
            desc_parts = []
            next_elem = rubric.next_sibling
            
            while next_elem:
                # Проверяем условия остановки
                should_stop = False
                
                if hasattr(next_elem, 'name') and next_elem.name:
                    if next_elem.name == 'div' and next_elem.get('class') == ['V8SH_rubric']:
                        should_stop = True
                    elif next_elem.name == 'p' and next_elem.get('class') == ['V8SH_chapter']:
                        should_stop = True
                
                if should_stop:
                    break
                
                # Собираем текст
                if hasattr(next_elem, 'get_text'):
                    text = next_elem.get_text(strip=True)
                    if text:
                        desc_parts.append(text)
                elif hasattr(next_elem, 'strip'):  # Текстовый узел (NavigableString)
                    text = str(next_elem).strip()
                    if text:
                        desc_parts.append(text)
                
                next_elem = next_elem.next_sibling
            
            param_desc = ' '.join(desc_parts)
            
            # Извлекаем тип
            type_match = re.search(r'Тип:\s*([^.]+?)\.', param_desc)
            param_type = type_match.group(1).strip() if type_match else ""
            
            parameters.append({
                'name': param_name,
                'description': param_desc,
                'type': param_type
            })
        
        return parameters
    
    def _extract_availability(self, soup: BeautifulSoup) -> str:
        """Извлечение доступности"""
        chapters = soup.find_all('p', class_='V8SH_chapter')
        for chapter in chapters:
            if 'Доступность:' in chapter.get_text():
                avail_parts = []
                next_elem = chapter.next_sibling
                while next_elem:
                    if (hasattr(next_elem, 'get') and hasattr(next_elem, 'name') and
                        next_elem.name == 'p' and next_elem.get('class') == ['V8SH_chapter']):
                        break
                    
                    if hasattr(next_elem, 'get_text'):
                        text = next_elem.get_text(strip=True)
                        if text:
                            avail_parts.append(text)
                    next_elem = next_elem.next_sibling
                
                return ' '.join(avail_parts)
        return ""
    
    def _extract_version_info(self, soup: BeautifulSoup) -> List[str]:
        """Извлечение информации о версиях"""
        version_infos = []
        version_tags = soup.find_all('p', class_='V8SH_versionInfo')
        for tag in version_tags:
            text = tag.get_text(strip=True)
            if text:
                version_infos.append(text)
        return version_infos
    
    def _extract_section_links(self, soup: BeautifulSoup, section_name: str, section_type: str) -> List[Dict[str, str]]:
        """
        Извлечение ссылок из раздела (Свойства/Методы/События).
        
        Returns:
            Список словарей с информацией о ссылках:
            [{"name": "...", "name_en": "...", "link": "...", "path": "..."}, ...]
        """
        links = []
        chapters = soup.find_all('p', class_='V8SH_chapter')
        
        for i, chapter in enumerate(chapters):
            chapter_text = chapter.get_text(strip=True)
            if section_name in chapter_text:
                # Находим все следующие элементы до следующего раздела
                next_chapter = chapters[i + 1] if i + 1 < len(chapters) else None
                
                # Проходим по всем следующим элементам через next_sibling
                current = chapter.next_sibling
                
                while current:
                    # Проверяем, не дошли ли до следующего раздела
                    if next_chapter and current == next_chapter:
                        break
                    if (hasattr(current, 'name') and hasattr(current, 'get') and
                        current.name == 'p' and current.get('class') == ['V8SH_chapter']):
                        break
                    
                    # Проверяем, является ли это прямой ссылкой <a>
                    if hasattr(current, 'name') and current.name == 'a':
                        href = current.get('href', '')
                        link_text = current.get_text(strip=True)
                        
                        if href and link_text and not any(l['link'] == href for l in links):
                            # Разделяем русское и английское название
                            name_match = re.match(r'^(?:&lt;)?(.+?)(?:&gt;)?\s*\([<]?([^<>)]+)[>]?\)\s*$', link_text)
                            if not name_match:
                                name_match = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', link_text)
                            
                            if name_match:
                                name = name_match.group(1).strip().replace('&lt;', '<').replace('&gt;', '>')
                                name_en = name_match.group(2).strip().replace('&lt;', '<').replace('&gt;', '>')
                            else:
                                name = link_text.replace('&lt;', '<').replace('&gt;', '>')
                                name_en = ""
                            
                            links.append({
                                'name': name,
                                'name_en': name_en,
                                'link': href,
                                'path': href
                            })
                    # Если это контейнер, ищем ссылки внутри
                    elif hasattr(current, 'find_all'):
                        for link_tag in current.find_all('a'):
                            href = link_tag.get('href', '')
                            link_text = link_tag.get_text(strip=True)
                            
                            if href and link_text and not any(l['link'] == href for l in links):
                                name_match = re.match(r'^(?:&lt;)?(.+?)(?:&gt;)?\s*\([<]?([^<>)]+)[>]?\)\s*$', link_text)
                                if not name_match:
                                    name_match = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', link_text)
                                
                                if name_match:
                                    name = name_match.group(1).strip().replace('&lt;', '<').replace('&gt;', '>')
                                    name_en = name_match.group(2).strip().replace('&lt;', '<').replace('&gt;', '>')
                                else:
                                    name = link_text.replace('&lt;', '<').replace('&gt;', '>')
                                    name_en = ""
                                
                                links.append({
                                    'name': name,
                                    'name_en': name_en,
                                    'link': href,
                                    'path': href
                                })
                    
                    # Переходим к следующему элементу
                    current = current.next_sibling
        
        return links
    
    def _extract_examples(self, soup: BeautifulSoup) -> List[str]:
        """Извлечение примеров кода"""
        examples = []
        chapters = soup.find_all('p', class_='V8SH_chapter')
        
        for chapter in chapters:
            chapter_text = chapter.get_text(strip=True)
            # Проверяем, что это раздел с примерами
            if not ('Пример:' in chapter_text or 'Примеры:' in chapter_text):
                continue
            
            # Используем find_next('table') - это более надежный способ,
            # который игнорирует текстовые узлы между элементами
            table = chapter.find_next('table')
            
            if table:
                # Проверяем, что таблица не слишком далеко (не за следующим разделом)
                next_chapter = chapter.find_next('p', class_='V8SH_chapter')
                
                # Если следующий раздел существует, проверяем порядок элементов
                if next_chapter and next_chapter != chapter:
                    # Получаем все элементы в порядке их появления в документе
                    all_elements = list(soup.descendants)
                    try:
                        table_pos = all_elements.index(table)
                        next_chapter_pos = all_elements.index(next_chapter)
                        
                        # Если таблица идет после следующего раздела - пропускаем
                        if table_pos > next_chapter_pos:
                            continue
                    except (ValueError, AttributeError):
                        # Если не удалось определить позиции, продолжаем
                        pass
                
                # Извлекаем текст из таблицы
                # В HTML код внутри таблицы разбит тегами <BR>, нужно правильно их обработать
                # Получаем строковое представление таблицы
                table_str = str(table)
                # Заменяем BR теги на переносы строк
                table_str = re.sub(r'<br\s*/?>|<BR\s*/?>', '\n', table_str, flags=re.IGNORECASE)
                
                # Парсим обратно для извлечения текста
                table_soup = BeautifulSoup(table_str, 'html5lib')
                table_elem = table_soup.find('table')
                
                # Извлекаем текст без separator (чтобы сохранить порядок)
                table_text = table_elem.get_text(separator='', strip=False) if table_elem else ''
                
                # Заменяем HTML сущности
                table_text = table_text.replace('&nbsp;', ' ')
                table_text = table_text.replace('&lt;', '<')
                table_text = table_text.replace('&gt;', '>')
                
                # Обрабатываем строки: нормализуем пробелы в каждой строке
                lines = table_text.split('\n')
                normalized_lines = []
                for line in lines:
                    # Убираем множественные пробелы/табы, но сохраняем структуру
                    line = re.sub(r'[ \t]+', ' ', line).strip()
                    if line:  # Добавляем только непустые строки
                        normalized_lines.append(line)
                
                table_text = '\n'.join(normalized_lines)
                
                if table_text:
                    examples.append(table_text)
                    continue  # Переходим к следующему разделу
            
            # Резервный способ: Поиск через find_next_sibling
            # Это пропускает текстовые узлы между элементами
            current = chapter
            max_steps = 10  # Ограничение на количество шагов для безопасности
            
            for _ in range(max_steps):
                current = current.find_next_sibling()
                if current is None:
                    break
                
                # Если нашли следующий раздел - останавливаемся
                if current.name == 'p' and current.get('class') == ['V8SH_chapter']:
                    break
                
                # Если нашли таблицу - извлекаем текст
                if current.name == 'table':
                    # Получаем строковое представление таблицы
                    table_str = str(current)
                    # Заменяем BR теги на переносы строк
                    table_str = re.sub(r'<br\s*/?>|<BR\s*/?>', '\n', table_str, flags=re.IGNORECASE)
                    
                    # Парсим обратно для извлечения текста
                    table_soup = BeautifulSoup(table_str, 'html5lib')
                    table_elem = table_soup.find('table')
                    
                    # Извлекаем текст
                    table_text = table_elem.get_text(separator='', strip=False) if table_elem else ''
                    table_text = table_text.replace('&nbsp;', ' ')
                    table_text = table_text.replace('&lt;', '<')
                    table_text = table_text.replace('&gt;', '>')
                    
                    # Нормализуем строки
                    lines = table_text.split('\n')
                    normalized_lines = []
                    for line in lines:
                        line = re.sub(r'[ \t]+', ' ', line).strip()
                        if line:
                            normalized_lines.append(line)
                    table_text = '\n'.join(normalized_lines)
                    
                    if table_text:
                        examples.append(table_text)
                    break
        
        return examples

