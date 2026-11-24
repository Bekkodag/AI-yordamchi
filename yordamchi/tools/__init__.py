"""Barcha tool modullarini yuklaydi va umumiy registry'ni qaytaradi."""

from __future__ import annotations

from .base import Risk, Tool, ToolError, ToolRegistry, registry


def load_all() -> ToolRegistry:
    from . import apps, browser, files, skills, memory, session, shell, system, web, windows  # noqa: F401  (registry'ga yoziladi)
    from .. import usertools

    usertools.ensure_loaded()  # paneldan yaratilgan vositalar (data/tools/*.json)
    return registry


__all__ = ["Risk", "Tool", "ToolError", "ToolRegistry", "load_all", "registry"]
