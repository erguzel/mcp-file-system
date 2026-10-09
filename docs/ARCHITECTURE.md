# Architecture

## Layers

```
cli.py ──► server.py ──► tools/*  (thin MCP adapters: schema, description, error translation, audit)
               │             │
               └──► context.py (composition root: builds and wires every service)
                             │
                 core/*  (domain logic, no MCP imports) ──► security/*
                             │
                          config.py (immutable Settings)
```

Dependencies only point downwards. `core`, `security` and `audit` never import `mcp`, so
they can be unit-tested and reused (e.g. from a CLI or another protocol) without a server.

| Module | Responsibility |
|---|---|
| `config.Settings` | Frozen configuration from CLI flags, `MCP_FS_*` env vars and defaults. |
| `security.PathGuard` | The single choke point for agent-supplied paths: root confinement, symlink resolution, deny policy. |
| `security.AccessPolicy` | Case-insensitive, gitignore-style deny list. |
| `security.regex` | `compile_pattern` / `SafePattern`: regexes evaluated against a shared `Deadline`. |
| `core.textio` | Encoding/newline detection, streaming lines, fast line counting, line splitting. |
| `core.etag` | `compute_etag` / `ensure_etag`: version stamps from `stat` that edits must present back. |
| `core.render` | Output helpers shared by result models: `clip_line`, `number_lines`, `truncate_text`, `human_size`, `human_count`. |
| `core.reader.FileReader` | `file_info`, `read_lines`, `read_ranges`, `read_between`. Directory entry counts go through `Walker`, so they honor the deny policy. |
| `core.finder` | `Walker` (policy-, hidden- and gitignore-aware walk), `FileFinder` (find, list, file selection). |
| `core.ignore.IgnoreRules` | Hierarchical `.gitignore` evaluation for walks; hides paths, never blocks direct reads. |
| `core.search` | `Searcher` service plus the `SearchBackend` strategy (`PythonSearchBackend`). |
| `core.templates` | `mask_line` (fixed masks for timestamps, UUIDs, IPs, hex values, numbers) and the bounded `TemplateTally`; shared by grep's `collapsed` mode and `log_summary`. |
| `core.logsummary.LogSummarizer` | `log_summary`: streams one file into a `TemplateTally` within the search time budget, with a `start` line to continue. |
| `core.outline` | `OutlineProvider` protocol, `OutlineRegistry`, `Outliner` service; Python (`ast`) and Dockerfile (pure Python) providers, regex Markdown fallback. |
| `core.outline.treesitter` | Tree-sitter providers: Markdown, JSON, TOML, YAML (Helm templates masked), shell (`TreeSitterOutlineProvider` base); `code.py` holds the optional `[code]` languages (JS/TS, Go, Java). |
| `core.peek` | `Peeker` service behind `peek`: shape summaries per format (`tabular`, `structured`, `text` modules) chosen by file name or suffix; streamed formats have fixed scan budgets. |
| `core.query` | `DataQuery` service behind `query`: key-path parsing (`keypath`), a `DataNode` view per format (`nodes`: lazy tree-sitter views for JSON and YAML with line spans, `tomllib` values for TOML) and depth-limited rendering (`render`). |
| `core.editor.FileEditor` | Guarded edits: etag checks, diffs, atomic writes, format preservation. |
| `core.history` | `EditHistory` for `undo_last_edit`: the bytes before each file's latest edit plus the etag after it; one level, in memory, LRU-bounded by file count and bytes. Built only when the tool is exposed. |
| `core.patch` | Pure unified-diff parsing (`parse_patch`) and all-or-nothing hunk placement (`apply_hunks`): exact context, nearest-offset search without fuzz, untouched line endings kept; reports never quote lines. |
| `core.models` | Result models. Each is the tool's structured output schema and has a `render()` text view. |
| `core.skeleton` | Language-independent skeleton rendering from fold ranges reported by providers (`fold_ranges`). |
| `core.tracking.ReadTracker` | Session memory of which lines of which file version were returned; powers "already returned" notes and `if_changed_since`. One per server process (one stdio session); a multi-session transport must key it by session. |
| `audit.AuditLog` | Optional JSON Lines audit log writer (no `mcp` imports); `tools._common.audited` wraps every exposed tool to record into it when enabled. |
| `tools/*` | One module per tool group, each exposing `register(registry, ctx)`; `tools/lite.py` holds the `lite` profile. |
| `tools._common.ToolRegistry` | Registers tools, applying the `tools` allowlist, the output-schema setting, error translation and audit wrapping. |

## Conventions

- **Line semantics** are identical everywhere: lines end at `\n`, `\r\n` or a lone `\r`,
  and a trailing terminator does not create an empty last line (`core.textio`).
- **Errors:** services raise subclasses of `errors.FsError` with agent-facing,
  actionable messages. `tools._common.translate_errors` turns them (and sanitized
  `OSError`s) into MCP tool errors. Any other exception is reported by the SDK as a
  generic failure and logged with its traceback to stderr.
