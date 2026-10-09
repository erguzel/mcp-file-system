# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `undo_last_edit` tool (full profile, `--allow-write`): restores the exact bytes from
  before this server's latest edit of a file, refused if the file changed since. One
  level per file, no redo, in memory for the server session (at most 100 files and
  64 MiB; no snapshots are kept when the tool is not exposed). A file the server created
  is renamed to `<name>.deleted-<UTC time>` instead of being deleted; a name blocked by
  the deny list leaves the file unchanged.
- `multi_replace` tool (full profile, `--allow-write`): up to 50 exact-text replacements
  in one file, applied in order and written once, so either all apply or none. Each
  edit must match its `expected_count` (default 1, 0 = any); a failing edit leaves the
  file unchanged and the error names it (`edit 2 of 3: ...`). The growing text is
  checked against `max_file_bytes` before every edit. The edits are logged as their
  count and total length only.
- `apply_patch` tool (full profile, `--allow-write`): applies a unified diff to one file,
  all hunks or none, guarded by the file's etag. Context and removed lines must match
  exactly; hunks whose line numbers are off are placed at the nearest exact match (no
  fuzz) and the offset is reported. Missing or ambiguous context fails the patch and the
  error names each failed hunk; `dry_run=true` reports which hunks would fail and why.
  Untouched lines keep their own line endings. The diff is logged as its length only.
- `read_ranges` tool (full profile): reads up to 20 `(path, start, end)` ranges from one
  or more files in one call, saving round trips. The ranges share one budget
  (`max_read_lines`, `max_output_chars`), spent in the given order; ranges cut by it
  are listed with the lines still to read and a ready-made `read_ranges` call to
  continue. A failing range (missing file, denied path, invalid range) is reported
  without stopping the others.
- `read_lines(around=N, radius=R)` (full profile): reads the lines around one line
  number, e.g. from a stack trace or grep hit, without computing `start`/`end`. The
  window is clamped to the file; `radius` defaults to 10 and is shrunk (with a note) when
  the window would exceed `max_read_lines`. Combining `around` with `start`/`end` is an
  error.
- `log_summary` tool (full profile): the most frequent line templates of a log file
  (masked as in `grep`'s collapsed mode) with counts and first/last line numbers. The
  file is streamed within `search_timeout_s`; when the budget runs out, the summary
  covers the lines scanned and names the `start` line to continue from. Output honors
  `max_results`, `max_line_chars` and `max_output_chars`; at most 10,000 distinct
  templates are kept.
- `grep(output_mode="collapsed")` (full profile): matching lines that differ only in
  numbers, hex values, UUIDs, IPv4/IPv6 addresses or timestamps are grouped into
  templates such as `[x532] <TS> ERROR timeout after <N>ms to <IP>`, with their count,
  file count and first/last location. Every matching line is counted within the time
  budget; `max_matches` limits the templates shown. Context lines are rejected in this
  mode. `lite`'s grep keeps its three modes.
- `query` tool (full profile): one value of a JSON/JSONC, YAML or TOML file by key path
  (`dependencies.react`, `jobs.test.steps[0]`, `"a.b".c`, `[-1]`, `[1].kind` for YAML
  document 1), shown to `depth` levels with deeper containers collapsed. Missing keys
  list the keys that exist; output honors `max_output_chars`, `max_line_chars` and
  `max_file_bytes`. JSON and YAML report the value's line span and tolerate syntax
  errors elsewhere in the file (with a note); YAML Helm templates are supported.
- `peek` tool (full profile): the shape of a data file without its content. CSV/TSV (columns, guessed
  types, row count, sample rows), JSON and TOML (key tree with types and array lengths,
  no values), logs (time span, level counts), manifests (`pyproject.toml`,
  `package.json`, `Cargo.toml`: name, version, dependencies) and Markdown (front matter,
  heading counts). CSV and logs are streamed with fixed scan budgets; JSON and TOML
  honor `max_file_bytes`.
- Tool profiles: `--profile lite` exposes a small but complete tool set with essential
  parameters and short descriptions for small local models (the tool listing shrinks to
  about a sixth of `full`'s). `lite`'s `grep` searches invalid regexes literally.
- `--tools NAME,...` (`MCP_FS_TOOLS`) allowlist and `--[no-]output-schema`
  (`MCP_FS_OUTPUT_SCHEMA`); output schemas default to off in `lite`.
- `file_info` reports an approximate token count for text files.
- `--audit-log FILE` (`MCP_FS_AUDIT_LOG`): one JSON line per tool call (tool, arguments,
  duration, result size, truncation, error type and message). File contents are never
  logged as arguments, and the file must be outside the roots.

- Outlines for JSON, TOML and YAML: key trees with line spans and inline scalar values,
  so `read_symbol("tool.ruff.lint")` or `read_symbol("jobs.test.steps[0]")` returns just
  that section. TOML tables include their directly following sub-tables.
- `read_skeleton` (full profile): a code file with function and method bodies folded to
  one `<start>-<end> ...` row each, keeping imports, signatures, docstrings and class
  structure. Python, shell, and JS/TS, Go and Java with `[code]`. About half the size of
  the full file on this (docstring-heavy) codebase.
- Change-aware reads: `read_lines` and `read_symbol` accept `if_changed_since=<etag>` and
  return a one-line "unchanged" answer when the file is unchanged and those lines were
  already returned in this session (otherwise content is sent, with a note). Re-reading
  already-returned lines adds an "already returned" note. Available in `lite` too.
- `grep` hits show their innermost enclosing symbol (functions, methods, config key
  paths, Markdown sections, Dockerfile instructions), so the agent can go straight to
  `read_symbol`.
- Optional `[code]` extra: outlines for JavaScript, TypeScript/TSX, Go (methods qualified
  by receiver, e.g. `Server.Handle`) and Java (nested types: `Outer.Inner.method`), using
  individual grammar packages. Without the extra, `outline` names the extra to
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

### Fixed

- Editing a file with text its encoding cannot store (e.g. `ş` or `€` in a legacy file
  read as Latin-1, or an unpaired surrogate) no longer fails with a generic tool error
  and a stack trace on stderr. All editing tools now report which encoding is involved
  and what to do, and leave the file unchanged.
- `.gitignore` files are split into patterns at line ends only. Characters such as a form
  feed inside a pattern no longer split it into two patterns that could hide unrelated
  paths from walks and searches.

### Security

- Denied paths inside the roots no longer reveal whether they exist: a missing `.env`
  now reports "denied by policy" like an existing one, instead of "No such file".
- `file_info` on a directory no longer reveals denied entries. It counted every entry
  (a directory holding only `.env` showed "entries: 1"), and its size and modification
  time change with each entry. It now counts what `list_directory(include_hidden=true)`
  shows, so gitignored entries, FIFOs and sockets, and symlinks leaving the roots are no
  longer counted either; `size_bytes` and `modified` are `null` for directories.
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
