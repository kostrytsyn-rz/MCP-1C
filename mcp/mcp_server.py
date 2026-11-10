from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from starlette.requests import Request
from starlette.responses import JSONResponse
import json
import requests
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, Prefetch, FusionQuery, Fusion, FieldCondition, MatchValue
from typing import Dict, Any, List, Literal
from pydantic import BaseModel, Field
import logging

from config import (
    QDRANT_HOST, QDRANT_PORT, EMBEDDING_SERVICE_URL,
    SERVER_HOST, SERVER_PORT, DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT,
    MIN_SEARCH_LIMIT, SERVER_NAME,
    EMBEDDING_REQUEST_TIMEOUT, HEALTH_CHECK_TIMEOUT,
    COLLECTION_PROCEDURES, MAX_FULL_CODE_SIZE,
    COLLECTION_SYNTAX, COLLECTION_OBJECTS, COLLECTION_MODULES, COLLECTION_CHUNKS
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(name=SERVER_NAME)

# Подключение к Qdrant
qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


from tools import bsl_syntax_tools
from tools import bsl_check_tools
from tools import metadata_tools
from tools import standards_tools

# Регистрация инструментов синтакс-помощника через декораторы
@mcp.tool
def bslsyntax_search_global_function(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """
    Поиск глобальных функций 1С.
    
    Используйте для поиска встроенных функций платформы 1С:Предприятие.
    
    Args:
        query: Описание или название функции (например: "длина строки", "формат даты", "округление числа")
        limit: Количество результатов (по умолчанию 5)
    
    Returns:
        Список функций с синтаксисом, параметрами и примерами
    
    Examples:
        bslsyntax_search_global_function("длина строки")
        bslsyntax_search_global_function("округление числа")
        bslsyntax_search_global_function("текущая дата")
    """
    return bsl_syntax_tools.search_1c_global_function(query, limit)

@mcp.tool
def bslsyntax_search_method(object_name: str | None = None, method_query: str = "", limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """
    Поиск методов объектов 1С.
    
    Args:
        object_name: Имя объекта (ТаблицаЗначений, Запрос, СписокЗначений, Файл и т.д.)
        method_query: Описание метода (например: "добавить строку", "выполнить запрос")
        limit: Количество результатов (по умолчанию 5)
    
    Returns:
        Список методов с синтаксисом и примерами
    
    Examples:
        bslsyntax_search_method("ТаблицаЗначений", "добавить")
        bslsyntax_search_method("Запрос", "выполнить")
        bslsyntax_search_method(method_query="получить элемент по индексу")
    """
    return bsl_syntax_tools.search_1c_object_method(object_name, method_query, limit)

@mcp.tool
def bslsyntax_search_property(query: str, object_name: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """
    Поиск свойств объектов 1С.
    
    Args:
        query: Описание свойства (например: "колонки", "количество элементов")
        object_name: Имя объекта (опционально, для уточнения поиска)
        limit: Количество результатов (по умолчанию 5)
    
    Returns:
        Список свойств с описанием и типами
    
    Examples:
        bslsyntax_search_property("колонки", "ТаблицаЗначений")
        bslsyntax_search_property("количество элементов")
        bslsyntax_search_property("имя файла", "Файл")
    """
    return bsl_syntax_tools.search_1c_property(query, object_name, limit)

@mcp.tool
def bslsyntax_get_details(item_name: str, item_type: str = "function") -> str:
    """
    Получить детальную информацию о синтаксисе.
    
    Используйте когда знаете точное имя функции/метода/свойства.
    
    Args:
        item_name: Точное имя (СтрДлина, Добавить, Колонки и т.д.)
        item_type: Тип элемента:
            - "function" или "global_function" - глобальная функция
            - "method" или "object_method" - метод объекта
            - "property" - свойство объекта
    
    Returns:
        Полное описание с примерами, параметрами, замечаниями
    
    Examples:
        bslsyntax_get_details("СтрДлина", "function")
        bslsyntax_get_details("Добавить", "method")
        bslsyntax_get_details("Колонки", "property")
    """
    return bsl_syntax_tools.get_1c_syntax_details(item_name, item_type)


# Регистрация инструментов проверки кода (BSL Language Server + стандарты)
@mcp.tool
def bslcheck_code(code: str) -> str:
    """
    Проверка кода 1С (BSL), переданного текстом. ИСПОЛЬЗУЙ после каждого изменения кода.

    Запускает BSL Language Server (синтаксис, ошибки, производительность, стиль) и проверки
    по стандартам 1С (ПолучитьОбъект() ради реквизита, Сообщить(), ТекущаяДата(), НайтиПоНаименованию).
    Возвращает построчный список замечаний: «строка:колонка [правило] сообщение» и строку кода.

    Args:
        code: Текст модуля или фрагмента (процедуры/функции целиком)

    Returns:
        Список замечаний (❌ ошибки, ⚠️ предупреждения) или «Замечаний нет»

    Examples:
        bslcheck_code("Процедура Тест()\n    Сообщить(Ссылка.ПолучитьОбъект().Наименование);\nКонецПроцедуры")
    """
    return bsl_check_tools.bslcheck_code(code)


@mcp.tool
def bslcheck_analyze(file_path: str) -> str:
    """
    Проверка .bsl файла из папки проектов (примонтирована в docker-compose.yml как /workspaces).

    То же, что bslcheck_code, но код читается из файла. Файл не изменяется.

    Args:
        file_path: Путь к .bsl файлу (например: /workspaces/МойПроект/Module.bsl или ./projects/1c/МойПроект/Module.bsl)

    Returns:
        Список замечаний BSL Language Server и проверок по стандартам
    """
    return bsl_check_tools.bslcheck_analyze(file_path)


@mcp.tool
def bslcheck_format(code: str | None = None, file_path: str | None = None) -> str:
    """
    Форматирование кода BSL Language Server (отступы, пробелы, регистр ключевых слов).

    Исходный файл НЕ изменяется - отформатированный код возвращается текстом.

    Args:
        code: Текст кода для форматирования
        file_path: Или путь к .bsl файлу в папке проектов (/workspaces/...)

    Returns:
        Отформатированный код
    """
    return bsl_check_tools.bslcheck_format(code, file_path)


@mcp.tool
def get_dev_standards(topic: str | None = None) -> str:
    """
    Стандарты и правила разработки 1С (правила проекта + стандарты команды из папки standards/).

    Используй ПЕРЕД написанием кода по теме задачи, чтобы следовать принятым практикам.

    Args:
        topic: Тема (например: "запросы", "транзакции", "реквизиты", "маркеры изменений", "форматирование").
               Без темы - оглавление всех стандартов.

    Returns:
        Разделы стандартов по теме или оглавление

    Examples:
        get_dev_standards()
        get_dev_standards("запросы")
        get_dev_standards("маркеры изменений")
    """
    return standards_tools.get_dev_standards(topic)


@mcp.resource("standards://{name}", mime_type="text/markdown")
def dev_standard_resource(name: str) -> str:
    """Файл стандартов разработки (правила проекта или стандарт команды из standards/)."""
    content = standards_tools.read_standard_file(name)
    if content is None:
        available = ", ".join(file_name for file_name, _ in standards_tools.list_standard_files())
        raise ValueError(f"Стандарт {name} не найден. Доступны: {available}")
    return content


@mcp.prompt
def develop_1c(task: str) -> str:
    """Задача по доработке конфигурации 1С с учётом стандартов и проверкой кода."""
    return (
        f"Задача по доработке конфигурации 1С: {task}\n\n"
        "Порядок работы:\n"
        "1. Найди затрагиваемые объекты (search_1c_objects, get_object_metadata) и существующий код "
        "(search_procedures_direct, find_procedure_code). Не придумывай объекты и реквизиты.\n"
        "2. Прочитай стандарты по теме задачи: get_dev_standards(\"<тема>\").\n"
        "3. Для встроенных функций и методов сверяйся с синтакс-помощником (bslsyntax_*).\n"
        "4. После написания кода проверь его через bslcheck_code и исправь все ❌ и по возможности ⚠️.\n"
        "5. Покажи итоговый код и перечисли, какие замечания проверки исправлены."
    )


class SearchRequest(BaseModel):
    """Модель запроса для поиска"""
    query: str = Field(
        description="Текст запроса для поиска",
        min_length=1,
        max_length=1000
    )
    search_mode: Literal["objects", "code", "procedures", "all"] = Field(
        default="all",
        description="Режим поиска"
    )
    object_type: str | None = Field(default=None, description="Фильтр по типу объекта")
    module_type: str | None = Field(default=None, description="Фильтр по типу модуля")
    limit: int = Field(default=DEFAULT_SEARCH_LIMIT, ge=MIN_SEARCH_LIMIT, le=MAX_SEARCH_LIMIT)


def get_query_embedding(query: str) -> List[float]:
    """Получение эмбеддинга для запроса"""
    payload = json.dumps({"texts": [query], "task": "retrieval.query"})
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(
            f"{EMBEDDING_SERVICE_URL}/embed",
            headers=headers,
            data=payload,
            timeout=EMBEDDING_REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        
        if "embeddings" not in data or not data["embeddings"]:
            raise Exception("Сервис эмбеддингов вернул пустой ответ")
        
        return data["embeddings"][0]
    except requests.RequestException as e:
        raise Exception(f"Ошибка получения эмбеддинга: {str(e)}")


def search_in_chunks(query: str, object_type: str = None, object_name: str = None, module_type: str = None, limit: int = 20):
    """Поиск в чанках кода"""
    try:
        query_embedding = get_query_embedding(query)
        
        filter_conditions = []
        if object_type:
            filter_conditions.append(FieldCondition(key="object_type", match=MatchValue(value=object_type)))
        if object_name:
            filter_conditions.append(FieldCondition(key="object_name", match=MatchValue(value=object_name)))
        if module_type:
            filter_conditions.append(FieldCondition(key="module_type", match=MatchValue(value=module_type)))
        
        query_filter = Filter(must=filter_conditions) if filter_conditions else None
        
        results = qdrant_client.query_points(
            collection_name=COLLECTION_CHUNKS,
            query=query_embedding,
            using="code",
            query_filter=query_filter,
            limit=limit,
            with_payload=True
        )
        
        return results.points
    except Exception as e:
        logger.error(f"Ошибка поиска в чанках: {e}")
        raise


def search_in_modules(query: str, object_type: str = None, object_name: str = None, 
                     module_type: str = None, limit: int = 10):
    """Поиск в полных модулях"""
    try:
        query_embedding = get_query_embedding(query)
        
        filter_conditions = []
        if object_type:
            filter_conditions.append(FieldCondition(key="object_type", match=MatchValue(value=object_type)))
        if object_name:
            filter_conditions.append(FieldCondition(key="object_name", match=MatchValue(value=object_name)))
        if module_type:
            filter_conditions.append(FieldCondition(key="module_type", match=MatchValue(value=module_type)))
        
        query_filter = Filter(must=filter_conditions) if filter_conditions else None
        
        results = qdrant_client.query_points(
            collection_name=COLLECTION_MODULES,
            query=query_embedding,
            using="code",
            query_filter=query_filter,
            limit=limit,
            with_payload=True
        )
        
        return results.points
    except Exception as e:
        logger.error(f"Ошибка поиска в модулях: {e}")
        raise


def search_in_objects(query: str, object_type: str = None, limit: int = 10):
    """Поиск в объектах с использованием мультивекторного поиска"""
    try:
        query_embedding = get_query_embedding(query)
        
        query_filter = None
        if object_type:
            query_filter = Filter(must=[
                FieldCondition(key="object_type", match=MatchValue(value=object_type))
            ])
        
        prefetch_list = [
            Prefetch(query=query_embedding, using="object_name", filter=query_filter, limit=limit * 2),
            Prefetch(query=query_embedding, using="friendly_name", filter=query_filter, limit=limit * 2),
            Prefetch(query=query_embedding, using="description", filter=query_filter, limit=limit * 2),
            Prefetch(query=query_embedding, using="structure", filter=query_filter, limit=limit * 2),
            Prefetch(query=query_embedding, using="relationships", filter=query_filter, limit=limit * 2)
        ]
        
        results = qdrant_client.query_points(
            collection_name=COLLECTION_OBJECTS,
            prefetch=prefetch_list,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True
        )
        
        return results.points
    except Exception as e:
        logger.error(f"Ошибка поиска в объектах: {e}")
        raise


def search_in_procedures(query: str, object_type: str = None, object_name: str = None,
                        is_export_only: bool = False, limit: int = 10):
    """Поиск в коллекции процедур"""
    try:
        query_embedding = get_query_embedding(query)
        
        filter_conditions = []
        if object_type:
            filter_conditions.append(FieldCondition(key="object_type", match=MatchValue(value=object_type)))
        if object_name:
            filter_conditions.append(FieldCondition(key="object_name", match=MatchValue(value=object_name)))
        if is_export_only:
            filter_conditions.append(FieldCondition(key="is_export", match=MatchValue(value=True)))
        
        query_filter = Filter(must=filter_conditions) if filter_conditions else None
        
        results = qdrant_client.query_points(
            collection_name=COLLECTION_PROCEDURES,
            query=query_embedding,
            using="code",
            query_filter=query_filter,
            limit=limit,
            with_payload=True
        )
        
        return results.points
    except Exception as e:
        logger.error(f"Ошибка поиска в процедурах: {e}")
        raise


def get_module_by_id(module_id: int) -> Dict[str, Any]:
    """Получение модуля по ID"""
    try:
        result = qdrant_client.retrieve(
            collection_name=COLLECTION_MODULES,
            ids=[module_id],
            with_payload=True
        )
        return result[0].payload if result else None
    except Exception as e:
        logger.error(f"Ошибка получения модуля {module_id}: {e}")
        return None


def get_procedure_by_id(procedure_id: int) -> Dict[str, Any]:
    """Получение процедуры по ID"""
    try:
        result = qdrant_client.retrieve(
            collection_name=COLLECTION_PROCEDURES,
            ids=[procedure_id],
            with_payload=True
        )
        return result[0].payload if result else None
    except Exception as e:
        logger.error(f"Ошибка получения процедуры {procedure_id}: {e}")
        return None


def get_procedure_from_collection(object_type: str, object_name: str, 
                                  procedure_name: str) -> Dict[str, Any] | None:
    """Получить процедуру из COLLECTION_PROCEDURES по имени"""
    try:
        results = qdrant_client.scroll(
            collection_name=COLLECTION_PROCEDURES,
            scroll_filter=Filter(must=[
                FieldCondition(key="object_type", match=MatchValue(value=object_type)),
                FieldCondition(key="object_name", match=MatchValue(value=object_name)),
                FieldCondition(key="procedure_name", match=MatchValue(value=procedure_name))
            ]),
            limit=1,
            with_payload=True,
            with_vectors=False  # Векторы не нужны
        )
        return results[0][0].payload if results[0] else None
    except Exception as e:
        logger.error(f"Ошибка получения процедуры {object_type}.{object_name}.{procedure_name}: {e}")
        return None

@mcp.tool
def get_object_metadata(
    object_type: str,
    object_name: str
) -> str:
    """
    Получить полную структуру объекта конфигурации 1С.
    
    Возвращает детальную информацию:
    - Базовые данные (синоним, комментарий)
    - Связи с другими объектами (владельцы, основания, движения)
    - Стандартные реквизиты
    - Реквизиты (с типами и комментариями)
    - Табличные части (со всеми реквизитами)
    - Для регистров: ресурсы, измерения, периодичность
    - Информация о модулях и коде
    
    Args:
        object_type: Тип объекта (Справочник, Документ, РегистрСведений и т.д.)
        object_name: Имя объекта (Контрагенты, Заказ и т.д.)
    
    Returns:
        Отформатированное markdown-описание структуры объекта
    
    Examples:
        get_object_metadata("Справочник", "Контрагенты")
        get_object_metadata("Документ", "Заказ")
        get_object_metadata("РегистрСведений", "ЦеныНоменклатуры")
    """
    try:
        # Получаем объект из Qdrant
        obj = get_object_by_name(object_type, object_name)
        
        if not obj:
            return f"❌ Объект {object_type}.{object_name} не найден в базе данных"
        
        output = []
        
        # ====================================================================
        # ЗАГОЛОВОК
        # ====================================================================
        emoji_map = {
            "Справочник": "📁",
            "Документ": "📄",
            "РегистрСведений": "📊",
            "РегистрНакопления": "📈",
            "Обработка": "⚙️",
            "Отчет": "📋",
            "ОбщийМодуль": "🔧",
            "Перечисление": "📑",
            "Константа": "🔢"
        }
        
        emoji = emoji_map.get(object_type, "📦")
        output.append(f"# {emoji} {object_type}.{object_name}")
        output.append("")
        
        # ====================================================================
        # БАЗОВАЯ ИНФОРМАЦИЯ
        # ====================================================================
        friendly_name = obj.get('friendly_name', object_name)
        comment = obj.get('comment', '')
        
        if friendly_name and friendly_name != object_name:
            output.append(f"**Синоним:** {friendly_name}")
        
        if comment:
            output.append(f"**Комментарий:** {comment}")
        
        if friendly_name or comment:
            output.append("")
        
        # ====================================================================
        # СВЯЗИ
        # ====================================================================
        relationships = obj.get('relationships', {})
        owners = relationships.get('owners', [])
        based_on = relationships.get('based_on', [])
        register_records = relationships.get('register_records', [])
        
        if owners or based_on or register_records:
            output.append("## 🔗 Связи")
            output.append("")
            
            if owners:
                output.append(f"**Владельцы:** {', '.join(owners)}")
            
            if based_on:
                output.append(f"**Основан на:** {', '.join(based_on)}")
            
            if register_records:
                output.append(f"**Движения регистров:** {', '.join(register_records)}")
            
            output.append("")
        
        # ====================================================================
        # СПЕЦИФИКА РЕГИСТРОВ (периодичность, режим записи)
        # ====================================================================
        register_info = obj.get('register_info', {})
        periodicity = register_info.get('periodicity', '')
        write_mode = register_info.get('write_mode', '')
        register_type = register_info.get('register_type', '')
        
        if periodicity or write_mode or register_type:
            output.append("## ⚙️ Параметры регистра")
            output.append("")
            
            if periodicity:
                output.append(f"**Периодичность:** {periodicity}")
            
            if write_mode:
                output.append(f"**Режим записи:** {write_mode}")
            
            if register_type:
                output.append(f"**Тип регистра:** {register_type}")
            
            output.append("")
        
        # ====================================================================
        # СТАНДАРТНЫЕ РЕКВИЗИТЫ
        # ====================================================================
        standard_attributes = obj.get('standard_attributes', [])
        
        if standard_attributes:
            output.append(f"## ⚙️ Стандартные реквизиты ({len(standard_attributes)})")
            output.append("")
            
            for std_attr in standard_attributes:
                output.append(f"- {std_attr}")
            
            output.append("")
        
        # ====================================================================
        # ИЗМЕРЕНИЯ (для регистров)
        # ====================================================================
        dimensions = register_info.get('dimensions', [])
        
        if dimensions:
            output.append(f"## 📐 Измерения ({len(dimensions)})")
            output.append("")
            
            for idx, dim in enumerate(dimensions, 1):
                dim_name = dim.get('Имя', dim.get('name', ''))
                dim_synonym = dim.get('Синоним', dim.get('synonym', dim_name))
                dim_type = dim.get('Тип', dim.get('type', ''))
                dim_comment = dim.get('Комментарий', dim.get('comment', ''))
                
                output.append(f"{idx}. **{dim_name}** ({dim_synonym}) — {dim_type}{_field_flags(dim)}")
                
                if dim_comment:
                    output.append(f"   {dim_comment}")
                
                output.append("")
        
        # ====================================================================
        # РЕСУРСЫ (для регистров)
        # ====================================================================
        resources = register_info.get('resources', [])
        
        if resources:
            output.append(f"## 📈 Ресурсы ({len(resources)})")
            output.append("")
            
            for idx, res in enumerate(resources, 1):
                res_name = res.get('Имя', res.get('name', ''))
                res_synonym = res.get('Синоним', res.get('synonym', res_name))
                res_type = res.get('Тип', res.get('type', ''))
                res_comment = res.get('Комментарий', res.get('comment', ''))
                
                output.append(f"{idx}. **{res_name}** ({res_synonym}) — {res_type}{_field_flags(res)}")
                
                if res_comment:
                    output.append(f"   {res_comment}")
                
                output.append("")
        
        # ====================================================================
        # РЕКВИЗИТЫ
        # ====================================================================
        attributes = obj.get('attributes', [])
        
        if attributes:
            output.append(f"## 📝 Реквизиты ({len(attributes)})")
            output.append("")
            
            for idx, attr in enumerate(attributes, 1):
                attr_name = attr.get('Имя', attr.get('name', ''))
                attr_synonym = attr.get('Синоним', attr.get('synonym', attr_name))
                attr_type = attr.get('Тип', attr.get('type', ''))
                attr_comment = attr.get('Комментарий', attr.get('comment', ''))
                
                output.append(f"{idx}. **{attr_name}** ({attr_synonym}) — {attr_type}{_field_flags(attr)}")
                
                if attr_comment:
                    output.append(f"   {attr_comment}")
                
                output.append("")
        
        # ====================================================================
        # ТАБЛИЧНЫЕ ЧАСТИ (ПОЛНАЯ ДЕТАЛИЗАЦИЯ)
        # ====================================================================
        tabular_sections = obj.get('tabular_sections', [])
        
        if tabular_sections:
            output.append(f"## 📊 Табличные части ({len(tabular_sections)})")
            output.append("")
            
            for ts_idx, ts in enumerate(tabular_sections, 1):
                ts_name = ts.get('Имя', ts.get('name', ''))
                ts_synonym = ts.get('Синоним', ts.get('synonym', ts_name))
                ts_comment = ts.get('Комментарий', ts.get('comment', ''))
                ts_attributes = ts.get('Реквизиты', ts.get('attributes', []))
                
                # Заголовок табличной части
                output.append(f"### {ts_idx}. {ts_name}")
                
                if ts_synonym and ts_synonym != ts_name:
                    output.append(f"**Синоним:** {ts_synonym}")
                
                if ts_comment:
                    output.append(f"**Комментарий:** {ts_comment}")
                
                output.append("")
                
                # Реквизиты табличной части
                if ts_attributes:
                    output.append(f"**Реквизиты табличной части ({len(ts_attributes)}):**")
                    output.append("")
                    
                    for attr_idx, ts_attr in enumerate(ts_attributes, 1):
                        ts_attr_name = ts_attr.get('Имя', ts_attr.get('name', ''))
                        ts_attr_synonym = ts_attr.get('Синоним', ts_attr.get('synonym', ts_attr_name))
                        ts_attr_type = ts_attr.get('Тип', ts_attr.get('type', ''))
                        ts_attr_comment = ts_attr.get('Комментарий', ts_attr.get('comment', ''))
                        
                        output.append(f"{attr_idx}. **{ts_attr_name}** ({ts_attr_synonym}) — {ts_attr_type}{_field_flags(ts_attr)}")
                        
                        if ts_attr_comment:
                            output.append(f"   {ts_attr_comment}")
                        
                        output.append("")
                else:
                    output.append("_Реквизиты отсутствуют_")
                    output.append("")
        
        # ====================================================================
        # ИНФОРМАЦИЯ О КОДЕ
        # ====================================================================
        has_modules = obj.get('has_modules', False)
        modules_count = obj.get('modules_count', 0)
        total_code_lines = obj.get('total_code_lines', 0)
        modules = obj.get('modules', [])
        
        if has_modules and modules_count > 0:
            output.append("## 💻 Код")
            output.append("")
            output.append(f"**Модулей:** {modules_count}")
            output.append(f"**Всего строк кода:** {total_code_lines}")
            output.append("")
            
            if modules:
                output.append("**Модули:**")
                for module in modules:
                    module_name = module.get('name', module.get('type', 'Unknown'))
                    module_lines = module.get('lines', 0)
                    output.append(f"- {module_name} ({module_lines} строк)")
                
                output.append("")
                output.append("💡 _Используйте `get_module_full_code()` или `list_object_modules()` для просмотра кода_")
        
        # ====================================================================
        # МЕТАДАННЫЕ ДЛЯ СТАТИСТИКИ
        # ====================================================================
        metadata = obj.get('metadata', {})
        
        if metadata:
            flags = []
            
            if metadata.get('is_hierarchical'):
                flags.append("🌳 Иерархический")
            
            if metadata.get('has_owner'):
                flags.append("👤 Подчинён владельцу")
            
            if metadata.get('has_tabular_sections'):
                ts_count = metadata.get('tabular_sections_count', 0)
                flags.append(f"📊 Табличных частей: {ts_count}")
            
            if flags:
                output.append("")
                output.append("---")
                output.append("")
                output.append(" • ".join(flags))
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Ошибка получения метаданных объекта {object_type}.{object_name}: {e}")
        import traceback
        return f"❌ Ошибка получения метаданных: {str(e)}\n\n```\n{traceback.format_exc()}\n```"

# Обработчики событий модулей объектов/менеджеров/наборов записей - вызываются платформой
PLATFORM_EVENT_HANDLERS = {
    "ПередЗаписью", "ПриЗаписи", "ПередУдалением", "ПриКопировании", "ОбработкаЗаполнения",
    "ОбработкаПроверкиЗаполнения", "ОбработкаПроведения", "ОбработкаУдаленияПроведения",
    "ПриУстановкеНовогоНомера", "ПриУстановкеНовогоКода", "ОбработкаПолученияДанныхВыбора",
    "ОбработкаПолученияПредставления", "ОбработкаПолученияПолейПредставления",
    "ОбработкаПолученияФормы", "ОбработкаПолученияДанныхВыбораНаСервере", "ПриЧтенииПредставленийНаСервере",
    "ПриСозданииНаСервере", "ПриОткрытии", "ПередЗакрытием", "ПриЗакрытии", "ОбработкаОповещения",
    "ПередЗаписьюНаСервере", "ПриЗаписиНаСервере", "ПослеЗаписиНаСервере", "ПослеЗаписи",
    "ПриЧтенииНаСервере", "ОбработкаВыбора", "ОбработкаКоманды", "ПриНачалеРаботыСистемы",
    "ПриЗавершенииРаботыСистемы", "ПередНачаломРаботыСистемы", "ПередЗавершениемРаботыСистемы",
    "ПриКомпоновкеРезультата", "ПриЗагрузкеДанныхИзНастроекНаСервере", "ПередЗагрузкойДанныхИзНастроекНаСервере",
}

INDEXED_VALUES = ("Индексировать", "Индексировать с доп. упорядочиванием", "Index", "IndexWithAdditionalOrder")


def _is_indexed_field(field: Dict[str, Any]) -> bool:
    """Поле (реквизит/измерение/ресурс) с включённым свойством «Индексирование»."""
    return field.get('Индексирование', field.get('indexing')) in INDEXED_VALUES


def _field_flags(field: Dict[str, Any]) -> str:
    """Краткие признаки поля для вывода: индексирование, проверка заполнения, ведущее, основной отбор."""
    flags = []
    indexing = field.get('Индексирование', field.get('indexing'))
    if indexing in INDEXED_VALUES:
        flags.append("индекс" + (" с доп. упорядочиванием" if "упорядоч" in indexing or "Additional" in indexing else ""))
    if field.get('ПроверкаЗаполнения', field.get('fill_checking')) in ("Выдавать ошибку", "ShowError"):
        flags.append("обязательное")
    if field.get('Ведущее', field.get('master')):
        flags.append("ведущее")
    if field.get('ОсновнойОтбор', field.get('main_filter')):
        flags.append("основной отбор")
    return f" [{'; '.join(flags)}]" if flags else ""


def get_object_by_name(object_type: str, object_name: str) -> Dict[str, Any]:
    """Получение объекта по типу и имени"""
    try:
        results = qdrant_client.scroll(
            collection_name=COLLECTION_OBJECTS,
            scroll_filter=Filter(must=[
                FieldCondition(key="object_type", match=MatchValue(value=object_type)),
                FieldCondition(key="object_name", match=MatchValue(value=object_name))
            ]),
            limit=1,
            with_payload=True,
            with_vectors=False  # Не запрашиваем векторы
        )
        
        return results[0][0].payload if results[0] else None
    except Exception as e:
        logger.error(f"Ошибка получения объекта {object_type}.{object_name}: {e}")
        return None


def get_modules_by_object(object_type: str, object_name: str) -> List[Dict[str, Any]]:
    """Получение всех модулей объекта"""
    try:
        results = qdrant_client.scroll(
            collection_name=COLLECTION_MODULES,
            scroll_filter=Filter(must=[
                FieldCondition(key="object_type", match=MatchValue(value=object_type)),
                FieldCondition(key="object_name", match=MatchValue(value=object_name))
            ]),
            limit=100,
            with_payload=True,
            with_vectors=False  # Не запрашиваем векторы
        )
        
        return [point.payload for point in results[0]] if results[0] else []
    except Exception as e:
        logger.error(f"Ошибка получения модулей для {object_type}.{object_name}: {e}")
        return []

@mcp.tool
def search_by_structure(
    attribute_name: str | None = None,
    attribute_type: str | None = None,
    tabular_section_name: str | None = None,
    object_type: str | None = None,
    search_in: Literal["attributes", "tabular_sections", "resources", "dimensions", "all"] = "all",
    limit: int = 20,
    indexed: bool | None = None
) -> str:
    """
    Поиск объектов конфигурации по структуре (реквизиты, табличные части, типы данных, индексирование).

    Позволяет найти объекты, которые:
    - Содержат реквизит с определённым именем или типом
    - Имеют табличную часть с определённым именем
    - Используют определённый тип данных
    - Имеют ресурсы или измерения (для регистров)
    - Имеют индексируемые поля (свойство «Индексирование»)

    Args:
        attribute_name: Имя реквизита/ресурса/измерения (частичное совпадение, case-insensitive)
        attribute_type: Тип данных (точное совпадение одного из типов, например "Справочник.Контрагенты")
        tabular_section_name: Имя табличной части (частичное совпадение)
        object_type: Фильтр по типу объекта (Справочник, Документ, РегистрСведений и т.д.)
        search_in: Где искать (attributes, tabular_sections, resources, dimensions, all)
        limit: Максимальное количество результатов
        indexed: True - только индексируемые поля (результат сортируется по их количеству, по убыванию),
                 False - только неиндексируемые. Можно использовать без других критериев.
    
    Returns:
        Отформатированный список найденных объектов с указанием найденных элементов
    
    Examples:
        # Найти все объекты с реквизитом "ИНН"
        search_by_structure(attribute_name="ИНН")
        
        # Найти справочники с реквизитом типа "Справочник.Контрагенты"
        search_by_structure(attribute_type="Справочник.Контрагенты", object_type="Справочник")
        
        # Найти объекты с табличной частью "Товары"
        search_by_structure(tabular_section_name="Товары")
        
        # Найти документы с реквизитом "Контрагент" типа "Справочник.Контрагенты"
        search_by_structure(
            attribute_name="Контрагент",
            attribute_type="Справочник.Контрагенты",
            object_type="Документ"
        )

        # Топ-5 регистров сведений по количеству индексируемых полей
        search_by_structure(object_type="РегистрСведений", indexed=True, limit=5)
    """
    try:
        # Проверка входных параметров
        if not attribute_name and not attribute_type and not tabular_section_name and indexed is None:
            return ("❌ Укажите хотя бы один критерий поиска: attribute_name, attribute_type, "
                    "tabular_section_name или indexed")

        # Подготовка фильтра по типу объекта
        scroll_filter_conditions = []
        if object_type:
            scroll_filter_conditions.append(
                FieldCondition(key="object_type", match=MatchValue(value=object_type))
            )

        scroll_filter = Filter(must=scroll_filter_conditions) if scroll_filter_conditions else None

        def iter_objects():
            """Постраничный обход объектов (без загрузки всей коллекции в память)."""
            offset = None
            while True:
                points, next_offset = qdrant_client.scroll(
                    collection_name=COLLECTION_OBJECTS,
                    scroll_filter=scroll_filter,
                    limit=200,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False
                )
                yield from points
                if not points or next_offset is None:
                    break
                offset = next_offset

        def type_matches(field_type: str) -> bool:
            # Составной тип хранится строкой "Тип1, Тип2" - сравниваем каждый тип отдельно
            return not attribute_type or attribute_type in [t.strip() for t in field_type.split(",")]

        def indexing_matches(field: Dict[str, Any]) -> bool:
            return indexed is None or _is_indexed_field(field) == indexed


        # Фильтрация объектов по критериям
        matched_objects = []

        for point in iter_objects():
            obj = point.payload
            matches = []  # Список найденных совпадений в этом объекте
            
            # Нормализация имён для поиска (case-insensitive)
            search_attr_name = attribute_name.lower() if attribute_name else None
            search_ts_name = tabular_section_name.lower() if tabular_section_name else None
            
            # ================================================================
            # ПОИСК В РЕКВИЗИТАХ
            # ================================================================
            if search_in in ["attributes", "all"]:
                attributes = obj.get('attributes', [])
                
                for attr in attributes:
                    attr_name = attr.get('Имя', attr.get('name', ''))
                    attr_synonym = attr.get('Синоним', attr.get('synonym', attr_name))
                    attr_type = attr.get('Тип', attr.get('type', ''))
                    attr_comment = attr.get('Комментарий', attr.get('comment', ''))
                    
                    # Проверка совпадения по имени (частичное, case-insensitive)
                    name_match = (not search_attr_name or 
                                 search_attr_name in attr_name.lower())
                    
                    # Проверка совпадения по типу (точное)
                    type_match = type_matches(attr_type)
                    
                    # Логика AND: оба условия должны выполняться
                    if name_match and type_match and indexing_matches(attr):
                        matches.append({
                            "location": "Реквизит",
                            "name": attr_name,
                            "synonym": attr_synonym,
                            "type": attr_type,
                            "comment": attr_comment,
                            "flags": _field_flags(attr)
                        })
            
            # ================================================================
            # ПОИСК В ТАБЛИЧНЫХ ЧАСТЯХ
            # ================================================================
            if search_in in ["tabular_sections", "all"]:
                tabular_sections = obj.get('tabular_sections', [])
                
                for ts in tabular_sections:
                    ts_name = ts.get('Имя', ts.get('name', ''))
                    ts_synonym = ts.get('Синоним', ts.get('synonym', ts_name))
                    
                    # Поиск самой табличной части по имени
                    if search_ts_name and search_ts_name in ts_name.lower():
                        matches.append({
                            "location": "Табличная часть",
                            "name": ts_name,
                            "synonym": ts_synonym,
                            "type": "",
                            "comment": ""
                        })
                    
                    # Поиск реквизитов внутри табличной части
                    if attribute_name or attribute_type or indexed is not None:
                        ts_attributes = ts.get('Реквизиты', ts.get('attributes', []))
                        
                        for ts_attr in ts_attributes:
                            ts_attr_name = ts_attr.get('Имя', ts_attr.get('name', ''))
                            ts_attr_synonym = ts_attr.get('Синоним', ts_attr.get('synonym', ts_attr_name))
                            ts_attr_type = ts_attr.get('Тип', ts_attr.get('type', ''))
                            
                            name_match = (not search_attr_name or 
                                         search_attr_name in ts_attr_name.lower())
                            type_match = type_matches(ts_attr_type)
                            
                            if name_match and type_match and indexing_matches(ts_attr):
                                matches.append({
                                    "location": f"ТЧ {ts_name} → Реквизит",
                                    "name": ts_attr_name,
                                    "synonym": ts_attr_synonym,
                                    "type": ts_attr_type,
                                    "comment": "",
                                    "flags": _field_flags(ts_attr)
                                })
            
            # ================================================================
            # ПОИСК В РЕСУРСАХ (для регистров)
            # ================================================================
            if search_in in ["resources", "all"]:
                register_info = obj.get('register_info', {})
                resources = register_info.get('resources', [])
                
                for res in resources:
                    res_name = res.get('Имя', res.get('name', ''))
                    res_synonym = res.get('Синоним', res.get('synonym', res_name))
                    res_type = res.get('Тип', res.get('type', ''))
                    res_comment = res.get('Комментарий', res.get('comment', ''))
                    
                    name_match = (not search_attr_name or 
                                 search_attr_name in res_name.lower())
                    type_match = type_matches(res_type)
                    
                    if name_match and type_match and indexing_matches(res):
                        matches.append({
                            "location": "Ресурс",
                            "name": res_name,
                            "synonym": res_synonym,
                            "type": res_type,
                            "comment": res_comment,
                            "flags": _field_flags(res)
                        })
            
            # ================================================================
            # ПОИСК В ИЗМЕРЕНИЯХ (для регистров)
            # ================================================================
            if search_in in ["dimensions", "all"]:
                register_info = obj.get('register_info', {})
                dimensions = register_info.get('dimensions', [])
                
                for dim in dimensions:
                    dim_name = dim.get('Имя', dim.get('name', ''))
                    dim_synonym = dim.get('Синоним', dim.get('synonym', dim_name))
                    dim_type = dim.get('Тип', dim.get('type', ''))
                    dim_comment = dim.get('Комментарий', dim.get('comment', ''))
                    
                    name_match = (not search_attr_name or 
                                 search_attr_name in dim_name.lower())
                    type_match = type_matches(dim_type)
                    
                    if name_match and type_match and indexing_matches(dim):
                        matches.append({
                            "location": "Измерение",
                            "name": dim_name,
                            "synonym": dim_synonym,
                            "type": dim_type,
                            "comment": dim_comment,
                            "flags": _field_flags(dim)
                        })
            
            # Если нашли совпадения - добавляем объект в результаты
            if matches:
                matched_objects.append({
                    "object": obj,
                    "matches": matches
                })
        
        # Для поиска по индексированию - сначала объекты с наибольшим числом совпадений
        if indexed:
            matched_objects.sort(key=lambda item: len(item["matches"]), reverse=True)
        
        total_matched = len(matched_objects)
        
        # Ограничиваем количество результатов
        matched_objects = matched_objects[:limit]
        
        # ====================================================================
        # ФОРМИРОВАНИЕ ВЫВОДА
        # ====================================================================
        if not matched_objects:
            search_criteria = []
            if attribute_name:
                search_criteria.append(f'имя содержит "{attribute_name}"')
            if attribute_type:
                search_criteria.append(f'тип "{attribute_type}"')
            if tabular_section_name:
                search_criteria.append(f'табличная часть "{tabular_section_name}"')
            if indexed is not None:
                search_criteria.append("индексируемые поля" if indexed else "неиндексируемые поля")
            
            criteria_text = ", ".join(search_criteria)
            
            return f"❌ Объекты не найдены по критериям: {criteria_text}\n\n💡 Попробуйте:\n- Проверить написание\n- Использовать более общие критерии\n- Убрать фильтр по типу объекта"
        
        output = []
        
        # Заголовок
        search_desc = []
        if attribute_name:
            search_desc.append(f'имя содержит "{attribute_name}"')
        if attribute_type:
            search_desc.append(f'тип "{attribute_type}"')
        if tabular_section_name:
            search_desc.append(f'табличная часть "{tabular_section_name}"')
        if indexed is not None:
            search_desc.append("индексируемые поля" if indexed else "неиндексируемые поля")
        
        search_text = ", ".join(search_desc)
        
        output.append(f"Найдено {total_matched} объект(ов) по критериям: {search_text}"
                      + (f" (показано {len(matched_objects)})" if total_matched > len(matched_objects) else ""))
        output.append("")
        
        # Эмодзи для типов
        emoji_map = {
            "Справочник": "📁",
            "Документ": "📄",
            "РегистрСведений": "📊",
            "РегистрНакопления": "📈",
            "Обработка": "⚙️",
            "Отчет": "📋",
            "ОбщийМодуль": "🔧",
            "Перечисление": "📑"
        }
        
        # Вывод результатов
        for idx, item in enumerate(matched_objects, 1):
            obj = item["object"]
            matches = item["matches"]
            
            obj_type = obj.get('object_type', '')
            obj_name = obj.get('object_name', '')
            friendly_name = obj.get('friendly_name', obj_name)
            
            emoji = emoji_map.get(obj_type, "📦")
            
            output.append(f"{idx}. {emoji} **{obj_type}.{obj_name}**"
                          + (f" — индексируемых полей: {len(matches)}" if indexed else ""))

            if friendly_name and friendly_name != obj_name:
                output.append(f"   _Синоним: {friendly_name}_")
            
            output.append("")
            output.append("   **Найденные элементы:**")
            
            # Группируем совпадения по location для красивого вывода
            for match in matches[:10]:  # Ограничиваем 10 элементами на объект
                location = match["location"]
                name = match["name"]
                synonym = match["synonym"]
                match_type = match["type"]
                comment = match["comment"]
                flags = match.get("flags", "")
                
                # Формируем строку вывода
                if synonym and synonym != name:
                    name_display = f"{name} ({synonym})"
                else:
                    name_display = name
                
                if match_type:
                    output.append(f"   - {location}: **{name_display}** — {match_type}{flags}")
                else:
                    output.append(f"   - {location}: **{name_display}**{flags}")
                
                if comment:
                    output.append(f"     _{comment}_")
            
            # Если совпадений больше 10 - показываем количество
            if len(matches) > 10:
                output.append(f"   - ... и ещё {len(matches) - 10} элемент(ов)")
            
            output.append("")
            
            # Дополнительная статистика
            metadata = obj.get('metadata', {})
            stats = []
            
            attrs_count = metadata.get('attributes_count', 0)
            if attrs_count:
                stats.append(f"Реквизитов: {attrs_count}")
            
            ts_count = metadata.get('tabular_sections_count', 0)
            if ts_count:
                stats.append(f"ТЧ: {ts_count}")
            
            modules_count = obj.get('modules_count', 0)
            if modules_count:
                stats.append(f"Модулей: {modules_count}")
            
            if stats:
                output.append(f"   _{', '.join(stats)}_")
                output.append("")
        
        # Подсказка
        output.append("---")
        output.append("")
        
        output.append("💡 **Подсказки:**")
        output.append("- Используйте `get_object_metadata(тип, имя)` для просмотра полной структуры")
        output.append("- Для просмотра кода: `list_object_modules(тип, имя)`")
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Ошибка поиска по структуре: {e}")
        import traceback
        return f"❌ Ошибка поиска: {str(e)}\n\n```\n{traceback.format_exc()}\n```"

@mcp.tool
def search_by_relationship(
    relationship_type: Literal["owners", "based_on", "register_records", "any"],
    target_object: str,
    object_type: str | None = None,
    limit: int = 20
) -> str:
    """
    Поиск объектов конфигурации по связям с другими объектами.
    
    Позволяет найти объекты, которые:
    - Имеют определённого владельца (owners)
    - Основаны на определённом объекте (based_on)
    - Создают движения по определённому регистру (register_records)
    - Имеют любую связь с указанным объектом (any)
    
    Args:
        relationship_type: Тип связи для поиска
            - "owners": владельцы справочников
            - "based_on": документы/справочники, на основе которых создаётся объект
            - "register_records": регистры, по которым создаются движения
            - "any": любой тип связи
        target_object: Искомый объект (можно указать с типом или без)
            Примеры: "Контрагенты", "Справочник.Контрагенты", "ОстаткиТоваров"
        object_type: Фильтр по типу искомых объектов (Справочник, Документ и т.д.)
        limit: Максимальное количество результатов
    
    Returns:
        Отформатированный список объектов с указанием их связей
    
    Examples:
        # Найти документы, создающие движения по регистру "ОстаткиТоваров"
        search_by_relationship("register_records", "ОстаткиТоваров")
        
        # Найти справочники с владельцем "Организации"
        search_by_relationship("owners", "Справочник.Организации", object_type="Справочник")
        
        # Найти что основано на документе "Заказ"
        search_by_relationship("based_on", "Документ.Заказ")
        
        # Найти все объекты, связанные с "Контрагенты"
        search_by_relationship("any", "Контрагенты")
    """
    try:
        # Подготовка фильтра по типу объекта
        scroll_filter_conditions = []
        if object_type:
            scroll_filter_conditions.append(
                FieldCondition(key="object_type", match=MatchValue(value=object_type))
            )
        
        scroll_filter = Filter(must=scroll_filter_conditions) if scroll_filter_conditions else None
        
        # Получаем все объекты
        all_objects = []
        offset = None
        limit_reached = False  # Флаг достижения лимита
        
        while True:
            results = qdrant_client.scroll(
                collection_name=COLLECTION_OBJECTS,
                scroll_filter=scroll_filter,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            
            points, next_offset = results
            
            if not points:
                break
            
            all_objects.extend(points)
            
            if next_offset is None:
                break
            
            offset = next_offset
            
            # Ограничение для безопасности
            if len(all_objects) >= 1000:
                logger.warning(f"search_by_relationship: Достигнут лимит безопасности в 1000 объектов. Поиск может быть неполным.")
                limit_reached = True
                break
        
        # ================================================================
        # НОРМАЛИЗАЦИЯ ЦЕЛЕВОГО ОБЪЕКТА
        # ================================================================
        # Поддержка гибкого поиска: "Контрагенты" → "Справочник.Контрагенты"
        target_normalized = target_object.strip()
        target_lower = target_normalized.lower()
        
        # Функция проверки совпадения (гибкая)
        def matches_target(value: str) -> bool:
            """Проверяет, совпадает ли значение с целевым объектом"""
            if not value:
                return False
            
            value_lower = value.lower()
            
            # Точное совпадение
            if value_lower == target_lower:
                return True
            
            # Совпадение без типа (если в target не указан тип)
            # "Контрагенты" совпадает с "Справочник.Контрагенты"
            if '.' not in target_normalized:
                if value_lower.endswith('.' + target_lower):
                    return True
                # Частичное совпадение в имени после точки
                if '.' in value_lower:
                    name_part = value_lower.split('.')[-1]
                    if target_lower in name_part:
                        return True
            
            # Частичное совпадение
            if target_lower in value_lower:
                return True
            
            return False
        
        # ================================================================
        # ФИЛЬТРАЦИЯ ОБЪЕКТОВ ПО СВЯЗЯМ
        # ================================================================
        matched_objects = []
        
        for point in all_objects:
            obj = point.payload
            relationships = obj.get('relationships', {})
            
            # Получаем все связи
            owners = relationships.get('owners', [])
            based_on = relationships.get('based_on', [])
            register_records = relationships.get('register_records', [])
            
            # Проверяем совпадение по типу связи
            found = False
            
            if relationship_type == "owners" or relationship_type == "any":
                for owner in owners:
                    if matches_target(owner):
                        found = True
                        break
            
            if relationship_type == "based_on" or relationship_type == "any":
                for base in based_on:
                    if matches_target(base):
                        found = True
                        break
            
            if relationship_type == "register_records" or relationship_type == "any":
                for register in register_records:
                    if matches_target(register):
                        found = True
                        break
            
            if found:
                matched_objects.append({
                    "object": obj,
                    "relationships": {
                        "owners": owners,
                        "based_on": based_on,
                        "register_records": register_records
                    }
                })
        
        # Ограничиваем количество результатов
        matched_objects = matched_objects[:limit]
        
        # ====================================================================
        # ФОРМИРОВАНИЕ ВЫВОДА
        # ====================================================================
        if not matched_objects:
            type_name_map = {
                "owners": "владельцем",
                "based_on": "основанием",
                "register_records": "движениями по регистру",
                "any": "связями с"
            }
            
            type_text = type_name_map.get(relationship_type, "связями")
            
            return f'❌ Объекты не найдены с {type_text} "{target_object}"\n\n💡 Попробуйте:\n- Проверить написание объекта\n- Указать полное имя с типом (например, "Справочник.Контрагенты")\n- Использовать relationship_type="any" для поиска любых связей'
        
        output = []
        
        # Заголовок
        type_description_map = {
            "owners": f'владельцем "{target_object}"',
            "based_on": f'основанием "{target_object}"',
            "register_records": f'движениями по регистру "{target_object}"',
            "any": f'связями с "{target_object}"'
        }
        
        header_text = type_description_map.get(relationship_type, f'связями с "{target_object}"')
        output.append(f"Найдено {len(matched_objects)} объект(ов) с {header_text}:")
        output.append("")
        
        # Эмодзи для типов
        emoji_map = {
            "Справочник": "📁",
            "Документ": "📄",
            "РегистрСведений": "📊",
            "РегистрНакопления": "📈",
            "Обработка": "⚙️",
            "Отчет": "📋",
            "ОбщийМодуль": "🔧",
            "Перечисление": "📑"
        }
        
        # Вывод результатов
        for idx, item in enumerate(matched_objects, 1):
            obj = item["object"]
            rels = item["relationships"]
            
            obj_type = obj.get('object_type', '')
            obj_name = obj.get('object_name', '')
            friendly_name = obj.get('friendly_name', obj_name)
            
            emoji = emoji_map.get(obj_type, "📦")
            
            output.append(f"{idx}. {emoji} **{obj_type}.{obj_name}**")
            
            if friendly_name and friendly_name != obj_name:
                output.append(f"   _Синоним: {friendly_name}_")
            
            output.append("")
            
            # Показываем ВСЕ связи объекта (для контекста)
            output.append("   **Связи:**")
            
            if rels["owners"]:
                output.append(f"   - **Владельцы:** {', '.join(rels['owners'])}")
            
            if rels["based_on"]:
                output.append(f"   - **Основан на:** {', '.join(rels['based_on'])}")
            
            if rels["register_records"]:
                # Если много регистров - ограничиваем вывод
                registers = rels["register_records"]
                if len(registers) <= 5:
                    output.append(f"   - **Движения:** {', '.join(registers)}")
                else:
                    first_five = ', '.join(registers[:5])
                    output.append(f"   - **Движения:** {first_five}, ... и ещё {len(registers) - 5}")
            
            # Если нет связей вообще
            if not rels["owners"] and not rels["based_on"] and not rels["register_records"]:
                output.append("   - _Связи не указаны_")
            
            output.append("")
            
            # Дополнительная информация
            stats = []
            
            # Количество реквизитов и ТЧ
            metadata = obj.get('metadata', {})
            attrs_count = metadata.get('attributes_count', 0)
            ts_count = metadata.get('tabular_sections_count', 0)
            
            if attrs_count:
                stats.append(f"Реквизитов: {attrs_count}")
            if ts_count:
                stats.append(f"ТЧ: {ts_count}")
            
            # Информация о коде
            modules_count = obj.get('modules_count', 0)
            total_lines = obj.get('total_code_lines', 0)
            
            if modules_count:
                stats.append(f"Модулей: {modules_count}")
            if total_lines:
                stats.append(f"Строк кода: {total_lines}")
            
            if stats:
                output.append(f"   _{', '.join(stats)}_")
                output.append("")
        
        # Подсказки
        output.append("---")
        output.append("")
        
        # Предупреждение если достигнут лимит
        if limit_reached:
            output.append("⚠️ **ВНИМАНИЕ:** Достигнут лимит безопасности в 1000 объектов.")
            output.append("   Результаты могут быть неполными. Попробуйте уточнить критерии поиска.")
            output.append("")
        
        output.append("💡 **Подсказки:**")
        output.append("- Используйте `get_object_metadata(тип, имя)` для просмотра полной структуры")
        output.append("- Для просмотра кода: `list_object_modules(тип, имя)`")
        
        # Дополнительная подсказка в зависимости от типа связи
        if relationship_type == "register_records":
            output.append("- Для просмотра структуры регистра: `get_object_metadata('РегистрНакопления', 'имя')` или `get_object_metadata('РегистрСведений', 'имя')`")
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Ошибка поиска по связям: {e}")
        import traceback
        return f"❌ Ошибка поиска: {str(e)}\n\n```\n{traceback.format_exc()}\n```"


# НОВЫЙ ИНСТРУМЕНТ: Диагностика базы данных
@mcp.tool
def diagnose_database(object_name_pattern: str | None = None) -> str:
    """Диагностика базы данных - проверка наличия данных.
    
    Используйте для проверки, что данные загружены корректно.
    
    Args:
        object_name_pattern: Паттерн для фильтрации объектов (например: "ЕУЗ")
    
    Returns:
        Статистика по базе данных и список объектов.
    """
    try:
        output = ["=== ДИАГНОСТИКА БАЗЫ ДАННЫХ ===\n"]
        
        # Статистика коллекций
        for coll_name in [COLLECTION_OBJECTS, COLLECTION_MODULES, COLLECTION_CHUNKS, COLLECTION_PROCEDURES,
                          COLLECTION_SYNTAX]:
            if qdrant_client.collection_exists(coll_name):
                info = qdrant_client.get_collection(coll_name)
                output.append(f"✓ {coll_name}: {info.points_count} точек")
            else:
                output.append(f"✗ {coll_name}: НЕ СУЩЕСТВУЕТ")
        
        output.append("\n=== СПИСОК ОБЪЕКТОВ ===\n")
        
        # Получаем все объекты
        filter_condition = None
        if object_name_pattern:
            # Используем scroll для поиска по паттерну
            results = qdrant_client.scroll(
                collection_name=COLLECTION_OBJECTS,
                limit=1000,
                with_payload=True,
                with_vectors=False
            )
            
            # Фильтруем по паттерну (по имени объекта и по синониму)
            filtered = []
            pattern_lower = object_name_pattern.lower()
            for point in results[0]:
                payload = point.payload
                obj_name = payload.get('object_name', '')
                friendly_name = payload.get('friendly_name', '')
                
                # Ищем по имени объекта или по синониму
                if pattern_lower in obj_name.lower() or pattern_lower in friendly_name.lower():
                    filtered.append(point)
            
            if filtered:
                output.append(f"Найдено объектов с '{object_name_pattern}': {len(filtered)}\n")
                for point in filtered[:20]:
                    payload = point.payload
                    output.append(f"  • {payload['object_type']}.{payload['object_name']}")
                    output.append(f"    Синоним: {payload.get('friendly_name', 'нет')}")
                    output.append(f"    Модулей: {payload.get('modules_count', 0)}")
                    output.append("")
            else:
                output.append(f"❌ Объекты с '{object_name_pattern}' НЕ НАЙДЕНЫ")
        else:
            # Показываем первые 20 объектов
            results = qdrant_client.scroll(
                collection_name=COLLECTION_OBJECTS,
                limit=20,
                with_payload=True,
                with_vectors=False
            )
            
            output.append(f"Всего объектов: показаны первые 20\n")
            for point in results[0]:
                payload = point.payload
                output.append(f"  • {payload['object_type']}.{payload['object_name']}")
                output.append(f"    Синоним: {payload.get('friendly_name', 'нет')}")
                output.append("")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"Ошибка диагностики: {str(e)}"


@mcp.tool
def search_1c_objects(query: str, object_type: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """Поиск объектов конфигурации 1С по именам и описаниям.

    Используйте для поиска объектов конфигурации по их именам, синонимам или описанию.

    Args:
        query: Текст запроса для поиска объекта
        object_type: Фильтр по типу объекта (Справочник, Документ, РегистрСведений и т.д.)
        limit: Максимальное количество результатов (по умолчанию 5)
    
    Returns:
        Список найденных объектов с их модулями.
        Для получения полного кода модуля используйте get_module_full_code().
    """
    try:
        results = search_in_objects(query, object_type, limit)
        
        if not results:
            return f"По запросу '{query}' ничего не найдено.\n\n💡 Попробуйте:\n  1. Использовать diagnose_database('{query}') для проверки наличия объекта\n  2. Изменить запрос (использовать английское или русское название)\n  3. Уменьшить длину запроса"
        
        output = [f"Найдено объектов: {len(results)}\n"]
        
        for i, result in enumerate(results, 1):
            payload = result.payload
            output.append(f"\n{'=' * 60}")
            output.append(f"Результат {i} (релевантность: {result.score:.3f})")
            output.append(f"Объект: {payload['object_type']}.{payload['object_name']}")
            output.append(f"Синоним: {payload.get('friendly_name', '')}")
            
            if payload.get('has_modules'):
                output.append(f"Модулей: {payload.get('modules_count', 0)}")
                output.append(f"Всего строк кода: {payload.get('total_code_lines', 0)}")
                
                modules = payload.get('modules', [])
                if modules:
                    output.append("\nМодули:")
                    for mod in modules:
                        mod_name = mod.get('name', mod.get('type', 'Unknown'))
                        output.append(f"  - {mod_name} ({mod['lines']} строк)")
        
        output.append(f"\n{'=' * 60}")
        output.append("💡 Для получения кода модуля используйте:")
        output.append("   get_module_full_code(object_type, object_name, 'имя_модуля')")
        first = results[0].payload if results else {}
        first_modules = first.get('modules') or []
        if first_modules:
            first_module = first_modules[0].get('name', first_modules[0].get('type', 'МодульОбъекта'))
            output.append(f"   Например: get_module_full_code('{first.get('object_type', '')}', "
                          f"'{first.get('object_name', '')}', '{first_module}')")
        
        return "\n".join(output)
    except Exception as e:
        return f"Ошибка поиска: {str(e)}\n\n💡 Попробуйте:\n  - diagnose_database() для проверки состояния БД\n  - Проверьте, что эмбеддинг-сервис запущен"


@mcp.tool
def search_1c_code(query: str, object_type: str | None = None, object_name: str | None = None, 
                   module_type: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """Поиск в коде модулей конфигурации 1С.

    Используйте для поиска конкретных функций, процедур или фрагментов кода.

    Args:
        query: Текст для поиска в коде
        object_type: Фильтр по типу объекта
        object_name: Фильтр по имени объекта
        module_type: Фильтр по типу модуля
        limit: Максимальное количество результатов
    
    Returns:
        Фрагменты кода с контекстом.
    """
    try:
        chunk_results = search_in_chunks(query, object_type, object_name, module_type, limit * 3)
        
        if not chunk_results:
            return f"По запросу '{query}' в коде ничего не найдено"
        
        output = [f"Найдено фрагментов кода: {len(chunk_results)}\n"]
        
        for i, result in enumerate(chunk_results[:limit], 1):
            payload = result.payload
            
            output.append(f"\n{'=' * 60}")
            output.append(f"Результат {i} (релевантность: {result.score:.3f})")
            output.append(f"Объект: {payload.get('object_type')}.{payload.get('object_name')}")
            module_display = payload.get('module_name', payload.get('module_type', 'Unknown'))
            output.append(f"Модуль: {module_display}")
            total_chunks = payload.get('total_chunks')
            chunk_number = payload.get('chunk_index', 0) + 1
            output.append(f"Чанк: {chunk_number}/{total_chunks}" if total_chunks else f"Чанк: {chunk_number}")
            output.append(f"Строки: {payload.get('start_line', 0)}-{payload.get('end_line', 0)}")
            
            procedures = payload.get('procedures_in_chunk', [])
            functions = payload.get('functions_in_chunk', [])
            
            if procedures:
                output.append(f"\nПроцедуры ({len(procedures)}):")
                for proc in procedures:
                    # Теперь proc содержит только {name, type, is_export}
                    export_mark = " [ЭКСПОРТ]" if proc.get('is_export') else ""
                    output.append(f"  - {proc.get('name', 'Unknown')}{export_mark}")
            
            if functions:
                output.append(f"\nФункции ({len(functions)}):")
                for func in functions:
                    # Теперь func содержит только {name, type, is_export}
                    export_mark = " [ЭКСПОРТ]" if func.get('is_export') else ""
                    output.append(f"  - {func.get('name', 'Unknown')}{export_mark}")
            
            code = payload.get('code_chunk', '')
            if code:
                output.append(f"\nКод:")
                output.append("```bsl")
                output.append(code[:600])
                if len(code) > 600:
                    output.append("...")
                output.append("```")
        
        return "\n".join(output)
    except Exception as e:
        return f"Ошибка поиска: {str(e)}"


@mcp.tool
def search_1c_procedures(query: str, object_type: str | None = None, object_name: str | None = None,
                        module_type: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """Поиск процедур и функций по их названию или назначению.

    Используйте для поиска конкретных процедур/функций.
    
    Этот инструмент возвращает только СПИСОК процедур и их сигнатуры.
    Для получения ПОЛНОГО КОДА используйте get_module_full_code() или find_procedure_code().

    Args:
        query: Описание или название процедуры/функции
        object_type: Фильтр по типу объекта
        object_name: Фильтр по имени объекта
        module_type: Фильтр по типу модуля
        limit: Максимальное количество результатов
    
    Returns:
        Список модулей с процедурами, их сигнатуры и инструкции для получения кода.
    """
    try:
        module_results = search_in_modules(query, object_type, object_name, module_type, limit * 2)
        
        if not module_results:
            return f"По запросу '{query}' процедуры не найдены"
        
        output = [f"Найдено модулей с подходящими процедурами: {len(module_results)}\n"]
        
        for i, result in enumerate(module_results[:limit], 1):
            payload = result.payload
            
            output.append(f"\n{'=' * 60}")
            output.append(f"Результат {i} (релевантность: {result.score:.3f})")
            output.append(f"Объект: {payload.get('object_type')}.{payload.get('object_name')}")
            module_display = payload.get('module_name', payload.get('module_type', 'Unknown'))
            output.append(f"Модуль: {module_display}")
            output.append(f"Строк кода: {payload.get('lines_count', 0)}")
            
            procedures = payload.get('procedures', [])
            if procedures:
                output.append(f"\nПроцедуры ({len(procedures)}):")
                for proc in procedures[:15]:
                    export_mark = " [ЭКСПОРТ]" if proc.get('is_export') else ""
                    output.append(f"  - {proc['signature']}{export_mark}")
            
            functions = payload.get('functions', [])
            if functions:
                output.append(f"\nФункции ({len(functions)}):")
                for func in functions[:15]:
                    export_mark = " [ЭКСПОРТ]" if func.get('is_export') else ""
                    output.append(f"  - {func['signature']}{export_mark}")
        
        output.append(f"\n{'=' * 60}")
        output.append("💡 Для получения полного кода процедуры используйте:")
        first_result = module_results[0].payload
        module_name = first_result.get('module_name', first_result.get('module_type', 'Unknown'))
        output.append(f"   get_module_full_code('{first_result.get('object_type')}', '{first_result.get('object_name')}', '{module_name}')")
        output.append("   или")
        output.append(f"   find_procedure_code('{first_result.get('object_type')}', '{first_result.get('object_name')}', 'имя_процедуры')")
        
        return "\n".join(output)
    except Exception as e:
        return f"Ошибка поиска: {str(e)}"


@mcp.tool
def find_procedure_code(object_type: str, object_name: str, procedure_name: str) -> str:
    """Найти конкретную процедуру/функцию и получить её код.
    
    Это комбинированный инструмент для прямого получения кода процедуры.
    Используйте его когда знаете точное имя процедуры.

    Args:
        object_type: Тип объекта (Справочник, Документ и т.д.)
        object_name: Имя объекта (Номенклатура, ЗаказПокупателя и т.д.)
        procedure_name: Название процедуры/функции (например: ПриЗаписи, ПередЗаписью)
    
    Returns:
        Код найденной процедуры с контекстом.
    """
    try:
        modules = get_modules_by_object(object_type, object_name)
        
        if not modules:
            return f"Объект {object_type}.{object_name} не найден или не имеет модулей"
        
        found_items = []
        
        for module in modules:
            full_code = module.get('full_code', '')
            procedures = module.get('procedures', [])
            functions = module.get('functions', [])
            
            # Поиск среди процедур
            for proc in procedures:
                proc_name = proc.get('name', '')
                if procedure_name.lower() in proc_name.lower() or proc_name.lower() in procedure_name.lower():
                    found_items.append({
                        'module': module,
                        'item': proc,
                        'type': 'Процедура'
                    })
            
            # Поиск среди функций
            for func in functions:
                func_name = func.get('name', '')
                if procedure_name.lower() in func_name.lower() or func_name.lower() in procedure_name.lower():
                    found_items.append({
                        'module': module,
                        'item': func,
                        'type': 'Функция'
                    })
        
        if not found_items:
            available_procs = []
            for module in modules:
                module_display = module.get('module_name', module.get('module_type', 'Unknown'))
                for proc in module.get('procedures', []):
                    available_procs.append(f"{module_display}: {proc.get('name')}")
                for func in module.get('functions', []):
                    available_procs.append(f"{module_display}: {func.get('name')}")
            
            output = [f"Процедура/функция '{procedure_name}' не найдена в {object_type}.{object_name}"]
            if available_procs:
                output.append("\nДоступные процедуры и функции:")
                for proc in available_procs[:20]:
                    output.append(f"  - {proc}")
                if len(available_procs) > 20:
                    output.append(f"  ... и ещё {len(available_procs) - 20}")
            return "\n".join(output)
        
        output = [f"Найдено: {len(found_items)} совпадений для '{procedure_name}'\n"]
        
        for i, item in enumerate(found_items, 1):
            module = item['module']
            proc = item['item']
            
            module_display = module.get('module_name', module.get('module_type', 'Unknown'))
            
            output.append(f"\n{'=' * 60}")
            output.append(f"Результат {i}")
            output.append(f"Объект: {object_type}.{object_name}")
            output.append(f"Модуль: {module_display}")
            output.append(f"Тип: {item['type']}")
            output.append(f"Название: {proc.get('name')}")
            output.append(f"Сигнатура: {proc.get('signature')}")
            
            if proc.get('is_export'):
                output.append("Экспортная: ДА")
            
            proc_data = get_procedure_from_collection(object_type, object_name, proc['name'])
            
            if proc_data and proc_data.get('full_code'):
                proc_code = proc_data['full_code']
                start_line = proc.get('start_line')
                end_line = proc.get('end_line')
                
                output.append(f"\nКод (строки {start_line}-{end_line}):")
                output.append("```bsl")
                output.append(proc_code)
                output.append("```")
            else:
                # Процедура не найдена в COLLECTION_PROCEDURES
                output.append("\n⚠️ Код процедуры не найден в базе данных.")
                output.append("Возможно, база данных была создана старой версией загрузчика.")
                output.append("Попробуйте использовать search_procedures_direct() для поиска процедуры.")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"Ошибка поиска процедуры: {str(e)}"


@mcp.tool
def get_module_full_code(object_type: str, object_name: str, module_name: str,
                        form_name: str | None = None) -> str:
    """Получить полный код модуля объекта конфигурации.

    Код восстанавливается из процедур в COLLECTION_PROCEDURES.

    Args:
        object_type: Тип объекта (Справочник, Документ и т.д.)
        object_name: Имя объекта (Номенклатура, ЗаказПокупателя и т.д.)
        module_name: Имя модуля (МодульОбъекта, МодульФормы_ФормаСпискаWEB и т.д.)
        form_name: [УСТАРЕЛО] Оставлен для обратной совместимости
    
    Returns:
        Полный код модуля со статистикой.
    """
    try:
        modules = get_modules_by_object(object_type, object_name)
        
        if not modules:
            return f"Объект {object_type}.{object_name} не найден или не имеет модулей"
        
        # Ищем по module_name
        target_module = None
        for module in modules:
            # Проверяем и module_name и module_type для обратной совместимости
            if (module.get('module_name', '') == module_name or 
                module.get('module_type', '') == module_name):
                target_module = module
                break
        
        if not target_module:
            available = []
            for m in modules:
                mod_name = m.get('module_name', m.get('module_type', 'Unknown'))
                available.append(mod_name)
            return f"Модуль '{module_name}' не найден. Доступные модули: {', '.join(available)}"
        
        module_display = target_module.get('module_name', target_module.get('module_type', 'Unknown'))
        
        procedures = target_module.get('procedures', [])
        functions = target_module.get('functions', [])
        
        if not procedures and not functions:
            return f"Модуль {module_display} не содержит процедур и функций"
        
        # Собираем все процедуры и функции, сортируем по start_line
        all_items = sorted(procedures + functions, key=lambda x: x.get('start_line', 0))
        
        # Получаем код каждой процедуры из COLLECTION_PROCEDURES
        code_parts = []
        missing_procedures = []
        
        for item in all_items:
            proc_name = item['name']
            proc_data = get_procedure_from_collection(object_type, object_name, proc_name)
            
            if proc_data and proc_data.get('full_code'):
                code_parts.append(proc_data['full_code'])
            else:
                missing_procedures.append(proc_name)
                logger.warning(f"Процедура {proc_name} не найдена в COLLECTION_PROCEDURES")
        
        if not code_parts:
            return f"❌ Не удалось восстановить код модуля {module_display}. Процедуры не найдены в базе."
        
        # Объединяем код процедур
        full_code = '\n\n'.join(code_parts)
        
        # Проверка размера кода и обрезка при необходимости
        code_truncated = False
        if len(full_code) > MAX_FULL_CODE_SIZE:
            logger.warning(f"Код модуля {object_type}.{object_name}.{module_display} превышает лимит {MAX_FULL_CODE_SIZE} символов. Обрезаем.")
            full_code = full_code[:MAX_FULL_CODE_SIZE]
            code_truncated = True
        
        output = []
        output.append(f"Полный код модуля (восстановлен из {len(code_parts)} процедур/функций):")
        output.append(f"Объект: {object_type}.{object_name}")
        output.append(f"Модуль: {module_display}")
        output.append(f"Строк кода: {target_module.get('lines_count', 0)}")
        output.append(f"Процедур: {target_module.get('procedures_count', 0)}")
        output.append(f"Функций: {target_module.get('functions_count', 0)}")
        
        if missing_procedures:
            output.append("")
            output.append(f"⚠️ **ВНИМАНИЕ:** Не найдены {len(missing_procedures)} процедур: {', '.join(missing_procedures[:5])}")
        
        if code_truncated:
            output.append("")
            output.append(f"⚠️ **ВНИМАНИЕ:** Код обрезан до {MAX_FULL_CODE_SIZE} символов из-за ограничений размера.")
            output.append(f"   Используйте `find_procedure_code()` для получения конкретных процедур.")
        
        output.append("")
        output.append("```bsl")
        output.append(full_code)
        if code_truncated:
            output.append("\n... [КОД ОБРЕЗАН]")
        output.append("```")
        
        return "\n".join(output)
    except Exception as e:
        return f"Ошибка получения кода: {str(e)}"


@mcp.tool
def list_metadata_objects(object_type: str | None = None, name_filter: str | None = None, limit: int = 200) -> str:
    """
    Список объектов конфигурации.
    
    Без object_type - сводка: сколько объектов каждого типа есть в конфигурации.
    С object_type - перечень объектов этого типа (имя, синоним, количество модулей).
    
    Args:
        object_type: Тип объекта (Справочник, Документ, РегистрСведений, ОбщийМодуль и т.д.)
        name_filter: Фильтр по подстроке имени или синонима (без учёта регистра)
        limit: Максимум объектов в перечне (по умолчанию 200, до 2000)
    
    Examples:
        list_metadata_objects()
        list_metadata_objects("РегистрСведений")
        list_metadata_objects("Справочник", name_filter="номенклатур")
    """
    return metadata_tools.list_metadata_objects(object_type, name_filter, limit)


@mcp.tool
def find_object_references(object_type: str, object_name: str, include_code: bool = True) -> str:
    """
    Где используется объект конфигурации (для рефакторинга и анализа влияния изменений).
    
    Ищет:
    - в метаданных: реквизиты/измерения/ресурсы с типом объекта, владельцы, ввод на основании, движения;
    - в коде (include_code=True): процедуры, где есть обращения вида «Справочники.Имя» и «Справочник.Имя»
      (для общего модуля - «ИмяМодуля.»).
    
    Args:
        object_type: Тип объекта (Справочник, Документ, РегистрНакопления, ОбщийМодуль и т.д.)
        object_name: Имя объекта
        include_code: Искать обращения в коде процедур (медленнее на больших конфигурациях)
    
    Examples:
        find_object_references("Справочник", "Номенклатура")
        find_object_references("РегистрНакопления", "ТоварыНаСкладах", include_code=False)
        find_object_references("ОбщийМодуль", "ОбщегоНазначения")
    """
    return metadata_tools.find_object_references(object_type, object_name, include_code)


@mcp.tool
def list_object_modules(object_type: str, object_name: str) -> str:
    """Получить список всех модулей объекта конфигурации.

    Args:
        object_type: Тип объекта (Справочник, Документ и т.д.)
        object_name: Имя объекта
    
    Returns:
        Список модулей с их характеристиками.
    """
    try:
        obj = get_object_by_name(object_type, object_name)
        
        if not obj:
            return f"Объект {object_type}.{object_name} не найден"
        
        modules = get_modules_by_object(object_type, object_name)
        
        output = []
        output.append(f"Объект: {object_type}.{object_name}")
        output.append(f"Синоним: {obj.get('friendly_name', '')}")
        output.append(f"Всего модулей: {len(modules)}")
        output.append(f"Всего строк кода: {obj.get('total_code_lines', 0)}")
        output.append("")
        
        if not modules:
            output.append("Модули не найдены")
        else:
            for i, module in enumerate(modules, 1):
                module_display = module.get('module_name', module.get('module_type', 'Unknown'))
                output.append(f"{i}. {module_display}")
                output.append(f"   Строк: {module.get('lines_count', 0)}")
                output.append(f"   Процедур: {module.get('procedures_count', 0)}")
                output.append(f"   Функций: {module.get('functions_count', 0)}")
                
                export_methods = module.get('export_methods', [])
                if export_methods:
                    output.append(f"   Экспортных методов: {len(export_methods)}")
                
                output.append("")
        
        output.append("💡 Для получения кода модуля используйте:")
        output.append("   get_module_full_code(object_type, object_name, 'имя_модуля')")
        if modules:
            first_module = modules[0].get('module_name', modules[0].get('module_type', 'Unknown'))
            output.append(f"   Например: get_module_full_code('{object_type}', '{object_name}', '{first_module}')")
        
        return "\n".join(output)
    except Exception as e:
        return f"Ошибка: {str(e)}"


@mcp.tool
def search_procedures_direct(
    query: str,
    object_type: str | None = None,
    object_name: str | None = None,
    is_export_only: bool = False,
    limit: int = DEFAULT_SEARCH_LIMIT
) -> str:
    """
    Прямой поиск процедур/функций в специализированной коллекции.
    
    **ПРЕИМУЩЕСТВА:**
    - ⚡ В 5-10 раз быстрее обычного поиска
    - 🎯 Каждая процедура - отдельная точка с полным кодом
    - ✅ Прямой доступ к метаданным и коду процедуры
    - 🔍 Точнее старого search_1c_procedures()
    
    **Используйте этот инструмент когда:**
    - Ищете процедуру по её назначению: "процедура расчета остатков"
    - Нужны только экспортные методы
    - Хотите быстро найти реализацию функционала
    
    Args:
        query: Описание искомой процедуры (например: "процедура проверки реквизитов")
        object_type: Фильтр по типу объекта (Справочник, Документ и т.д.)
        object_name: Фильтр по имени объекта
        is_export_only: Искать только экспортные процедуры (True/False)
        limit: Количество результатов (по умолчанию 5)
    
    Returns:
        Список найденных процедур с полным кодом и метаданными
    
    Examples:
        search_procedures_direct("процедура расчета остатков")
        search_procedures_direct("проверка реквизитов", object_type="Справочник")
        search_procedures_direct("обработка события", is_export_only=True)
    """
    try:
        results = search_in_procedures(query, object_type, object_name, is_export_only, limit)
        
        if not results:
            return f"Процедуры по запросу '{query}' не найдены"
        
        output = []
        output.append(f"🔍 Найдено процедур: {len(results)}\n")
        
        for i, result in enumerate(results, 1):
            payload = result.payload
            score = getattr(result, 'score', 0)
            
            output.append(f"{'=' * 70}")
            output.append(f"Результат {i} (релевантность: {score:.3f})")
            output.append(f"{'=' * 70}")
            output.append(f"📦 Объект: {payload.get('object_type')}.{payload.get('object_name')}")
            output.append(f"📄 Модуль: {payload.get('module_name', 'Unknown')}")
            output.append(f"🔧 {payload.get('procedure_type', 'Процедура')}: {payload.get('procedure_name')}")
            output.append(f"📝 Сигнатура: {payload.get('signature', '')}")
            
            if payload.get('is_export'):
                output.append("🌐 Экспортная: ДА")
            
            params = payload.get('params', [])
            if params:
                output.append(f"📋 Параметры ({len(params)}):")
                for param in params:
                    # params может быть списком строк или списком словарей
                    if isinstance(param, dict):
                        param_str = f"   - {param.get('name', '')}"
                        if param.get('default'):
                            param_str += f" = {param.get('default')}"
                        output.append(param_str)
                    else:
                        # param - это строка
                        output.append(f"   - {param}")
            
            calls_to = payload.get('calls_to', [])
            if calls_to:
                output.append(f"🔗 Вызывает ({len(calls_to)}): {', '.join(calls_to[:5])}")
                if len(calls_to) > 5:
                    output.append(f"   ... и еще {len(calls_to) - 5}")
            
            comments = payload.get('comments', '')
            if comments:
                output.append(f"💬 Комментарии:")
                for line in comments.split('\n')[:3]:
                    output.append(f"   {line}")
            
            output.append(f"\n📍 Строки: {payload.get('start_line')}-{payload.get('end_line')}")
            
            # Полный код процедуры
            full_code = payload.get('full_code', '')
            if full_code:
                output.append(f"\n💻 Код процедуры:")
                output.append("```bsl")
                # Показываем первые 30 строк или весь код если короткий
                code_lines = full_code.split('\n')
                if len(code_lines) > 30:
                    output.append('\n'.join(code_lines[:30]))
                    output.append(f"\n... еще {len(code_lines) - 30} строк")
                    output.append(f"\n💡 Полный код: {len(code_lines)} строк")
                else:
                    output.append(full_code)
                output.append("```")
            
            output.append("")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"❌ Ошибка поиска процедур: {str(e)}"


@mcp.tool
def analyze_procedure_calls(
    object_type: str,
    object_name: str,
    procedure_name: str
) -> str:
    """
    Анализ зависимостей процедуры - что вызывает и кто вызывает.
    
    **ВОЗМОЖНОСТИ:**
    - 🔗 Какие процедуры/функции вызывает эта процедура
    - 🔙 Какие процедуры вызывают эту (reverse lookup)
    - 📊 Граф зависимостей внутри объекта
    
    **Полезно для:**
    - Рефакторинга (понять влияние изменений)
    - Документирования кода
    - Понимания логики работы
    - Поиска циклических зависимостей
    
    Args:
        object_type: Тип объекта (Справочник, Документ и т.д.)
        object_name: Имя объекта
        procedure_name: Имя процедуры для анализа
    
    Returns:
        Детальный анализ зависимостей процедуры
    
    Examples:
        analyze_procedure_calls("Справочник", "ЕУЗ", "ПриЗаписи")
        analyze_procedure_calls("Документ", "ЗаказПокупателя", "ПроверитьРеквизиты")
    """
    try:
        # Найти саму процедуру
        filter_conditions = [
            FieldCondition(key="object_type", match=MatchValue(value=object_type)),
            FieldCondition(key="object_name", match=MatchValue(value=object_name)),
            FieldCondition(key="procedure_name", match=MatchValue(value=procedure_name))
        ]
        
        results = qdrant_client.scroll(
            collection_name=COLLECTION_PROCEDURES,
            scroll_filter=Filter(must=filter_conditions),
            limit=1,
            with_payload=True
        )
        
        if not results[0]:
            return f"❌ Процедура '{procedure_name}' не найдена в {object_type}.{object_name}"
        
        target_proc = results[0][0].payload
        
        output = []
        output.append(f"🔍 Анализ зависимостей процедуры")
        output.append(f"{'=' * 70}")
        output.append(f"📦 Объект: {object_type}.{object_name}")
        output.append(f"🔧 Процедура: {procedure_name}")
        output.append(f"📝 Сигнатура: {target_proc.get('signature', '')}")
        output.append(f"📍 Строки: {target_proc.get('start_line')}-{target_proc.get('end_line')}")
        output.append("")
        
        # ЧТО ВЫЗЫВАЕТ ЭТА ПРОЦЕДУРА
        calls_to = target_proc.get('calls_to', [])
        output.append(f"🔗 Эта процедура вызывает ({len(calls_to)}):")
        if calls_to:
            for call in calls_to:
                output.append(f"   → {call}")
        else:
            output.append("   (нет вызовов других процедур)")
        output.append("")
        
        # КТО ВЫЗЫВАЕТ ЭТУ ПРОЦЕДУРУ (reverse lookup)
        # Ищем процедуры того же объекта, где в calls_to есть наша процедура
        all_procedures = qdrant_client.scroll(
            collection_name=COLLECTION_PROCEDURES,
            scroll_filter=Filter(must=[
                FieldCondition(key="object_type", match=MatchValue(value=object_type)),
                FieldCondition(key="object_name", match=MatchValue(value=object_name))
            ]),
            limit=100,
            with_payload=True
        )
        
        callers = []
        for proc_result in all_procedures[0]:
            proc = proc_result.payload
            if procedure_name in proc.get('calls_to', []):
                callers.append({
                    'name': proc.get('procedure_name'),
                    'signature': proc.get('signature', ''),
                    'is_export': proc.get('is_export', False)
                })
        
        output.append(f"🔙 Эту процедуру вызывают ({len(callers)}):")
        if callers:
            for caller in callers:
                export_mark = " 🌐" if caller['is_export'] else ""
                output.append(f"   ← {caller['name']}{export_mark}")
                output.append(f"      {caller['signature']}")
        else:
            output.append("   (нет обратных вызовов / процедура не используется)")
        output.append("")
        
        # СТАТИСТИКА
        output.append("📊 Статистика:")
        output.append(f"   Прямых вызовов: {len(calls_to)}")
        output.append(f"   Обратных вызовов: {len(callers)}")
        output.append(f"   Экспортная: {'Да' if target_proc.get('is_export') else 'Нет'}")
        
        if len(callers) == 0 and not target_proc.get('is_export'):
            output.append("")
            module_name = target_proc.get('module_name', '') or ''
            if procedure_name in PLATFORM_EVENT_HANDLERS:
                output.append("ℹ️ Обработчик события объекта - вызывается платформой, прямых вызовов в коде нет.")
            elif module_name.startswith(("МодульФормы", "МодульКоманды")):
                output.append("ℹ️ Процедура модуля формы/команды: обработчики событий формы, элементов и команд")
                output.append("   привязываются в описании формы (Form.xml) и вызываются платформой.")
            else:
                output.append("⚠️ Прямых вызовов не найдено. Перед удалением проверьте вызовы, которые не видны")
                output.append("   по коду: ОписаниеОповещения(\"Имя\", ...), подписки на события,")
                output.append("   регламентные задания, Выполнить()/ВычислитьВБезопасномРежиме().")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"❌ Ошибка анализа: {str(e)}"


@mcp.tool
def find_similar_procedures(
    object_type: str,
    object_name: str,
    procedure_name: str,
    limit: int = 5
) -> str:
    """
    Найти процедуры с похожей логикой через эмбеддинги.
    
    **ВОЗМОЖНОСТИ:**
    - 🔍 Семантический поиск похожих процедур
    - 🎯 Использует векторное сходство для сравнения логики
    - 📊 Показывает степень похожести
    
    **Полезно для:**
    - Поиска дублирования кода
    - Обучения (найти примеры реализации)
    - Рефакторинга (объединить похожие процедуры)
    - Поиска паттернов в кодовой базе
    
    Args:
        object_type: Тип объекта (Справочник, Документ и т.д.)
        object_name: Имя объекта
        procedure_name: Имя процедуры-образца
        limit: Количество похожих процедур (по умолчанию 5)
    
    Returns:
        Список процедур с похожей логикой с оценкой сходства
    
    Examples:
        find_similar_procedures("Справочник", "ЕУЗ", "ПриЗаписи", limit=3)
        find_similar_procedures("Документ", "ЗаказПокупателя", "РассчитатьСумму")
    """
    try:
        # Найти процедуру-образец
        filter_conditions = [
            FieldCondition(key="object_type", match=MatchValue(value=object_type)),
            FieldCondition(key="object_name", match=MatchValue(value=object_name)),
            FieldCondition(key="procedure_name", match=MatchValue(value=procedure_name))
        ]
        
        # ОПТИМИЗИРОВАНО: with_vectors=True только для получения одного вектора
        results = qdrant_client.scroll(
            collection_name=COLLECTION_PROCEDURES,
            scroll_filter=Filter(must=filter_conditions),
            limit=1,
            with_payload=True,
            with_vectors=True
        )
        
        if not results[0]:
            return f"❌ Процедура '{procedure_name}' не найдена в {object_type}.{object_name}"
        
        target_proc = results[0][0]
        target_payload = target_proc.payload
        target_vector = target_proc.vector.get('code') if hasattr(target_proc, 'vector') else None
        
        if not target_vector:
            return "❌ Вектор процедуры не найден"
        
        # Найти похожие процедуры
        similar_results = qdrant_client.query_points(
            collection_name=COLLECTION_PROCEDURES,
            query=target_vector,
            using="code",
            limit=limit + 1,  # +1 чтобы исключить саму процедуру
            with_payload=True
        )
        
        output = []
        output.append(f"🔍 Процедуры похожие на '{procedure_name}'")
        output.append(f"📦 Объект-образец: {object_type}.{object_name}")
        output.append(f"📝 Сигнатура: {target_payload.get('signature', '')}")
        output.append("")
        output.append(f"Найдено похожих процедур: {len(similar_results.points) - 1}")
        output.append("=" * 70)
        output.append("")
        
        count = 0
        for result in similar_results.points:
            payload = result.payload
            
            # Пропускаем саму процедуру-образец
            if (payload.get('object_type') == object_type and 
                payload.get('object_name') == object_name and 
                payload.get('procedure_name') == procedure_name):
                continue
            
            count += 1
            if count > limit:
                break
            
            score = getattr(result, 'score', 0)
            similarity_percent = score * 100
            
            output.append(f"Результат {count} - Сходство: {similarity_percent:.1f}%")
            output.append("-" * 70)
            output.append(f"📦 Объект: {payload.get('object_type')}.{payload.get('object_name')}")
            output.append(f"📄 Модуль: {payload.get('module_name', 'Unknown')}")
            output.append(f"🔧 Процедура: {payload.get('procedure_name')}")
            output.append(f"📝 Сигнатура: {payload.get('signature', '')}")
            
            if payload.get('is_export'):
                output.append("🌐 Экспортная: ДА")
            
            # Показываем первые 10 строк кода для сравнения
            full_code = payload.get('full_code', '')
            if full_code:
                code_lines = full_code.split('\n')
                output.append(f"\n💻 Начало кода ({len(code_lines)} строк):")
                output.append("```bsl")
                output.append('\n'.join(code_lines[:10]))
                if len(code_lines) > 10:
                    output.append("...")
                output.append("```")
            
            # Анализ похожести
            target_calls = set(target_payload.get('calls_to', []))
            similar_calls = set(payload.get('calls_to', []))
            common_calls = target_calls & similar_calls
            
            if common_calls:
                output.append(f"\n🔗 Общие вызовы ({len(common_calls)}): {', '.join(list(common_calls)[:5])}")
            
            output.append("")
        
        if count == 0:
            output.append("Похожие процедуры не найдены")
        
        output.append("")
        output.append("💡 Совет: Высокое сходство (>70%) может указывать на дублирование кода")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"❌ Ошибка поиска похожих процедур: {str(e)}"


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request) -> JSONResponse:
    return JSONResponse({
        "message": "MCP 1C RAG Server - Полнофункциональный RAG для 1С",
        "collections": {
            "objects": COLLECTION_OBJECTS,
            "modules": COLLECTION_MODULES,
            "chunks": COLLECTION_CHUNKS,
            "procedures": COLLECTION_PROCEDURES,
            "syntax": COLLECTION_SYNTAX
        },
        "features": [
            "Поиск объектов по именам/описаниям",
            "Точный поиск в коде через чанки",
            "Поиск процедур и функций с параметрами",
            "Прямой поиск процедур",
            "Анализ зависимостей процедур",
            "Поиск похожих процедур",
            "Получение полного кода модулей",
            "Список модулей объекта с деталями",
            "Прямое получение кода процедуры",
            "Диагностика базы данных",
            "Поиск по синтаксису 1С",
            "Проверка кода BSL (BSL Language Server + стандарты) с построчными замечаниями",
            "Форматирование кода BSL",
            "Стандарты разработки для агента (get_dev_standards, standards://)",
            "Список объектов конфигурации и поиск мест использования объекта",
            "Поиск по структуре, в том числе по индексируемым полям"
        ],
        "tools": [
            "diagnose_database - диагностика БД",
            "list_metadata_objects - список объектов конфигурации",
            "get_object_metadata - структура объекта",
            "search_by_structure - поиск по реквизитам/типам/индексированию",
            "search_by_relationship - поиск по связям",
            "find_object_references - где используется объект",
            "search_1c_objects - поиск объектов",
            "search_1c_code - поиск в коде",
            "search_1c_procedures - поиск процедур/функций",
            "search_procedures_direct - прямой поиск процедур",
            "analyze_procedure_calls - анализ зависимостей процедуры",
            "find_similar_procedures - найти похожие процедуры",
            "find_procedure_code - получить код процедуры",
            "get_module_full_code - получить код модуля",
            "list_object_modules - список модулей объекта",
            "bslsyntax_search_global_function - поиск глобальных функций платформы",
            "bslsyntax_search_method - поиск методов объектов",
            "bslsyntax_search_property - поиск свойств объектов",
            "bslsyntax_get_details - детальная информация о синтаксисе",
            "bslcheck_code - проверка кода (текстом) BSL LS + стандарты",
            "bslcheck_analyze - проверка .bsl файла",
            "bslcheck_format - форматирование BSL кода",
            "get_dev_standards - стандарты разработки"
        ],
        "syntax_helper": {
            "enabled": True,
            "collection": COLLECTION_SYNTAX,
            "description": "Поиск по официальной документации 1С",
            "tools": [
                "bslsyntax_search_global_function",
                "bslsyntax_search_method",
                "bslsyntax_search_property",
                "bslsyntax_get_details"
            ]
        },
        "bsl_syntax_checker": {
            "enabled": True,
            "description": "Проверка и форматирование BSL кода через BSL Language Server",
            "tools": [
                "bslcheck_code",
                "bslcheck_analyze",
                "bslcheck_format"
            ]
        }
    })


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Проверка работоспособности"""
    try:
        collections_status = {}
        for coll_name in [COLLECTION_OBJECTS, COLLECTION_MODULES, COLLECTION_CHUNKS, COLLECTION_PROCEDURES]:
            if qdrant_client.collection_exists(coll_name):
                info = qdrant_client.get_collection(coll_name)
                collections_status[coll_name] = {
                    "exists": True,
                    "points_count": info.points_count
                }
            else:
                collections_status[coll_name] = {"exists": False}
        
        embedding_status = "OK"
        try:
            test_emb = get_query_embedding("тест")
            embedding_dimension = len(test_emb)
        except Exception as e:
            embedding_status = f"ERROR: {str(e)}"
            embedding_dimension = None
        
        return JSONResponse({
            "status": "healthy",
            "qdrant": "OK",
            "embedding_service": embedding_status,
            "embedding_dimension": embedding_dimension,
            "collections": collections_status
        })
    except Exception as e:
        return JSONResponse({
            "status": "unhealthy",
            "error": str(e)
        })


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info"
    )