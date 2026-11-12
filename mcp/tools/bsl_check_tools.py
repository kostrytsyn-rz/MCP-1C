#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP инструменты для проверки кода 1С (BSL).

Предоставляет 3 инструмента:
- bslcheck_code - проверка фрагмента кода (текстом): BSL Language Server + проверки по стандартам
- bslcheck_analyze - проверка .bsl файла из примонтированной папки
- bslcheck_format - форматирование кода (текстом или файлом), результат возвращается текстом

Анализ выполняется на временной копии кода, поэтому рабочая папка может быть смонтирована read-only.
"""

import sys
import os
import re
import shutil
import subprocess
import json
import tempfile
import logging
from typing import Any, Dict, List, Optional, Tuple

# Добавляем путь к родительской директории для импорта config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    BSL_LANGUAGE_SERVER_PATH, BSL_COMMAND_TIMEOUT, BSL_CONFIGURATION_PATH, WORKSPACES_MOUNT_PATHS
)

# Логирование
logger = logging.getLogger(__name__)

# Максимум замечаний в ответе (чтобы не переполнять контекст агента)
MAX_DIAGNOSTICS = 100

SEVERITY_ORDER = {"Error": 0, "Warning": 1, "Information": 2, "Hint": 3}
SEVERITY_ICON = {"Error": "❌", "Warning": "⚠️", "Information": "ℹ️", "Hint": "💡"}

# Проверки по стандартам разработки 1С, которых нет в BSL Language Server.
# (регулярное выражение, код, сообщение с рекомендацией)
STANDARD_CHECKS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r'\.ПолучитьОбъект\(\s*\)\s*\.\s*[А-Яа-яЁёA-Za-z_]', re.IGNORECASE),
     "GetObjectForAttribute",
     "Объект читается целиком ради одного реквизита. Используйте ОбщегоНазначения.ЗначениеРеквизитаОбъекта"
     "(Ссылка, \"Реквизит\") / ЗначенияРеквизитовОбъекта() или запрос (БСП)."),
    (re.compile(r'\.НайтиПоНаименованию\s*\(', re.IGNORECASE),
     "FindByDescription",
     "Поиск по наименованию ненадёжен (наименования не уникальны и меняются). "
     "Используйте предопределённые элементы, код или ссылку."),
    (re.compile(r'(?<![\wА-Яа-яЁё.])Сообщить\s*\(', re.IGNORECASE),
     "MessageUsage",
     "Вместо Сообщить() используйте ОбщегоНазначения.СообщитьПользователю() (БСП) "
     "или СообщениеПользователю."),
    (re.compile(r'(?<![\wА-Яа-яЁё.])ТекущаяДата\s*\(', re.IGNORECASE),
     "CurrentDate",
     "ТекущаяДата() возвращает время компьютера. Используйте ТекущаяДатаСеанса() (стандарт 643)."),
]


def _convert_host_path_to_container(host_path: str) -> str:
    """
    Конвертирует путь с хоста в путь внутри контейнера.

    Примеры:
        YOUR_PATH/Проект/file.bsl -> /workspaces/Проект/file.bsl
        ./projects/1c/Проект/file.bsl -> /workspaces/Проект/file.bsl
        /workspaces/Проект/file.bsl -> /workspaces/Проект/file.bsl (уже контейнерный путь)

    Примечание: Пути монтирования настраиваются через переменную окружения
    WORKSPACES_MOUNT_PATHS или определяются автоматически из docker-compose.yml
    """
    # Проверяем запущены ли мы в Docker
    if not os.path.exists("/.dockerenv"):
        # Локальный запуск - путь не меняем
        return host_path

    # Нормализуем путь (конвертируем \ в /)
    normalized_path = host_path.replace("\\", "/")

    # Если путь уже начинается с /workspaces, возвращаем как есть
    if normalized_path.startswith("/workspaces"):
        return normalized_path

    # Строим список маппингов из переменной окружения
    mount_mappings = []

    if WORKSPACES_MOUNT_PATHS:
        # Парсим переменную окружения: "host1:container1,host2:container2"
        for mapping in WORKSPACES_MOUNT_PATHS.split(","):
            mapping = mapping.strip()
            if ":" in mapping:
                host_prefix, container_prefix = mapping.split(":", 1)
                host_prefix = host_prefix.strip().replace("\\", "/")
                container_prefix = container_prefix.strip()
                mount_mappings.append((host_prefix, container_prefix))

    # Если переменная не задана, пробуем определить автоматически
    if not mount_mappings:
        common_mounts = [
            ("./projects/1c", "/workspaces"),
            ("./projects", "/workspaces"),
        ]
        for host_prefix, container_prefix in common_mounts:
            # Проверяем существует ли контейнерный путь
            if os.path.exists(container_prefix):
                mount_mappings.append((host_prefix, container_prefix))
                break

    # Применяем маппинги
    for host_prefix, container_prefix in mount_mappings:
        normalized_host_prefix = host_prefix.replace("\\", "/")

        # Проверяем совпадение (case-insensitive для Windows путей)
        if normalized_path.lower().startswith(normalized_host_prefix.lower()):
            relative_path = normalized_path[len(normalized_host_prefix):].lstrip("/")
            container_path = container_prefix.rstrip("/") + "/" + relative_path
            logger.info(f"Конвертация пути: {host_path} -> {container_path}")
            return container_path

    logger.warning(f"Не найден маппинг для пути: {host_path}. Используется исходный путь.")
    return host_path


def _get_bsl_jar_path() -> str:
    """Получение пути к BSL Language Server JAR"""
    if not os.path.exists(BSL_LANGUAGE_SERVER_PATH):
        raise FileNotFoundError(
            f"BSL Language Server не найден: {BSL_LANGUAGE_SERVER_PATH}\n"
            f"Убедитесь, что Docker образ собран корректно."
        )
    return BSL_LANGUAGE_SERVER_PATH


def _run_bsl_command(args: list, timeout: int = BSL_COMMAND_TIMEOUT) -> Tuple[str, str, int]:
    """
    Запуск команды BSL Language Server.

    Args:
        args: Аргументы команды (без java -jar)
        timeout: Таймаут выполнения в секундах

    Returns:
        (stdout, stderr, return_code)
    """
    cmd = ["java", "-jar", _get_bsl_jar_path()] + args
    logger.debug(f"Запуск BSL Language Server: {' '.join(cmd)}")

    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise Exception(f"BSL Language Server не ответил за {timeout} секунд")

    if completed.stderr:
        logger.debug(f"BSL stderr: {completed.stderr}")
    return completed.stdout, completed.stderr, completed.returncode


def _configuration_args() -> List[str]:
    """Параметр --configuration, если задан файл настроек BSL LS (.bsl-language-server.json)."""
    if BSL_CONFIGURATION_PATH and os.path.isfile(BSL_CONFIGURATION_PATH):
        return ["--configuration", BSL_CONFIGURATION_PATH]
    return []


def _analyze_source(code: str, file_name: str = "Module.bsl") -> List[Dict[str, Any]]:
    """
    Анализ кода BSL Language Server во временной папке.

    Returns:
        Список замечаний: {line, column, severity, code, message} (строки с 1)
    """
    work_dir = tempfile.mkdtemp(prefix="bslcheck_")
    try:
        src_dir = os.path.join(work_dir, "src")
        out_dir = os.path.join(work_dir, "out")
        os.makedirs(src_dir)
        os.makedirs(out_dir)
        with open(os.path.join(src_dir, file_name), "w", encoding="utf-8-sig") as f:
            f.write(code)

        stdout, stderr, return_code = _run_bsl_command(
            ["--analyze", "--srcDir", src_dir, "--reporter", "json", "--outputDir", out_dir]
            + _configuration_args()
        )

        report_path = os.path.join(out_dir, "bsl-json.json")
        if not os.path.exists(report_path):
            raise Exception(f"BSL Language Server не сформировал отчёт (код {return_code}): "
                            f"{(stderr or stdout or '').strip()[-2000:]}")

        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)

        diagnostics = []
        for file_info in report.get("fileinfos", []):
            for diag in file_info.get("diagnostics", []):
                start = (diag.get("range") or {}).get("start") or {}
                diagnostics.append({
                    "line": start.get("line", 0) + 1,
                    "column": start.get("character", 0) + 1,
                    "severity": diag.get("severity", "Warning"),
                    "code": diag.get("code", ""),
                    "message": diag.get("message", "")
                })
        return diagnostics
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _check_standards(code: str) -> List[Dict[str, Any]]:
    """Проверки по стандартам 1С, которых нет в BSL Language Server (без учёта комментариев)."""
    diagnostics = []
    for line_no, line in enumerate(code.splitlines(), 1):
        code_part = line.split("//", 1)[0]
        if not code_part.strip():
            continue
        for pattern, rule_code, message in STANDARD_CHECKS:
            match = pattern.search(code_part)
            if match:
                diagnostics.append({
                    "line": line_no,
                    "column": match.start() + 1,
                    "severity": "Warning",
                    "code": f"std:{rule_code}",
                    "message": message
                })
    return diagnostics


def _format_report(diagnostics: List[Dict[str, Any]], code: str, title: str,
                   bsl_error: Optional[str] = None) -> str:
    """Компактный отчёт для агента: «строка:колонка [код] сообщение» + строка кода."""
    lines = code.splitlines()
    diagnostics = sorted(diagnostics, key=lambda d: (SEVERITY_ORDER.get(d["severity"], 9), d["line"], d["column"]))
    counts = {sev: sum(1 for d in diagnostics if d["severity"] == sev) for sev in SEVERITY_ORDER}

    output = [f"# {title}", ""]
    summary = ", ".join(f"{SEVERITY_ICON[s]} {s}: {c}" for s, c in counts.items() if c)
    output.append(f"Замечаний: {len(diagnostics)}" + (f" ({summary})" if summary else ""))
    if bsl_error:
        output.append(f"⚠️ BSL Language Server недоступен, выполнены только проверки по стандартам: {bsl_error}")
    output.append("")

    if not diagnostics:
        output.append("✅ Замечаний нет.")
        return "\n".join(output)

    for diag in diagnostics[:MAX_DIAGNOSTICS]:
        icon = SEVERITY_ICON.get(diag["severity"], "•")
        output.append(f"{icon} {diag['line']}:{diag['column']} [{diag['code']}] {diag['message']}")
        if 0 < diag["line"] <= len(lines):
            output.append(f"    > {lines[diag['line'] - 1].strip()[:200]}")
    if len(diagnostics) > MAX_DIAGNOSTICS:
        output.append(f"... и ещё {len(diagnostics) - MAX_DIAGNOSTICS} замечаний")

    output += ["", "💡 Исправьте ошибки (❌) обязательно, предупреждения (⚠️) - по возможности, "
                   "затем повторите проверку."]
    return "\n".join(output)


# Проверки по стандартам, которые дублируют диагностики BSL Language Server:
# если BSL LS сообщил о том же в этой строке, наше замечание не выводим
STANDARD_CHECK_DUPLICATES = {
    "std:MessageUsage": "DeprecatedMessage",
    "std:CurrentDate": "DeprecatedCurrentDate",
}


def _check(code: str, title: str, file_name: str = "Module.bsl") -> str:
    standard = _check_standards(code)
    bsl_error = None
    bsl_diagnostics: List[Dict[str, Any]] = []
    try:
        bsl_diagnostics = _analyze_source(code, file_name)
    except Exception as e:
        logger.error(f"Ошибка BSL Language Server: {e}")
        bsl_error = str(e)
    reported = {(d["code"], d["line"]) for d in bsl_diagnostics}
    standard = [d for d in standard
                if (STANDARD_CHECK_DUPLICATES.get(d["code"]), d["line"]) not in reported]
    return _format_report(bsl_diagnostics + standard, code, title, bsl_error)


def _read_workspace_file(file_path: str) -> Tuple[Optional[str], Optional[str], str]:
    """Чтение .bsl файла из примонтированной папки. Returns: (код, ошибка, путь в контейнере)."""
    container_path = _convert_host_path_to_container(file_path)
    if not os.path.exists(container_path):
        return None, (f"Файл не найден: {container_path} (исходный путь: {file_path}). "
                      f"Убедитесь что папка примонтирована в docker-compose.yml"), container_path
    if not container_path.lower().endswith('.bsl'):
        return None, f"Неподдерживаемый формат файла. Ожидается .bsl, получен: {container_path}", container_path
    with open(container_path, 'r', encoding='utf-8-sig') as f:
        return f.read(), None, container_path


def bslcheck_code(code: str) -> str:
    """Проверка фрагмента/модуля BSL, переданного текстом."""
    if not code or not code.strip():
        return "❌ Передайте код для проверки (параметр code)"
    return _check(code, "Проверка кода BSL")


def bslcheck_analyze(file_path: str) -> str:
    """Проверка .bsl файла из примонтированной папки."""
    try:
        code, error, container_path = _read_workspace_file(file_path)
        if error:
            return f"❌ {error}"
        logger.info(f"Анализ файла: {container_path} (исходный путь: {file_path})")
        return _check(code, f"Проверка {os.path.basename(container_path)}", os.path.basename(container_path))
    except Exception as e:
        logger.error(f"Ошибка анализа BSL: {e}", exc_info=True)
        return f"❌ Ошибка анализа: {e}"


def bslcheck_format(code: Optional[str] = None, file_path: Optional[str] = None) -> str:
    """
    Форматирование кода BSL Language Server. Исходный файл не изменяется -
    отформатированный код возвращается текстом.
    """
    try:
        if not code and file_path:
            code, error, _ = _read_workspace_file(file_path)
            if error:
                return f"❌ {error}"
        if not code or not code.strip():
            return "❌ Передайте код (code) или путь к .bsl файлу (file_path)"

        work_dir = tempfile.mkdtemp(prefix="bslformat_")
        try:
            module_path = os.path.join(work_dir, "Module.bsl")
            with open(module_path, "w", encoding="utf-8-sig") as f:
                f.write(code)

            stdout, stderr, return_code = _run_bsl_command(["--format", "--src", module_path])
            if return_code != 0:
                return f"❌ Ошибка форматирования (код {return_code}): {(stderr or stdout).strip()[-2000:]}"

            with open(module_path, "r", encoding="utf-8-sig") as f:
                formatted = f.read()
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        if formatted == code:
            return "✅ Код уже отформатирован, изменений нет."
        return f"Отформатированный код:\n\n```bsl\n{formatted}\n```"
    except Exception as e:
        logger.error(f"Ошибка форматирования BSL: {e}", exc_info=True)
        return f"❌ Ошибка форматирования: {e}"
