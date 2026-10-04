"""Server configuration.

Settings are read from (highest precedence first) explicit keyword arguments (the CLI
passes its flags this way), then ``MCP_FS_*`` environment variables, then defaults.
Complex values in the environment are JSON, e.g. ``MCP_FS_ROOTS='["/srv/a", "/srv/b"]'``;
``MCP_FS_TOOLS`` also accepts a comma-separated list.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
Profile = Literal["full", "lite"]

MIB = 1024 * 1024

DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    # Version-control internals (may contain credentials in remote URLs or hooks).
    ".git/",
    ".hg/",
    ".svn/",
    # Environment files, but not their documented templates.
    ".env",
    ".env.*",
    "!.env.example",
    "!.env.sample",
    "!.env.template",
    # Keys and certificates.
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    # Credential stores and tool configs that commonly hold tokens.
    ".ssh/",
    ".aws/",
    ".gnupg/",
    ".docker/config.json",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
)
"""Gitignore-style patterns that are always blocked, even for direct access."""


def _cwd_roots() -> tuple[Path, ...]:
    return (Path.cwd(),)


class Settings(BaseSettings):
    """Immutable runtime configuration shared by every component."""

    model_config = SettingsConfigDict(env_prefix="MCP_FS_", frozen=True, extra="forbid")

    roots: Annotated[
        tuple[Path, ...],
        Field(
            min_length=1,
            description="Directories the server may access. Relative paths resolve against the "
            "first root. Defaults to the current working directory.",
        ),
    ] = Field(default_factory=_cwd_roots)
    allow_write: bool = Field(
        default=False, description="Register the editing tools (replace, edit, write)."
    )
    deny_globs: tuple[str, ...] = Field(
        default=DEFAULT_DENY_GLOBS,
        description="Gitignore-style patterns that are never readable or writable.",
    )
    respect_gitignore: bool = Field(
        default=True, description="Hide .gitignore'd paths from directory walks and searches."
    )
    max_file_bytes: int = Field(
        default=10 * MIB,
        gt=0,
        description="Largest file that is loaded fully into memory (outline, symbols, edits).",
    )
    max_read_lines: int = Field(default=2000, gt=0, description="Most lines returned per read.")
    max_line_chars: int = Field(
        default=2000, ge=80, description="Longer lines are clipped in output."
    )
    max_output_chars: int = Field(
        default=50_000, ge=1000, description="Budget for the text of a single tool result."
    )
    max_matches: int = Field(default=500, gt=0, description="Upper bound for search hits.")
    max_results: int = Field(
        default=1000, gt=0, description="Upper bound for paths returned by listings and finds."
    )
    max_pattern_length: int = Field(
        default=1000, gt=0, description="Longest accepted regular expression."
    )
    search_timeout_s: float = Field(
        default=10.0, gt=0, description="Wall-clock budget for one search or regex operation."
    )
    log_level: LogLevel = Field(default="WARNING", description="Log level (logs go to stderr).")
    profile: Profile = Field(
        default="full",
        description="Tool set: 'full' (every tool and parameter) or 'lite' (a small, complete "
        "core with essential parameters, for small local models).",
    )
    tools: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        description="Allowlist of tool names within the profile; empty means all of them.",
    )
    output_schema: bool | None = Field(
        default=None,
        description="Publish output schemas. Default: on for 'full', off for 'lite'.",
    )

    @field_validator("roots")
    @classmethod
    def _resolve_roots(cls, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        resolved: list[Path] = []
        for root in roots:
            try:
                real = root.expanduser().resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"root does not exist: {root}") from exc
            if not real.is_dir():
                raise ValueError(f"root is not a directory: {root}")
            if real not in resolved:
                resolved.append(real)
        return tuple(resolved)

    @field_validator("tools", mode="before")
    @classmethod
    def _split_tools(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(name.strip() for name in value.split(",") if name.strip())
        return value

    @property
    def emit_output_schema(self) -> bool:
        """Whether tools publish output schemas (explicit setting, else the profile default)."""
        if self.output_schema is not None:
            return self.output_schema
        return self.profile == "full"

    @property
    def primary_root(self) -> Path:
        """The root that relative paths resolve against."""
        return self.roots[0]
