#!/usr/bin/env python3
"""
Скрипт для проверки аннотаций в распарсенных модулях форм
Проверяет:
1. XML события извлечены из Form.xml
2. Аннотации добавлены в BSL код при парсинге
3. Аннотации корректно применены при объединении
"""

from pathlib import Path
import re
import json

def find_all_form_xmls(base_dir: Path) -> list:
    """Находит все Form.xml файлы"""
    return list(base_dir.rglob('**/Forms/*/Ext/Form.xml'))

def check_xml_events(xml_path: Path) -> list:
    """Проверяет наличие событий с callType в XML"""
    import xml.etree.ElementTree as ET
    
    events = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        ns = {'': 'http://v8.1c.ru/8.3/xcf/logform'}
        
        for event in root.findall('.//Event', ns):
            event_name = event.get('name')
            call_type = event.get('callType')
            handler_name = event.text
            
            if handler_name and call_type:
                events.append({
                    'event': event_name,
                    'callType': call_type,
                    'handler': handler_name.strip()
                })
    except:
        pass
    
    return events

def check_bsl_annotations(bsl_path: Path, expected_handlers: list) -> dict:
    """Проверяет наличие аннотаций в BSL коде"""
    if not bsl_path.exists():
        return {'found': False, 'reason': 'file_not_found'}
    
    with open(bsl_path, 'r', encoding='utf-8-sig') as f:
        code = f.read()
    
    found_annotations = []
    annotation_pattern = re.compile(
        r'&(?:Перед|После|Вместо|Before|After|Instead)\s*\(\s*["\']?(\w+)["\']?\s*\)',
        re.IGNORECASE
    )
    
    for match in annotation_pattern.finditer(code):
        found_annotations.append(match.group(0))
    
    # Проверяем каждый ожидаемый handler
    results = []
    for handler_info in expected_handlers:
        handler_name = handler_info['handler']
        # Ищем процедуру в коде
        proc_pattern = rf'(?:Процедура|Procedure)\s+{handler_name}\s*\('
        proc_found = bool(re.search(proc_pattern, code, re.IGNORECASE))
        
        if proc_found:
            # Проверяем, есть ли аннотация перед процедурой
            # Ищем строку с процедурой и проверяем предыдущие строки
            lines = code.split('\n')
            proc_line_idx = None
            for i, line in enumerate(lines):
                if re.search(proc_pattern, line, re.IGNORECASE):
                    proc_line_idx = i
                    break
            
            annotation_found = False
            if proc_line_idx is not None and proc_line_idx > 0:
                # Проверяем несколько строк выше (с учетом пустых строк и комментариев)
                for i in range(max(0, proc_line_idx - 3), proc_line_idx):
                    if annotation_pattern.search(lines[i]):
                        annotation_found = True
                        break
            
            results.append({
                'handler': handler_name,
                'expected_callType': handler_info['callType'],
                'procedure_found': True,
                'annotation_found': annotation_found
            })
        else:
            results.append({
                'handler': handler_name,
                'expected_callType': handler_info['callType'],
                'procedure_found': False,
                'annotation_found': False
            })
    
    return {
        'found': True,
        'annotations': found_annotations,
        'handlers_check': results
    }

