#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Экспорт синтаксис-помощника 1С (structure.json) в Markdown.

Структура результата:
    <out_dir>/README.md                - индекс всех объектов
    <out_dir>/objects/<Объект>.md      - объект: описание, свойства, методы, события
"""

import os
import re
from typing import Any, Dict, List

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str) -> str:
    """Имя файла без недопустимых символов (Windows/Linux)."""
    cleaned = _INVALID_FILENAME_CHARS.sub('_', name).strip(' .')
    return cleaned or 'unnamed'


def _clean(text: Any) -> str:
    """Нормализация текста из HTML справки (неразрывные пробелы и т.п.)."""
    if not text:
        return ''
    return str(text).replace(' ', ' ').strip()


def _render_item(item: Dict[str, Any], level: str = '###') -> List[str]:
    """Свойство/метод/событие объекта в Markdown."""
    name = _clean(item.get('name'))
    name_en = _clean(item.get('name_en'))
    lines = [f"{level} {name}" + (f" ({name_en})" if name_en and name_en != name else ''), '']

    syntax = _clean(item.get('syntax'))
    if syntax:
        lines += ['```bsl', syntax, '```', '']

    for label, key in (('Использование', 'usage'), ('Тип', 'type')):
        value = _clean(item.get(key))
        if value:
            lines += [f"**{label}:** {value}", '']

    description = _clean(item.get('description'))
    if description:
        lines += [description, '']

    parameters = item.get('parameters') or []
    if parameters:
        lines += ['**Параметры:**', '']
        for param in parameters:
            param_name = _clean(param.get('name'))
            param_desc = _clean(param.get('description'))
            lines.append(f"- `{param_name}` — {param_desc}" if param_desc else f"- `{param_name}`")
        lines.append('')

    returns = _clean(item.get('returns') or item.get('return_value'))
    if returns:
        lines += [f"**Возвращаемое значение:** {returns}", '']

    availability = _clean(item.get('availability'))
    if availability:
        lines += [f"**Доступность:** {availability}", '']

    version_info = [_clean(v) for v in (item.get('version_info') or []) if _clean(v)]
    if version_info:
        lines += ['**Версии:** ' + ' '.join(version_info), '']

    for example in item.get('examples') or []:
        example = _clean(example)
        if example:
            lines += ['**Пример:**', '', '```bsl', example, '```', '']

    return lines


def render_object(title: str, obj: Dict[str, Any]) -> str:
    """Объект синтаксис-помощника целиком в Markdown."""
    title_en = _clean(obj.get('title_en'))
    lines = [f"# {title}" + (f" ({title_en})" if title_en and title_en != title else ''), '']

    version = _clean(obj.get('version'))
    if version:
        lines += [f"*Доступен с версии {version}*", '']

    description = _clean(obj.get('description'))
    if description:
        lines += [description, '']

    availability = _clean(obj.get('availability'))
    if availability:
        lines += [f"**Доступность:** {availability}", '']

    sections = (('properties', 'Свойства'), ('methods', 'Методы'), ('events', 'События'))

    # Оглавление
    toc = []
    for key, caption in sections:
        items = obj.get(key) or []
        if items:
            toc.append(f"- [{caption}](#{caption.lower()}) ({len(items)})")
    if toc:
        lines += toc + ['']

    for key, caption in sections:
        items = obj.get(key) or []
        if not items:
            continue
        lines += [f"## {caption}", '']
        for item in sorted(items, key=lambda i: _clean(i.get('name')).lower()):
            lines += _render_item(item)

    for example in obj.get('examples') or []:
        example = _clean(example)
        if example:
            lines += ['## Пример', '', '```bsl', example, '```', '']

    return '\n'.join(lines).rstrip() + '\n'


def export_to_markdown(structure: Dict[str, Any], out_dir: str) -> Dict[str, int]:
    """
    Экспорт structure.json в набор Markdown-файлов.

    Args:
        structure: Содержимое structure.json (ключ 'objects')
        out_dir: Папка для результата

    Returns:
        Статистика: количество объектов, свойств, методов, событий
    """
    objects: Dict[str, Dict[str, Any]] = structure.get('objects') or {}
    objects_dir = os.path.join(out_dir, 'objects')
    os.makedirs(objects_dir, exist_ok=True)

    stats = {'objects': 0, 'properties': 0, 'methods': 0, 'events': 0}
    index_lines = ['# Синтаксис-помощник 1С:Предприятие', '', '| Объект | Англ. | Свойств | Методов | Событий |',
                   '|---|---|---|---|---|']
    used_names: Dict[str, int] = {}

    for title in sorted(objects, key=str.lower):
        obj = objects[title]
        file_name = _safe_filename(title)
        # Разные объекты могут дать одинаковое имя файла (регистр, спецсимволы)
        key = file_name.lower()
        if key in used_names:
            used_names[key] += 1
            file_name = f"{file_name}_{used_names[key]}"
        else:
            used_names[key] = 1

        with open(os.path.join(objects_dir, f"{file_name}.md"), 'w', encoding='utf-8') as f:
            f.write(render_object(title, obj))

        counts = {k: len(obj.get(k) or []) for k in ('properties', 'methods', 'events')}
        stats['objects'] += 1
        for k, v in counts.items():
            stats[k] += v

        link = 'objects/' + file_name.replace(' ', '%20') + '.md'
        title_cell = title.replace('|', '\\|')
        index_lines.append(f"| [{title_cell}]({link}) | {_clean(obj.get('title_en'))} | "
                           f"{counts['properties']} | {counts['methods']} | {counts['events']} |")

    index_lines += ['', f"Объектов: {stats['objects']}, свойств: {stats['properties']}, "
                        f"методов: {stats['methods']}, событий: {stats['events']}", '']
    with open(os.path.join(out_dir, 'README.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines))

    return stats
