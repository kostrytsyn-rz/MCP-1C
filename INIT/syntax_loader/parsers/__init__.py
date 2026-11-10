"""
Парсеры для обработки файлов документации 1С
"""

# Ленивая загрузка модулей чтобы избежать проблем с зависимостями
# при импорте новых парсеров структуры
__all__ = ['create_syntax_collection', 'index_syntax_data', 'export_to_markdown']

def __getattr__(name):
    """Ленивая загрузка модулей"""
    if name in ('create_syntax_collection', 'index_syntax_data'):
        from .syntax_indexer import create_syntax_collection, index_syntax_data
        if name == 'create_syntax_collection':
            return create_syntax_collection
        else:
            return index_syntax_data
    elif name == 'export_to_markdown':
        from .markdown_exporter import export_to_markdown
        return export_to_markdown
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
