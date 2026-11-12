#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер выгруженной конфигурации 1С для подготовки данных к векторизации v2.0
Добавлено: извлечение всех процедур и функций с позициями в коде
"""

import os
import json
import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass, asdict, field

from form_bin_reader import read_form_module


@dataclass
class ProcedureInfo:
    """Информация о процедуре/функции"""
    name: str
    is_export: bool
    is_function: bool  # True - функция, False - процедура
    params: List[str]
    start_line: int
    end_line: int = 0  # Заполняется при парсинге
    # Информация об аннотациях расширений
    annotation_type: str = ""  # "Перед", "После", "Вместо" или пустая строка
    annotation_target: str = ""  # Имя перехватываемой процедуры


@dataclass
class ModuleInfo:
    """Информация о модуле"""
    object_type: str
    object_name: str
    module_type: str  # ObjectModule, ManagerModule, FormModule, RecordSetModule, CommandModule, CommonModule
    file_path: str
    lines_count: int
    export_methods: List[str]
    has_server_code: bool
    has_client_code: bool
    procedures: List[Dict] = field(default_factory=list)
    functions: List[Dict] = field(default_factory=list)


@dataclass
class ObjectInfo:
    """Информация об объекте конфигурации"""
    object_type: str
    object_name: str
    friendly_name: str = ""
    comment: str = ""
    has_object_module: bool = False
    has_manager_module: bool = False
    forms_count: int = 0
    attributes: List[Dict] = field(default_factory=list) # Список реквизитов
    tabular_sections: List[Dict] = field(default_factory=list) # Список табличных частей
    modules: List[ModuleInfo] = field(default_factory=list)
    standard_attributes: List[str] = field(default_factory=list)
    register_records: List[str] = field(default_factory=list)
    based_on: List[str] = field(default_factory=list)
    owners: List[str] = field(default_factory=list)
    # Специфические поля для регистров сведений
    periodicity: str = ""  # Периодичность (Секунда, Минута, День и т.д.)
    write_mode: str = ""  # Режим записи (Подчиненная записи регистратора, Независимый)
    register_type: str = "" # Тип регистра накопления (Остатки / Обороты)
    resources: List[Dict] = field(default_factory=list)  # Ресурсы регистра
    dimensions: List[Dict] = field(default_factory=list)  # Измерения регистра
    enum_values: List[Dict] = field(default_factory=list)  # Значения перечисления
    properties: Dict[str, str] = field(default_factory=dict)

    

class Config1CParser:
    """Парсер конфигурации 1С"""

    # Типы объектов для обработки
    OBJECT_TYPES = {
        'Catalogs': 'Справочник',
        'Documents': 'Документ',
        'InformationRegisters': 'РегистрСведений',
        'AccumulationRegisters': 'РегистрНакопления',
        'Constants': 'Константа',
        'Enums': 'Перечисление',
        'CommonModules': 'ОбщийМодуль',
        'ChartsOfCharacteristicTypes': 'ПланВидовХарактеристик',
        'DataProcessors': 'Обработка',
        'Reports': 'Отчет',
        'CommonForms': 'ОбщаяФорма'
    }

    def __init__(self, config_dir: str, output_dir: str):
        self.config_dir = Path(config_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.objects: List[ObjectInfo] = []
        # Индекс процедур и функций для проверки вызовов
        # Формат: {имя_объекта_или_модуля: {множество_имен_процедур_функций}}
        # Например: {"ПользователиОС": {"Добавить", "Очистить"}, "ОбщийМодуль.ИмяМодуля": {"Сохранить"}}
        self.procedures_index: Dict[str, set] = {}
        
        # Глобальный индекс всех процедур/функций (без привязки к модулям) для быстрой проверки прямых вызовов
        # Просто множество всех имен процедур и функций во всей конфигурации
        self.all_procedures_functions: set = set()

        # Модули обычных форм, извлечённые из Ext/Form.bin: {виртуальный путь Ext/Form/Module.bsl: код}
        self.form_bin_modules: Dict[str, str] = {}
        self.form_bin_stats = {'forms': 0, 'modules': 0, 'empty': 0, 'errors': 0}

    @staticmethod
    def _detect_module_type(file_name: str, file_path: Path, eng_type: str) -> str:
        """Тип модуля по имени файла и пути"""
        if "ObjectModule.bsl" in file_name:
            return "ObjectModule"
        if file_name == "ValueManagerModule.bsl":
            # Модуль менеджера значения константы (до ManagerModule: имя содержит "ManagerModule.bsl")
            return "ValueManagerModule"
        if "ManagerModule.bsl" in file_name:
            return "ManagerModule"
        if "CommandModule.bsl" in file_name:
            return "CommandModule"
        if eng_type == "CommonModules":
            return "CommonModule"
        if "RecordSetModule.bsl" in file_name:
            # Модуль набора записей для регистров (находится в папке Ext)
            return "RecordSetModule"
        if "Form" in str(file_path.parent):
            return "FormModule.Ext"
        return "Unknown"

    def _iter_module_files(self, obj_dir: Path, eng_type: str):
        """Модули объекта: файлы *.bsl и модули обычных форм из Ext/Form.bin.

        Для Form.bin (если рядом нет Ext/Form/Module.bsl) отдаётся виртуальный путь
        .../Ext/Form/Module.bsl — дальше он обрабатывается как модуль управляемой формы.
        Код такого модуля лежит в self.form_bin_modules.

        Yields:
            (file_path, module_type, form_xml_path)
        """
        for root, dirs, files in os.walk(obj_dir):
            root_path = Path(root)
            for file in files:
                if file.endswith(".bsl"):
                    file_path = root_path / file
                elif (file.lower() == "form.bin" and root_path.name.lower() == "ext"
                      and not (root_path / "Form" / "Module.bsl").exists()):
                    file_path = root_path / "Form" / "Module.bsl"
                    if str(file_path) not in self.form_bin_modules:
                        if not self._load_form_bin(root_path / file, file_path):
                            continue
                else:
                    continue

                module_type = self._detect_module_type(file, file_path, eng_type)

                # Для модулей форм ищем XML файл с событиями: Forms/ИмяФормы/Ext/Form.xml
                # (file_path = .../Ext/Form/Module.bsl; у обычной формы Form.xml нет)
                form_xml_path = None
                if "Form" in str(file_path.parent):
                    form_xml = file_path.parent.parent / "Form.xml"
                    if form_xml.exists():
                        form_xml_path = form_xml

                yield file_path, module_type, form_xml_path

    def _load_form_bin(self, bin_path: Path, virtual_path: Path) -> bool:
        """Извлечь модуль обычной формы из Form.bin. False — модуля нет или ошибка."""
        self.form_bin_stats['forms'] += 1
        try:
            code = read_form_module(bin_path)
        except Exception as e:
            self.form_bin_stats['errors'] += 1
            print(f"  ⚠️ Не удалось прочитать {bin_path}: {e}")
            return False
        if not code or not code.strip():
            self.form_bin_stats['empty'] += 1
            return False
        # Переводы строк — как при чтении .bsl в текстовом режиме (\r\n и одиночный \r -> \n),
        # иначе при записи получится \r\r\n и сдвинутся номера строк
        code = code.replace('\r\n', '\n').replace('\r', '\n')
        self.form_bin_modules[str(virtual_path)] = code
        self.form_bin_stats['modules'] += 1
        return True

    def _read_module_code(self, module_file: Path) -> str:
        """Код модуля: из файла или из извлечённого Form.bin"""
        code = self.form_bin_modules.get(str(module_file))
        if code is not None:
            return code
        with open(module_file, 'r', encoding='utf-8-sig') as f:
            return f.read()

    def parse(self):
        """Основной метод парсинга — сначала код, потом метаданные"""
        print("🚀 Начало парсинга конфигурации 1С v2.1 (код + метаданные)...")

        # --- ПРОХОД 1: Собираем все процедуры/функции из всех модулей и заполняем индекс ---
        print("📋 Проход 1: Сбор всех процедур и функций...")
        # Кеш для кода модулей между проходами (оптимизация - не читать файлы дважды)
        modules_cache = {}  # {(file_path, obj_name, module_type): {'code': str, 'form_events': dict}}
        
        for eng_type, rus_type in self.OBJECT_TYPES.items():
            type_dir = self.config_dir / eng_type
            if not type_dir.exists():
                continue

            for obj_dir in type_dir.iterdir():
                if not obj_dir.is_dir():
                    continue

                obj_name = obj_dir.name
                
                # Собираем информацию о всех модулях объекта (*.bsl + модули обычных форм из Form.bin)
                for file_path, module_type, form_xml_path in self._iter_module_files(obj_dir, eng_type):
                    # Парсим процедуры/функции БЕЗ вызовов и заполняем индекс
                    # Кешируем код модуля для второго прохода
                    code, form_events = self._collect_procedures_functions(file_path, rus_type, obj_name, module_type, form_xml_path)
                    modules_cache[(str(file_path), obj_name, module_type)] = {
                        'code': code,
                        'form_events': form_events,
                        'file_path': file_path,
                        'obj_type': rus_type,
                        'obj_name': obj_name,
                        'module_type': module_type,
                        'form_xml_path': form_xml_path
                    }

        print(f"✅ Собрано процедур/функций в индекс: {len(self.all_procedures_functions)}")
        s = self.form_bin_stats
        if s['forms']:
            print(f"📄 Обычные формы (Form.bin): {s['forms']}, модулей извлечено: {s['modules']}, "
                  f"пустых: {s['empty']}, ошибок: {s['errors']}")
        
        # --- ПРОХОД 2: Извлекаем вызовы для всех модулей (индекс уже заполнен) ---
        print("📋 Проход 2: Извлечение вызовов процедур/функций...")
        
        for eng_type, rus_type in self.OBJECT_TYPES.items():
            type_dir = self.config_dir / eng_type
            if not type_dir.exists():
                continue

            print(f"📂 Обработка типа: {rus_type} ({eng_type})")

            processed = {}
            # --- Собираем код (папки с .bsl) ---
            for obj_dir in type_dir.iterdir():
                if not obj_dir.is_dir():
                    continue

                obj_name = obj_dir.name
                obj_info = self.parse_object(obj_dir, eng_type, rus_type, modules_cache)
                if obj_info:
                    processed[obj_name] = obj_info
                    self.objects.append(obj_info)

            # --- 2. Теперь собираем XML метаданные ---
            for xml_file in type_dir.glob("*.xml"):
                obj_name = xml_file.stem
                if obj_name.startswith('.'):
                    continue

                try:
                    metadata = self.parse_xml_metadata(xml_file)
                except Exception as e:
                    print(f"  ⚠️ Ошибка парсинга XML {xml_file}: {e}")
                    continue

                if obj_name in processed:
                    # --- Объект уже есть: обновляем метаданные ---
                    obj_info = processed[obj_name]
                    obj_info.friendly_name = metadata.get('synonym', obj_name)
                    obj_info.comment = metadata.get('comment', '')
                    obj_info.standard_attributes = metadata.get('standard_attributes', [])
                    obj_info.register_records = metadata.get('register_records', [])
                    obj_info.based_on = metadata.get('based_on', [])
                    obj_info.owners = metadata.get('owners', [])
                    obj_info.attributes = metadata.get('attributes', [])
                    obj_info.tabular_sections = metadata.get('tabular_sections', [])
                    obj_info.periodicity = metadata.get('periodicity', '')
                    obj_info.write_mode = metadata.get('write_mode', '')
                    obj_info.register_type = metadata.get('register_type', '')
                    obj_info.resources = metadata.get('resources', [])
                    obj_info.dimensions = metadata.get('dimensions', [])
                    obj_info.enum_values = metadata.get('enum_values', [])
                    obj_info.properties = metadata.get('properties', {})
                else:
                    # --- Объекта не было (нет папки) — создаём новый ---
                    obj_info = ObjectInfo(
                        object_type=rus_type,
                        object_name=obj_name,
                        friendly_name=metadata.get('synonym', obj_name),
                        comment=metadata.get('comment', ''),
                        has_object_module=False,
                        has_manager_module=False,
                        forms_count=0,
                        attributes=metadata.get('attributes', []),
                        tabular_sections=metadata.get('tabular_sections', []),
                        standard_attributes=metadata.get('standard_attributes', []),
                        register_records=metadata.get('register_records', []),
                        based_on=metadata.get('based_on', []),
                        owners=metadata.get('owners', []),
                        periodicity=metadata.get('periodicity', ''),
                        write_mode=metadata.get('write_mode', ''),
                        register_type=metadata.get('register_type', ''),
                        resources=metadata.get('resources', []),
                        dimensions=metadata.get('dimensions', []),
                        modules=[],
                        enum_values=metadata.get('enum_values', []),
                        properties=metadata.get('properties', {})        
                    )
                    self.objects.append(obj_info)
                    processed[obj_name] = obj_info

            print(f"  ✓ Обработано объектов типа {rus_type}: {len(processed)}")

        print(f"✅ Всего объектов: {len(self.objects)}")
        self.save_results()

    def parse_object(self, obj_dir: Path, eng_type: str, rus_type: str, modules_cache: dict = None) -> ObjectInfo:
        """Парсинг одного объекта конфигурации"""
        obj_name = obj_dir.name

        modules = []

        # Ищем все модули (*.bsl + модули обычных форм из Form.bin)
        for file_path, module_type, form_xml_path in self._iter_module_files(obj_dir, eng_type):
            cached = (modules_cache or {}).get((str(file_path), obj_name, module_type))
            module_info = self.parse_module(
                file_path, rus_type, obj_name, module_type, form_xml_path=form_xml_path,
                cached_code=cached['code'] if cached else None,
                cached_form_events=cached['form_events'] if cached else None
            )
            # Модуля из Form.bin нет на диске — код для save_module_files берём из памяти
            if module_info.modified_code is None and str(file_path) in self.form_bin_modules:
                module_info.modified_code = self.form_bin_modules[str(file_path)]
            modules.append(module_info)

        # Создаем описание объекта
        obj_info = ObjectInfo(
            object_type=rus_type,
            object_name=obj_name,
            has_object_module=any(m.module_type == "ObjectModule" for m in modules),
            has_manager_module=any(m.module_type == "ManagerModule" for m in modules),
            forms_count=len([m for m in modules if m.module_type.startswith("FormModule")]),
            modules=modules
        )

        return obj_info

    def parse_xml_metadata(self, xml_file: Path) -> Dict:
        """
        Dispatcher: разбирает xml и вызывает парсер по типу объекта.
        Возвращает словарь метаданных.
        """
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()

            ns = {
                'md': 'http://v8.1c.ru/8.3/MDClasses',
                'v8': 'http://v8.1c.ru/8.1/data/core',
                'cfg': 'http://v8.1c.ru/8.1/data/enterprise/current-config',
                'xr': 'http://v8.1c.ru/8.3/xcf/readable',
                'xs': 'http://www.w3.org/2001/XMLSchema',
                'xsi': 'http://www.w3.org/2001/XMLSchema-instance'
            }

            first_child = next(iter(root), None)
            if first_child is None:
                return {"name": "", "synonym": "", "comment": "",
                        "standard_attributes": [], "attributes": [], "tabular_sections": []}

            local_tag = first_child.tag.split('}', 1)[-1]  # Catalog, Document, Report, ...

            # Dispatch по типу — пока реализован Catalog, легко добавлять другие
            if local_tag == 'Catalog':
                return self._parse_catalog_xml(first_child, ns)
            elif local_tag == 'Document':
                return self._parse_document_xml(first_child, ns)
            elif local_tag == 'InformationRegister':
                return self._parse_information_register_xml(first_child, ns)
            elif local_tag == 'AccumulationRegister':
                return self._parse_accumulation_register_xml(first_child, ns)
            elif local_tag == 'Constant':
                return self._parse_constant_xml(first_child, ns)
            elif local_tag == 'Enum':
                return self._parse_enum_xml(first_child, ns)
            elif local_tag == 'CommonModule':
                return self._parse_common_module_xml(first_child, ns)


            # По-умолчанию — общая минимальная структура
            return {
                "name": self._find_text(first_child, 'md:Properties/md:Name', ns),
                "synonym": self._find_v8_content(first_child, 'md:Properties/md:Synonym', ns),
                "comment": self._find_text(first_child, 'md:Properties/md:Comment', ns),
                "standard_attributes": [],
                "attributes": [],
                "tabular_sections": [],
                "periodicity": "",
                "write_mode": "",
                "register_type": "",
                "resources": [],
                "dimensions": []
            }

        except Exception as e:
            print(f"⚠️ Ошибка парсинга XML {xml_file}: {e}")
            return {"name": "", "synonym": "", "comment": "",
                    "standard_attributes": [], "attributes": [], "tabular_sections": []}

    def _find_text(self, root_elem: ET.Element, xpath: str, ns: Dict[str, str]) -> str:
        node = root_elem.find(xpath, ns)
        return node.text.strip() if node is not None and node.text else ""

    def _find_v8_content(self, root_elem: ET.Element, xpath: str, ns: Dict[str, str]) -> str:
        node = root_elem.find(xpath, ns)
        if node is None:
            return ""
        # сначала ищем v8:item с lang=ru
        for item in node.findall('v8:item', ns):
            lang = item.find('v8:lang', ns)
            content = item.find('v8:content', ns)
            if lang is not None and lang.text == 'ru' and content is not None and content.text:
                return content.text
        # fallback: любой content
        content = node.find('.//v8:content', ns)
        return content.text if content is not None and content.text else ""

    # Значения свойств реквизитов/измерений/ресурсов (перевод на русский)
    FIELD_INDEXING_MAP = {
        "DontIndex": "Не индексировать",
        "Index": "Индексировать",
        "IndexWithAdditionalOrder": "Индексировать с доп. упорядочиванием",
    }
    FIELD_FILL_CHECKING_MAP = {
        "DontCheck": "Не проверять",
        "ShowError": "Выдавать ошибку",
    }

    def _field_props(self, props: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Дополнительные свойства реквизита/измерения/ресурса (только присутствующие в XML):
        indexing (Индексирование), fill_checking (ПроверкаЗаполнения),
        master (Ведущее), main_filter (ОсновнойОтбор), use_in_totals (ИспользованиеВИтогах)
        """
        result = {}
        if props is None:
            return result

        indexing = self._find_text(props, 'md:Indexing', ns)
        if indexing:
            result["indexing"] = self.FIELD_INDEXING_MAP.get(indexing, indexing)

        fill_checking = self._find_text(props, 'md:FillChecking', ns)
        if fill_checking:
            result["fill_checking"] = self.FIELD_FILL_CHECKING_MAP.get(fill_checking, fill_checking)

        for xml_name, key in (('md:Master', 'master'), ('md:MainFilter', 'main_filter'),
                              ('md:UseInTotals', 'use_in_totals')):
            value = self._find_text(props, xml_name, ns)
            if value:
                result[key] = value.lower() == 'true'

        return result

    def _parse_catalog_xml(self, catalog_elem: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Парсер для Catalog (справочника).
        Возвращает структуру:
        {
          name, synonym, comment,
          standard_attributes: [name,...],
          attributes: [{name,synonym,type,comment}, ...],
          tabular_sections: [{name,synonym,comment,attributes:[...]} , ...]
        }
        """
        # Properties
        props = catalog_elem.find('md:Properties', ns)
        name = self._find_text(props, 'md:Name', ns) if props is not None else ""
        synonym = self._find_v8_content(props, 'md:Synonym', ns) if props is not None else name
        comment = self._find_text(props, 'md:Comment', ns) if props is not None else ""

        # === Owners (владельцы справочника) ===
        owners = []
        if props is not None:
            owners_elem = props.find('md:Owners', ns)
            if owners_elem is not None:
                for item in owners_elem.findall('xr:Item', ns):
                    owner_ref = item.text or item.get('xsi:type', '')
                    if owner_ref:
                        owners.append(self.normalize_type(owner_ref))
                        
        # === BasedOn (основание - на основе какого справочника) ===
        based_on = []
        if props is not None:
            based_on_elem = props.find('md:BasedOn', ns)
            if based_on_elem is not None:
                for item in based_on_elem.findall('xr:Item', ns):
                    based_ref = item.text or item.get('xsi:type', '')
                    if based_ref:
                        based_on.append(self.normalize_type(based_ref))
                        
        # StandardAttributes — часто в md:StandardAttributes с элементами xr:StandardAttribute
        standard_attrs = []
        std_block = props.find('md:StandardAttributes', ns) if props is not None else None
        std_attr_translate = self.get_standard_attribute_map()  # <-- получаем словарь

        if std_block is not None:
            for el in std_block:
                name_attr = el.get('name') or el.findtext('md:Name', default=None)
                if name_attr:
                    std_rus = std_attr_translate.get(name_attr, name_attr)
                    standard_attrs.append(std_rus)


        # Attributes (ChildObjects/Attribute)
        attributes = []
        for attr in catalog_elem.findall('md:ChildObjects/md:Attribute', ns):
            prop = attr.find('md:Properties', ns)
            if prop is None:
                continue
            attr_name = self._find_text(prop, 'md:Name', ns)
            attr_syn = self._find_v8_content(prop, 'md:Synonym', ns) or attr_name
            attr_comment = self._find_text(prop, 'md:Comment', ns)

            # Type: ищем v8:Type внутри md:Type
            types = []
            type_el = prop.find('md:Type', ns)
            if type_el is not None:
                # возможны несколько v8:Type
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                # если нет v8:Type, возможно текст прямо в md:Type
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))

            attributes.append({
                "name": attr_name,
                "synonym": attr_syn,
                "type": ", ".join(types) if types else "Неопределено",
                "comment": attr_comment,
                **self._field_props(prop, ns)
            })

        # TabularSections
        tabular_sections = []
        for ts in catalog_elem.findall('md:ChildObjects/md:TabularSection', ns):
            ts_props = ts.find('md:Properties', ns)
            ts_name = self._find_text(ts_props, 'md:Name', ns) if ts_props is not None else ""
            ts_syn = self._find_v8_content(ts_props, 'md:Synonym', ns) if ts_props is not None else ts_name
            ts_comment = self._find_text(ts_props, 'md:Comment', ns) if ts_props is not None else ""

            ts_attributes = []
            for tsa in ts.findall('md:ChildObjects/md:Attribute', ns):
                tsa_props = tsa.find('md:Properties', ns)
                if tsa_props is None:
                    continue
                tsa_name = self._find_text(tsa_props, 'md:Name', ns)
                tsa_syn = self._find_v8_content(tsa_props, 'md:Synonym', ns) or tsa_name
                tsa_comment = self._find_text(tsa_props, 'md:Comment', ns)

                types = []
                type_el = tsa_props.find('md:Type', ns)
                if type_el is not None:
                    for t in self._type_nodes(type_el, ns):
                        if t.text:
                            types.append(self.normalize_type(t.text.strip()))
                    if not types and type_el.text and type_el.text.strip():
                        types.append(self.normalize_type(type_el.text.strip()))

                ts_attributes.append({
                    "name": tsa_name,
                    "synonym": tsa_syn,
                    "type": ", ".join(types) if types else "Неопределено",
                    "comment": tsa_comment,
                    **self._field_props(tsa_props, ns)
                })

            tabular_sections.append({
                "name": ts_name,
                "synonym": ts_syn,
                "comment": ts_comment,
                "attributes": ts_attributes
            })

        return {
            "name": name,
            "synonym": synonym,
            "comment": comment,
            "owners": owners,
            "based_on": based_on,
            "standard_attributes": standard_attrs,
            "attributes": attributes,
            "tabular_sections": tabular_sections
        }

    def _parse_document_xml(self, document_elem: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Парсер для Document (Документ) из 1C конфигурации.
        Извлекает все реквизиты, табличные части и регистры.
        """
        
        # ===== Properties: Name, Synonym, Comment, RegisterRecords =====
        props = document_elem.find('md:Properties', ns)
        name = self._find_text(props, 'md:Name', ns) if props is not None else ""
        synonym = self._find_v8_content(props, 'md:Synonym', ns) if props is not None else name
        comment = self._find_text(props, 'md:Comment', ns) if props is not None else ""

        # === BasedOn (основание - на основе каких документов/справочников) ===
        based_on = []
        if props is not None:
            based_on_elem = props.find('md:BasedOn', ns)
            if based_on_elem is not None:
                for item in based_on_elem.findall('xr:Item', ns):
                    based_ref = item.text or item.get('xsi:type', '')
                    if based_ref:
                        based_on.append(self.normalize_type(based_ref))
                        
        # RegisterRecords - ссылки на регистры
        register_records = []
        if props is not None:
            registers_elem = props.find('md:RegisterRecords', ns)
            if registers_elem is not None:
                for item in registers_elem.findall('xr:Item', ns):
                    register_ref = item.text or item.get('xsi:type', '')
                    if register_ref:
                        register_records.append(self.normalize_type(register_ref))

        # ===== StandardAttributes (стандартные реквизиты документа) =====
        standard_attrs = []
        std_attr_translate = self.get_standard_attribute_map()
        
        if props is not None:
            std_block = props.find('md:StandardAttributes', ns)
            if std_block is not None:
                for std_attr_elem in std_block:
                    # xr:StandardAttribute с атрибутом name
                    name_attr = std_attr_elem.get('name')
                    if name_attr:
                        std_rus = std_attr_translate.get(name_attr, name_attr)
                        standard_attrs.append(std_rus)

        # ===== ChildObjects/Attribute (реквизиты документа) =====
        attributes = []
        child_objects = document_elem.find('md:ChildObjects', ns)
        
        if child_objects is not None:
            for attr_elem in child_objects.findall('md:Attribute', ns):
                attr_props = attr_elem.find('md:Properties', ns)
                if attr_props is None:
                    continue
                
                attr_name = self._find_text(attr_props, 'md:Name', ns)
                attr_syn = self._find_v8_content(attr_props, 'md:Synonym', ns) or attr_name
                attr_comment = self._find_text(attr_props, 'md:Comment', ns)
                
                # Type: ищем v8:Type внутри md:Type (как в справочнике)
                types = []
                type_el = attr_props.find('md:Type', ns)
                if type_el is not None:
                    # возможны несколько v8:Type
                    for t in self._type_nodes(type_el, ns):
                        if t.text:
                            types.append(self.normalize_type(t.text.strip()))
                    # если нет v8:Type, возможно текст прямо в md:Type
                    if not types and type_el.text and type_el.text.strip():
                        types.append(self.normalize_type(type_el.text.strip()))
                
                attributes.append({
                    "name": attr_name,
                    "synonym": attr_syn,
                    "type": ", ".join(types) if types else "Неопределено",
                    "comment": attr_comment,
                    **self._field_props(attr_props, ns)
                })

        # ===== ChildObjects/TabularSection (табличные части) =====
        tabular_sections = []
        
        if child_objects is not None:
            for ts_elem in child_objects.findall('md:TabularSection', ns):
                ts_props = ts_elem.find('md:Properties', ns)
                if ts_props is None:
                    continue
                
                ts_name = self._find_text(ts_props, 'md:Name', ns)
                ts_syn = self._find_v8_content(ts_props, 'md:Synonym', ns) or ts_name
                ts_comment = self._find_text(ts_props, 'md:Comment', ns)
                
                # === StandardAttributes табличной части ===
                ts_standard_attrs = []
                ts_std_block = ts_props.find('md:StandardAttributes', ns)
                if ts_std_block is not None:
                    for std_attr in ts_std_block:
                        std_name = std_attr.get('name')
                        if std_name:
                            ts_standard_attrs.append(std_attr_translate.get(std_name, std_name))
                
                # === ChildObjects/Attribute (реквизиты табличной части) ===
                ts_attributes = []
                ts_child_objects = ts_elem.find('md:ChildObjects', ns)
                if ts_child_objects is not None:
                    for ts_attr_elem in ts_child_objects.findall('md:Attribute', ns):
                        tsa_props = ts_attr_elem.find('md:Properties', ns)
                        if tsa_props is None:
                            continue
                        
                        tsa_name = self._find_text(tsa_props, 'md:Name', ns)
                        tsa_syn = self._find_v8_content(tsa_props, 'md:Synonym', ns) or tsa_name
                        tsa_comment = self._find_text(tsa_props, 'md:Comment', ns)
                        
                        # Type для реквизита табличной части (как в справочнике)
                        types = []
                        type_el = tsa_props.find('md:Type', ns)
                        if type_el is not None:
                            for t in self._type_nodes(type_el, ns):
                                if t.text:
                                    types.append(self.normalize_type(t.text.strip()))
                            if not types and type_el.text and type_el.text.strip():
                                types.append(self.normalize_type(type_el.text.strip()))
                        
                        ts_attributes.append({
                            "name": tsa_name,
                            "synonym": tsa_syn,
                            "type": ", ".join(types) if types else "Неопределено",
                            "comment": tsa_comment,
                            **self._field_props(tsa_props, ns)
                        })
                
                tabular_sections.append({
                    "name": ts_name,
                    "synonym": ts_syn,
                    "comment": ts_comment,
                    "standard_attributes": ts_standard_attrs,
                    "attributes": ts_attributes
                })

        return {
            "name": name,
            "synonym": synonym,
            "comment": comment,
            "register_records": register_records,
            "based_on": based_on,
            "standard_attributes": standard_attrs,
            "attributes": attributes,
            "tabular_sections": tabular_sections
        }

    def _parse_information_register_xml(self, reg_elem: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Парсер для InformationRegister (Регистр сведений).
        Извлекает: Name, Synonym, Comment, периодичность, режим записи
        Ресурсы, Реквизиты (Attribute), Измерения (Dimension)
        """
        
        # ===== Properties =====
        props = reg_elem.find('md:Properties', ns)
        name = self._find_text(props, 'md:Name', ns) if props is not None else ""
        synonym = self._find_v8_content(props, 'md:Synonym', ns) if props is not None else name
        comment = self._find_text(props, 'md:Comment', ns) if props is not None else ""
        
        # Дополнительные свойства регистра
        periodicity = self._find_text(props, 'md:InformationRegisterPeriodicity', ns) if props is not None else ""
        write_mode = self._find_text(props, 'md:WriteMode', ns) if props is not None else ""
        
        # Словарь перевода периодичности
        periodicity_map = {
            'Millisecond': 'Миллисекунда',
            'Second': 'Секунда',
            'Minute': 'Минута',
            'Hour': 'Час',
            'Day': 'День',
            'Month': 'Месяц',
            'Quarter': 'Квартал',
            'Year': 'Год',
            'Nonperiodical': 'Непериодический',
            'nonperiodic': 'Непериодический'
        }
        periodicity = periodicity_map.get(periodicity, periodicity)
        
        # Словарь перевода режима записи
        write_mode_map = {
            'RecorderSubordinate': 'Подчиненная записи регистратора',
            'Independent': 'Независимый',
            'Separator': 'Разделитель'
        }
        write_mode = write_mode_map.get(write_mode, write_mode)
        
        # ===== Resources (Ресурсы) =====
        resources = []
        for resource_elem in reg_elem.findall('md:ChildObjects/md:Resource', ns):
            res_props = resource_elem.find('md:Properties', ns)
            if res_props is None:
                continue
            
            res_name = self._find_text(res_props, 'md:Name', ns)
            res_syn = self._find_v8_content(res_props, 'md:Synonym', ns) or res_name
            res_comment = self._find_text(res_props, 'md:Comment', ns)
            
            # Type
            types = []
            type_el = res_props.find('md:Type', ns)
            if type_el is not None:
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))
            
            resources.append({
                "name": res_name,
                "synonym": res_syn,
                "type": ", ".join(types) if types else "Неопределено",
                "comment": res_comment,
                **self._field_props(res_props, ns)
            })
        
        # ===== Attributes (Реквизиты) =====
        attributes = []
        for attr_elem in reg_elem.findall('md:ChildObjects/md:Attribute', ns):
            attr_props = attr_elem.find('md:Properties', ns)
            if attr_props is None:
                continue
            
            attr_name = self._find_text(attr_props, 'md:Name', ns)
            attr_syn = self._find_v8_content(attr_props, 'md:Synonym', ns) or attr_name
            attr_comment = self._find_text(attr_props, 'md:Comment', ns)
            
            # Type
            types = []
            type_el = attr_props.find('md:Type', ns)
            if type_el is not None:
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))
            
            attributes.append({
                "name": attr_name,
                "synonym": attr_syn,
                "type": ", ".join(types) if types else "Неопределено",
                "comment": attr_comment,
                **self._field_props(attr_props, ns)
            })
        
        # ===== Dimensions (Измерения) =====
        dimensions = []
        for dim_elem in reg_elem.findall('md:ChildObjects/md:Dimension', ns):
            dim_props = dim_elem.find('md:Properties', ns)
            if dim_props is None:
                continue
            
            dim_name = self._find_text(dim_props, 'md:Name', ns)
            dim_syn = self._find_v8_content(dim_props, 'md:Synonym', ns) or dim_name
            dim_comment = self._find_text(dim_props, 'md:Comment', ns)
            
            # Type
            types = []
            type_el = dim_props.find('md:Type', ns)
            if type_el is not None:
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))
            
            dimensions.append({
                "name": dim_name,
                "synonym": dim_syn,
                "type": ", ".join(types) if types else "Неопределено",
                "comment": dim_comment,
                **self._field_props(dim_props, ns)
            })
        
        return {
            "name": name,
            "synonym": synonym,
            "comment": comment,
            "periodicity": periodicity,
            "write_mode": write_mode,
            "resources": resources,
            "attributes": attributes,
            "dimensions": dimensions
        }

    def _parse_accumulation_register_xml(self, reg_elem: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Парсер для AccumulationRegister (Регистр накопления).
        Извлекает: Name, Synonym, Comment, RegisterType,
        а также ChildObjects: Resource, Attribute, Dimension
        """

        # ===== Properties =====
        props = reg_elem.find('md:Properties', ns)
        name = self._find_text(props, 'md:Name', ns) if props is not None else ""
        synonym = self._find_v8_content(props, 'md:Synonym', ns) if props is not None else name
        comment = self._find_text(props, 'md:Comment', ns) if props is not None else ""

        register_type = self._find_text(props, 'md:RegisterType', ns)
        register_type_map = {
            "Balance": "Остатки",
            "Turnovers": "Обороты"
        }
        register_type = register_type_map.get(register_type, register_type)

        # ===== Resources =====
        resources = []
        for res_elem in reg_elem.findall('md:ChildObjects/md:Resource', ns):
            res_props = res_elem.find('md:Properties', ns)
            if res_props is None:
                continue
            res_name = self._find_text(res_props, 'md:Name', ns)
            res_syn = self._find_v8_content(res_props, 'md:Synonym', ns) or res_name
            res_comment = self._find_text(res_props, 'md:Comment', ns)

            # Тип
            types = []
            type_el = res_props.find('md:Type', ns)
            if type_el is not None:
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))

            resources.append({
                "name": res_name,
                "synonym": res_syn,
                "comment": res_comment,
                "type": ", ".join(types) if types else "Неопределено",
                **self._field_props(res_props, ns)
            })

        # ===== Attributes =====
        attributes = []
        for attr_elem in reg_elem.findall('md:ChildObjects/md:Attribute', ns):
            attr_props = attr_elem.find('md:Properties', ns)
            if attr_props is None:
                continue
            attr_name = self._find_text(attr_props, 'md:Name', ns)
            attr_syn = self._find_v8_content(attr_props, 'md:Synonym', ns) or attr_name
            attr_comment = self._find_text(attr_props, 'md:Comment', ns)

            types = []
            type_el = attr_props.find('md:Type', ns)
            if type_el is not None:
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))

            attributes.append({
                "name": attr_name,
                "synonym": attr_syn,
                "comment": attr_comment,
                "type": ", ".join(types) if types else "Неопределено",
                **self._field_props(attr_props, ns)
            })

        # ===== Dimensions =====
        dimensions = []
        for dim_elem in reg_elem.findall('md:ChildObjects/md:Dimension', ns):
            dim_props = dim_elem.find('md:Properties', ns)
            if dim_props is None:
                continue
            dim_name = self._find_text(dim_props, 'md:Name', ns)
            dim_syn = self._find_v8_content(dim_props, 'md:Synonym', ns) or dim_name
            dim_comment = self._find_text(dim_props, 'md:Comment', ns)

            types = []
            type_el = dim_props.find('md:Type', ns)
            if type_el is not None:
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))

            dimensions.append({
                "name": dim_name,
                "synonym": dim_syn,
                "comment": dim_comment,
                "type": ", ".join(types) if types else "Неопределено",
                **self._field_props(dim_props, ns)
            })

        return {
            "name": name,
            "synonym": synonym,
            "comment": comment,
            "register_type": register_type,
            "resources": resources,
            "attributes": attributes,
            "dimensions": dimensions
        }

    def _parse_constant_xml(self, const_elem: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Парсер для Constant (Константа).
        Извлекает: Name, Synonym, Comment, Type.
        """

        # ===== Properties =====
        props = const_elem.find('md:Properties', ns)
        name = self._find_text(props, 'md:Name', ns) if props is not None else ""
        synonym = self._find_v8_content(props, 'md:Synonym', ns) if props is not None else name
        comment = self._find_text(props, 'md:Comment', ns) if props is not None else ""

        # ===== Type =====
        types = []
        if props is not None:
            type_el = props.find('md:Type', ns)
            if type_el is not None:
                for t in self._type_nodes(type_el, ns):
                    if t.text:
                        types.append(self.normalize_type(t.text.strip()))
                if not types and type_el.text and type_el.text.strip():
                    types.append(self.normalize_type(type_el.text.strip()))

        return {
            "name": name,
            "synonym": synonym,
            "comment": comment,
            "attributes": [{
                "name": name,
                "synonym": synonym,
                "type": ", ".join(types) if types else "Неопределено",
                "comment": comment
            }]
        }

    def _parse_enum_xml(self, enum_elem: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Парсер для Enum (Перечисление).
        Извлекает:
          - Из Properties: Name, Synonym, Comment
          - Из ChildObjects/EnumValue: Name, Synonym, Comment
        """

        # ===== Properties =====
        props = enum_elem.find('md:Properties', ns)
        name = self._find_text(props, 'md:Name', ns) if props is not None else ""
        synonym = self._find_v8_content(props, 'md:Synonym', ns) if props is not None else name
        comment = self._find_text(props, 'md:Comment', ns) if props is not None else ""

        # ===== Enum Values =====
        values = []
        for val_elem in enum_elem.findall('md:ChildObjects/md:EnumValue', ns):
            val_props = val_elem.find('md:Properties', ns)
            if val_props is None:
                continue

            val_name = self._find_text(val_props, 'md:Name', ns)
            val_synonym = self._find_v8_content(val_props, 'md:Synonym', ns) or val_name
            val_comment = self._find_text(val_props, 'md:Comment', ns)

            values.append({
                "name": val_name,
                "synonym": val_synonym,
                "comment": val_comment
            })

        return {
            "name": name,
            "synonym": synonym,
            "comment": comment,
            "enum_values": values  # <--- отдельное поле для значений
        }

    def _parse_common_module_xml(self, module_elem: ET.Element, ns: Dict[str, str]) -> Dict:
        """
        Парсер для CommonModule (Общий модуль).
        Извлекает все свойства из Properties с переводом на русский язык.
        """

        props = module_elem.find('md:Properties', ns)
        if props is None:
            return {}

        # Переводы имён свойств
        translate_map = {
            "Name": "Имя",
            "Synonym": "Синоним",
            "Comment": "Комментарий",
            "Global": "Глобальный",
            "Server": "Серверный",
            "ExternalConnection": "Внешнее соединение",
            "Client": "Клиент (тонкий)",
            "ClientManagedApplication": "Клиент (управляемое приложение)",
            "ClientOrdinaryApplication": "Клиент (обычное приложение)",
            "MobileAppClient": "Клиент (мобильное приложение)",
            "MobileAppServer": "Сервер мобильного приложения",
            "ExtensionManagedApplication": "Расширение управляемого приложения",
            "Privileged": "Привилегированный",
            "ServerCall": "Серверный вызов",
            "ReturnValuesReuse": "Повторное использование возвращаемых значений"
        }

        # Переводы значений ReturnValuesReuse
        reuse_map = {
            "DontUse": "Не использовать",
            "DuringRequest": "Во время запроса",
            "DuringSession": "В течение сессии"
        }

        properties = {}
        for child in props:
            tag = self._strip_ns(child.tag)
            text = child.text.strip() if child.text else ""

            # Особая обработка синонима
            if tag == "Synonym":
                text = self._find_v8_content(props, 'md:Synonym', ns)

            # Перевод ReturnValuesReuse
            if tag == "ReturnValuesReuse":
                text = reuse_map.get(text, text)

            rus_name = translate_map.get(tag, tag)
            properties[rus_name] = text

        name = properties.get("Имя", "")
        synonym = properties.get("Синоним", name)
        comment = properties.get("Комментарий", "")

        return {
            "name": name,
            "synonym": synonym,
            "comment": comment,
            "properties": properties
        }

    def _strip_ns(self, tag: str) -> str:
        """Убирает namespace из тега XML"""
        if '}' in tag:
            return tag.split('}', 1)[1]
        return tag

    # Вид ссылочного типа в XML -> имя вида объекта метаданных (как object_type в выгрузке)
    REF_KIND_MAP = {
        "CatalogRef": "Справочник",
        "DocumentRef": "Документ",
        "EnumRef": "Перечисление",
        "ChartOfCharacteristicTypesRef": "ПланВидовХарактеристик",
        "ChartOfAccountsRef": "ПланСчетов",
        "ChartOfCalculationTypesRef": "ПланВидовРасчета",
        "ExchangePlanRef": "ПланОбмена",
        "BusinessProcessRef": "БизнесПроцесс",
        "BusinessProcessRoutePointRef": "ТочкаМаршрутаБизнесПроцесса",
        "TaskRef": "Задача",
        "DefinedType": "ОпределяемыйТип",
        "Characteristic": "Характеристика",
        # Виды метаданных без «Ref» (владельцы справочников, ввод на основании и т.п.)
        "Catalog": "Справочник",
        "Document": "Документ",
        "Enum": "Перечисление",
        "ChartOfCharacteristicTypes": "ПланВидовХарактеристик",
        "ChartOfAccounts": "ПланСчетов",
        "ChartOfCalculationTypes": "ПланВидовРасчета",
        "ExchangePlan": "ПланОбмена",
        "BusinessProcess": "БизнесПроцесс",
        "Task": "Задача",
        # Регистры (ссылки в движениях документов и т.п.)
        "AccumulationRegister": "РегистрНакопления",
        "InformationRegister": "РегистрСведений",
        "AccountingRegister": "РегистрБухгалтерии",
        "CalculationRegister": "РегистрРасчета",
        "InformationRegisterRecordKey": "РегистрСведенийКлючЗаписи",
        "AccumulationRegisterRecordKey": "РегистрНакопленияКлючЗаписи",
    }

    # Платформенные типы
    PRIMITIVE_TYPE_MAP = {
        "xs:dateTime": "Дата/Время",
        "xs:date": "Дата",
        "xs:decimal": "Число",
        "xs:integer": "Целое",
        "xs:string": "Строка",
        "xs:boolean": "Логический",
        "xs:base64Binary": "ДвоичныеДанные",
        "v8:UUID": "УникальныйИдентификатор",
        "v8:ValueStorage": "ХранилищеЗначения",
        "v8:StandardPeriod": "СтандартныйПериод",
        "v8:StandardBeginningDate": "СтандартнаяДатаНачала",
        "v8:ValueListType": "СписокЗначений",
        "v8:ValueTable": "ТаблицаЗначений",
        "v8:TypeDescription": "ОписаниеТипов",
        "v8:FixedArray": "ФиксированныйМассив",
        "v8:FixedStructure": "ФиксированнаяСтруктура",
        "v8:Null": "Null",
        "v8:AccountType": "ВидСчета",
        "v8:AccumulationRecordType": "ВидДвиженияНакопления",
        "v8:AccountingRecordType": "ВидДвиженияБухгалтерии",
        "v8ui:Color": "Цвет",
        "v8ui:Font": "Шрифт",
        "v8ui:Picture": "Картинка",
    }

    def _type_nodes(self, type_el: ET.Element, ns: Dict[str, str]) -> List[ET.Element]:
        """
        Узлы типа реквизита: v8:Type (обычные типы) и v8:TypeSet
        (определяемые типы cfg:DefinedType.X, характеристики cfg:Characteristic.X).
        Без v8:TypeSet такие реквизиты получали тип «Неопределено».
        """
        return [node for node in type_el.iter()
                if node.tag in (f"{{{ns['v8']}}}Type", f"{{{ns['v8']}}}TypeSet")]

    def normalize_type(self, raw_type: str) -> str:
        """
        Преобразует строки типов из XML в читабельный вид (имена видов - как object_type объектов).
        Примеры:
          "cfg:CatalogRef.Пользователи"         -> "Справочник.Пользователи"
          "cfg:DefinedType.ФизическоеЛицо"      -> "ОпределяемыйТип.ФизическоеЛицо"
          "AccumulationRegister.ТоварыНаСкладах" -> "РегистрНакопления.ТоварыНаСкладах"
          "xs:dateTime"                          -> "Дата/Время"
          "v8:UUID"                              -> "УникальныйИдентификатор"
        """
        if not raw_type:
            return raw_type

        rt = raw_type.strip()

        if rt in self.PRIMITIVE_TYPE_MAP:
            return self.PRIMITIVE_TYPE_MAP[rt]

        # cfg:<Вид>.<Имя>, <Вид>.<Имя> или просто <Вид>
        m = re.match(r'^(?:cfg:)?([A-Za-z]+)(?:\.(.+))?$', rt)
        if m and m.group(1) in self.REF_KIND_MAP:
            kind = self.REF_KIND_MAP[m.group(1)]
            return f"{kind}.{m.group(2)}" if m.group(2) else kind

        # Если это cfg:... без известных шаблонов — просто убрать префикс cfg: и оставить как есть
        if rt.startswith('cfg:'):
            return rt.replace('cfg:', '')

        return rt

    def get_standard_attribute_map(self) -> Dict[str, str]:
        """
        Возвращает словарь перевода стандартных реквизитов 1С
        (используется для Catalog, Document и других объектов)
        """
        return {
            "PredefinedDataName": "ИмяПредопределённыхДанных",
            "Predefined": "Предопределённый",
            "Ref": "Ссылка",
            "DeletionMark": "ПометкаУдаления",
            "IsFolder": "ЭтоГруппа",
            "Owner": "Владелец",
            "Parent": "Родитель",
            "Description": "Наименование",
            "Code": "Код",
            "Posted": "Проведен",
            "Number": "Номер",
            "Date": "Дата",
            "Recorder": "Регистратор",
            "Active": "Активный",
            "Comment": "Комментарий",
            "LineNumber": "НомерСтроки",
            "Period": "Период",
            "Resource": "Ресурс",
            "Dimension": "Измерение",
            "Attribute": "Реквизит"
        }

    def _collect_procedures_functions(self, module_file: Path, obj_type: str,
                                       obj_name: str, module_type: str, 
                                       form_xml_path: Path = None):
        """Собрать все процедуры/функции из модуля и заполнить индекс (без извлечения вызовов)
        
        Returns:
            tuple: (code, form_events) - для кеширования во втором проходе
        """
        code = self._read_module_code(module_file)

        # Парсим события из XML формы, если это модуль формы
        form_events = {}
        if form_xml_path and form_xml_path.exists():
            form_events = self.parse_form_events_from_xml(form_xml_path)
            if form_events:
                code = self.inject_annotations_into_code(code, form_events)

        # Собираем процедуры и функции БЕЗ вызовов
        procedures, functions = self.parse_all_procedures_functions(code, form_events, obj_name, obj_type, module_type, extract_calls=False)

        # Добавляем процедуры и функции в индекс
        index_key = obj_name
        if obj_type == 'ОбщийМодуль':
            index_key = f"{obj_type}.{obj_name}"
        
        if index_key not in self.procedures_index:
            self.procedures_index[index_key] = set()
        
        for proc in procedures:
            self.procedures_index[index_key].add(proc['name'])
            self.all_procedures_functions.add(proc['name'])
        for func in functions:
            self.procedures_index[index_key].add(func['name'])
            self.all_procedures_functions.add(func['name'])
        
        return code, form_events

    def parse_module(self, module_file: Path, obj_type: str,
                     obj_name: str, module_type: str, 
                     target_file_path: str = None, form_xml_path: Path = None,
                     cached_code: str = None, cached_form_events: dict = None) -> ModuleInfo:
        """Парсинг модуля BSL с учетом событий из XML формы
        
        Args:
            cached_code: Кешированный код модуля (из первого прохода)
            cached_form_events: Кешированные события формы (из первого прохода)
        """
        if cached_code is not None:
            # Используем кешированные данные
            code = cached_code
            form_events = cached_form_events if cached_form_events is not None else {}
        else:
            # Читаем файл заново (fallback, если кеша нет)
            code = self._read_module_code(module_file)

            # Парсим события из XML формы, если это модуль формы
            form_events = {}
            if form_xml_path and form_xml_path.exists():
                form_events = self.parse_form_events_from_xml(form_xml_path)
                
                # Если есть события из XML, добавляем аннотации прямо в код
                if form_events:
                    code = self.inject_annotations_into_code(code, form_events)

        lines = code.split('\n')
        lines_count = len(lines)

        # Индекс уже заполнен в первом проходе, теперь просто извлекаем вызовы
        # Сначала собираем имена процедур/функций для передачи в extract_procedure_calls
        procedures, functions = self.parse_all_procedures_functions(code, form_events, obj_name, obj_type, module_type, extract_calls=False)
        procedures_names = {p['name'] for p in procedures}
        functions_names = {f['name'] for f in functions}
        
        # Теперь извлекаем вызовы для всех процедур/функций (глобальный индекс уже заполнен)
        procedures, functions = self.parse_all_procedures_functions(code, form_events, obj_name, obj_type, module_type, extract_calls=True, procedures_names=procedures_names, functions_names=functions_names)

        export_methods = [p['name'] for p in procedures if p['is_export']]
        export_methods.extend([f['name'] for f in functions if f['is_export']])

        has_server = '&НаСервере' in code or '&AtServer' in code
        has_client = '&НаКлиенте' in code or '&AtClient' in code

        file_path = target_file_path if target_file_path else str(module_file.relative_to(self.config_dir))

        module_info = ModuleInfo(
            object_type=obj_type,
            object_name=obj_name,
            module_type=module_type,
            file_path=file_path,
            lines_count=lines_count,
            export_methods=export_methods,
            has_server_code=has_server,
            has_client_code=has_client,
            procedures=procedures,
            functions=functions
        )
        
        # Сохраняем модифицированный код (с добавленными аннотациями) для последующего сохранения
        module_info.modified_code = code if form_events else None
        
        return module_info
    
    def parse_form_events_from_xml(self, form_xml_path: Path) -> Dict[str, Dict]:
        """
        Парсит XML формы и извлекает события с callType.
        Возвращает словарь: {имя_процедуры: {'event_name': ..., 'call_type': ...}}
        """
        events = {}
        try:
            tree = ET.parse(form_xml_path)
            root = tree.getroot()
            
            # Ищем элемент Events
            # Пространство имен формы
            ns = {'': 'http://v8.1c.ru/8.3/xcf/logform'}
            
            # Ищем все элементы Event
            for event in root.findall('.//Event', ns):
                event_name = event.get('name')  # OnCreateAtServer
                call_type = event.get('callType')  # Before, After, Instead
                handler_name = event.text  # ааПриСозданииНаСервереПосле
                
                if handler_name and call_type:
                    # Преобразуем event_name в имя процедуры (OnCreateAtServer -> ПриСозданииНаСервере)
                    target_proc_name = self._event_name_to_proc_name(event_name)
                    
                    # Преобразуем callType
                    annotation_type = ""
                    if call_type == "Before":
                        annotation_type = "Перед"
                    elif call_type == "After":
                        annotation_type = "После"
                    elif call_type in ("Instead", "Override"):  # Override используется в XML расширений
                        annotation_type = "Вместо"
                    
                    events[handler_name.strip()] = {
                        'event_name': event_name,
                        'target_procedure': target_proc_name,
                        'annotation_type': annotation_type
                    }
        
        except Exception as e:
            # Если не удалось распарсить XML, просто возвращаем пустой словарь
            pass
        
        return events
    
    def _event_name_to_proc_name(self, event_name: str) -> str:
        """
        Преобразует английское имя события в русское имя процедуры.
        OnCreateAtServer -> ПриСозданииНаСервере
        """
        # Словарь основных событий
        event_mapping = {
            'OnCreateAtServer': 'ПриСозданииНаСервере',
            'OnOpen': 'ПриОткрытии',
            'OnClose': 'ПриЗакрытии',
            'BeforeWrite': 'ПередЗаписью',
            'AfterWrite': 'ПослеЗаписи',
            'OnWrite': 'ПриЗаписи',
            'BeforeDelete': 'ПередУдалением',
            'OnCopy': 'ПриКопировании',
            'Filling': 'ОбработкаЗаполнения',
            'FillingCheckProcessing': 'ОбработкаПроверкиЗаполнения',
            'Posting': 'ОбработкаПроведения',
            'UndoPosting': 'ОбработкаУдаленияПроведения',
        }
        
        return event_mapping.get(event_name, event_name)
    
    def inject_annotations_into_code(self, code: str, form_events: Dict[str, Dict]) -> str:
        """
        Добавляет аннотации в код BSL перед процедурами, которые имеют события из XML.
        Это позволяет скрипту объединения видеть аннотации как обычные строки в коде.
        """
        lines = code.split('\n')
        result_lines = []
        
        # Паттерны для поиска процедур
        proc_pattern = re.compile(
            r'^\s*(?:Процедура|Procedure)\s+(\w+)\s*\(',
            re.IGNORECASE
        )
        func_pattern = re.compile(
            r'^\s*(?:Функция|Function)\s+(\w+)\s*\(',
            re.IGNORECASE
        )
        
        for i, line in enumerate(lines):
            # Проверяем, начинается ли процедура или функция
            proc_match = proc_pattern.match(line)
            func_match = func_pattern.match(line)
            
            if proc_match or func_match:
                proc_name = proc_match.group(1) if proc_match else func_match.group(1)
                
                # Проверяем, есть ли для этой процедуры событие из XML
                if proc_name in form_events:
                    event_info = form_events[proc_name]
                    annotation_type = event_info['annotation_type']
                    target_proc = event_info['target_procedure']
                    
                    # Формируем аннотацию
                    if annotation_type == "Перед":
                        annotation = f'&Перед("{target_proc}")'
                    elif annotation_type == "После":
                        annotation = f'&После("{target_proc}")'
                    elif annotation_type == "Вместо":
                        annotation = f'&Вместо("{target_proc}")'
                    else:
                        annotation = None
                    
                    # Добавляем аннотацию перед процедурой
                    if annotation:
                        result_lines.append(annotation)
            
            result_lines.append(line)
        
        return '\n'.join(result_lines)

    def find_procedure_start_line(self, lines: List[str], proc_line_num: int, max_lines: int = 20) -> int:
        """
        Найти реальное начало процедуры/функции с учетом директив компиляции и комментариев.
        
        Логика:
        1. Ищем строку с Процедура/Функция на proc_line_num
        2. Идем вверх, ищем директивы компиляции (&НаСервере, &НаКлиенте и т.д.)
        3. Продолжаем идти вверх, ищем комментарии (строки начинающиеся с //)
        4. Останавливаемся когда встречаем строку БЕЗ // или & в начале
        5. Возвращаем первую строку с директивой/комментарием (или саму строку с Процедура/Функция)
        
        Args:
            lines: Все строки кода
            proc_line_num: Номер строки с Процедура/Функция (1-based)
            max_lines: Максимум строк для поиска (по умолчанию 20)
        
        Returns:
            actual_start_line: Реальная начальная строка (с учетом комментариев/директив)
        """
        actual_start = proc_line_num  # По умолчанию начало = строка с Процедура/Функция
        
        # Ищем от proc_line_num - 1 вверх
        search_start = max(0, proc_line_num - max_lines - 1)
        for i in range(proc_line_num - 2, search_start - 1, -1):  # proc_line_num - 2 = строка ПЕРЕД Процедура/Функция
            if i < 0:
                break
            
            line = lines[i]
            line_stripped = line.strip()
            
            # Пустые строки пропускаем, но не прерываем поиск
            if not line_stripped:
                continue
            
            # Директива компиляции (&НаСервере, &НаКлиенте и т.д.) или комментарий (//)
            if line_stripped.startswith('&') or line_stripped.startswith('//'):
                actual_start = i + 1  # Обновляем реальное начало (1-based)
                continue
            
            # Встретили строку БЕЗ // или & в начале - прекращаем поиск
            break
        
        return actual_start

    _DECLARATION_RE = re.compile(r'^(?:Асинх\s+|Async\s+)?(?:Процедура|Функция|Procedure|Function)\s+\w+\s*\(',
                                 re.IGNORECASE)
    _STRING_LITERAL_RE = re.compile(r'"[^"\n]*"')

    def extract_procedure_calls(self, code: str, obj_name: str = None, obj_type: str = None, 
                                caller_name: str = None, current_module_procedures: set = None) -> List[str]:
        """
        Извлечь вызовы других процедур/функций из кода.
        Анализ на основе идентификаторов с круглыми скобками.
        
        Простая логика: если вызов есть в индексе процедур (procedures_index) - добавляем его в calls_to.
        Если нет - это глобальная функция или метод стандартного объекта, пропускаем.
        
        Args:
            code: Код процедуры/функции
            obj_name: Имя объекта (для проверки индекса)
            obj_type: Тип объекта (для проверки индекса)
            caller_name: Имя вызывающей процедуры/функции
            current_module_procedures: Множество имен процедур/функций текущего модуля (не используется)
        
        Returns:
            Список строк в формате "Объект.Метод" или "Метод" для прямых вызовов
        """
        calls = []

        # Убираем то, что не является вызовами: строку объявления процедуры/функции
        # (иначе процедура «вызывает сама себя»), комментарии и строковые литералы
        # (тексты запросов, сообщения), продолжения многострочных строк ("|...").
        code_lines = []
        for line in code.split('\n'):
            stripped = line.strip()
            if self._DECLARATION_RE.match(stripped) or stripped.startswith('|'):
                continue
            line = self._STRING_LITERAL_RE.sub('""', line)
            code_lines.append(line.split('//', 1)[0])
        code = '\n'.join(code_lines)

        # Паттерн 1: прямой вызов процедуры/функции (идентификатор + скобка, НЕ после точки:
        # Массив.Добавить() и Модуль.Метод() обрабатываются паттерном 2)
        # Примеры: Сохранить(), ПолучитьЗначение(), МассивСкладовНаСервере()
        direct_pattern = r'(?<![.\w])([А-Яа-яЁёA-Za-z_]\w*)\s*\('
        direct_calls = re.findall(direct_pattern, code)
        
        # Паттерн 2: вызов через точку (объект.метод или переменная.метод)
        # Примеры: Объект.Сохранить(), Модуль.ПолучитьЗначение(), ПользователиОС.Добавить()
        # Исключаем вложенные вызовы (Объект.Свойство.Метод) - это свойства объектов
        dot_pattern = r'\b([А-Яа-яA-Za-z_][А-Яа-яA-Za-z0-9_]*)\.([А-Яа-яA-Za-z_][А-Яа-яA-Za-z0-9_]*)\s*\('
        dot_matches = re.finditer(dot_pattern, code)
        
        # Фильтруем служебные слова языка 1С
        keywords = {
            'Если', 'If', 'Для', 'For', 'Пока', 'While', 
            'Процедура', 'Procedure', 'Функция', 'Function',
            'Новый', 'New', 'Тогда', 'Then', 'Иначе', 'Else',
            'КонецЕсли', 'EndIf', 'КонецЦикла', 'EndFor', 
            'КонецПопытки', 'EndTry', 'Попытка', 'Try',
            'ВызватьИсключение', 'Raise', 'И', 'And', 'Или', 'Or',
            'Не', 'Not', 'Возврат', 'Return'
        }
        
        # Обрабатываем прямые вызовы (например, МассивСкладовНаСервере())
        for call_name in direct_calls:
            # Пропускаем ключевые слова
            if call_name in keywords:
                continue
            
            # Проверяем, есть ли эта процедура/функция в глобальном индексе
            if call_name in self.all_procedures_functions:
                calls.append(call_name)
        
        # Обрабатываем вызовы через точку (например, ПользователиОС.Добавить())
        for match in dot_matches:
            object_name = match.group(1)
            method_name = match.group(2)
            
            # Пропускаем вложенные вызовы через свойства объектов
            # (Объект, ЭтаФорма, Элементы, Параметры - это стандартные объекты формы)
            if object_name in ['Объект', 'ЭтаФорма', 'Элементы', 'Параметры']:
                continue
            
            # Проверяем, есть ли эта процедура/функция в индексе
            found = False
            
            # Проверяем прямые имена объектов (например, "ПользователиОС")
            if object_name in self.procedures_index:
                if method_name in self.procedures_index[object_name]:
                    found = True
            
            # Проверяем формат "ОбщийМодуль.Имя" для общих модулей
            if not found:
                for key in self.procedures_index.keys():
                    if key.endswith(f".{object_name}") or key == f"ОбщийМодуль.{object_name}":
                        if method_name in self.procedures_index[key]:
                            found = True
                            break
            
            # Если найдена в индексе - это пользовательская процедура/функция
            if found:
                calls.append(f"{object_name}.{method_name}")
        
        # Убираем дубликаты и сортируем
        unique_calls = sorted(list(set(calls)))
        return unique_calls

    def parse_all_procedures_functions(self, code: str, form_events: Dict[str, Dict] = None, 
                                       obj_name: str = None, obj_type: str = None, module_type: str = None,
                                       extract_calls: bool = True, procedures_names: set = None, functions_names: set = None) -> Tuple[List[Dict], List[Dict]]:
        """Парсинг всех процедур и функций с позициями в коде, аннотациями расширений и событиями из XML"""
        if form_events is None:
            form_events = {}
        
        procedures = []
        functions = []
        
        lines = code.split('\n')
        
        # Паттерны для процедур и функций
        proc_pattern = re.compile(
            r'^\s*(?:Процедура|Procedure)\s+(\w+)\s*\((.*?)\)\s*(Экспорт|Export)?',
            re.IGNORECASE
        )
        func_pattern = re.compile(
            r'^\s*(?:Функция|Function)\s+(\w+)\s*\((.*?)\)\s*(Экспорт|Export)?',
            re.IGNORECASE
        )
        end_proc_pattern = re.compile(r'^\s*(?:КонецПроцедуры|EndProcedure)\s*$', re.IGNORECASE)
        end_func_pattern = re.compile(r'^\s*(?:КонецФункции|EndFunction)\s*$', re.IGNORECASE)
        
        # Паттерн для аннотаций расширений
        # Поддерживаем: &Перед, &После, &Вместо, &ИзменениеИКонтроль и English варианты
        annotation_pattern = re.compile(
            r'^\s*&(?:Перед|После|Вместо|ИзменениеИКонтроль|Before|After|Instead|ChangeAndValidate)\s*\(\s*["\']?(\w+)["\']?\s*\)',
            re.IGNORECASE
        )
        
        # Стек для отслеживания вложенных процедур/функций
        current_procedure = None
        current_function = None
        last_annotation = None  # Хранит информацию об аннотации с предыдущей строки
        
        # Собираем все имена процедур/функций текущего модуля для быстрой проверки
        # Делаем предварительный проход для сбора имен (быстрее чем перебирать все ключи индекса)
        current_module_names = set()
        for line in lines:
            proc_match = proc_pattern.match(line)
            if proc_match:
                current_module_names.add(proc_match.group(1))
            func_match = func_pattern.match(line)
            if func_match:
                current_module_names.add(func_match.group(1))
        
        for line_num, line in enumerate(lines, start=1):
            if current_procedure and end_proc_pattern.match(line):
                current_procedure['end_line'] = line_num
                if current_procedure['end_line'] <= current_procedure['start_line']:
                    current_procedure = None
                    continue
                
                proc_code_lines = lines[current_procedure['start_line'] - 1:current_procedure['end_line']]
                proc_code = '\n'.join(proc_code_lines)
                if extract_calls:
                    current_procedure['calls_to'] = self.extract_procedure_calls(
                        proc_code, obj_name, obj_type, current_procedure['name'], current_module_names
                    )
                else:
                    current_procedure['calls_to'] = []
                
                procedures.append(current_procedure)
                current_procedure = None
                continue
            
            if current_function and end_func_pattern.match(line):
                if line_num <= current_function['start_line']:
                    continue
                current_function['end_line'] = line_num
                
                # Извлекаем код функции и вызовы
                func_code_lines = lines[current_function['start_line'] - 1:current_function['end_line']]
                func_code = '\n'.join(func_code_lines)
                if extract_calls:
                    current_function['calls_to'] = self.extract_procedure_calls(
                        func_code, obj_name, obj_type, current_function['name'], current_module_names
                    )
                else:
                    current_function['calls_to'] = []
                
                functions.append(current_function)
                current_function = None
                continue
            
            # Проверяем аннотацию
            annotation_match = annotation_pattern.match(line)
            if annotation_match:
                annotation_text = line.strip()
                target_name = annotation_match.group(1)
                
                # Определяем тип аннотации
                annotation_type = ""
                if re.search(r'&(?:Перед|Before)', annotation_text, re.IGNORECASE):
                    annotation_type = "Перед"
                elif re.search(r'&(?:После|After)', annotation_text, re.IGNORECASE):
                    annotation_type = "После"
                elif re.search(r'&(?:Вместо|Instead)', annotation_text, re.IGNORECASE):
                    annotation_type = "Вместо"
                elif re.search(r'&(?:ИзменениеИКонтроль|ChangeAndValidate)', annotation_text, re.IGNORECASE):
                    # ИзменениеИКонтроль по умолчанию ведёт себя как "Вместо"
                    annotation_type = "Вместо"
                
                last_annotation = {
                    'type': annotation_type,
                    'target': target_name
                }
                continue
            
            # Ищем начало процедуры
            proc_match = proc_pattern.match(line)
            if proc_match:
                # ДОПОЛНИТЕЛЬНАЯ ВАЛИДАЦИЯ: Проверяем что строка действительно начинается с "Процедура" или "Procedure"
                line_stripped = line.strip()
                if not (line_stripped.startswith('Процедура') or line_stripped.startswith('Procedure') or 
                        line_stripped.lower().startswith('процедура') or line_stripped.lower().startswith('procedure')):
                    # Это не определение процедуры, пропускаем
                    continue
                
                name = proc_match.group(1)
                params_str = proc_match.group(2).strip()
                is_export = proc_match.group(3) is not None
                
                params = []
                if params_str:
                    params = [p.strip().split('=')[0].strip() for p in params_str.split(',')]
                
                # Проверяем аннотацию из кода или из XML
                annotation_type = ""
                annotation_target = ""
                
                if last_annotation:
                    # Есть аннотация в коде (&Перед, &После, &Вместо)
                    annotation_type = last_annotation['type']
                    annotation_target = last_annotation['target']
                elif name in form_events:
                    # Есть событие из XML формы
                    event_info = form_events[name]
                    annotation_type = event_info['annotation_type']
                    annotation_target = event_info['target_procedure']
                
                # ВАЛИДАЦИЯ: Если предыдущая процедура еще не закрыта - закрываем её сначала
                if current_procedure:
                    current_procedure['end_line'] = line_num - 1
                    
                    # Извлекаем код процедуры и вызовы
                    proc_code_lines = lines[current_procedure['start_line'] - 1:current_procedure['end_line']]
                    proc_code = '\n'.join(proc_code_lines)
                    if extract_calls:
                        current_procedure['calls_to'] = self.extract_procedure_calls(
                            proc_code, obj_name, obj_type, current_procedure['name'], current_module_names
                        )
                    else:
                        current_procedure['calls_to'] = []
                    
                    procedures.append(current_procedure)
                    current_procedure = None
                
                # Определяем реальное начало процедуры (с учетом директив &НаСервере, &НаКлиенте и комментариев)
                actual_start_line = self.find_procedure_start_line(lines, line_num)
                
                current_procedure = {
                    'name': name,
                    'is_export': is_export,
                    'is_function': False,
                    'params': params,
                    'start_line': actual_start_line,  # Реальное начало (первая строка с директивой/комментарием или сама строка с Процедура)
                    'end_line': 0,
                    'annotation_type': annotation_type,
                    'annotation_target': annotation_target
                }
                
                # Добавляем процедуру в индекс сразу при обнаружении
                if obj_name:
                    index_key = obj_name
                    if obj_type == 'ОбщийМодуль':
                        index_key = f"{obj_type}.{obj_name}"
                    if index_key not in self.procedures_index:
                        self.procedures_index[index_key] = set()
                    self.procedures_index[index_key].add(name)
                
                last_annotation = None  # Сбрасываем аннотацию
                continue
            
            # Ищем начало функции
            func_match = func_pattern.match(line)
            if func_match:
                # ДОПОЛНИТЕЛЬНАЯ ВАЛИДАЦИЯ: Проверяем что строка действительно начинается с "Функция" или "Function"
                line_stripped = line.strip()
                if not (line_stripped.startswith('Функция') or line_stripped.startswith('Function') or 
                        line_stripped.lower().startswith('функция') or line_stripped.lower().startswith('function')):
                    # Это не определение функции, пропускаем
                    continue
                
                name = func_match.group(1)
                params_str = func_match.group(2).strip()
                is_export = func_match.group(3) is not None
                
                params = []
                if params_str:
                    params = [p.strip().split('=')[0].strip() for p in params_str.split(',')]
                
                # Проверяем аннотацию из кода или из XML
                annotation_type = ""
                annotation_target = ""
                
                if last_annotation:
                    # Есть аннотация в коде (&Перед, &После, &Вместо)
                    annotation_type = last_annotation['type']
                    annotation_target = last_annotation['target']
                elif name in form_events:
                    # Есть событие из XML формы
                    event_info = form_events[name]
                    annotation_type = event_info['annotation_type']
                    annotation_target = event_info['target_procedure']
                
                # ВАЛИДАЦИЯ: Если предыдущая функция еще не закрыта - закрываем её сначала
                if current_function:
                    current_function['end_line'] = line_num - 1
                    
                    # Извлекаем код функции и вызовы
                    func_code_lines = lines[current_function['start_line'] - 1:current_function['end_line']]
                    func_code = '\n'.join(func_code_lines)
                    if extract_calls:
                        current_function['calls_to'] = self.extract_procedure_calls(
                            func_code, obj_name, obj_type, current_function['name'], current_module_names
                        )
                    else:
                        current_function['calls_to'] = []
                        
                    functions.append(current_function)
                    current_function = None
                
                # Находим реальное начало функции (с учетом директив &НаСервере, &НаКлиенте и комментариев)
                actual_start_line = self.find_procedure_start_line(lines, line_num)
                
                current_function = {
                    'name': name,
                    'is_export': is_export,
                    'is_function': True,
                    'params': params,
                    'start_line': actual_start_line,  # Реальное начало (первая строка с директивой/комментарием или сама строка с Функция)
                    'annotation_type': annotation_type,
                    'annotation_target': annotation_target
                }
                
                # Добавляем функцию в индекс сразу при обнаружении
                if obj_name:
                    index_key = obj_name
                    if obj_type == 'ОбщийМодуль':
                        index_key = f"{obj_type}.{obj_name}"
                    if index_key not in self.procedures_index:
                        self.procedures_index[index_key] = set()
                    self.procedures_index[index_key].add(name)
                
                last_annotation = None  # Сбрасываем аннотацию
                continue
            
            # Если не нашли процедуру/функцию после аннотации, сбрасываем
            if last_annotation and line.strip() and not line.strip().startswith('//'):
                last_annotation = None
        
        # Если не нашли закрывающие теги (плохой код), ставим конец файла
        if current_procedure:
            end_line = len(lines)
            if end_line > current_procedure['start_line']:
                current_procedure['end_line'] = end_line
                
                # Извлекаем код процедуры и вызовы
                proc_code_lines = lines[current_procedure['start_line'] - 1:current_procedure['end_line']]
                proc_code = '\n'.join(proc_code_lines)
                if extract_calls:
                    current_procedure['calls_to'] = self.extract_procedure_calls(
                        proc_code, obj_name, obj_type, current_procedure['name'], current_module_names
                    )
                else:
                    current_procedure['calls_to'] = []
                
                procedures.append(current_procedure)
        
        if current_function:
            end_line = len(lines)
            if end_line > current_function['start_line']:
                current_function['end_line'] = end_line
                
                # Извлекаем код функции и вызовы
                func_code_lines = lines[current_function['start_line'] - 1:current_function['end_line']]
                func_code = '\n'.join(func_code_lines)
                if extract_calls:
                    current_function['calls_to'] = self.extract_procedure_calls(
                        func_code, obj_name, obj_type, current_function['name'], current_module_names
                    )
                else:
                    current_function['calls_to'] = []
                
                functions.append(current_function)
        
        return procedures, functions

    def find_export_methods(self, code: str) -> List[str]:
        """Поиск экспортных процедур и функций (DEPRECATED, оставлено для совместимости)"""
        methods = []

        patterns = [
            r'(?:Процедура|Procedure)\s+(\w+)\s*\([^)]*\)\s*(?:Экспорт|Export)',
            r'(?:Функция|Function)\s+(\w+)\s*\([^)]*\)\s*(?:Экспорт|Export)'
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                method_name = match.group(1)
                if method_name not in methods:
                    methods.append(method_name)

        return methods

    def save_results(self):
        """Сохранение результатов парсинга"""
        print("\n💾 Сохранение результатов...")

        # 1. Сохраняем objects.csv для векторизации
        self.save_objects_csv()

        # 2. Создаем markdown файлы для RAG
        self.create_markdown_files()

        # 3. Сохраняем код модулей (и обновляем пути!)
        self.save_module_files()

        # 4. Сохраняем modules_metadata.json (ПОСЛЕ обновления путей!)
        self.save_modules_metadata()

        # 5. Сохраняем метаданные объектов
        self.save_metadata_files()

        # 5.1 Создаем markdown версии метаданных
        self.create_metadata_markdown()

        # 6. Создаем ZIP-архив с результатами
        # ОТКЛЮЧЕНО: архив создается в parser_ui.py с правильным именем
        # self.create_zip_archive()

        print("✅ Все результаты сохранены!")

    def create_zip_archive(self):
        """Создание ZIP-архива с результатами парсинга"""
        import zipfile
        from datetime import datetime
        
        # Имя архива с датой и временем
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"1c_parsed_{timestamp}.zip"
        archive_path = self.output_dir.parent / archive_name
        
        print(f"\n📦 Создание ZIP-архива...")
        
        try:
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Обходим все файлы в output_dir
                for root, dirs, files in os.walk(self.output_dir):
                    for file in files:
                        file_path = Path(root) / file
                        # Относительный путь внутри архива
                        arcname = file_path.relative_to(self.output_dir)
                        zipf.write(file_path, arcname)
                        
            # Получаем размер архива
            archive_size_mb = archive_path.stat().st_size / (1024 * 1024)
            
            print(f"  ✓ ZIP-архив создан: {archive_path}")
            print(f"  ✓ Размер: {archive_size_mb:.2f} MB")
            
        except Exception as e:
            print(f"  ⚠️ Ошибка создания архива: {e}")

    def save_objects_csv(self):
        """Сохранение CSV файла с объектами"""
        csv_file = self.output_dir / "objects.csv"

        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'object_type', 'object_name', 'friendly_name',
                'has_modules', 'modules_count', 'forms_count'
            ])

            for obj in self.objects:
                writer.writerow([
                    obj.object_type,
                    obj.object_name,
                    obj.friendly_name,
                    'true' if obj.modules else 'false',
                    len(obj.modules),
                    obj.forms_count
                ])

        print(f"  ✓ CSV: {csv_file}")

    def save_modules_metadata(self):
        """Сохранение метаданных о модулях (ПОСЛЕ обновления путей!)"""
        json_file = self.output_dir / "modules_metadata.json"

        modules_data = []
        for obj in self.objects:
            for module in obj.modules:
                # Конвертируем в dict (пути уже обновлены в save_module_files)
                module_dict = asdict(module)
                modules_data.append(module_dict)

        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(modules_data, f, ensure_ascii=False, indent=2)

        print(f"  ✓ JSON: {json_file} (с обновленными путями и процедурами/функциями)")
        
        # Проверка: показываем пример пути
        if modules_data:
            print(f"  ✓ Пример пути: {modules_data[0]['file_path']}")

    def create_markdown_files(self):
        """Создание markdown файлов для каждого объекта"""
        md_dir = self.output_dir / "markdown"
        md_dir.mkdir(exist_ok=True)

        for obj in self.objects:
            # Создаем каталог для типа объекта
            type_dir = md_dir / obj.object_type
            type_dir.mkdir(exist_ok=True)

            # Создаем markdown файл
            md_file = type_dir / f"{obj.object_name}.md"

            content = self.generate_object_markdown(obj)

            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(content)

        print(f"  ✓ Markdown файлы: {md_dir}")

    def generate_object_markdown(self, obj: ObjectInfo) -> str:
        """Генерация markdown описания объекта"""
        lines = []

        # Заголовок
        lines.append(f"# {obj.object_type}.{obj.object_name}")
        lines.append("")
        lines.append(f"**Синоним:** {obj.friendly_name}")
        lines.append(f"**Полное имя:** {obj.object_type}.{obj.object_name}")
        lines.append("")

        # Описание
        if obj.comment:
            lines.append("## Описание")
            lines.append("")
            lines.append(obj.comment)
            lines.append("")

        # Модули
        lines.append("## Модули")
        lines.append("")

        if obj.has_object_module:
            lines.append("- ✅ Модуль объекта")
        if obj.has_manager_module:
            lines.append("- ✅ Модуль менеджера")
        if any(m.module_type == "RecordSetModule" for m in obj.modules):
            lines.append("- ✅ Модуль набора записей")
        if obj.forms_count > 0:
            lines.append(f"- ✅ Формы с модулями: {obj.forms_count}")

        if not obj.modules:
            lines.append("- ❌ Модули отсутствуют")

        lines.append("")

        # НОВОЕ: Процедуры и функции (не только экспортные)
        for module in obj.modules:
            if module.procedures or module.functions:
                lines.append(f"### {module.module_type}")
                lines.append("")
                
                if module.procedures:
                    lines.append("**Процедуры:**")
                    for proc in module.procedures:
                        export_mark = " [Экспорт]" if proc['is_export'] else ""
                        params = ", ".join(proc['params'])
                        lines.append(f"- `{proc['name']}({params}){export_mark}` — строки {proc['start_line']}-{proc.get('end_line', '?')}")
                    lines.append("")
                
                if module.functions:
                    lines.append("**Функции:**")
                    for func in module.functions:
                        export_mark = " [Экспорт]" if func['is_export'] else ""
                        params = ", ".join(func['params'])
                        lines.append(f"- `{func['name']}({params}){export_mark}` — строки {func['start_line']}-{func.get('end_line', '?')}")
                    lines.append("")

        # Реквизиты
        if obj.attributes:
            lines.append("## Реквизиты")
            lines.append("")

            for attr in obj.attributes:
                name = attr.get('name', '')
                synonym = attr.get('synonym', name)
                lines.append(f"- **{name}** - {synonym}")

            lines.append("")

        # Статистика по коду
        if obj.modules:
            lines.append("## Статистика")
            lines.append("")
            total_lines = sum(m.lines_count for m in obj.modules)
            total_procedures = sum(len(m.procedures) for m in obj.modules)
            total_functions = sum(len(m.functions) for m in obj.modules)
            
            lines.append(f"- Всего строк кода: {total_lines}")
            lines.append(f"- Количество модулей: {len(obj.modules)}")
            lines.append(f"- Всего процедур: {total_procedures}")
            lines.append(f"- Всего функций: {total_functions}")

            server_modules = sum(1 for m in obj.modules if m.has_server_code)
            client_modules = sum(1 for m in obj.modules if m.has_client_code)

            if server_modules:
                lines.append(f"- Серверные модули: {server_modules}")
            if client_modules:
                lines.append(f"- Клиентские модули: {client_modules}")

            lines.append("")

        return "\n".join(lines)

    def save_module_files(self) -> Dict[str, str]:
        """Копирование файлов модулей в структурированные каталоги
        
        Returns:
            Словарь соответствия старых путей к новым
        """
        modules_dir = self.output_dir / "modules"
        modules_dir.mkdir(exist_ok=True)
        
        # Словарь для хранения соответствий старый_путь -> новый_путь
        path_mapping = {}

        for obj in self.objects:
            if not obj.modules:
                continue

            # Создаем каталог для объекта
            obj_dir = modules_dir / obj.object_type / obj.object_name
            obj_dir.mkdir(parents=True, exist_ok=True)

            for module in obj.modules:
                # Старый путь
                old_path = module.file_path
                
                # Копируем файл модуля
                source = self.config_dir / old_path

                # Определяем имя целевого файла
                if module.module_type == "ObjectModule":
                    target_name = "МодульОбъекта.bsl"
                elif module.module_type == "ManagerModule":
                    target_name = "МодульМенеджера.bsl"
                elif module.module_type == "ValueManagerModule":
                    target_name = "МодульМенеджераЗначения.bsl"
                elif module.module_type == "CommonModule":
                    target_name = "Модуль.bsl"
                elif module.module_type.startswith("FormModule") and obj.object_type == "ОбщаяФорма":
                    # Общая форма: объект и есть форма (CommonForms/<Имя>/Ext/Form/Module.bsl)
                    target_name = "МодульФормы.bsl"
                elif module.module_type.startswith("FormModule"):
                    # Извлекаем имя формы из пути
                    form_path = Path(old_path)
                    form_name = "Форма"
                    for part in form_path.parts:
                        if part == "Forms" or part == "Формы":
                            idx = form_path.parts.index(part)
                            if idx + 1 < len(form_path.parts):
                                form_name = form_path.parts[idx + 1]
                            break
                    target_name = f"МодульФормы_{form_name}.bsl"
                elif module.module_type == "CommandModule":
                    # Извлекаем имя команды из пути
                    command_path = Path(old_path)
                    command_name = "Команда"
                    for part in command_path.parts:
                        if part == "Commands" or part == "Команды":
                            idx = command_path.parts.index(part)
                            if idx + 1 < len(command_path.parts):
                                command_name = command_path.parts[idx + 1]
                            break
                    target_name = f"МодульКоманды_{command_name}.bsl"
                elif module.module_type == "RecordSetModule":
                    # Модуль набора записей для регистров
                    target_name = "МодульНабораЗаписей.bsl"
                else:
                    target_name = f"{module.module_type}.bsl"

                target = obj_dir / target_name

                # Используем модифицированный код (с аннотациями из XML) если есть
                if hasattr(module, 'modified_code') and module.modified_code:
                    code = module.modified_code
                else:
                    # Иначе читаем из исходного файла
                    with open(source, 'r', encoding='utf-8-sig') as f:
                        code = f.read()

                # Сохраняем код БЕЗ заголовочного комментария (чтобы не сдвигать нумерацию строк)
                with open(target, 'w', encoding='utf-8') as f:
                    f.write(code)
                
                # Новый путь (относительно output_dir)
                relative_path = target.relative_to(self.output_dir)
                new_path = str(relative_path).replace('\\', '/')
                
                # Сохраняем соответствие
                path_mapping[old_path] = new_path
                
                # ВАЖНО: Обновляем путь в объекте модуля
                module.file_path = new_path

        print(f"  ✓ Модули: {modules_dir}")
        print(f"  ✓ Обновлено путей: {len(path_mapping)}")
        
        return path_mapping
    
    def save_metadata_files(self):
        """Сохраняет метаданные объектов (только непустые поля, с русскими ключами)"""
        metadata_dir = self.output_dir / "metadata"
        metadata_dir.mkdir(exist_ok=True)

        # Безопасная проверка наличия и непустоты поля
        def has_value(obj, attr_name):
            if not hasattr(obj, attr_name):
                return False
            value = getattr(obj, attr_name)
            if value is None:
                return False
            if isinstance(value, (list, dict, str)):
                return bool(value)
            return True

        # Сопоставление английских имён с русскими
        FIELD_MAP = {
            "object_type": "ТипОбъекта",
            "object_name": "ИмяОбъекта",
            "friendly_name": "Синоним",
            "comment": "Комментарий",
            "standard_attributes": "СтандартныеРеквизиты",
            "owners": "Владельцы",
            "based_on": "ОснованНа",
            "register_records": "Регистры",
            "attributes": "Реквизиты",
            "tabular_sections": "ТабличныеЧасти",
            "periodicity": "Периодичность",
            "write_mode": "РежимЗаписи",
            "resources": "Ресурсы",
            "dimensions": "Измерения",
            "register_type": "ТипРегистра",
            "enum_values": "Значения",
            "properties": "Свойства"
        }

        # Перевод вложенных ключей
        INNER_MAP = {
            "name": "Имя",
            "synonym": "Синоним",
            "type": "Тип",
            "comment": "Комментарий",
            "indexing": "Индексирование",
            "fill_checking": "ПроверкаЗаполнения",
            "master": "Ведущее",
            "main_filter": "ОсновнойОтбор",
            "use_in_totals": "ИспользованиеВИтогах",
            "standard_attributes": "СтандартныеРеквизиты",
            "attributes": "Реквизиты",
            "tabular_sections": "ТабличныеЧасти",
            "resources": "Ресурсы",
            "dimensions": "Измерения"
        }

        def translate_keys(value):
            """Рекурсивно переводит ключи словарей внутри списков"""
            if isinstance(value, dict):
                new_dict = {}
                for k, v in value.items():
                    new_key = INNER_MAP.get(k, k)
                    new_dict[new_key] = translate_keys(v)
                return new_dict
            elif isinstance(value, list):
                return [translate_keys(v) for v in value]
            else:
                return value

        for obj in self.objects:
            obj_dir = metadata_dir / obj.object_type
            obj_dir.mkdir(exist_ok=True)

            metadata_file = obj_dir / f"{obj.object_name}.json"

            # Базовые поля всегда присутствуют
            metadata_dict = {
                FIELD_MAP["object_type"]: obj.object_type,
                FIELD_MAP["object_name"]: obj.object_name,
                FIELD_MAP["friendly_name"]: obj.friendly_name
            }

            # Добавляем только непустые значения
            for field_name in [
                "comment", "properties", "standard_attributes", "owners", "based_on", 
                "periodicity", "write_mode", "register_type", "register_records", 
                "attributes", "tabular_sections", "resources", "dimensions", "enum_values"
            ]:
                value = getattr(obj, field_name, None)
                if not value:
                    continue

                json_key = FIELD_MAP.get(field_name, field_name)
                # применяем перевод вложенных ключей
                metadata_dict[json_key] = translate_keys(value)

            # Сохраняем JSON с русскими ключами
            with open(metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata_dict, f, ensure_ascii=False, indent=2)

        print(f"  ✓ Метаданные сохранены: {metadata_dir}")

    def create_metadata_markdown(self):
        """Создает Markdown-версии метаданных объектов"""
        md_dir = self.output_dir / "metadata_markdown"
        md_dir.mkdir(exist_ok=True)

        def has_value(obj, attr_name):
            """Безопасно проверяет наличие непустого значения"""
            if not hasattr(obj, attr_name):
                return False
            value = getattr(obj, attr_name)
            if value is None:
                return False
            if isinstance(value, (list, dict, str)):
                return bool(value)
            return True

        FIELD_MAP = {
            "object_type": "Тип объекта",
            "object_name": "Имя объекта",
            "friendly_name": "Синоним",
            "comment": "Комментарий",
            "standard_attributes": "Стандартные реквизиты",
            "owners": "Владельцы",
            "based_on": "Основан на",
            "register_records": "Заполняемые регистры",
            "attributes": "Реквизиты",
            "tabular_sections": "Табличные части",
            "periodicity": "Периодичность",
            "write_mode": "Режим записи",
            "resources": "Ресурсы",
            "dimensions": "Измерения",
            "register_type": "Тип регистра",
            "enum_values": "Значения",
            "properties": "Свойства"
        }

        for obj in self.objects:
            obj_dir = md_dir / obj.object_type
            obj_dir.mkdir(exist_ok=True)
            md_file = obj_dir / f"{obj.object_name}.md"

            lines = [
                f"# {obj.object_type}.{obj.object_name}",
                "",
                f"**Синоним:** {obj.friendly_name or obj.object_name}",
                ""
            ]

            for field_name in [
                "comment", "properties", "owners", "based_on", "register_records",
                "periodicity", "write_mode", "register_type",
                "standard_attributes", "resources", "dimensions",
                "attributes", "tabular_sections", "enum_values"
            ]:
                if not has_value(obj, field_name):
                    continue

                title = FIELD_MAP.get(field_name, field_name)
                lines.append(f"## {title}")
                lines.append("")
                value = getattr(obj, field_name)

                # --- 1. Если просто текст ---
                if isinstance(value, str):
                    lines.append(value)

                # --- 2. Если список ---
                elif isinstance(value, list):
                    # список строк (обычные значения)
                    if all(isinstance(v, str) for v in value):
                        for item in value:
                            lines.append(f"- {item}")

                    # список словарей (реквизиты, ресурсы, табличные части и т.д.)
                    elif all(isinstance(v, dict) for v in value):
                        for item in value:
                            # Табличная часть (определяется по наличию "attributes")
                            if "attributes" in item:
                                name = item.get("name", "")
                                syn = item.get("synonym", "")
                                lines.append(f"### Табличная часть: {name} — {syn}")
                                lines.append("")
                                for tsa in item.get("attributes", []):
                                    tsa_name = tsa.get("name", "")
                                    tsa_syn = tsa.get("synonym", tsa_name)
                                    tsa_type = tsa.get("type", "")
                                    tsa_comment = tsa.get("comment", "")
                                    desc = f"({tsa_type})" if tsa_type else ""
                                    if tsa_comment:
                                        desc += f" — {tsa_comment}"
                                    lines.append(f"- **{tsa_name}** — {tsa_syn} {desc}")
                                lines.append("")

                            # Обычный реквизит / ресурс / измерение
                            else:
                                name = item.get("name", "")
                                syn = item.get("synonym", name)
                                type_ = item.get("type", "")
                                comment = item.get("comment", "")
                                suffix = f" ({type_})" if type_ else ""
                                if comment:
                                    suffix += f" — {comment}"
                                lines.append(f"- **{name}** — {syn}{suffix}")
                        lines.append("")

                    # неизвестная структура (fallback)
                    else:
                        for item in value:
                            lines.append(f"- {item}")

                lines.append("")

            if len(lines) <= 3:
                lines.append("_Нет доступных метаданных_")

            with open(md_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

        print(f"  ✓ Markdown метаданные сохранены: {md_dir}")





def main():
    """Точка входа"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Парсер выгруженной конфигурации 1С для RAG v2.0'
    )
    parser.add_argument(
        'config_dir',
        help='Путь к каталогу с выгруженной конфигурацией (результат DumpConfigToFiles)'
    )
    parser.add_argument(
        'output_dir',
        help='Путь к каталогу для сохранения обработанных данных'
    )

    args = parser.parse_args()

    # Проверяем существование входного каталога
    if not os.path.exists(args.config_dir):
        print(f"❌ Ошибка: каталог {args.config_dir} не существует")
        return 1

    # Создаем парсер и запускаем
    parser_obj = Config1CParser(args.config_dir, args.output_dir)
    parser_obj.parse()

    print("\n" + "=" * 50)
    print("🎉 Обработка завершена успешно!")
    print("=" * 50)
    print(f"\nРезультаты сохранены в: {args.output_dir}")
    print("\nСтруктура:")
    print("  ├── objects.csv            - Список объектов для индексации")
    print("  ├── modules_metadata.json  - Метаданные всех модулей + процедуры/функции")
    print("  ├── markdown/              - Описания объектов в Markdown")
    print("  └── modules/               - Код модулей с метаинформацией")
    print("\n✨ НОВОЕ в v2.0:")
    print("  - Извлечение ВСЕХ процедур и функций (не только экспортных)")
    print("  - Позиции процедур/функций в коде (start_line, end_line)")
    print("  - Параметры процедур/функций")
    print("  - Автоматическое создание ZIP-архива")
    print(f"\n📦 ZIP-архив готов к загрузке в Docker!")
    
    # Показываем путь к архиву
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"1c_parsed_{timestamp}.zip"
    archive_path = Path(args.output_dir).parent / archive_name
    if archive_path.exists():
        print(f"   {archive_path}")

    return 0


if __name__ == '__main__':
    exit(main())