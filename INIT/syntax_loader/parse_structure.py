#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для парсинга структуры синтаксис-помощника 1С из распакованной папки.

Использование:
    python parse_structure.py <путь_к_распакованной_папке> [--output output.json] [--markdown md_dir]
"""

import argparse
import json
import sys
import os
import logging
from pathlib import Path

# Папка скрипта (syntax_loader) в sys.path для импорта пакета parsers
# при запуске из любой рабочей директории
syntax_loader_dir = os.path.dirname(os.path.abspath(__file__))
if syntax_loader_dir not in sys.path:
    sys.path.insert(0, syntax_loader_dir)

from parsers.hierarchical_collector import HierarchicalCollector
from parsers.markdown_exporter import export_to_markdown

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Парсинг структуры синтаксис-помощника 1С'
    )
    parser.add_argument(
        'input_dir',
        help='Путь к распакованной папке с файлами синтаксис-помощника'
    )
    parser.add_argument(
        '--output',
        '-o',
        default='structure.json',
        help='Путь к выходному JSON файлу (по умолчанию: structure.json)'
    )
    parser.add_argument(
        '--markdown',
        '-m',
        metavar='MD_DIR',
        help='Дополнительно выгрузить синтаксис-помощник в Markdown в указанную папку'
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = Path(args.input_dir).resolve()
    input_dir = str(input_path)

    if not input_path.exists():
        print(f"Директория не найдена: {input_dir} (исходный путь: {args.input_dir})")
        return 1
    if not input_path.is_dir():
        print(f"Указанный путь не является директорией: {input_dir}")
        return 1

    if not os.path.isabs(args.output):
        output_path = os.path.abspath(os.path.join(script_dir, args.output))
    else:
        output_path = args.output

    try:
        collector = HierarchicalCollector(input_dir)
        structure_data = collector.collect_all()
        structure_data['stats'] = collector.stats
        print(f"Сохранение результата в {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(structure_data, f, ensure_ascii=False, indent=2)
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            print(f"✅ Файл успешно создан: {output_path}")
            print(f"   Размер файла: {file_size / 1024 / 1024:.2f} MB")
        else:
            print(f"❌ Файл не был создан: {output_path}")
            return 1
        print("Парсинг завершен успешно!")
        stats = structure_data.get('stats', {})
        for key, value in stats.items():
            print(f"  {key}: {value}")

        if args.markdown:
            md_dir = os.path.abspath(args.markdown)
            md_stats = export_to_markdown(structure_data, md_dir)
            print(f"✅ Markdown выгружен в {md_dir}: {md_stats}")
    except Exception as e:
        print(f"Ошибка при парсинге: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
