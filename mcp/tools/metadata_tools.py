#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP инструменты для обзора метаданных конфигурации 1С.

Предоставляет 2 инструмента:
- list_metadata_objects - список объектов конфигурации (сводка по типам или перечень объектов типа)
- find_object_references - где используется объект (метаданные + код)
"""

import sys
import os
from collections import Counter, defaultdict
from typing import Any, Dict, Iterator, List, Optional

# Добавляем путь к родительской директории для импорта config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchText
import logging

from config import QDRANT_HOST, QDRANT_PORT, COLLECTION_OBJECTS, COLLECTION_PROCEDURES

logger = logging.getLogger(__name__)

qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

# Тип объекта -> имя менеджера в коде (Справочники.Номенклатура)
MANAGER_NAMES = {
    "Справочник": "Справочники",
    "Документ": "Документы",
    "Перечисление": "Перечисления",
    "РегистрСведений": "РегистрыСведений",
    "РегистрНакопления": "РегистрыНакопления",
    "РегистрБухгалтерии": "РегистрыБухгалтерии",
    "РегистрРасчета": "РегистрыРасчета",
    "ПланВидовХарактеристик": "ПланыВидовХарактеристик",
    "ПланСчетов": "ПланыСчетов",
    "ПланВидовРасчета": "ПланыВидовРасчета",
    "ПланОбмена": "ПланыОбмена",
    "БизнесПроцесс": "БизнесПроцессы",
    "Задача": "Задачи",
    "Константа": "Константы",
    "Обработка": "Обработки",
    "Отчет": "Отчеты",
    "ЖурналДокументов": "ЖурналыДокументов",
    "РегламентноеЗадание": "РегламентныеЗадания",
}

MAX_LIST_LIMIT = 2000
MAX_CODE_REFERENCES = 200


def _scroll(collection: str, scroll_filter: Optional[Filter], payload_fields: List[str],
            page_size: int = 500) -> Iterator[Dict[str, Any]]:
    """Постраничный обход коллекции, только нужные поля payload."""
    offset = None
    while True:
        points, next_offset = qdrant_client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=page_size,
            offset=offset,
            with_payload=payload_fields,
            with_vectors=False
        )
        for point in points:
            yield point.payload or {}
        if not points or next_offset is None:
            break
        offset = next_offset


def _field(item: Dict[str, Any], ru: str, en: str, default: Any = "") -> Any:
    return item.get(ru, item.get(en, default))


def _split_types(type_str: str) -> List[str]:
    return [t.strip() for t in (type_str or "").split(",") if t.strip()]


def list_metadata_objects(object_type: Optional[str] = None, name_filter: Optional[str] = None,
                          limit: int = 200) -> str:
    """
    Список объектов конфигурации.

    Без object_type - сводка: сколько объектов каждого типа загружено.
    С object_type - перечень объектов этого типа (имя, синоним, модулей).
    """
    try:
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        needle = name_filter.lower() if name_filter else None

        if not object_type:
            counts = Counter()
            for payload in _scroll(COLLECTION_OBJECTS, None, ["object_type", "object_name", "friendly_name"]):
                if needle and needle not in payload.get("object_name", "").lower() \
                        and needle not in (payload.get("friendly_name") or "").lower():
                    continue
                counts[payload.get("object_type", "?")] += 1

            if not counts:
                return "❌ Объекты не найдены. Проверьте, что конфигурация загружена (diagnose_database)."

            output = [f"Объектов в конфигурации: {sum(counts.values())}"
                      + (f" (фильтр: «{name_filter}»)" if name_filter else ""), "",
                      "| Тип объекта | Количество |", "|---|---|"]
            for obj_type, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                output.append(f"| {obj_type} | {count} |")
            output += ["", "💡 Перечень объектов типа: list_metadata_objects(object_type=\"Справочник\")"]
            return "\n".join(output)

        type_filter = Filter(must=[FieldCondition(key="object_type", match=MatchValue(value=object_type))])
        items = []
        for payload in _scroll(COLLECTION_OBJECTS, type_filter,
                               ["object_name", "friendly_name", "modules_count", "comment"]):
            name = payload.get("object_name", "")
            synonym = payload.get("friendly_name") or ""
            if needle and needle not in name.lower() and needle not in synonym.lower():
                continue
            items.append((name, synonym, payload.get("modules_count", 0)))

        if not items:
            return (f"❌ Объекты типа «{object_type}» не найдены"
                    + (f" по фильтру «{name_filter}»" if name_filter else "")
                    + ". Сводка по типам: list_metadata_objects()")

        items.sort(key=lambda it: it[0].lower())
        shown = items[:limit]
        output = [f"{object_type}: {len(items)} объект(ов)"
                  + (f", показано {len(shown)}" if len(shown) < len(items) else "")
                  + (f" (фильтр: «{name_filter}»)" if name_filter else ""), ""]
        for name, synonym, modules_count in shown:
            line = f"- {name}"
            if synonym and synonym != name:
                line += f" — {synonym}"
            if modules_count:
                line += f" (модулей: {modules_count})"
            output.append(line)
        if len(shown) < len(items):
            output += ["", f"... ещё {len(items) - len(shown)}. Уточните name_filter или увеличьте limit (до {MAX_LIST_LIMIT})."]
        output += ["", "💡 Структура объекта: get_object_metadata(тип, имя)"]
        return "\n".join(output)

    except Exception as e:
        logger.error(f"Ошибка list_metadata_objects: {e}", exc_info=True)
        return f"❌ Ошибка получения списка объектов: {e}"


def _metadata_references(object_type: str, object_name: str) -> List[Dict[str, str]]:
    """Ссылки на объект в метаданных: типы реквизитов, владельцы, ввод на основании, движения."""
    full_name = f"{object_type}.{object_name}"
    refs = []

    fields = ["object_type", "object_name", "attributes", "tabular_sections", "register_info", "relationships"]
    for obj in _scroll(COLLECTION_OBJECTS, None, fields, page_size=200):
        source = f"{obj.get('object_type', '')}.{obj.get('object_name', '')}"
        if source == full_name:
            continue

        def check_fields(items: List[Dict[str, Any]], location: str):
            for item in items or []:
                if full_name in _split_types(_field(item, "Тип", "type")):
                    refs.append({"source": source, "where": f"{location} {_field(item, 'Имя', 'name')}"})

        check_fields(obj.get("attributes"), "Реквизит")
        for ts in obj.get("tabular_sections") or []:
            check_fields(_field(ts, "Реквизиты", "attributes", []), f"ТЧ {_field(ts, 'Имя', 'name')} → реквизит")
        register_info = obj.get("register_info") or {}
        check_fields(register_info.get("dimensions"), "Измерение")
        check_fields(register_info.get("resources"), "Ресурс")

        relationships = obj.get("relationships") or {}
        for key, caption in (("owners", "Владелец"), ("based_on", "Ввод на основании"),
                             ("register_records", "Движения")):
            values = relationships.get(key) or []
            if full_name in values or object_name in values:
                refs.append({"source": source, "where": caption})

    return refs


def _code_patterns(object_type: str, object_name: str) -> List[str]:
    """Варианты обращения к объекту в коде и запросах."""
    if object_type == "ОбщийМодуль":
        return [f"{object_name}."]
    patterns = [f"{object_type}.{object_name}"]  # запросы, ТипЗнч(...) = Тип("Справочник.X")
    manager = MANAGER_NAMES.get(object_type)
    if manager:
        patterns.insert(0, f"{manager}.{object_name}")  # Справочники.X.НайтиПоКоду(...)
    return patterns


def _code_references(object_type: str, object_name: str) -> Dict[str, Any]:
    """Процедуры, в коде которых встречается обращение к объекту (точное вхождение подстроки)."""
    by_module = defaultdict(list)
    seen = set()
    total = 0
    truncated = False

    for pattern in _code_patterns(object_type, object_name):
        code_filter = Filter(must=[FieldCondition(key="full_code", match=MatchText(text=pattern))])
        for proc in _scroll(COLLECTION_PROCEDURES, code_filter,
                            ["object_type", "object_name", "module_name", "procedure_name"]):
            key = (proc.get("object_type"), proc.get("object_name"), proc.get("module_name"), proc.get("procedure_name"))
            if key in seen:
                continue
            seen.add(key)
            total += 1
            if total > MAX_CODE_REFERENCES:
                truncated = True
                break
            by_module[f"{key[0]}.{key[1]} / {key[2]}"].append(key[3])
        if truncated:
            break

    return {"by_module": by_module, "total": min(total, MAX_CODE_REFERENCES), "truncated": truncated}


def find_object_references(object_type: str, object_name: str, include_code: bool = True) -> str:
    """Где используется объект: в метаданных других объектов и в коде процедур."""
    try:
        full_name = f"{object_type}.{object_name}"
        output = [f"# Использование {full_name}", ""]

        meta_refs = _metadata_references(object_type, object_name)
        sources_count = len({ref["source"] for ref in meta_refs})
        output.append(f"## Метаданные ({len(meta_refs)} ссылок в {sources_count} объектах)")
        output.append("")
        if meta_refs:
            grouped = defaultdict(list)
            for ref in meta_refs:
                grouped[ref["source"]].append(ref["where"])
            for source in sorted(grouped):
                output.append(f"- **{source}**: {', '.join(grouped[source])}")
        else:
            output.append("Ссылок в метаданных не найдено.")
        output.append("")

        if include_code:
            code = _code_references(object_type, object_name)
            patterns = ", ".join(f"`{p}`" for p in _code_patterns(object_type, object_name))
            output.append(f"## Код ({code['total']}{'+' if code['truncated'] else ''} процедур, поиск: {patterns})")
            output.append("")
            if code["by_module"]:
                for module in sorted(code["by_module"]):
                    procs = code["by_module"][module]
                    output.append(f"- **{module}**: {', '.join(procs)}")
                if code["truncated"]:
                    output.append("")
                    output.append(f"⚠️ Показаны первые {MAX_CODE_REFERENCES} процедур.")
            else:
                output.append("Обращений в коде не найдено.")
            output.append("")

        output.append("💡 Код процедуры: find_procedure_code(тип, имя, процедура). "
                      "Поиск по подстроке регистрозависимый: динамические обращения (Метаданные.Справочники[Имя]) не находятся.")
        return "\n".join(output)

    except Exception as e:
        logger.error(f"Ошибка find_object_references: {e}", exc_info=True)
        return f"❌ Ошибка поиска ссылок: {e}"