- **Budgets:** every result is bounded, by a setting (`max_read_lines`, `max_output_chars`,
  `max_line_chars`, `max_matches`, `max_results`, `max_file_bytes`, `max_pattern_length`,
  `search_timeout_s`) or by a named module constant for fixed per-call caps (e.g.
  `MAX_BATCH_RANGES`, `MAX_EDITS`). When a result is truncated, it says so and tells the
  agent how to continue.
- **Etags** are `"{mtime_ns:x}-{size:x}"`, which needs only a `stat` call. Readers return
  them; editors require them back.
- **Small-model-friendly schemas:** tool parameters never use `X | None = None` (it
  becomes an `anyOf` schema). "Unset" is an empty string or `0`, mapped back to `None`
  by `unset_str` / `unset_int` before calling the service.
- **Profiles:** `full` exposes everything; `lite` is a narrow but complete subset with
  essential parameters and short descriptions (`tools/lite.py`). Both call the same
  services, so behavior is identical for the same arguments, except that `lite`'s `grep`
  retries an invalid regular expression as literal text.

## Extension points

### Add a tool

1. Implement the behavior in a `core` service (or extend one) and return a
   `core.models.ToolResult` subclass with a `render()` method.
2. Create `tools/<group>.py` with `register(registry, ctx)`. In it, define a sync function
   decorated with `@registry.tool(READ_ONLY)` (or `WRITES`). Its return type is
   `Annotated[CallToolResult, YourResult]` and its body returns `to_call_result(...)`.
   Describe parameters with `Annotated[..., Field(description=...)]`; the docstring
   becomes the tool description. Use concrete defaults, not `None`.
3. Add `register` to the `full` entry of `READ_TOOLSETS` or `WRITE_TOOLSETS` in
   `tools/__init__.py`. Add a `lite` variant only if the tool is essential for small
   models (keep `lite` small).

Sync tools run in a worker thread (the SDK uses `anyio.to_thread`), so blocking file
I/O never stalls the event loop.

### Add an outline language

Implement the `OutlineProvider` protocol (`language`, `extensions`, `outline(text)`) and
register it:

```python
from mcp_file_system.context import AppContext
from mcp_file_system.core.outline import default_registry
from mcp_file_system.server import create_server

registry = default_registry()
registry.register(MyTomlOutlineProvider())
server = create_server(context=AppContext.from_settings(settings, outline_registry=registry))
```

Code providers can add `fold_ranges(text)` (1-based inclusive ranges, usually function
bodies) to support `read_skeleton`; tree-sitter providers get it from `block_folds()` by
listing their function node types.

Providers may also declare `filenames` (lower-case globs such as `"dockerfile.*"`) and
`interpreters` (e.g. `frozenset({"bash", "sh"})`). The registry matches by suffix first,
then by file name, then by the interpreter in a shebang line. Providers whose grammar
lives in an optional extra are registered only if the import succeeds; list their
suffixes in `registry.OPTIONAL_SUFFIXES` so `outline` can tell users which extra to
install.

For a language with a tree-sitter grammar, subclass `TreeSitterOutlineProvider`
(`core/outline/treesitter/_base.py`): implement `_grammar()` and
`_extract(root, source)`, reading node text with `node_text(node, source)`. Override
`_prepare(source)` to transform the input before parsing without changing byte offsets
(the YAML provider masks `{{ ... }}` template directives this way). The
base class normalizes line endings and creates a fresh `Parser` per call, because
parsers are not thread-safe and tools run in worker threads. Add the provider to
`tests/unit/test_outline_fuzz.py`: tree-sitter is a C extension, so a bug there crashes
the server process instead of raising, and only such tests catch it before users do.

Before adding a grammar, grep its external scanner (`src/scanner.c`) for `<ctype.h>`
classifiers such as `isdigit()` or `isalpha()` applied to the lookahead character. If
there are any, set `masks_wide_chars = True` on the provider. glibc implements these
classifiers as a table lookup defined only for -128..255, so a wider code point reads
outside the table: the crash happens only on Linux and only intermittently, so tests on
macOS or a lucky CI run will not show it. The Markdown and Bash providers set the flag
for this reason.

### Add a search backend

Implement `SearchBackend.search(files, request, deadline) -> GrepResult`. Backends get
files that the walker has already vetted, so security policy stays in one place. A
ripgrep backend, for example, would pass the explicit file list to `rg`. Plug it in with
`AppContext.from_settings(settings, search_backend=MyBackend())`.

## Deploying as an HTTP server (future)

`create_server()` does not depend on the transport. Serving over HTTP needs a CLI switch
around `server.run("streamable-http", host=..., port=...)` and the checklist below:

- per-session state: `ReadTracker` and `EditHistory` exist once per process today and
  must be keyed by session, or one client sees another's "already returned" notes and
  undo history,

- authentication (bearer token or OAuth via the SDK's `auth`/`token_verifier` options),
- binding to `127.0.0.1` behind a reverse proxy with TLS,
- a container image that runs as a non-root user, with the workspace mounted (read-only
  unless writes are needed),
- a health endpoint and request logging to stderr.
