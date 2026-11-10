"""
MCP инструменты для синтакс-помощника 1С, проверки BSL и обзора метаданных
"""

from . import bsl_syntax_tools
from . import bsl_check_tools
from . import metadata_tools

__all__ = ['bsl_syntax_tools', 'bsl_check_tools', 'metadata_tools']
