# AGENTS.md

Instructions for AI coding agents working on this repository. Humans should read
`CONTRIBUTING.md`; this file is the short, strict version for agents.

**Project:** `mcp-file-system` is a Python 3.12+ MCP server that gives AI agents
fine-grained, partial access to files (line ranges, grep, outlines, guarded edits).
Security is the core feature, not an add-on.

## Hard rules (read these first)

1. Every path that comes from a tool call **must** go through `PathGuard`
   (`guard.resolve`, `guard.resolve_file`, `guard.resolve_dir`) before any filesystem
   access. Never call `open()`, `Path.read_*`, `os.scandir`, etc. on raw input.
2. Do **not** weaken the security controls: root confinement, the deny list, regex time
   budgets, size and output limits, or read-only by default. Any change under
   `src/mcp_file_system/security/` needs a new test in `tests/security/`.
3. Keep `src/mcp_file_system/core/` and `src/mcp_file_system/security/` free of `mcp`
   imports. Only `tools/`, `server.py`, and `cli.py` may import `mcp`. A test enforces
   this, and the layer direction under "Code conventions"
   (`tests/unit/test_import_boundaries.py`).
4. Implement `ROADMAP.md` items **in order**, one item per change. The next item is the
   first unticked one whose status does not defer it; an item whose status says its
   remaining part is deferred counts as deferred even if part of it shipped. Do not start
   any other item unless the human asks.

## Commands

Use `uv` for everything. Do not use `pip` directly in this repository.

```bash
uv sync --all-extras                      # install/refresh the dev environment (incl. [code])
uv run ruff format .                      # format
uv run ruff check . --fix                 # lint (and auto-fix)
uv run mypy src tests                     # strict type check
uv run pytest                             # all tests
uv run pytest tests/unit/test_reader.py   # one file
uv run pytest -k read_between             # tests matching a name
uv run pytest --cov                       # tests + coverage gate (fails below 90%)
```

All checks in one line (run this before saying a task is done):

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy src tests && uv run pytest --cov
```

## Definition of done

A change is finished only when **all** of these are true:

- [ ] The one-line check above passes with no errors.
- [ ] New behavior has tests; bug fixes have a test that fails without the fix.
- [ ] Public functions and classes have Google-style docstrings.
- [ ] User-visible changes have a `CHANGELOG.md` entry under `## [Unreleased]`.
- [ ] If a `ROADMAP.md` item was completed, its status checkbox is ticked and its **What**
      matches what shipped.
- [ ] User-visible changes (tools, flags, settings) are documented in `README.md`.
- [ ] Every doc that mentions what the change renames, adds, removes or re-measures is
      updated in the same commit: search `*.md`, `docs/`, docstrings and test names for
      the names and numbers involved. A new module under `core/`, `security/` or `tools/`
      also gets a row in the project map below and in `docs/ARCHITECTURE.md`
      (`tests/unit/test_conventions.py` checks both).
- [ ] Exact measured figures (listing sizes, savings, timings) appear only in `README.md`;
      `CHANGELOG.md` and `ROADMAP.md` may give an approximate ratio (e.g. "about half")
      but no exact numbers.

## Project map

| Path | Purpose |
|---|---|
| `src/mcp_file_system/cli.py` | Command-line entry point (`mcp-file-system`); `__main__.py` makes `python -m mcp_file_system` run it. |
| `src/mcp_file_system/server.py` | `create_server()`: builds the MCP server (transport-agnostic); `FileSystemServer` audits calls rejected before a tool runs. |
| `src/mcp_file_system/context.py` | `AppContext`: wires settings and services together. |
| `src/mcp_file_system/config.py` | `Settings` (CLI flags + `MCP_FS_*` env vars), `DEFAULT_DENY_GLOBS`. |
| `src/mcp_file_system/errors.py` | `FsError` and its subclasses (agent-facing errors). |
| `src/mcp_file_system/security/` | `PathGuard`, `AccessPolicy` (deny list), safe regex with deadlines. |
| `src/mcp_file_system/audit.py` | Optional audit log writer (one JSON line per tool call; content arguments as lengths only). |
| `src/mcp_file_system/core/` | Domain logic: reading (`reader`, `textio`, `tracking`, `etag`), search (`search`, `finder`, `ignore`, `templates`, `logsummary`), structure (`outline`, `skeleton`, `peek`, `query`), editing (`editor`, `patch`, `history`), results (`models`, `render`). |
| `src/mcp_file_system/tools/` | Thin MCP tool adapters, one module per group, each with `register()`; `_common.py` holds the `ToolRegistry`, shared parameter types and the audit wrapper. |
| `tests/unit/`, `tests/security/`, `tests/integration/` | Tests; shared helpers in `tests/conftest.py`. |
| `docs/ARCHITECTURE.md` | Layers, conventions, and how to extend (read before larger changes). |
| `ROADMAP.md` | Ordered list of planned work. |

## Code conventions

