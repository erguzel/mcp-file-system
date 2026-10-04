# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Tool profiles: `--profile lite` exposes a small but complete tool set with essential
  parameters and short descriptions for small local models (the tool listing shrinks
  from ~27k to ~7.1k characters). `lite`'s `grep` searches invalid regexes literally.
- `--tools NAME,...` (`MCP_FS_TOOLS`) allowlist and `--[no-]output-schema`
  (`MCP_FS_OUTPUT_SCHEMA`); output schemas default to off in `lite`.
- `file_info` reports an approximate token count for text files.
- `--audit-log FILE` (`MCP_FS_AUDIT_LOG`): one JSON line per tool call (tool, arguments,
  duration, result size, truncation, error type). File contents are never logged, and
  the file must be outside the roots.

- Outlines for JSON, TOML and YAML: key trees with line spans and inline scalar values,
  so `read_symbol("tool.ruff.lint")` or `read_symbol("jobs.test.steps[0]")` returns just
  that section. TOML tables include their directly following sub-tables.
- `read_skeleton` (full profile): a code file with function and method bodies folded to
  one `<start>-<end> ...` row each, keeping imports, signatures, docstrings and class
  structure. Python, shell, and JS/TS, Go and Java with `[code]`. About 50–60% smaller
  than the full file on this (docstring-heavy) codebase.
- Change-aware reads: `read_lines` and `read_symbol` accept `if_changed_since=<etag>` and
  return a one-line "unchanged" answer when the file is unchanged and those lines were
  already returned in this session (otherwise content is sent, with a note). Re-reading
  already-returned lines adds an "already returned" note. Available in `lite` too.
- `grep` hits show their innermost enclosing symbol (functions, methods, config key
  paths, Markdown sections, Dockerfile instructions), so the agent can go straight to
  `read_symbol`.
- Optional `[code]` extra: outlines for JavaScript, TypeScript/TSX, Go (methods qualified
  by receiver, e.g. `Server.Handle`) and Java (nested types: `Outer.Inner.method`), using
  individual pinned grammar packages. Without the extra, `outline` names the extra to
  install.
- Dockerfile outlines (pure Python): build stages (`FROM ... AS name`) and their
  instructions, with line continuations, `# escape=` and BuildKit heredocs. `Dockerfile`,
  `Dockerfile.*`, `*.dockerfile` and `Containerfile` are recognized by name.
- Shell script outlines (functions) via `tree-sitter-bash`; files without a suffix are
  recognized by their shebang (`#!/usr/bin/env bash`, `#!/usr/bin/env python3`).
- Helm/Go templates in YAML (`{{ ... }}`) outline like plain YAML, with the original
  directives shown in signatures, so templated Kubernetes manifests are covered.
- Markdown outlines now use the tree-sitter grammar: setext headings are recognized and
  lines inside HTML blocks or indented code are no longer mistaken for headings. The
  regex provider remains as a fallback if a grammar cannot be loaded.

### Security

- Denied paths inside the roots no longer reveal whether they exist: a missing `.env`
  now reports "denied by policy" like an existing one, instead of "No such file".
- Outlining a Markdown or shell file can no longer crash the server on Linux. The
  tree-sitter-markdown and tree-sitter-bash scanners pass characters above U+00FF to
  `isdigit()`, which reads outside glibc's lookup table (e.g. `1` or `{` followed by an
  emoji). These grammars now parse a copy in which such characters are replaced with
  ASCII of the same byte length; symbol names still come from the original text.

### Changed

- New core dependencies: `tree-sitter` (pinned `>=0.25.2,<0.26`: 0.26.0 crashes with heap
  corruption during tree walks) and the Markdown, JSON, TOML, YAML and Bash grammars.

- Tool parameters no longer use nullable types, so input schemas contain no `anyOf`.
  "Unset" values are now `""` or `0` (for example `read_lines(end=-1)`,
  `grep(glob="")`, `replace_in_file(expected_count=0)` for any count). Results are
  unchanged.

## [0.1.0] - 2026-10-03

### Added

- Read-only tools: `file_info`, `read_lines`, `read_between`, `grep`, `list_directory`,
  `find_files`, `outline` (Python, Markdown) and `read_symbol`.
- Opt-in editing tools (`--allow-write`): `replace_in_file`, `edit_lines` and
  `write_file`, with etag checks, diffs, dry runs and atomic writes.
- Root confinement, case-insensitive deny list, ReDoS-safe regexes and output budgets.
- Configuration via CLI flags and `MCP_FS_*` environment variables.
- CI on Linux, macOS and Windows for Python 3.12–3.14, with dependency auditing.
- `ROADMAP.md` with the planned improvements and `AGENTS.md` with conventions for AI
  coding agents.
