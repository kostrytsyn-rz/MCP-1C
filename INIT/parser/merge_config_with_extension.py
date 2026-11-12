#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт объединения основной конфигурации 1С и расширения.
Интеллектуальное объединение метаданных, кода и других данных.
"""

import sys
import json
import shutil
import csv
import re
import codecs
from pathlib import Path
from typing import Dict, List

# Импортируем парсер
from parse_1c_conf import Config1CParser


class IntelligentConfigMerger:
    """Интеллектуальное объединение конфигураций"""
    
    def __init__(self, main_config_dir: str, extension_dir: str, output_dir: str):
        self.main_config_dir = Path(main_config_dir)
        self.extension_dir = Path(extension_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Используем имена папок источников для именования результатов
        self.main_name = self.main_config_dir.name
        self.ext_name = self.extension_dir.name
        
        self.main_output = self.output_dir / self.main_name
        self.ext_output = self.output_dir / self.ext_name
        self.merged_output = self.output_dir / "merged"
        
        # Кэш объединенных определяемых типов
        self.merged_defined_types = {}
    
    def run(self):
        """Основной процесс объединения"""
        print("=" * 70)
        print("🚀 Интеллектуальное объединение конфигурации 1С с расширением")
        print("=" * 70)
        
        # Проверяем наличие уже распарсенных данных
        main_already_parsed = self._is_already_parsed(self.main_output)
        ext_already_parsed = self._is_already_parsed(self.ext_output)
        
        # Шаг 1: Парсим и сохраняем основную конфигурацию
        if main_already_parsed:
            print(f"\n📂 Основная конфигурация уже распарсена")
            print(f"   ✓ Используем: {self.main_output}")
        else:
            print(f"\n📂 Парсинг основной конфигурации...")
            print(f"   Источник: {self.main_config_dir}")
            print(f"   Результат: {self.main_output}")
            parser_main = Config1CParser(str(self.main_config_dir), str(self.main_output))
            parser_main.parse()
            print(f"   ✓ Обработано: {len(parser_main.objects)} объектов")
            
            # Переименовываем архив основной конфигурации
            self._rename_archive(self.main_output, self.main_config_dir.name)
        
        # Шаг 2: Парсим и сохраняем расширение
        if ext_already_parsed:
            print(f"\n📦 Расширение уже распарсено")
            print(f"   ✓ Используем: {self.ext_output}")
        else:
            print(f"\n📦 Парсинг расширения...")
            print(f"   Источник: {self.extension_dir}")
            print(f"   Результат: {self.ext_output}")
            parser_ext = Config1CParser(str(self.extension_dir), str(self.ext_output))
            parser_ext.parse()
            print(f"   ✓ Обработано: {len(parser_ext.objects)} объектов")
            
            # Переименовываем архив расширения
            self._rename_archive(self.ext_output, self.extension_dir.name)
        
        # Шаг 3: Интеллектуальное объединение
        print(f"\n🔀 Интеллектуальное объединение...")
        print(f"   Результат: {self.merged_output}")
        self.intelligent_merge()
        
        print("\n" + "=" * 70)
        print("✅ Объединение завершено!")
        print("=" * 70)
        print(f"\n📁 Структура результата:")
        print(f"   {self.output_dir}/")
        print(f"   ├── {self.main_config_dir.name}/        (основная конфигурация)")
        print(f"   ├── {self.extension_dir.name}/         (расширение)")
        print(f"   └── merged/                (объединенный результат)")
    
    def _is_already_parsed(self, output_dir: Path) -> bool:
        """Проверяет, распарсена ли уже конфигурация"""
        if not output_dir.exists():
            return False
        
        # Проверяем наличие ключевых файлов
        required_files = [
            output_dir / "objects.csv",
            output_dir / "modules_metadata.json"
        ]
        
        required_dirs = [
            output_dir / "modules",
            output_dir / "metadata"
        ]
        
        for file in required_files:
            if not file.exists():
                return False
        
        for dir in required_dirs:
            if not dir.exists():
                return False
        
        return True
    
    def _rename_archive(self, output_dir: Path, config_name: str):
        """Переименовывает ZIP-архив по имени конфигурации"""
        # Ищем последний созданный архив
        parent_dir = output_dir.parent
        archives = list(parent_dir.glob("1c_parsed_*.zip"))
        
        if archives:
            # Берем последний по времени
            latest_archive = max(archives, key=lambda p: p.stat().st_mtime)
            # Переименовываем и перемещаем в папку конфигурации
            new_name = f"{config_name}.zip"
            new_path = output_dir / new_name
            shutil.move(str(latest_archive), str(new_path))
            print(f"   ✓ Архив: {new_name}")
    
    def intelligent_merge(self):
        """Интеллектуальное объединение всех данных"""
        self.merged_output.mkdir(parents=True, exist_ok=True)
        
        # 0. Объединяем определяемые типы (сначала, чтобы использовать при объединении метаданных)
        print("   [ТИПЫ] Объединение определяемых типов...")
        self.merge_defined_types()
        
        # 1. Объединяем метаданные (JSON)
        print("   [JSON] Объединение метаданных...")
        self.merge_metadata_json()
        
        # 2. Объединяем код модулей
        print("   [КОД] Объединение кода модулей...")
        self.merge_module_code()
        
        print("   [META] Перепарсинг объединенных модулей для актуальных границ...")
        self.reparse_merged_modules_metadata()
        
        # 4. Генерируем markdown из объединенных данных
        print("   [MD] Генерация markdown...")
        self.generate_merged_markdown()
        
        # 5. Объединяем CSV
        print("   [CSV] Объединение objects.csv...")
        self.merge_csv_files()
        
        # 6. Архив создаётся в parser_ui.py после всех объединений
        # print("   📦 Создание архива merged.zip...")
        # self._create_merged_archive()
        
        print("   ✅ Объединение завершено")
    
    def _create_merged_archive(self):
        """Создание ZIP-архива объединенного результата"""
        import zipfile
        
        archive_path = self.merged_output / "merged.zip"
        
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in self.merged_output.rglob('*'):
                if file_path.is_file() and file_path != archive_path:
                    arcname = file_path.relative_to(self.merged_output)
                    zipf.write(file_path, arcname)
        
        size_mb = archive_path.stat().st_size / (1024 * 1024)
        print(f"      ✓ Создан: merged.zip ({size_mb:.1f} MB)")
    
    def merge_defined_types(self):
        """
        Объединение определяемых типов из основной конфигурации и расширения.
        Создает кэш объединенных типов для использования при объединении метаданных.
        """
        metadata_main = self.main_output / "metadata"
        metadata_ext = self.ext_output / "metadata"
        metadata_out = self.merged_output / "metadata"
        metadata_out.mkdir(parents=True, exist_ok=True)
        
        defined_types_dir = metadata_out / "ОпределяемыеТипы"
        defined_types_dir.mkdir(parents=True, exist_ok=True)
        
        # Загружаем определяемые типы из основной конфигурации
        main_defined_types_dir = metadata_main / "ОпределяемыеТипы"
        if main_defined_types_dir.exists():
            for main_file in main_defined_types_dir.glob("*.json"):
                with open(main_file, 'r', encoding='utf-8') as f:
                    main_data = json.load(f)
                
                name = main_data.get("ИмяОбъекта", "")
                type_str = main_data.get("Тип", "")
                
                # Парсим типы из строки "Тип1, Тип2, Тип3"
                types_list = [t.strip() for t in type_str.split(',') if t.strip()]
                
                self.merged_defined_types[name] = {
                    "name": name,
                    "synonym": main_data.get("Синоним", ""),
                    "comment": main_data.get("Комментарий", ""),
                    "types": types_list
                }
        
        # Дополняем типы из расширения
        ext_defined_types_dir = metadata_ext / "ОпределяемыеТипы"
        if ext_defined_types_dir.exists():
            for ext_file in ext_defined_types_dir.glob("*.json"):
                with open(ext_file, 'r', encoding='utf-8') as f:
                    ext_data = json.load(f)
                
                name = ext_data.get("ИмяОбъекта", "")
                type_str = ext_data.get("Тип", "")
                
                # Парсим типы из строки
                ext_types_list = [t.strip() for t in type_str.split(',') if t.strip()]
                
                if name in self.merged_defined_types:
                    # Объединяем списки типов (union, без дубликатов)
                    existing_types = set(self.merged_defined_types[name]["types"])
                    new_types = set(ext_types_list)
                    merged_types = sorted(existing_types | new_types)
                    
                    self.merged_defined_types[name]["types"] = merged_types
                    
                    # Обновляем синоним и комментарий из расширения (если есть)
                    if ext_data.get("Синоним"):
                        self.merged_defined_types[name]["synonym"] = ext_data["Синоним"]
                    if ext_data.get("Комментарий"):
                        self.merged_defined_types[name]["comment"] = ext_data["Комментарий"]
                else:
                    # Новый определяемый тип только в расширении
                    self.merged_defined_types[name] = {
                        "name": name,
                        "synonym": ext_data.get("Синоним", ""),
                        "comment": ext_data.get("Комментарий", ""),
                        "types": ext_types_list
                    }
        
        # Сохраняем объединенные определяемые типы (JSON)
        for name, dt_data in self.merged_defined_types.items():
            out_file = defined_types_dir / f"{name}.json"
            
            result = {
                "ТипОбъекта": "ОпределяемыйТип",
                "ИмяОбъекта": dt_data["name"],
                "Синоним": dt_data["synonym"],
                "Тип": ", ".join(dt_data["types"]) if dt_data["types"] else "Неопределено"
            }
            
            if dt_data["comment"]:
                result["Комментарий"] = dt_data["comment"]
            
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        
        # Генерируем markdown для определяемых типов
        md_defined_types_dir = metadata_out.parent / "metadata_markdown" / "ОпределяемыеТипы"
        md_defined_types_dir.mkdir(parents=True, exist_ok=True)
        
        for name, dt_data in self.merged_defined_types.items():
            md_file = md_defined_types_dir / f"{name}.md"
            
            md_content = f"# {dt_data['synonym'] or name}\n\n"
            md_content += f"**Имя:** {name}\n\n"
            
            if dt_data["comment"]:
                md_content += f"**Комментарий:** {dt_data['comment']}\n\n"
            
            md_content += "## Типы\n\n"
            if dt_data["types"]:
                for t in dt_data["types"]:
                    md_content += f"- {t}\n"
            else:
                md_content += "Не определено\n"
            
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(md_content)
        
        print(f"      ✓ Объединено определяемых типов: {len(self.merged_defined_types)}")
    
    def merge_metadata_json(self):
        """Объединение JSON метаданных объектов"""
        metadata_main = self.main_output / "metadata"
        metadata_ext = self.ext_output / "metadata"
        metadata_out = self.merged_output / "metadata"
        metadata_out.mkdir(parents=True, exist_ok=True)
        
        # Копируем всю структуру из основной (кроме ОпределяемыеТипы - они уже объединены)
        if metadata_main.exists():
            for item in metadata_main.iterdir():
                # Пропускаем ОпределяемыеТипы - они уже объединены в merge_defined_types()
                if item.name == "ОпределяемыеТипы":
                    continue
                
                target = metadata_out / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
        
        # Объединяем с расширением
        if metadata_ext.exists():
            for ext_file in metadata_ext.rglob("*.json"):
                rel_path = ext_file.relative_to(metadata_ext)
                
                # Пропускаем ОпределяемыеТипы - они уже объединены
                if rel_path.parts[0] == "ОпределяемыеТипы":
                    continue
                
                main_file = metadata_main / rel_path
                out_file = metadata_out / rel_path
                
                if main_file.exists():
                    # Объект есть в обоих - объединяем
                    merged = self._merge_metadata_files(main_file, ext_file)
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_file, 'w', encoding='utf-8') as f:
                        json.dump(merged, f, ensure_ascii=False, indent=2)
                else:
                    # Объект только в расширении - копируем
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ext_file, out_file)
        
        # (даже в тех, которые не были переопределены в расширении)
        print("      Обновление типов из объединенных определяемых типов...")
        self._update_all_defined_types(metadata_out)
    
    def _update_all_defined_types(self, metadata_dir: Path):
        """
        Обновляет типы из объединенных определяемых типов во всех объектах метаданных.
        Это необходимо для объектов, которые не были переопределены в расширении,
        но используют определяемые типы, которые были расширены.
        """
        if not self.merged_defined_types:
            return
        
        for json_file in metadata_dir.rglob("*.json"):
            # Пропускаем сами определяемые типы
            if "ОпределяемыеТипы" in json_file.parts:
                continue
            
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                modified = False
                
                # Обновляем реквизиты
                if "Реквизиты" in data:
                    data["Реквизиты"] = self._update_attributes_types(data["Реквизиты"])
                    modified = True
                
                # Обновляем табличные части
                if "ТабличныеЧасти" in data:
                    for ts in data["ТабличныеЧасти"]:
                        if "Реквизиты" in ts:
                            ts["Реквизиты"] = self._update_attributes_types(ts["Реквизиты"])
                            modified = True
                
                # Обновляем ресурсы (для регистров)
                if "Ресурсы" in data:
                    data["Ресурсы"] = self._update_attributes_types(data["Ресурсы"])
                    modified = True
                
                # Обновляем измерения (для регистров)
                if "Измерения" in data:
                    data["Измерения"] = self._update_attributes_types(data["Измерения"])
                    modified = True
                
                # Обновляем значения (для перечислений)
                if "Значения" in data:
                    data["Значения"] = self._update_attributes_types(data["Значения"])
                    modified = True
                
                # Сохраняем если были изменения
                if modified:
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
            
            except Exception as e:
                print(f"      ВНИМАНИЕ: Ошибка при обновлении {json_file.name}: {e}")
    
    def _update_attributes_types(self, attributes: List[Dict]) -> List[Dict]:
        """Обновляет типы в списке атрибутов из объединенных определяемых типов"""
        for attr in attributes:
            defined_type_name = attr.get("defined_type", "")
            if defined_type_name and defined_type_name in self.merged_defined_types:
                # Получаем объединенный список типов
                merged_types = self.merged_defined_types[defined_type_name]["types"]
                if merged_types:
                    attr["Тип"] = ", ".join(merged_types)
        return attributes
    
    def _merge_metadata_files(self, main_file: Path, ext_file: Path) -> Dict:
        """Объединение двух JSON файлов метаданных"""
        with open(main_file, 'r', encoding='utf-8') as f:
            main_data = json.load(f)
        
        with open(ext_file, 'r', encoding='utf-8') as f:
            ext_data = json.load(f)
        
        # Базовые поля берем из расширения (если есть), иначе из основной
        result = {
            "ТипОбъекта": ext_data.get("ТипОбъекта") or main_data.get("ТипОбъекта"),
            "ИмяОбъекта": ext_data.get("ИмяОбъекта") or main_data.get("ИмяОбъекта"),
            "Синоним": ext_data.get("Синоним") or main_data.get("Синоним"),
        }
        
        # Комментарий - из расширения если есть
        if ext_data.get("Комментарий"):
            result["Комментарий"] = ext_data["Комментарий"]
        elif main_data.get("Комментарий"):
            result["Комментарий"] = main_data["Комментарий"]
        
        # Объединяем списковые поля
        for field in ["СтандартныеРеквизиты", "Владельцы", "ОснованНа", "Регистры"]:
            main_list = main_data.get(field, [])
            ext_list = ext_data.get(field, [])
            if main_list or ext_list:
                result[field] = list(set(main_list + ext_list))
        
        # Объединяем реквизиты (без дубликатов по имени)
        if main_data.get("Реквизиты") or ext_data.get("Реквизиты"):
            result["Реквизиты"] = self._merge_attributes(
                main_data.get("Реквизиты", []),
                ext_data.get("Реквизиты", [])
            )
        
        # Объединяем табличные части
        if main_data.get("ТабличныеЧасти") or ext_data.get("ТабличныеЧасти"):
            result["ТабличныеЧасти"] = self._merge_tabular_sections(
                main_data.get("ТабличныеЧасти", []),
                ext_data.get("ТабличныеЧасти", [])
            )
        
        # Для регистров - объединяем ресурсы и измерения
        for field in ["Ресурсы", "Измерения"]:
            if main_data.get(field) or ext_data.get(field):
                result[field] = self._merge_attributes(
                    main_data.get(field, []),
                    ext_data.get(field, [])
                )
        
        # Простые поля - приоритет у расширения
        for field in ["Периодичность", "РежимЗаписи", "ТипРегистра"]:
            val = ext_data.get(field) or main_data.get(field)
            if val:
                result[field] = val
        
        # Свойства - объединяем словари
        if main_data.get("Свойства") or ext_data.get("Свойства"):
            result["Свойства"] = {
                **main_data.get("Свойства", {}),
                **ext_data.get("Свойства", {})
            }
        
        # Значения перечислений
        if main_data.get("Значения") or ext_data.get("Значения"):
            result["Значения"] = self._merge_attributes(
                main_data.get("Значения", []),
                ext_data.get("Значения", [])
            )
        
        return result
    
    def _merge_attributes(self, main_attrs: List[Dict], ext_attrs: List[Dict]) -> List[Dict]:
        """
        Объединение реквизитов/ресурсов/измерений (без дубликатов по имени).
        Также обновляет типы из объединенных определяемых типов.
        """
        attrs_dict = {}
        
        for attr in main_attrs:
            name = attr.get("Имя", "")
            if name:
                attrs_dict[name] = attr
        
        for attr in ext_attrs:
            name = attr.get("Имя", "")
            if name:
                attrs_dict[name] = attr  # Перезапишет если есть
        
        # Обновляем типы из объединенных определяемых типов
        for attr_name, attr in attrs_dict.items():
            defined_type_name = attr.get("defined_type", "")
            if defined_type_name and defined_type_name in self.merged_defined_types:
                # Получаем объединенный список типов
                merged_types = self.merged_defined_types[defined_type_name]["types"]
                if merged_types:
                    attr["Тип"] = ", ".join(merged_types)
        
        return list(attrs_dict.values())
    
    def _merge_tabular_sections(self, main_ts: List[Dict], ext_ts: List[Dict]) -> List[Dict]:
        """Объединение табличных частей"""
        ts_dict = {}
        
        for ts in main_ts:
            name = ts.get("Имя", "")
            if name:
                ts_dict[name] = ts
        
        for ext_ts_item in ext_ts:
            name = ext_ts_item.get("Имя", "")
            if not name:
                continue
            
            if name in ts_dict:
                # ТЧ есть в обоих - объединяем реквизиты
                main_ts_item = ts_dict[name]
                merged_ts = {
                    "Имя": name,
                    "Синоним": ext_ts_item.get("Синоним") or main_ts_item.get("Синоним", name),
                    "Комментарий": ext_ts_item.get("Комментарий") or main_ts_item.get("Комментарий", ""),
                    "Реквизиты": self._merge_attributes(
                        main_ts_item.get("Реквизиты", []),
                        ext_ts_item.get("Реквизиты", [])
                    )
                }
                # Стандартные реквизиты
                main_std = main_ts_item.get("СтандартныеРеквизиты", [])
                ext_std = ext_ts_item.get("СтандартныеРеквизиты", [])
                if main_std or ext_std:
                    merged_ts["СтандартныеРеквизиты"] = list(set(main_std + ext_std))
                
                ts_dict[name] = merged_ts
            else:
                ts_dict[name] = ext_ts_item
        
        return list(ts_dict.values())
    
    def merge_module_code(self):
        """Объединение кода модулей"""
        modules_main = self.main_output / "modules"
        modules_ext = self.ext_output / "modules"
        modules_out = self.merged_output / "modules"
        modules_out.mkdir(parents=True, exist_ok=True)
        
        # Копируем всю структуру из основной
        if modules_main.exists():
            shutil.copytree(modules_main, modules_out, dirs_exist_ok=True)
        
        # Объединяем с расширением
        if modules_ext.exists():
            for ext_file in modules_ext.rglob("*.bsl"):
                rel_path = ext_file.relative_to(modules_ext)
                main_file = modules_main / rel_path
                out_file = modules_out / rel_path
                
                if main_file.exists():
                    # Модуль есть в обоих - объединяем код
                    merged_code = self._merge_module_files(main_file, ext_file)
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_file, 'w', encoding='utf-8') as f:
                        f.write(merged_code)
                else:
                    # Модуль только в расширении - копируем
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ext_file, out_file)
    
    def _extract_current_procedure_code(self, lines: list, proc_info: dict) -> str:
        """Извлекает текущий код процедуры из lines, включая комментарии расширений"""
        start_idx = proc_info['start_line'] - 1
        end_idx = proc_info['end_line']
        
        # Извлекаем код процедуры
        procedure_code = '\n'.join(lines[start_idx:end_idx])
        
        # Проверяем есть ли комментарии расширений ВНУТРИ тела процедуры
        # (для &ИзменениеИКонтроль комментарии находятся в теле, а не в заголовке)
        has_extension_markers = ('РАСШИРЕНИЕ' in procedure_code or 
                                'EXTENSION' in procedure_code or
                                'ДОБАВЛЕНО В РАСШИРЕНИИ' in procedure_code)
        
        if has_extension_markers:
            return procedure_code
        
        # Если внутри нет, проверяем комментарии ПЕРЕД процедурой (для заголовков)
        comment_start_idx = start_idx
        found_extension_comment = False
        
        for i in range(start_idx - 1, max(-1, start_idx - 30), -1):  # Проверяем до 30 строк назад
            line = lines[i].strip()
            
            # Если это комментарий расширения - отмечаем что нашли
            if line.startswith('//') and ('РАСШИРЕНИЕ' in line or 'EXTENSION' in line):
                comment_start_idx = i
                found_extension_comment = True
            # Если нашли комментарий расширения раньше, продолжаем собирать блок
            elif found_extension_comment and line.startswith('//'):
                comment_start_idx = i
            # Если найден комментарий расширения, но встретили не комментарий - останавливаемся
            elif found_extension_comment and not line.startswith('//') and line:
                break
        
        return '\n'.join(lines[comment_start_idx:end_idx])
    
    def _apply_incremental_change_and_control(self, current_code: str, ext_proc_code: str, proc_name: str) -> str:
        """
        Применяет операторы #Вставка/#Удаление из нового расширения 
        к процедуре, которая уже была изменена предыдущим расширением.
        
        БЕЗ добавления комментариев - вставки и так помечены своими комментариями.
        """
        # Извлекаем операторы #Вставка/#Удаление из кода расширения
        insertions, deletions = self._extract_insertion_deletion_operators(ext_proc_code)
        
        lines = current_code.split('\n')
        
        # Собираем все строки до процедуры (декораторы типа &НаСервере)
        before_procedure_lines = []
        procedure_start_idx = -1
        
        for i, line in enumerate(lines):
            # Нашли объявление процедуры - останавливаемся
            if re.match(r'^\s*(Процедура|Procedure|Функция|Function)\s+', line, re.IGNORECASE):
                procedure_start_idx = i
                break
            # Сохраняем только декораторы, НЕ заголовочные комментарии расширений (с '═══')
            # Но сохраняем комментарии вставок (с '───' без 'ИзменениеИКонтроль')
            strip_line = line.strip()
            is_extension_header = (strip_line.startswith('//') and '═══' in line and 
                                   ('ИзменениеИКонтроль' in line or 'РАСШИРЕНИЕ' in line))
            if not is_extension_header:
                before_procedure_lines.append(line)
        
        # Собираем финальный код БЕЗ комментариев расширений
        result_lines = before_procedure_lines + lines[procedure_start_idx:]
        result_code = '\n'.join(result_lines)
        
        # Применяем вставки
        for insertion_code, position_marker in insertions:
            result_code = self._apply_insertion(result_code, insertion_code, position_marker)
        
        # Применяем удаления
        for deletion_marker in deletions:
            result_code = self._apply_deletion(result_code, deletion_marker)
        
        return result_code
    
    def _extract_insertion_deletion_operators(self, ext_code: str) -> tuple:
        """
        Извлекает операторы #Вставка/#Удаление из кода расширения
        Возвращает: (insertions, deletions)
        """
        lines = ext_code.split('\n')
        insertions = []  # [(код_вставки, маркер_позиции), ...]
        deletions = []   # [маркер_удаления, ...]
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Ищем #Вставка
            if '#Вставка' in line or '#Insertion' in line:
                # Собираем код до #КонецВставки
                insertion_lines = []
                i += 1
                while i < len(lines) and '#КонецВставки' not in lines[i] and '#EndInsertion' not in lines[i]:
                    insertion_lines.append(lines[i])
                    i += 1
                
                # Определяем позицию вставки (смотрим на код до/после #Вставка)
                position_marker = self._find_position_marker(lines, i)
                insertions.append(('\n'.join(insertion_lines), position_marker))
            
            # Ищем #Удаление
            elif '#Удаление' in line or '#Deletion' in line:
                # Собираем строки до #КонецУдаления
                deletion_lines = []
                i += 1
                while i < len(lines) and '#КонецУдаления' not in lines[i] and '#EndDeletion' not in lines[i]:
                    deletion_lines.append(lines[i])
                    i += 1
                
                deletions.append('\n'.join(deletion_lines))
            
            i += 1
        
        return insertions, deletions
    
    def _find_position_marker(self, lines: list, current_pos: int) -> str:
        """Находит маркер позиции для вставки - следующую содержательную строку кода"""
        # Ищем следующую непустую строку после #КонецВставки
        for i in range(current_pos + 1, len(lines)):
            line = lines[i].strip()
            # Если нашли КонецПроцедуры/КонецФункции - значит вставка в конце
            if line.startswith('КонецПроцедуры') or line.startswith('КонецФункции') or \
               line.startswith('EndProcedure') or line.startswith('EndFunction'):
                return "END_OF_PROCEDURE"  # Специальный маркер для вставки в конце
            # Обычная строка кода - используем как маркер
            elif line and not line.startswith('//') and not line.startswith('#'):
                return line
        # Если ничего не нашли - вставка в начале
        return "START_OF_PROCEDURE"
    
    def _apply_insertion(self, code: str, insertion_code: str, position_marker: str) -> str:
        """Вставляет код в позицию, определяемую маркером"""
        lines = code.split('\n')
        comment = f"\t// ─── ДОБАВЛЕНО В РАСШИРЕНИИ [{self.ext_name}] ───\n"
        insertion = comment + insertion_code + f"\n\t// ─── КОНЕЦ ДОБАВЛЕНИЯ [{self.ext_name}] ───\n"
        insertion_lines = insertion.split('\n')
        
        # Обработка специальных маркеров
        if position_marker == "END_OF_PROCEDURE":
            # Вставка в конце процедуры (перед КонецПроцедуры/КонецФункции)
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()
                if line.startswith('КонецПроцедуры') or line.startswith('КонецФункции') or \
                   line.startswith('EndProcedure') or line.startswith('EndFunction'):
                    # Вставляем перед КонецПроцедуры
                    for j, ins_line in enumerate(insertion_lines):
                        lines.insert(i + j, ins_line)
                    break
            return '\n'.join(lines)
        
        elif position_marker == "START_OF_PROCEDURE":
            # Вставка в начале процедуры (после объявления)
            for i, line in enumerate(lines):
                if re.match(r'^\s*(Процедура|Procedure|Функция|Function)\s+', line, re.IGNORECASE):
                    for j, ins_line in enumerate(insertion_lines):
                        lines.insert(i + 1 + j, ins_line)
                    break
            return '\n'.join(lines)
        
        # Обычный маркер - ищем конкретную строку кода
        for i, line in enumerate(lines):
            if position_marker and position_marker.strip() in line:
                # Вставляем перед этой строкой
                for j, ins_line in enumerate(insertion_lines):
                    lines.insert(i + j, ins_line)
                break
        else:
            # Маркер не найден - по умолчанию вставляем в начало
            for i, line in enumerate(lines):
                if re.match(r'^\s*(Процедура|Procedure|Функция|Function)\s+', line, re.IGNORECASE):
                    for j, ins_line in enumerate(insertion_lines):
                        lines.insert(i + 1 + j, ins_line)
                    break
        
        return '\n'.join(lines)
    
    def _apply_deletion(self, code: str, deletion_marker: str) -> str:
        """Закомментирует код, соответствующий маркеру удаления"""
        # Для упрощения просто добавляем комментарий о том, что код был удален в расширении
        # Полная реализация требует более сложного сопоставления строк
        return code
    
    def _apply_change_and_control(self, ext_proc_code: str, proc_name: str) -> str:
        """
        Применяет изменения из процедуры с аннотацией &ИзменениеИКонтроль
        
        Берет ПОЛНОСТЬЮ процедуру из расширения и:
        1. Убирает аннотацию &ИзменениеИКонтроль
        2. Переименовывает процедуру на имя целевой процедуры (из аннотации)
        3. Убирает операторы #Вставка/#КонецВставки, #Удаление/#КонецУдаления
        4. Код в блоках #Удаление закомментирует с указанием расширения
        5. Добавляет комментарий о происхождении кода
        """
        ext_lines = ext_proc_code.split('\n')
        result_lines = []
        
        i = 0
        in_insertion = False
        in_deletion = False
        annotation_found = False
        
        while i < len(ext_lines):
            line = ext_lines[i]
            
            # Пропускаем аннотацию &ИзменениеИКонтроль
            if re.search(r'&(?:ИзменениеИКонтроль|ChangeAndValidate)\s*\(', line, re.IGNORECASE):
                annotation_found = True
                i += 1
                continue
            
            # Заменяем имя процедуры/функции на целевое имя
            proc_match = re.match(r'^(\s*)(Процедура|Procedure|Функция|Function)\s+(\w+)(\s*\(.*)', line, re.IGNORECASE)
            if proc_match and annotation_found:
                indent = proc_match.group(1)
                keyword = proc_match.group(2)
                rest = proc_match.group(4)
                # Заменяем имя процедуры/функции на целевое
                result_lines.append(f"{indent}{keyword} {proc_name}{rest}")
                annotation_found = False  # Сбрасываем флаг после обработки
                i += 1
                continue
            
            # Проверяем начало блока вставки
            if '#Вставка' in line or '#Insertion' in line:
                in_insertion = True
                # Убираем строку с #Вставка, но добавляем комментарий
                result_lines.append(f"\t// ─── ДОБАВЛЕНО В РАСШИРЕНИИ [{self.ext_name}] ───")
                i += 1
                continue
            
            # Проверяем конец блока вставки
            if '#КонецВставки' in line or '#EndInsertion' in line:
                in_insertion = False
                result_lines.append(f"\t// ─── КОНЕЦ ДОБАВЛЕНИЯ [{self.ext_name}] ───")
                i += 1
                continue
            
            # Проверяем начало блока удаления
            if '#Удаление' in line or '#Deletion' in line:
                in_deletion = True
                # Добавляем комментарий о закомментированном коде
                result_lines.append(f"\t// ═══ ЗАКОММЕНТИРОВАНО В РАСШИРЕНИИ [{self.ext_name}] ═══")
                i += 1
                continue
            
            # Проверяем конец блока удаления
            if '#КонецУдаления' in line or '#EndDeletion' in line:
                in_deletion = False
                result_lines.append(f"\t// ═══ КОНЕЦ ЗАКОММЕНТИРОВАННОГО КОДА [{self.ext_name}] ═══")
                i += 1
                continue
            
            # Обычная строка
            if in_deletion:
                # Закомментируем строку
                if line.strip():
                    # Сохраняем отступы
                    indent = len(line) - len(line.lstrip())
                    commented_line = ' ' * indent + '//' + line.lstrip()
                    result_lines.append(commented_line)
                else:
                    result_lines.append(line)
            else:
                # Обычная строка или строка из блока вставки - оставляем как есть
                result_lines.append(line)
            
            i += 1
        
        return '\n'.join(result_lines)
    
    def _merge_module_files(self, main_file: Path, ext_file: Path) -> str:
        """
        Интеллектуальное объединение двух файлов модулей с учетом аннотаций.
        Поддержка: &Перед, &После, &Вместо, &ИзменениеИКонтроль
        """
        # Выводим информацию о файле (закомментировано - избыточно)
        # rel_path = main_file.relative_to(self.main_output / "modules")
        # print(f"\n   📝 {rel_path}")
        
        with open(main_file, 'r', encoding='utf-8') as f:
            main_code = f.read()
        
        with open(ext_file, 'r', encoding='utf-8') as f:
            ext_code = f.read()
        
        # ОПТИМИЗИРОВАНО: Используем легковесный парсинг только для объединения
        # (финальные границы будут перепарсены в reparse_merged_modules_metadata)
        parser = Config1CParser(str(main_file.parent), str(main_file.parent))
        main_procs, main_funcs = parser.parse_all_procedures_functions(main_code)
        ext_procs, ext_funcs = parser.parse_all_procedures_functions(ext_code)
        
        # Объединяем все процедуры и функции для удобства
        main_all = main_procs + main_funcs
        ext_all = ext_procs + ext_funcs
        
        # Создаем словарь процедур основной конфы: {имя: процедура}
        # ПРИМЕЧАНИЕ: Границы здесь используются только для вставки/замены во время объединения
        # Финальные актуальные границы будут получены в reparse_merged_modules_metadata()
        main_proc_dict = {proc['name']: proc for proc in main_all}
        
        # Результирующий код
        result_lines = main_code.split('\n')
        
        # Список процедур из расширения для добавления в конец (без аннотаций)
        procs_to_append = []
        
        # Отслеживаем процедуры, которые были полностью заменены
        replaced_procedures = set()
        
        # Группируем процедуры по типу аннотации для правильной приоритетности
        change_control_procs = []  # &ИзменениеИКонтроль (Вместо с операторами)
        vmesto_procs = []           # &Вместо (без операторов)
        pered_procs = []            # &Перед
        posle_procs = []            # &После
        no_annotation_procs = []    # Без аннотаций
        
        for ext_proc in ext_all:
            annotation_type = ext_proc.get('annotation_type', '')
            annotation_target = ext_proc.get('annotation_target', '')
            ext_proc_code = self._extract_procedure_code(ext_code, ext_proc)
            
            if annotation_type and annotation_target:
                if annotation_type == "Вместо":
                    # Проверяем наличие операторов #Вставка/#Удаление
                    if '#Вставка' in ext_proc_code or '#Deletion' in ext_proc_code or '#Удаление' in ext_proc_code or '#Insertion' in ext_proc_code:
                        change_control_procs.append((ext_proc, ext_proc_code))
                    else:
                        vmesto_procs.append((ext_proc, ext_proc_code))
                elif annotation_type == "Перед":
                    pered_procs.append((ext_proc, ext_proc_code))
                elif annotation_type == "После":
                    posle_procs.append((ext_proc, ext_proc_code))
            else:
                no_annotation_procs.append((ext_proc, ext_proc_code))
        
        # Обрабатываем в порядке приоритета:
        
        # 1. &ИзменениеИКонтроль (Вместо с операторами)
        # print(f"   • Обработка &ИзменениеИКонтроль: {len(change_control_procs)} процедур")
        for ext_proc, ext_proc_code in change_control_procs:
            annotation_target = ext_proc.get('annotation_target', '')
            if annotation_target in main_proc_dict:
                target_proc = main_proc_dict[annotation_target]
                
                # Проверяем, была ли процедура уже обработана через &ИзменениеИКонтроль
                current_proc_code = self._extract_current_procedure_code(result_lines, target_proc)
                
                # Ищем маркеры расширений (комментарии вставок или заголовки)
                has_extension_insertions = 'ДОБАВЛЕНО В РАСШИРЕНИИ' in current_proc_code
                has_extension_headers = ('// РАСШИРЕНИЕ' in current_proc_code and 'ИзменениеИКонтроль' in current_proc_code)
                is_already_change_control = has_extension_insertions or has_extension_headers
                
                if is_already_change_control:
                    # Процедура уже была изменена - применяем операторы инкрементально
                    # print(f"      ⊕ {annotation_target} - дополняем существующие изменения")
                    modified_code = self._apply_incremental_change_and_control(current_proc_code, ext_proc_code, annotation_target)
                    # modified_code УЖЕ содержит все комментарии - НЕ добавляем их снова!
                    result_lines = self._replace_procedure_with_code(
                        result_lines, target_proc, modified_code, annotation_target, is_change_control=False
                    )
                else:
                    # Первое изменение - применяем как обычно
                    # print(f"      ✓ {annotation_target}")
                    modified_code = self._apply_change_and_control(ext_proc_code, annotation_target)
                    # Добавляем комментарий через is_change_control=True
                    result_lines = self._replace_procedure_with_code(
                        result_lines, target_proc, modified_code, annotation_target, is_change_control=True
                    )
                replaced_procedures.add(annotation_target)
                
                result_code = '\n'.join(result_lines)
                updated_procs, updated_funcs = parser.parse_all_procedures_functions(result_code)
                main_proc_dict = {p['name']: p for p in updated_procs + updated_funcs}
            else:
                warning = f"// ⚠️ ВНИМАНИЕ [{self.ext_name}]: Целевая процедура '{annotation_target}' не найдена\n"
                procs_to_append.append(warning + ext_proc_code)
        
        # 2. &Вместо (без операторов) - полная замена
        # print(f"   • Обработка &Вместо: {len(vmesto_procs)} процедур")
        for ext_proc, ext_proc_code in vmesto_procs:
            annotation_target = ext_proc.get('annotation_target', '')
            if annotation_target in replaced_procedures:
                # print(f"      ⊘ Пропускаем {annotation_target} - уже заменена через &ИзменениеИКонтроль")
                continue
            if annotation_target in main_proc_dict:
                target_proc = main_proc_dict[annotation_target]
                result_lines = self._replace_procedure(
                    result_lines, target_proc, ext_proc_code, annotation_target
                )
                replaced_procedures.add(annotation_target)
                # print(f"      ✓ {annotation_target}")
                
                # Пересканируем процедуры
                result_code = '\n'.join(result_lines)
                updated_procs, updated_funcs = parser.parse_all_procedures_functions(result_code)
                main_proc_dict = {p['name']: p for p in updated_procs + updated_funcs}
            else:
                warning = f"// ⚠️ ВНИМАНИЕ [{self.ext_name}]: Целевая процедура '{annotation_target}' не найдена\n"
                procs_to_append.append(warning + ext_proc_code)
        
        # 3. &Перед - теперь работает с ОБНОВЛЕННЫМИ процедурами!
        # print(f"   • Обработка &Перед: {len(pered_procs)} процедур")
        for ext_proc, ext_proc_code in pered_procs:
            annotation_target = ext_proc.get('annotation_target', '')
            if annotation_target in main_proc_dict:
                target_proc = main_proc_dict[annotation_target]
                result_lines = self._insert_before_procedure(
                    result_lines, target_proc, ext_proc_code, annotation_target
                )
                # print(f"      ✓ {annotation_target}")
                
                # Пересканируем процедуры
                result_code = '\n'.join(result_lines)
                updated_procs, updated_funcs = parser.parse_all_procedures_functions(result_code)
                main_proc_dict = {p['name']: p for p in updated_procs + updated_funcs}
            else:
                warning = f"// ⚠️ ВНИМАНИЕ [{self.ext_name}]: Целевая процедура '{annotation_target}' не найдена\n"
                procs_to_append.append(warning + ext_proc_code)
        
        # 4. &После - теперь работает с ОБНОВЛЕННЫМИ процедурами!
        # print(f"   • Обработка &После: {len(posle_procs)} процедур")
        for ext_proc, ext_proc_code in posle_procs:
            annotation_target = ext_proc.get('annotation_target', '')
            if annotation_target in main_proc_dict:
                target_proc = main_proc_dict[annotation_target]
                result_lines = self._insert_after_procedure(
                    result_lines, target_proc, ext_proc_code, annotation_target
                )
                # print(f"      ✓ {annotation_target}")
                
                # Пересканируем процедуры
                result_code = '\n'.join(result_lines)
                updated_procs, updated_funcs = parser.parse_all_procedures_functions(result_code)
                main_proc_dict = {p['name']: p for p in updated_procs + updated_funcs}
            else:
                warning = f"// ⚠️ ВНИМАНИЕ [{self.ext_name}]: Целевая процедура '{annotation_target}' не найдена\n"
                procs_to_append.append(warning + ext_proc_code)
        
        # 5. Процедуры без аннотаций - добавляем в конец
        for ext_proc, ext_proc_code in no_annotation_procs:
            procs_to_append.append(ext_proc_code)
        
        # Добавляем процедуры без аннотаций в конец
        if procs_to_append:
            separator = "\n\n" + "=" * 80 + "\n"
            separator += f"// ДОБАВЛЕНО ИЗ РАСШИРЕНИЯ [{self.ext_name}] (процедуры без аннотаций)\n"
            separator += "=" * 80 + "\n\n"
            result_lines.append(separator)
            result_lines.extend(procs_to_append)
        
        return '\n'.join(result_lines)
    
    def _extract_procedure_code(self, code: str, proc_info: dict) -> str:
        """Извлекает код процедуры/функции по информации о ней"""
        lines = code.split('\n')
        start = proc_info['start_line'] - 1
        end = proc_info['end_line']
        
        # Проверяем, есть ли аннотация перед процедурой
        if proc_info.get('annotation_type'):
            # Включаем строку с аннотацией
            if start > 0:
                start -= 1
        
        return '\n'.join(lines[start:end])
    
    def _insert_before_procedure(self, lines: list, target_proc: dict, insert_code: str, target_name: str) -> list:
        """Вставляет код В НАЧАЛО целевой процедуры (внутрь)"""
        # Находим строку после объявления процедуры (первая строка тела)
        target_line = target_proc['start_line']  # Следующая строка после "Процедура..."
        
        # Извлекаем только тело процедуры из расширения (без объявления и КонецПроцедуры)
        ext_body = self._extract_procedure_body(insert_code)
        
        comment = f"\t// ───────────────────────────────────────────────────────────────\n"
        comment += f"\t// РАСШИРЕНИЕ [{self.ext_name}]: &Перед(\"{target_name}\")\n"
        comment += f"\t// ───────────────────────────────────────────────────────────────\n"
        insert_lines = (comment + ext_body + f"\n\t// ───────────── КОНЕЦ КОДА ИЗ РАСШИРЕНИЯ [{self.ext_name}] ─────────────\n").split('\n')
        
        return lines[:target_line] + insert_lines + lines[target_line:]
    
    def _insert_after_procedure(self, lines: list, target_proc: dict, insert_code: str, target_name: str) -> list:
        """Вставляет код В КОНЕЦ целевой процедуры (внутрь, перед КонецПроцедуры)"""
        # Находим строку перед КонецПроцедуры/КонецФункции
        target_line = target_proc['end_line'] - 1  # Перед строкой "КонецПроцедуры"
        
        # Извлекаем только тело процедуры из расширения
        ext_body = self._extract_procedure_body(insert_code)
        
        comment = f"\n\t// ───────────────────────────────────────────────────────────────\n"
        comment += f"\t// РАСШИРЕНИЕ [{self.ext_name}]: &После(\"{target_name}\")\n"
        comment += f"\t// ───────────────────────────────────────────────────────────────\n"
        insert_lines = (comment + ext_body + f"\n\t// ───────────── КОНЕЦ КОДА ИЗ РАСШИРЕНИЯ [{self.ext_name}] ─────────────\n").split('\n')
        
        return lines[:target_line] + insert_lines + lines[target_line:]
    
    def _replace_procedure_with_code(self, lines: list, target_proc: dict, modified_code: str, target_name: str, is_change_control: bool = False) -> list:
        """
        Заменяет процедуру на модифицированный код (для &ИзменениеИКонтроль)
        """
        # target_proc['start_line'] - номер строки с "Процедура" (нумерация с 1)
        # target_proc['end_line'] - номер строки с "КонецПроцедуры" (нумерация с 1)
        # Преобразуем в индексы массива (нумерация с 0)
        start_idx = target_proc['start_line'] - 1  # Индекс строки с "Процедура"
        end_idx = target_proc['end_line'] - 1      # Индекс строки с "КонецПроцедуры"
        
        # Ищем комментарии расширений перед процедурой и удаляем их
        # (они будут заменены новыми или сохранены в modified_code)
        comment_start_idx = start_idx
        for i in range(start_idx - 1, -1, -1):
            line = lines[i].strip()
            if not line:
                break
            if line.startswith('//'):
                if 'РАСШИРЕНИЕ' in line or 'EXTENSION' in line or '═══' in line or '───' in line:
                    comment_start_idx = i
                else:
                    break
            else:
                break
        
        # Для &ИзменениеИКонтроль НЕ добавляем большие комментарии -
        # вставки и так помечены своими комментариями
        insert_lines = modified_code.split('\n')
        
        # Заменяем строки с comment_start_idx по end_idx включительно
        # modified_code уже содержит "Процедура" и "КонецПроцедуры" с правильным именем
        return lines[:comment_start_idx] + insert_lines + lines[end_idx + 1:]
    
    def _replace_procedure(self, lines: list, target_proc: dict, insert_code: str, target_name: str) -> list:
        """
        ЗАМЕНЯЕТ тело процедуры кодом из расширения (оставляя объявление).
        При этом СОХРАНЯЕТ секции других расширений (&Перед, &После).
        """
        # Оставляем строку с объявлением процедуры
        start_line = target_proc['start_line']  # Первая строка после объявления
        end_line = target_proc['end_line'] - 1  # Перед КонецПроцедуры
        
        # Извлекаем текущее тело процедуры и ищем в нём секции расширений
        current_body_lines = lines[start_line:end_line]
        
        # Находим и сохраняем секции других расширений
        extension_sections = self._extract_extension_sections(current_body_lines)
        
        # Извлекаем только тело процедуры из нового расширения
        ext_body = self._extract_procedure_body(insert_code)
        
        # Формируем новое тело: сначала код из &Вместо
        comment = f"\t// ═══════════════════════════════════════════════════════════════\n"
        comment += f"\t// РАСШИРЕНИЕ [{self.ext_name}]: &Вместо(\"{target_name}\") - КОД ЗАМЕНЕН\n"
        comment += f"\t// ═══════════════════════════════════════════════════════════════\n"
        new_body = comment + ext_body + f"\n\t// ═══════════════ КОНЕЦ КОДА ИЗ РАСШИРЕНИЯ [{self.ext_name}] ════════════\n"
        
        # Добавляем сохраненные секции расширений в конец
        if extension_sections['after']:
            new_body += '\n'.join(extension_sections['after'])
        
        insert_lines = new_body.split('\n')
        
        return lines[:start_line] + insert_lines + lines[end_line:]
    
    def _extract_extension_sections(self, body_lines: list) -> dict:
        """
        Извлекает секции расширений из текущего тела процедуры.
        Возвращает словарь с секциями 'before' и 'after'.
        """
        sections = {'before': [], 'after': []}
        
        in_extension_section = False
        current_section = []
        section_type = None
        
        for line in body_lines:
            # Проверяем начало секции расширения
            if '// РАСШИРЕНИЕ [' in line or '// РАСШИРЕНИЕ[' in line:
                in_extension_section = True
                current_section = [line]
                # Определяем тип секции
                if '&Перед' in line or '&Before' in line:
                    section_type = 'before'
                elif '&После' in line or '&After' in line:
                    section_type = 'after'
                continue
            
            # Проверяем конец секции расширения
            if in_extension_section:
                current_section.append(line)
                # Конец секции - строка с "КОНЕЦ КОДА" или "END OF" и дефисами
                if (('КОНЕЦ КОДА ИЗ РАСШИРЕНИЯ' in line or 'END OF EXTENSION CODE' in line) and 
                    '─' in line):
                    # Сохраняем секцию
                    if section_type:
                        sections[section_type].extend(current_section)
                        sections[section_type].append('')  # Пустая строка после секции
                    in_extension_section = False
                    current_section = []
                    section_type = None
        
        return sections
    
    def _extract_procedure_body(self, proc_code: str) -> str:
        """
        Извлекает только тело процедуры (без объявления и КонецПроцедуры/КонецФункции)
        """
        lines = proc_code.split('\n')
        
        # Пропускаем аннотацию и объявление процедуры
        start_idx = 0
        for i, line in enumerate(lines):
            if re.match(r'^\s*(?:Процедура|Procedure|Функция|Function)\s+', line, re.IGNORECASE):
                start_idx = i + 1
                break
        
        # Находим конец (КонецПроцедуры/КонецФункции)
        end_idx = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if re.match(r'^\s*(?:КонецПроцедуры|EndProcedure|КонецФункции|EndFunction)\s*$', lines[i], re.IGNORECASE):
                end_idx = i
                break
        
        # Извлекаем тело
        body_lines = lines[start_idx:end_idx]
        
        return '\n'.join(body_lines)
    
    def merge_modules_metadata(self):
        """
        УСТАРЕЛО: Объединение modules_metadata.json (не используется, т.к. границы неактуальны после объединения кода).
        Оставлено для обратной совместимости.
        """
        # Этот метод больше не используется, т.к. границы процедур изменяются после объединения кода
        # Вместо этого используется reparse_merged_modules_metadata() после объединения кода
        pass
    
    def reparse_merged_modules_metadata(self):
        """Перепарсивает объединенные модули для получения актуальных границ процедур/функций после объединения кода"""
        modules_dir = self.merged_output / "modules"
        json_out = self.merged_output / "modules_metadata.json"
        
        if not modules_dir.exists():
            print(f"   ⚠️ Папка modules не найдена: {modules_dir}")
            return
        
        if not self.merged_output.exists():
            print(f"   ⚠️ Выходная папка не найдена: {self.merged_output}")
            return
        
        # Парсер для перепарсинга объединенных модулей
        parser = Config1CParser(str(self.merged_output), str(self.merged_output))
        parser.objects = []  # Не парсим объекты, только модули
        
        # Читаем objects.csv для получения списка объектов
        objects_csv = self.merged_output / "objects.csv"
        if not objects_csv.exists():
            print(f"   ⚠️ objects.csv не найден, пропускаем перепарсинг")
            return
        
        # Читаем объекты из CSV
        import csv
        objects_info = []
        with open(objects_csv, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                objects_info.append({
                    'object_type': row['object_type'],
                    'object_name': row['object_name']
                })
        
        # Перепарсиваем все модули
        modules_metadata = []
        processed_modules = 0
        
        for obj_info in objects_info:
            obj_type = obj_info['object_type']
            obj_name = obj_info['object_name']
            obj_modules_dir = modules_dir / obj_type / obj_name
            
            if not obj_modules_dir.exists():
                continue
            
            # Перепарсиваем каждый модуль
            for module_file in obj_modules_dir.glob("*.bsl"):
                try:
                    with open(module_file, 'r', encoding='utf-8-sig') as f:
                        module_code = f.read()
                    
                    # Парсим процедуры и функции с правильными границами
                    procedures, functions = parser.parse_all_procedures_functions(module_code)
                    
                    # Определяем тип модуля из имени файла
                    module_type = module_file.stem
                    
                    # Формируем метаданные модуля
                    module_meta = {
                        'object_type': obj_type,
                        'object_name': obj_name,
                        'module_type': module_type,
                        'file_path': str(module_file.relative_to(self.merged_output)),
                        'lines_count': len(module_code.split('\n')),
                        'export_methods': [
                            p['name'] for p in procedures + functions if p.get('is_export', False)
                        ],
                        'has_server_code': '&НаСервере' in module_code or '&AtServer' in module_code,
                        'has_client_code': '&НаКлиенте' in module_code or '&AtClient' in module_code,
                        'procedures': procedures,
                        'functions': functions
                    }
                    
                    modules_metadata.append(module_meta)
                    processed_modules += 1
                except Exception as e:
                    continue
        
        # Сохраняем обновленные метаданные
        try:
            with open(json_out, 'w', encoding='utf-8') as f:
                json.dump(modules_metadata, f, ensure_ascii=False, indent=2)
            print(f"   ✅ Сохранено {processed_modules} модулей в {json_out}")
        except Exception as e:
            print(f"   ❌ Ошибка сохранения modules_metadata.json: {e}")
            import traceback
            traceback.print_exc()
    
    def generate_merged_markdown(self):
        """Генерация markdown из объединенных метаданных"""
        print("      Регенерация metadata_markdown из объединенных JSON...")
        
        # Регенерируем metadata_markdown из объединенных JSON метаданных
        metadata_dir = self.merged_output / "metadata"
        mdm_out = self.merged_output / "metadata_markdown"
        
        # Создаем папку для markdown
        mdm_out.mkdir(parents=True, exist_ok=True)
        
        # Обрабатываем все типы объектов
        if metadata_dir.exists():
            for obj_type_dir in metadata_dir.iterdir():
                if not obj_type_dir.is_dir():
                    continue
                
                # Пропускаем ОпределяемыеТипы - для них markdown уже создан
                if obj_type_dir.name == "ОпределяемыеТипы":
                    continue
                
                # Создаем папку для этого типа объектов
                md_type_dir = mdm_out / obj_type_dir.name
                md_type_dir.mkdir(parents=True, exist_ok=True)
                
                # Генерируем markdown для каждого объекта
                for json_file in obj_type_dir.glob("*.json"):
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            obj_data = json.load(f)
                        
                        md_content = self._generate_object_markdown(obj_data)
                        
                        md_file = md_type_dir / f"{json_file.stem}.md"
                        with open(md_file, 'w', encoding='utf-8') as f:
                            f.write(md_content)
                    
                    except Exception as e:
                        print(f"      ВНИМАНИЕ: Ошибка генерации markdown для {json_file.name}: {e}")
        
        # Копируем markdown модулей из основной конфигурации как базу
        md_main = self.main_output / "markdown"
        md_out = self.merged_output / "markdown"
        
        if md_main.exists():
            shutil.copytree(md_main, md_out, dirs_exist_ok=True)
    
    def _generate_object_markdown(self, obj_data: Dict) -> str:
        """Генерирует markdown для объекта из его JSON метаданных"""
        obj_type = obj_data.get("ТипОбъекта", "Объект")
        obj_name = obj_data.get("ИмяОбъекта", "Без имени")
        synonym = obj_data.get("Синоним", obj_name)
        
        md = f"# {obj_type}.{obj_name}\n\n"
        md += f"**Синоним:** {synonym}\n\n"
        
        # Комментарий
        if obj_data.get("Комментарий"):
            md += f"**Комментарий:** {obj_data['Комментарий']}\n\n"
        
        # Реквизиты
        if obj_data.get("Реквизиты"):
            md += "## Реквизиты\n\n"
            for attr in obj_data["Реквизиты"]:
                name = attr.get("Имя", "")
                syn = attr.get("Синоним", name)
                typ = attr.get("Тип", "")
                md += f"- **{name}** — {syn} ({typ})\n"
            md += "\n"
        
        # Табличные части
        if obj_data.get("ТабличныеЧасти"):
            md += "## Табличные части\n\n"
            for ts in obj_data["ТабличныеЧасти"]:
                ts_name = ts.get("Имя", "")
                ts_syn = ts.get("Синоним", ts_name)
                md += f"### {ts_name} — {ts_syn}\n\n"
                
                if ts.get("Реквизиты"):
                    for attr in ts["Реквизиты"]:
                        name = attr.get("Имя", "")
                        syn = attr.get("Синоним", name)
                        typ = attr.get("Тип", "")
                        md += f"- **{name}** — {syn} ({typ})\n"
                    md += "\n"
        
        # Ресурсы (для регистров)
        if obj_data.get("Ресурсы"):
            md += "## Ресурсы\n\n"
            for res in obj_data["Ресурсы"]:
                name = res.get("Имя", "")
                syn = res.get("Синоним", name)
                typ = res.get("Тип", "")
                md += f"- **{name}** — {syn} ({typ})\n"
            md += "\n"
        
        # Измерения (для регистров)
        if obj_data.get("Измерения"):
            md += "## Измерения\n\n"
            for dim in obj_data["Измерения"]:
                name = dim.get("Имя", "")
                syn = dim.get("Синоним", name)
                typ = dim.get("Тип", "")
                md += f"- **{name}** — {syn} ({typ})\n"
            md += "\n"
        
        return md
    
    def merge_csv_files(self):
        """Объединение objects.csv"""
        csv_main = self.main_output / "objects.csv"
        csv_ext = self.ext_output / "objects.csv"
        csv_out = self.merged_output / "objects.csv"
        
        objects_dict = {}
        
        if csv_main.exists():
            with open(csv_main, 'r', encoding='utf-8-sig') as f:
                for obj in csv.DictReader(f):
                    key = f"{obj['object_type']}.{obj['object_name']}"
                    objects_dict[key] = obj
                    objects_dict[key]['source'] = 'main'
        
        if csv_ext.exists():
            with open(csv_ext, 'r', encoding='utf-8-sig') as f:
                for obj in csv.DictReader(f):
                    key = f"{obj['object_type']}.{obj['object_name']}"
                    if key in objects_dict:
                        objects_dict[key]['source'] = 'main+extension'
                    else:
                        objects_dict[key] = obj
                        objects_dict[key]['source'] = 'extension'
        
        # Всегда: пересчитываем фактическое количество модулей из папки modules
        modules_dir = self.merged_output / "modules"
        if modules_dir.exists():
            for key, obj in objects_dict.items():
                obj_type = obj['object_type']
                obj_name = obj['object_name']
                obj_modules_dir = modules_dir / obj_type / obj_name
                
                if obj_modules_dir.exists() and obj_modules_dir.is_dir():
                    # Считаем реальное количество .bsl файлов
                    actual_modules_count = len(list(obj_modules_dir.glob("*.bsl")))
                    obj['modules_count'] = str(actual_modules_count)
                # Если папки нет - оставляем значение из CSV (или 0)
                elif 'modules_count' not in obj or not obj.get('modules_count'):
                    obj['modules_count'] = '0'
        
        # Сохраняем
        if objects_dict:
            with open(csv_out, 'w', newline='', encoding='utf-8-sig') as f:
                first_obj = next(iter(objects_dict.values()))
                fieldnames = list(first_obj.keys())
                if 'source' not in fieldnames:
                    fieldnames.append('source')
                
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(objects_dict.values())
    


def main():
    """Точка входа"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Интеллектуальное объединение основной конфигурации 1С и расширения'
    )
    parser.add_argument('main_config', help='Путь к основной конфигурации')
    parser.add_argument('extension', help='Путь к расширению')
    parser.add_argument('output_dir', help='Путь для сохранения объединенных данных')
    
    args = parser.parse_args()
    
    if not Path(args.main_config).exists():
        print(f"❌ Основная конфигурация не найдена: {args.main_config}")
        return 1
    
    if not Path(args.extension).exists():
        print(f"❌ Расширение не найдено: {args.extension}")
        return 1
    
    merger = IntelligentConfigMerger(args.main_config, args.extension, args.output_dir)
    
    try:
        merger.run()
        return 0
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