- **Layers:** `cli → server → tools → core → security`, with `context` as the
  composition root between `server`/`tools` and the services. `config`, `errors` and
  `audit` are leaves: `security` uses `errors`; `core` uses `security`, `config` and
  `errors`; `context` and `tools` may also use `audit`. Dependencies point downwards
  only (diagram in `docs/ARCHITECTURE.md`; enforced by
  `tests/unit/test_import_boundaries.py`).
- **Errors:** raise a subclass of `errors.FsError` with a message that tells the agent
  what to do next (e.g. "Use read_lines instead"). Never put paths outside the allowed
  roots, file contents of denied files, or stack traces in messages. A hint that a `lite`
  tool can return names only tools and parameters `lite` also has (tests in
  `tests/integration/test_profiles.py` check this).
- **Results:** services return a `core.models.ToolResult` subclass. Its `render()` returns
  compact text for the model; the model itself is the structured output.
- **Limits:** every result must be bounded, by a setting (`max_read_lines`,
  `max_output_chars`, `max_line_chars`, `max_matches`, `max_results`, `max_file_bytes`,
  `max_pattern_length`, `search_timeout_s`) or, for a fixed per-call cap, by a named module
  constant (e.g. `MAX_BATCH_RANGES`, `MAX_EDITS`). When output is cut, say so and say how
  to continue (for example `next_start`).
- **Lines:** a line ends at `\n`, `\r\n`, or a lone `\r`. Use the helpers in
  `core/textio.py` (`split_lines`, `open_lines`, `count_lines`); do not use
  `str.splitlines()`, which also splits on other characters
  (`tests/unit/test_conventions.py` scans `src/` for it).
- **Regex from users:** always compile with `security.compile_pattern(...)` and a
  `Deadline`. Never use the `re` module on agent-supplied patterns.
- **Style:** type hints everywhere (`mypy --strict` must pass), line length 100, no
  `print()` (stdout carries the MCP protocol; log to stderr via `logging`).

### Adding a tool (pattern to copy)

1. Put the logic in a `core` service and return a `ToolResult` subclass.
2. Register it in a `tools/` module:

```python
@registry.tool(READ_ONLY)  # WRITES for tools that modify files
def my_tool(
    path: PathArg,
    limit: Annotated[int, Field(ge=1, description="Maximum items to return.")] = 50,
) -> Annotated[CallToolResult, MyResult]:
    """One-sentence summary the model sees as the tool description.

    Optional second paragraph: when to use this tool instead of others.
    """
    return to_call_result(ctx.my_service.do_it(path, limit))
```

3. Add the module's `register(registry, ctx)` to the `"full"` entry of `READ_TOOLSETS`
   or `WRITE_TOOLSETS` in `tools/__init__.py`. Write tools go only in `WRITE_TOOLSETS`.
4. **Never** use `X | None = None` for tool parameters (it becomes an `anyOf` schema that
   small models handle poorly). Use `""` or `0` for "unset" and convert with
   `unset_str` / `unset_int` from `tools/_common.py`. Two tests enforce this:
   `test_input_schemas_have_no_nullable_unions` (`tests/integration/test_profiles.py`)
   rejects `anyOf` in any input schema, and `tests/unit/test_conventions.py` requires the
   `unset_*` call for every `""`/`0` default, except context line counts (`0` is a value)
   and `if_changed_since`/`key_path` (the service reads `""` as unset).
5. Do **not** add the tool to the `lite` profile (`tools/lite.py`) unless the human asks;
   `lite` must stay small.

## Test conventions

- Import helpers with `from conftest import write, symlink_or_skip, junction_or_skip, numbered`.
- Create test files with `write(path, content)`. It writes exact bytes, so tests behave
  the same on Linux, macOS, and Windows. Do not rely on platform newline translation.
- Use the `root` fixture for the workspace and `outside` for a directory that must stay
  unreachable. Use `make_ctx(**settings)` to change limits.
- Create symlinks only through `symlink_or_skip(link, target)` (Windows may forbid them),
  and Windows junctions only through `junction_or_skip(link, target)` (skipped elsewhere).
  `tests/unit/test_conventions.py` fails on direct `write_text`, `symlink_to` and similar
  calls outside `conftest.py`.
- Mark OS-specific tests with `pytest.mark.skipif(...)`.
- Test file names must be unique across all test directories (there are no
  `__init__.py` files).
- No network access in tests.

## Commits

- Format: [Conventional Commits](https://www.conventionalcommits.org/), e.g.
  `feat(outline): add TOML provider`, `fix(security): ...`, `docs: ...`.
- One logical change per commit; every commit must pass the one-line check.
- Explain *why* in the commit body, not only what.

## When to stop and ask the human

- Any trade-off that relaxes a security control, even temporarily.
- Renaming or removing a tool, a tool parameter, a CLI flag, or a setting (breaking change).
- Anything destructive outside the repository.

## If this server's own tools are available to you

Use them the way this project intends: locate with `grep` (try `output_mode="count"`),
`find_files`, or `outline` first; then read only the needed part with `read_symbol`,
`read_lines`, or `read_between`. Do not re-read lines that are already in your context;
compare etags with `file_info` to check whether a file changed.
