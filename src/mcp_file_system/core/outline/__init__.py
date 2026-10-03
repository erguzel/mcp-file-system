"""Structural outlines of source and documentation files, with pluggable languages."""

from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.registry import OutlineRegistry, default_registry
from mcp_file_system.core.outline.service import Outliner

__all__ = ["OutlineProvider", "OutlineRegistry", "Outliner", "default_registry"]
