"""Command-line entry point (``mcp-file-system``)."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from mcp_file_system import __version__
from mcp_file_system.config import LogLevel, Profile, Settings
from mcp_file_system.server import create_server

EXIT_CONFIG_ERROR = 2
EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser. Unset options fall back to MCP_FS_* env vars."""
    parser = argparse.ArgumentParser(
        prog="mcp-file-system",
        description="MCP filesystem server with fine-grained, partial-read tools (stdio).",
        epilog="Every option can also be set via an MCP_FS_<NAME> environment variable.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-r",
        "--root",
        dest="roots",
        action="append",
        type=Path,
        metavar="DIR",
        help="Allowed directory (repeatable). Default: the current working directory.",
    )
    parser.add_argument(
        "--allow-write",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable the editing tools (default: read-only).",
    )
    parser.add_argument(
        "--deny",
        action="append",
        metavar="GLOB",
        help="Extra gitignore-style pattern to block (repeatable; adds to the defaults).",
    )
    parser.add_argument(
        "--gitignore",
        dest="respect_gitignore",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Hide .gitignore'd paths from walks and searches (default: on).",
    )
    parser.add_argument(
        "--profile",
        choices=get_args(Profile),
        help="Tool set: 'full' (default) or 'lite' (small core for small local models).",
    )
    parser.add_argument(
        "--tools",
        metavar="NAME[,NAME...]",
        help="Only expose these tools of the profile (comma-separated).",
    )
    parser.add_argument(
        "--output-schema",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Publish tool output schemas (default: on for full, off for lite).",
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        metavar="FILE",
        help="Append one JSON line per tool call to FILE (must be outside the roots).",
    )
    limits = parser.add_argument_group("limits")
    limits.add_argument("--max-file-bytes", type=int, metavar="N")
    limits.add_argument("--max-read-lines", type=int, metavar="N")
    limits.add_argument("--max-output-chars", type=int, metavar="N")
    limits.add_argument("--max-matches", type=int, metavar="N")
    limits.add_argument("--max-results", type=int, metavar="N")
    limits.add_argument("--search-timeout", dest="search_timeout_s", type=float, metavar="SEC")
    parser.add_argument("--log-level", choices=get_args(LogLevel), help="Log level (stderr).")
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    """Merge CLI arguments over environment-derived settings."""
    names = (
        "roots",
        "allow_write",
        "respect_gitignore",
        "max_file_bytes",
        "max_read_lines",
        "max_output_chars",
        "max_matches",
        "max_results",
        "search_timeout_s",
        "log_level",
        "profile",
        "tools",
        "output_schema",
        "audit_log",
    )
    overrides: dict[str, Any] = {
        name: getattr(args, name) for name in names if getattr(args, name) is not None
    }
    if "roots" in overrides:
        overrides["roots"] = tuple(overrides["roots"])
    settings = Settings(**overrides)
    if args.deny:
        settings = Settings(**overrides, deny_globs=(*settings.deny_globs, *args.deny))
    return settings


def main(argv: Sequence[str] | None = None) -> int:
    """Run the server over stdio until the client disconnects."""
    args = build_parser().parse_args(argv)
    try:
        settings = settings_from_args(args)
    except ValidationError as exc:
        sys.stderr.write(f"mcp-file-system: invalid configuration\n{exc}\n")
        return EXIT_CONFIG_ERROR
    # stdout carries the MCP protocol; logs must go to stderr.
    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        server = create_server(settings)
    except (ValueError, OSError) as exc:  # e.g. unknown --tools name, unwritable audit log
        sys.stderr.write(f"mcp-file-system: invalid configuration\n{exc}\n")
        return EXIT_CONFIG_ERROR
    try:
        server.run("stdio")
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    return 0
