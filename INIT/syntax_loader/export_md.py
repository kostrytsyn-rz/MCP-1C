#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Экспорт готового structure.json в Markdown (без .hbk файлов).

Использование:
    python export_md.py [structure.json] [--output md_dir]
"""

import argparse
import json
import os
import sys

syntax_loader_dir = os.path.dirname(os.path.abspath(__file__))
if syntax_loader_dir not in sys.path:
    sys.path.insert(0, syntax_loader_dir)

from parsers.markdown_exporter import export_to_markdown


def main():
    parser = argparse.ArgumentParser(description='Экспорт structure.json синтаксис-помощника 1С в Markdown')
    parser.add_argument(
        'structure',
        nargs='?',
        default=os.path.join(syntax_loader_dir, 'structure.json'),
        help='Путь к structure.json (по умолчанию: structure.json рядом со скриптом)'
    )
    parser.add_argument(
        '--output',
        '-o',
        default='syntax_md',
        help='Папка для Markdown файлов (по умолчанию: syntax_md)'
    )
    args = parser.parse_args()

    if not os.path.isfile(args.structure):
        print(f"Файл не найден: {args.structure}")
        return 1

    with open(args.structure, 'r', encoding='utf-8') as f:
        structure = json.load(f)

    out_dir = os.path.abspath(args.output)
    stats = export_to_markdown(structure, out_dir)
    print(f"✅ Markdown выгружен в {out_dir}")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
