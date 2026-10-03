"""A secure MCP filesystem server with fine-grained, partial-read tools for AI agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-file-system")
except PackageNotFoundError:  # pragma: no cover - only when running from a raw source tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
