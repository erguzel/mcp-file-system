# Architecture

## Layers

```
cli.py ──► server.py ──► tools/*  (thin MCP adapters: schema, description, error translation)
               │             │
               └──► context.py (composition root: builds and wires every service)
                             │
                 core/*  (domain logic, no MCP imports) ──► security/*
                             │
                          config.py (immutable Settings)
```

Dependencies only point downwards. `core` and `security` never import `mcp`, so they
can be unit-tested and reused (e.g. from a CLI or another protocol) without a server.

| Module | Responsibility |
|---|---|
| `config.Settings` | Frozen configuration from CLI flags, `MCP_FS_*` env vars and defaults. |
| `security.PathGuard` | The single choke point for agent-supplied paths: root confinement, symlink resolution, deny policy. |
| `security.AccessPolicy` | Case-insensitive, gitignore-style deny list. |
| `security.regex` | `compile_pattern` / `SafePattern`: regexes evaluated against a shared `Deadline`. |
| `core.textio` | Encoding/newline detection, streaming lines, fast line counting, line splitting. |
| `core.reader.FileReader` | `file_info`, `read_lines`, `read_between`. |
| `core.finder` | `Walker` (policy-, hidden- and gitignore-aware walk), `FileFinder` (find, list, file selection). |
| `core.search` | `Searcher` service plus the `SearchBackend` strategy (`PythonSearchBackend`). |
| `core.outline` | `OutlineProvider` protocol, `OutlineRegistry`, Python/Markdown providers, `Outliner` service. |
| `core.editor.FileEditor` | Guarded edits: etag checks, diffs, atomic writes, format preservation. |
| `core.models` | Result models. Each is the tool's structured output schema and has a `render()` text view. |
| `tools/*` | One module per tool group, each exposing `register(registry, ctx)`; `tools/lite.py` holds the `lite` profile. |
| `tools._common.ToolRegistry` | Registers tools, applying the `tools` allowlist, the output-schema setting and error translation. |

## Conventions

- **Line semantics** are identical everywhere: lines end at `\n`, `\r\n` or a lone `\r`,
  and a trailing terminator does not create an empty last line (`core.textio`).
- **Errors:** services raise subclasses of `errors.FsError` with agent-facing,
  actionable messages. `tools._common.translate_errors` turns them (and sanitized
  `OSError`s) into MCP tool errors. Any other exception is reported by the SDK as a
  generic failure and logged with its traceback to stderr.
- **Budgets:** every result is bounded (`max_read_lines`, `max_output_chars`,
  `max_matches`, `max_results`, `search_timeout_s`). When a result is truncated, it
  says so and tells the agent how to continue.
- **Etags** are `"{mtime_ns:x}-{size:x}"`, which needs only a `stat` call. Readers return
  them; editors require them back.
- **Small-model-friendly schemas:** tool parameters never use `X | None = None` (it
  becomes an `anyOf` schema). "Unset" is an empty string or `0`, mapped back to `None`
  by `unset_str` / `unset_int` before calling the service.
- **Profiles:** `full` exposes everything; `lite` is a narrow but complete subset with
  essential parameters and short descriptions (`tools/lite.py`). Both call the same
  services, so behavior is identical for the same arguments.

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

### Add a search backend

Implement `SearchBackend.search(files, request, deadline) -> GrepResult`. Backends get
files that the walker has already vetted, so security policy stays in one place. A
ripgrep backend, for example, would pass the explicit file list to `rg`. Plug it in with
`AppContext.from_settings(settings, search_backend=MyBackend())`.

## Deploying as an HTTP server (future)

`create_server()` does not depend on the transport. Serving over HTTP needs only a CLI
switch around `server.run("streamable-http", host=..., port=...)`. The production
checklist for that step:

- authentication (bearer token or OAuth via the SDK's `auth`/`token_verifier` options),
- binding to `127.0.0.1` behind a reverse proxy with TLS,
- a container image that runs as a non-root user, with the workspace mounted (read-only
  unless writes are needed),
- a health endpoint and request logging to stderr.