def main():
    # Настройка вывода для Windows
    import sys
    if sys.platform == 'win32':
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    
    print("=" * 80)
    print("ПРОВЕРКА АННОТАЦИЙ ДЛЯ МОДУЛЕЙ ФОРМ")
    print("=" * 80)
    
    # 1. Проверяем исходные конфигурации
    extension_dir = Path("in_conf/conf_ut/ut_aa")
    
    print(f"\n[DIR] Сканирование: {extension_dir}")
    form_xmls = find_all_form_xmls(extension_dir)
    print(f"   Найдено Form.xml файлов: {len(form_xmls)}")
    
    total_events = 0
    forms_with_events = []
    
    for xml_path in form_xmls:
        events = check_xml_events(xml_path)
        if events:
            total_events += len(events)
            rel_path = xml_path.relative_to(extension_dir)
            forms_with_events.append({
                'xml_path': xml_path,
                'rel_path': rel_path,
                'events': events
            })
            
            print(f"\n   [XML] {rel_path}")
            for evt in events:
                print(f"      * {evt['event']} [{evt['callType']}] -> {evt['handler']}")
    
    print(f"\n[STATS] Статистика:")
    print(f"   * Форм с событиями: {len(forms_with_events)}")
    print(f"   * Всего событий с callType: {total_events}")
    
    # 2. Проверяем распарсенные файлы в output
    output_dir = Path("out_conf/conf_ut_merged/ut_aa")
    
    if not output_dir.exists():
        print(f"\n[WARN] Выходная директория не найдена: {output_dir}")
        print("   Запустите сначала: python merge_config_with_extension.py")
        return
    
    print(f"\n" + "=" * 80)
    print("ПРОВЕРКА РАСПАРСЕННЫХ МОДУЛЕЙ")
    print("=" * 80)
    
    modules_dir = output_dir / "modules"
    
    annotations_added = 0
    annotations_missing = 0
    
    for form_info in forms_with_events:
        # Определяем путь к BSL модулю в output
        # Пример: Documents/ЗаказПоставщику/Forms/ФормаВыбора/Ext/Form.xml
        #      -> modules/Документ/ЗаказПоставщику/МодульФормы_ФормаВыбора.bsl
        
        rel_path = form_info['rel_path']
        parts = rel_path.parts
        
        if len(parts) >= 4 and parts[0] in ['Documents', 'Catalogs', 'DataProcessors', 'Reports']:
            obj_type_map = {
                'Documents': 'Документ',
                'Catalogs': 'Справочник',
                'DataProcessors': 'Обработка',
                'Reports': 'Отчет'
            }
            rus_type = obj_type_map.get(parts[0], parts[0])
            obj_name = parts[1]
            form_name = parts[3]
            
            # Ищем соответствующий BSL файл
            possible_names = [
                f"МодульФормы_{form_name}.bsl",
                f"FormModule_{form_name}.bsl",
                f"МодульФормы.bsl"
            ]
            
            bsl_found = False
            for possible_name in possible_names:
                bsl_path = modules_dir / rus_type / obj_name / possible_name
                if bsl_path.exists():
                    bsl_found = True
                    break
            
            if not bsl_found:
                # Пробуем найти любой файл с МодульФормы в имени
                obj_dir = modules_dir / rus_type / obj_name
                if obj_dir.exists():
                    for bsl_file in obj_dir.glob("МодульФормы*.bsl"):
                        bsl_path = bsl_file
                        bsl_found = True
                        break
            
            print(f"\n[BSL] {rus_type}.{obj_name} / {form_name}")
            
            if bsl_found:
                check_result = check_bsl_annotations(bsl_path, form_info['events'])
                
                if check_result['found']:
                    print(f"   [OK] BSL файл: {bsl_path.relative_to(output_dir)}")
                    print(f"   Аннотаций в коде: {len(check_result['annotations'])}")
                    
                    for handler_check in check_result['handlers_check']:
                        status = "[OK]" if handler_check['annotation_found'] else "[FAIL]"
                        print(f"   {status} {handler_check['handler']} [{handler_check['expected_callType']}]")
                        
                        if handler_check['annotation_found']:
                            annotations_added += 1
                        else:
                            annotations_missing += 1
                            if handler_check['procedure_found']:
                                print(f"      [WARN] Процедура найдена, но аннотация отсутствует!")
                else:
                    print(f"   [FAIL] BSL файл не найден")
                    annotations_missing += len(form_info['events'])
            else:
                print(f"   [FAIL] BSL файл не найден в: {modules_dir / rus_type / obj_name}")
                annotations_missing += len(form_info['events'])
    
    # Итоговая статистика
    print(f"\n" + "=" * 80)
    print("ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    print(f"[OK] Аннотации добавлены корректно: {annotations_added}")
    print(f"[FAIL] Аннотации отсутствуют: {annotations_missing}")
    if annotations_added + annotations_missing > 0:
        print(f"[STATS] Успешность: {annotations_added / (annotations_added + annotations_missing) * 100:.1f}%")
    else:
        print(f"[STATS] Нет данных для проверки")
    
    # 3. Проверяем merged результаты
    merged_dir = Path("out_conf/conf_ut_merged/merged")
    if merged_dir.exists():
        print(f"\n" + "=" * 80)
        print("ПРОВЕРКА ОБЪЕДИНЕННЫХ МОДУЛЕЙ")
        print("=" * 80)
        print(f"[DIR] {merged_dir}")
        
        # Проверяем, что аннотации применились при объединении
        merged_modules = merged_dir / "modules"
        if merged_modules.exists():
            merged_forms = list(merged_modules.rglob("МодульФормы*.bsl"))
            print(f"   Объединенных модулей форм: {len(merged_forms)}")
            
            # Проверяем наличие меток расширения
            extension_markers = 0
            for form_file in merged_forms:
                with open(form_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if "РАСШИРЕНИЕ [ut_aa]" in content:
                        extension_markers += 1
            
            print(f"   С метками расширения [ut_aa]: {extension_markers}")

if __name__ == "__main__":
    main()

