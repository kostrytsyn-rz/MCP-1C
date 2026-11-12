# -*- coding: utf-8 -*-
"""
Чтение модуля обычной формы 1С из Ext/Form.bin (контейнер v8, формат файлов 8.x).

Разбор контейнера — пакет v8unpack (https://github.com/saby-integration/v8unpack),
класс v8unpack.container.Container / Container64. Это внутренний API пакета,
поэтому версия зафиксирована в requirements.txt.

Чем Form.bin отличается от .cf: записи верхнего уровня обычно НЕ сжаты
(штатный v8unpack.extract по умолчанию пытается их распаковать и падает),
поэтому сжатие определяется попыткой raw deflate.
Структура обычной формы: контейнер с записями "form" (описание) и "module" (код).
"""

import io
import zlib
from pathlib import Path
from typing import Optional

from v8unpack.container import Container, Container64
from v8unpack.container_reader import detect_format

SIGNATURE_32 = b"\xff\xff\xff\x7f"
SIGNATURE_64 = b"\xff" * 8


def _nested_container(data: bytes):
    """Класс контейнера для вложенных данных или None, если это не контейнер."""
    if data[:4] == SIGNATURE_32:
        return Container
    if data[:8] == SIGNATURE_64:
        return Container64
    return None


def _maybe_inflate(data: bytes) -> bytes:
    """Raw deflate (wbits=-15), если данные сжаты; иначе как есть."""
    if not data or data[:3] == b"\xef\xbb\xbf" or data[:1] == b"{" or _nested_container(data):
        return data
    try:
        result = zlib.decompressobj(-15).decompress(data)
        return result or data
    except zlib.error:
        return data


def _find_module(container) -> Optional[bytes]:
    """Поиск записи "module" (не контейнера) в контейнере и вложенных контейнерах."""
    for name, entry in container.files.items():
        data = _maybe_inflate(b"".join(entry.data))
        cls = _nested_container(data)
        if cls:
            nested = cls()
            nested.read(io.BytesIO(data))
            found = _find_module(nested)
            if found is not None:
                return found
        elif name == "module":
            return data
    return None


def read_form_module(bin_path: Path) -> Optional[str]:
    """Текст модуля обычной формы из Form.bin или None, если модуля нет.

    Текст возвращается без изменений (номера строк совпадают с конфигуратором).

    Raises:
        ValueError: файл не является контейнером 1С (например, код хранится иначе)
    """
    data = Path(bin_path).read_bytes()
    if not _nested_container(data):
        raise ValueError("Form.bin не является контейнером 1С — формат не поддерживается")
    stream = io.BytesIO(data)
    container = detect_format(stream, 0)
    container.read(stream)
    raw = _find_module(container)
    if raw is None:
        return None
    return raw.decode("utf-8-sig", errors="replace")
