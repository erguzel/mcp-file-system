# mcp-file-system

A secure [Model Context Protocol](https://modelcontextprotocol.io) server that gives AI agents
**fine-grained, partial access to files**. Agents can read exact line ranges, grep with
context, pull out a section between two patterns, or read one function by name, without
loading whole files into their context window.

- **Partial reads:** numbered line ranges (with tail), `sed -n '/a/,/b/p'`-style sections,
  symbol-level reads for Python, Markdown, JSON, TOML, YAML (including Helm templates),
  shell scripts and Dockerfiles, plus JavaScript/TypeScript, Go and Java with the
  `[code]` extra (e.g. read just `tool.ruff.lint` from a `pyproject.toml`).
- **Search:** recursive grep with context, `count` / `files_with_matches` / `collapsed` modes and globs.
  It honors `.gitignore` and skips binaries.
- **Navigation:** depth-limited trees, glob finds, file metadata (lines, encoding, newline
  style).
- **Guarded edits (opt-in):** unique-match replace, line-range edits and file creation.
  Every edit checks an etag, returns a unified diff, supports dry runs and writes atomically.
- **Secure by default:**
  - access is confined to the configured roots, symlink escapes are rejected, and a deny list covers secrets
  - regular expressions run with a time budget, so a bad pattern cannot hang the server (no ReDoS)
  - output is capped by size limits
  - the server is read-only unless you enable writes

## Installation

Requires Python 3.12+.

```bash
# Into a project's virtual environment, from a local checkout
pip install /path/to/mcp-file-system

# Straight from the repository
pip install "git+https://github.com/erguzel/mcp-file-system.git"

# Or run without installing (uv)
uvx --from "git+https://github.com/erguzel/mcp-file-system.git" mcp-file-system --help
```

Outlines for JavaScript/TypeScript, Go and Java come from the optional `[code]` extra
(small, pinned grammar packages; nothing is downloaded at runtime):

```bash
pip install "mcp-file-system[code] @ git+https://github.com/erguzel/mcp-file-system.git"
uv tool install --editable "mcp-file-system[code] @ /path/to/mcp-file-system"
```

Without it, those files still work with every reading and search tool; `outline`
explains how to add the extra.

## Usage

The server speaks MCP over stdio:

```bash
mcp-file-system --root /path/to/project                  # read-only
mcp-file-system --root /path/to/project --allow-write    # enable editing tools
mcp-file-system --root /path/to/project --profile lite   # small local models
```

Without `--root`, the current working directory is the root. Repeat `--root` to allow
several directories. Relative paths in tool calls resolve against the first root.

### Claude Code

```bash
claude mcp add files -- mcp-file-system --root "$PWD"
# or, without installing:
claude mcp add files -- uvx --from "git+https://github.com/erguzel/mcp-file-system.git" \
    mcp-file-system --root "$PWD"
```

### Claude Desktop and other JSON-configured clients

```json
{
  "mcpServers": {
    "files": {
      "command": "mcp-file-system",
      "args": ["--root", "/absolute/path/to/project"]
    }
  }
}
```

### Profiles: `full` and `lite`

Every tool definition (name, description and parameter schema) is sent to the model with
every request. Large models handle the full set easily. Small local models (roughly
7–14B, e.g. in LM Studio) tool-call more reliably and keep more context free with less.

| | `full` (default) | `lite` |
|---|---|---|
| Tools | all 13 (+6 editing) | 7 (+3 editing): no `multi_replace`, `apply_patch`, `undo_last_edit`, `read_ranges`, `read_between`, `read_skeleton`, `peek`, `query`, `log_summary` |
| Parameters | all | essential only; hidden ones keep their defaults |
| Output schemas | published | omitted (results still carry structured content) |
| Tool listing size* | ~35k characters | ~7.1k characters |

\* With editing enabled, measured on version 0.1.x.

`lite` is narrow but complete: it still covers navigating, searching, sizing up, reading
parts of files and editing, so a small model never needs detours. Its `grep` searches
an invalid regular expression as literal text instead of failing, its `replace_in_file`
requires a unique literal match, and its `write_file` only creates new files.

Use `--tools grep,read_lines,...` to expose only some tools of a profile, and
`--[no-]output-schema` to override the output-schema default.

## Tools

| Tool | Purpose |
|---|---|
| `file_info` | Size, approximate token count, line count, encoding, newline style, binary flag and **etag** of a file (or entry count of a directory). |
| `read_lines` | Numbered lines `start..end`. Negative values count from the end (`start=-50` is a tail). `around=N` (full profile) reads `N-radius..N+radius` instead (`radius` defaults to 10), for line numbers from stack traces or grep hits. Continues via `next_start`. `if_changed_since=<etag>` returns a one-line "unchanged" answer instead of lines you already have. |
| `read_ranges` | Several `(path, start, end)` ranges, from one or more files, in one call (at most 20). The ranges share one output budget (`max_read_lines`, `max_output_chars`), spent in order; ranges cut by the budget are listed with a ready-made `read_ranges` call to continue. A failing range is reported without stopping the others. |
| `read_between` | Lines from the Nth match of `start_pattern` to the next `end_pattern`, like `sed -n '/a/,/b/p'`. |
| `grep` | Regex or literal search in a file or directory tree, with context lines, `only_matching`, globs and four output modes. Hits show their enclosing symbol (`[Parser.parse]`, `[jobs.test.steps[0]]`), ready for `read_symbol`. `output_mode="collapsed"` (full profile) groups matching lines that differ only in numbers, hex values, UUIDs, IPs or timestamps into templates with counts and first/last location, e.g. `[x532] <TS> ERROR timeout after <N>ms to <IP>`. |
| `list_directory` | Indented tree with sizes, depth-limited. |
| `find_files` | Gitignore-style glob search for files or directories. |
| `outline` | Code symbols with signatures (Python; JS/TS, Go, Java with `[code]`), shell functions, Dockerfile stages and instructions, Markdown headings (incl. setext), or key trees with inline scalar values (JSON, TOML, YAML incl. Helm templates), all with line spans. `Dockerfile`/`Containerfile` are recognized by name and extensionless scripts by their shebang. |
| `read_skeleton` | A code file with function bodies folded (imports, signatures, docstrings and class structure stay); each fold shows its line range for `read_lines`. Python, shell, and JS/TS, Go, Java with `[code]`. |
| `peek` | The *shape* of a data file instead of its content: CSV/TSV columns, guessed types, row count and 3 sample rows; JSON/TOML key trees with types and array lengths (`items: array[1532] of {id: int, name: str}`); log time span and level counts; name, version and dependencies of `pyproject.toml`, `package.json`, `Cargo.toml`; Markdown front matter and heading counts. |
| `log_summary` | A log file as its most frequent line templates: numbers, hex values, UUIDs, IPs and timestamps are masked, so thousands of similar lines become one row such as `[x532] <TS> ERROR timeout after <N>ms to <IP>` with first and last line numbers. Streams the file within the search time budget; a partial summary names the `start` line to continue from. |
| `query` | One value of a JSON (incl. comments), YAML (incl. multi-document and Helm templates) or TOML file by key path, e.g. `dependencies.react`, `jobs.test.steps[0]`, `"app.kubernetes.io/name"`, `[1].kind`. Nested values are shown to `depth` levels (default 2), deeper ones collapse to `{3 keys}` / `[40 items]`; every row uses the key-path syntax, so the agent can drill down. Works on minified files such as `package-lock.json`. YAML aliases are shown as written, not resolved. |
| `read_symbol` | The source of one symbol, e.g. `Parser.parse`, `Usage > Install`, `tool.ruff.lint` or `jobs.test.steps[0]`. Accepts `if_changed_since` like `read_lines`. |
| `replace_in_file`* | Literal or regex replacement that must match exactly `expected_count` times (default 1). |
| `multi_replace`* | Several exact-text replacements in one file, applied in order and written once: all or none (full profile, at most 50). Each must match `expected_count` times (default 1, 0 = any); a failing edit leaves the file unchanged and is named in the error. |
| `edit_lines`* | Replace, insert or delete a line range. Requires the etag from the read that showed those lines. |
| `apply_patch`* | Apply a unified diff to one file, all hunks or none (full profile). Requires the file's etag. Context and removed lines must match exactly; a hunk whose line numbers are off is placed at the nearest exact match and its offset reported, while missing or ambiguous context fails the patch and names the hunk. `dry_run=true` lists which hunks would fail and why. Header counts are recomputed from the hunk body; new files go through `write_file`. |
| `write_file`* | Create a file (with parent directories), or overwrite one when its etag is supplied. |
| `undo_last_edit`* | Restore the exact bytes from before this server's latest edit of a file (full profile). Refused if the file changed since that edit. One level per file, no redo, kept in memory for the server session only (at most 100 files and 64 MiB). A file the server created is not deleted but renamed to `<name>.deleted-<UTC time>` next to it; in a git project it shows up as untracked, so delete it by hand once it is no longer needed. |

\* Only registered with `--allow-write`.

Each tool returns compact text for the model and the same data as structured content,
so clients can also consume results programmatically.

Reads are **change-aware** within a session: re-reading lines that were already sent
(and have not changed) adds a short note, and `if_changed_since=<etag>` skips resending
them entirely. The "unchanged" answer is only given for lines this session really
received, so a misused etag never leaves the model without content.

A typical agent workflow:

1. `grep(pattern, output_mode="count")` to see where something lives.
2. `outline(path)` or `file_info(path)` to size up the file.
3. `read_symbol` / `read_lines` / `read_between` for just the relevant part.
4. `replace_in_file` or `edit_lines(..., expected_etag=...)`, optionally with
   `dry_run=true` first; `multi_replace` or `apply_patch` for several changes at once.
5. `undo_last_edit(path)` if an edit went wrong.

## Configuration

Every option can be set on the command line or through an `MCP_FS_*` environment variable.
CLI flags take precedence. Complex values in the environment are JSON.

| Setting | CLI flag | Default | Description |
|---|---|---|---|
| `roots` | `--root DIR` (repeatable) | CWD | Allowed directories (`MCP_FS_ROOTS='["/a","/b"]'`). |
| `allow_write` | `--allow-write` | `false` | Register the editing tools. |
| `deny_globs` | `--deny GLOB` (adds) | secrets list | Gitignore-style patterns that are never accessible. |
| `respect_gitignore` | `--[no-]gitignore` | `true` | Hide ignored paths from walks and searches. |
| `max_file_bytes` | `--max-file-bytes` | 10 MiB | Largest file loaded fully (outline, symbols, edits). |
| `max_read_lines` | `--max-read-lines` | 2000 | Most lines per read. |
| `max_output_chars` | `--max-output-chars` | 50000 | Text budget per tool result. |
| `max_line_chars` | | 2000 | Longer lines are clipped (around the match, for grep). |
| `max_matches` | `--max-matches` | 500 | Upper bound for grep hits. |
| `max_results` | `--max-results` | 1000 | Upper bound for listings and finds. |
| `max_pattern_length` | | 1000 | Longest accepted regex. |
| `search_timeout_s` | `--search-timeout` | 10 | Wall-clock budget per search or regex operation. |
| `log_level` | `--log-level` | `WARNING` | Logs always go to stderr. |
| `profile` | `--profile` | `full` | Tool set: `full` or `lite` (see [Profiles](#profiles-full-and-lite)). |
| `tools` | `--tools NAME,...` | all | Only expose these tools of the profile (`MCP_FS_TOOLS=grep,read_lines`). |
| `output_schema` | `--[no-]output-schema` | by profile | Publish output schemas (on for `full`, off for `lite`). |
| `audit_log` | `--audit-log FILE` | off | Append one JSON line per tool call (see [Audit log](#audit-log)). |

## Audit log

`--audit-log FILE` appends one JSON line per tool call, for you rather than the model.
It never changes tool results or tool definitions, so it costs no tokens, and agents
cannot read it: the file must be outside the allowed roots (startup fails otherwise).

```json
{"ts": "2026-10-04T12:00:01Z", "tool": "read_lines", "profile": "lite",
 "args": {"path": "src/a.py", "start": 10, "end": 40},
 "duration_ms": 2.5, "result_chars": 1240, "truncated": false, "error": null}
```

- **No file contents:** content-carrying arguments (`replace_in_file`'s pattern and
  replacement, `edit_lines`' new text, `apply_patch`'s diff, `write_file`'s content) are
  logged as `{"chars": N}`; `multi_replace`'s edits as `{"items": N, "chars": N}`. Errors are logged with their type, e.g. `StaleFileError`.
- **Cost:** about 25 µs and 200 bytes per call (≈1 MB per 5,000 calls). Each record is
  appended and the file closed again, so external log rotation is safe.
- **Failure handling:** if the log cannot be written (e.g. a full disk), the tool call still
  succeeds and a warning goes to stderr.

```bash
jq -r .tool audit.jsonl | sort | uniq -c | sort -rn   # most used tools
jq 'select(.error != null)' audit.jsonl               # failed calls
```

## Security model

See [SECURITY.md](SECURITY.md) for the threat model. In short:

- Every path goes through one choke point (`PathGuard`). It resolves symlinks and requires
  the real path to lie inside a root. Errors never reveal whether paths outside the roots
  exist.
- A case-insensitive deny list blocks `.env*`, keys and certificates, `.git/`, `.ssh/`,
  cloud and package-manager credentials, and similar files, even through symlinks.
  `.gitignore` only hides paths from walks; gitignored files can still be read by
  explicit path.
- Regular expressions use the `regex` engine with a time budget. Pathological patterns
  stop with partial results instead of hanging the server.
- Writes are off by default. When enabled, they are confined like reads, checked against
  etags (re-verified immediately before writing) and written atomically. They preserve
  encoding, BOM, newline style and permissions.

## Development

```bash
uv sync --all-extras        # create .venv with all dev tools and optional grammars
uv run pytest --cov         # tests (coverage gate: 90%)
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests       # strict typing
uv run pre-commit install   # optional git hooks
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
which covers how to add tools, outline languages or search backends. Planned work is in
[ROADMAP.md](ROADMAP.md). AI coding agents should follow [AGENTS.md](AGENTS.md).

## License

MIT, see [LICENSE](LICENSE).
