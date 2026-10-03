# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
