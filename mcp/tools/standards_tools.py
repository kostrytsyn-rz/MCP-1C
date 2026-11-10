#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Стандарты разработки 1С для агента.

Источники:
- DEV_RULES_PATH - правила проекта (1C_DEVELOPMENT_RULES.md);
- STANDARDS_DIR/*.md - стандарты команды (README.md пропускается).

Файлы читаются при каждом вызове, перезапуск сервера после правки не нужен.
"""

import sys
import os
import re
import logging
from typing import Dict, List, Optional, Tuple

# Добавляем путь к родительской директории для импорта config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEV_RULES_PATH, STANDARDS_DIR

logger = logging.getLogger(__name__)

MAX_RESPONSE_CHARS = 20000
_HEADING_RE = re.compile(r'^(#{2,3})\s+(.+?)\s*$')


def list_standard_files() -> List[Tuple[str, str]]:
    """Список (имя, путь) доступных файлов стандартов."""
    files = []
    rules_path = os.path.normpath(DEV_RULES_PATH) if DEV_RULES_PATH else ""
    if rules_path and os.path.isfile(rules_path):
        files.append((os.path.basename(rules_path), rules_path))
    if STANDARDS_DIR and os.path.isdir(STANDARDS_DIR):
        for name in sorted(os.listdir(STANDARDS_DIR)):
            path = os.path.normpath(os.path.join(STANDARDS_DIR, name))
            # Правила проекта могут лежать в той же папке - не дублируем их
            if path == rules_path:
                continue
            if name.lower().endswith(".md") and name.lower() != "readme.md" and os.path.isfile(path):
                files.append((name, path))
    return files


def read_standard_file(name: str) -> Optional[str]:
    """Содержимое файла стандартов по имени (только из известных источников)."""
    for file_name, path in list_standard_files():
        if file_name == name:
            with open(path, "r", encoding="utf-8-sig") as f:
                return f.read()
    return None


def _split_sections(text: str) -> List[Dict[str, str]]:
    """Разбивка markdown на разделы по заголовкам ## и ###."""
    sections = []
    current = {"title": "", "level": 1, "body": []}
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current["title"] or "".join(current["body"]).strip():
                sections.append(current)
            current = {"title": match.group(2), "level": len(match.group(1)), "body": [line]}
        else:
            current["body"].append(line)
    sections.append(current)
    return [{"title": s["title"], "level": s["level"], "text": "\n".join(s["body"]).strip()} for s in sections]


def get_dev_standards(topic: Optional[str] = None) -> str:
    """Оглавление стандартов или разделы по теме."""
    try:
        files = list_standard_files()
        if not files:
            return ("❌ Стандарты не найдены. Проверьте монтирование папки standards/ "
                    f"(DEV_RULES_PATH={DEV_RULES_PATH}, STANDARDS_DIR={STANDARDS_DIR})")

        if not topic or not topic.strip():
            output = ["# Стандарты разработки", ""]
            for name, path in files:
                with open(path, "r", encoding="utf-8-sig") as f:
                    sections = _split_sections(f.read())
                output.append(f"## {name}")
                for section in sections:
                    if section["title"]:
                        indent = "  " if section["level"] == 3 else ""
                        output.append(f"{indent}- {section['title']}")
                output.append("")
            output.append("💡 Раздел по теме: get_dev_standards(\"запросы\"), get_dev_standards(\"транзакции\")")
            return "\n".join(output)

        words = [w for w in re.split(r'\s+', topic.lower().strip()) if w]
        found = []
        for name, path in files:
            with open(path, "r", encoding="utf-8-sig") as f:
                sections = _split_sections(f.read())
            for section in sections:
                title = section["title"].lower()
                text = section["text"].lower()
                # Совпадение в заголовке важнее совпадения в тексте
                score = sum(3 for w in words if w in title) + sum(1 for w in words if w in text)
                if score and all(w in text for w in words):
                    found.append((score, name, section))

        if not found:
            return (f"❌ По теме «{topic}» разделов не найдено. "
                    "Оглавление: get_dev_standards() без параметров.")

        found.sort(key=lambda item: -item[0])
        output = [f"# Стандарты: {topic}", ""]
        size = 0
        shown = 0
        for _, name, section in found:
            block = f"_Источник: {name}_\n\n{section['text']}\n"
            if shown and size + len(block) > MAX_RESPONSE_CHARS:
                output.append(f"... найдено ещё разделов: {len(found) - shown}. Уточните тему.")
                break
            output += [block, ""]
            size += len(block)
            shown += 1
        return "\n".join(output)

    except Exception as e:
        logger.error(f"Ошибка get_dev_standards: {e}", exc_info=True)
        return f"❌ Ошибка чтения стандартов: {e}"
