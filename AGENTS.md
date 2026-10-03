# AGENTS.md

Instructions for AI coding agents working on this repository. Humans should read
`CONTRIBUTING.md`; this file is the short, strict version for agents.

**Project:** `mcp-file-system` is a Python 3.12+ MCP server that gives AI agents
fine-grained, partial access to files (line ranges, grep, outlines, guarded edits).
Security is the core feature, not an add-on.

## Hard rules (read these first)

1. **Never** commit, push, tag, merge, rebase, or rewrite git history unless the human
   explicitly asks for it in the current conversation.
2. Write all code, comments, docstrings, documentation, and commit messages in **English**.
3. Every path that comes from a tool call **must** go through `PathGuard`
   (`guard.resolve`, `guard.resolve_file`, `guard.resolve_dir`) before any filesystem
   access. Never call `open()`, `Path.read_*`, `os.scandir`, etc. on raw input.
4. Do **not** weaken the security controls: root confinement, the deny list, regex time
   budgets, size and output limits, or read-only by default. Any change under
   `src/mcp_file_system/security/` needs a new test in `tests/security/`.
5. Keep `src/mcp_file_system/core/` and `src/mcp_file_system/security/` free of `mcp`
   imports. Only `tools/`, `server.py`, and `cli.py` may import `mcp`.
6. Do **not** add or upgrade dependencies without asking the human first.
7. Implement `ROADMAP.md` items **in order**, one item per change. Do not start an item
   that is not next unless the human asks.
8. If a rule here conflicts with a direct instruction from the human, follow the human
   and mention the conflict.

## Commands

Use `uv` for everything. Do not use `pip` directly in this repository.

```bash
uv sync                                   # install/refresh the dev environment
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
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`.
- [ ] If a `ROADMAP.md` item was completed, its status checkbox is ticked.
- [ ] User-visible changes (tools, flags, settings) are documented in `README.md`.

## Project map

| Path | Purpose |
|---|---|
| `src/mcp_file_system/cli.py` | Command-line entry point (`mcp-file-system`). |
| `src/mcp_file_system/server.py` | `create_server()`: builds the MCP server (transport-agnostic). |
| `src/mcp_file_system/context.py` | `AppContext`: wires settings and services together. |
| `src/mcp_file_system/config.py` | `Settings` (CLI flags + `MCP_FS_*` env vars), `DEFAULT_DENY_GLOBS`. |
| `src/mcp_file_system/errors.py` | `FsError` and its subclasses (agent-facing errors). |
| `src/mcp_file_system/security/` | `PathGuard`, `AccessPolicy` (deny list), safe regex with deadlines. |
| `src/mcp_file_system/core/` | Domain logic: reader, search, finder, outline, editor, result models. |
| `src/mcp_file_system/tools/` | Thin MCP tool adapters, one module per group, each with `register()`. |
| `tests/unit/`, `tests/security/`, `tests/integration/` | Tests; shared helpers in `tests/conftest.py`. |
| `docs/ARCHITECTURE.md` | Layers, conventions, and how to extend (read before larger changes). |
| `ROADMAP.md` | Ordered list of planned work. |

## Code conventions

- **Layers:** `cli/server → tools → core → security → config`. Dependencies point
  downwards only.
- **Errors:** raise a subclass of `errors.FsError` with a message that tells the agent
  what to do next (e.g. "Use read_lines instead"). Never put paths outside the allowed
  roots, file contents of denied files, or stack traces in messages.
- **Results:** services return a `core.models.ToolResult` subclass. Its `render()` returns
  compact text for the model; the model itself is the structured output.
- **Limits:** every result must be bounded by a setting (`max_read_lines`,
  `max_output_chars`, `max_matches`, `max_results`, `search_timeout_s`). When output is
  cut, say so and say how to continue (for example `next_start`).
- **Lines:** a line ends at `\n`, `\r\n`, or a lone `\r`. Use the helpers in
  `core/textio.py` (`split_lines`, `open_lines`, `count_lines`); do not use
  `str.splitlines()`, which also splits on other characters.
- **Regex from users:** always compile with `security.compile_pattern(...)` and a
  `Deadline`. Never use the `re` module on agent-supplied patterns.
- **Style:** type hints everywhere (`mypy --strict` must pass), line length 100, no
  `print()` (stdout carries the MCP protocol; log to stderr via `logging`).

### Adding a tool (pattern to copy)

1. Put the logic in a `core` service and return a `ToolResult` subclass.
2. Register it in a `tools/` module:

```python
@server.tool(annotations=READ_ONLY)
@translate_errors
def my_tool(
    path: PathArg,
    limit: Annotated[int, Field(ge=1, description="Maximum items to return.")] = 50,
) -> Annotated[CallToolResult, MyResult]:
    """One-sentence summary the model sees as the tool description.

    Optional second paragraph: when to use this tool instead of others.
    """
    return to_call_result(ctx.my_service.do_it(path, limit))
```

3. Add the module's `register` to `READ_ONLY_TOOLSETS` or `WRITE_TOOLSETS` in
   `tools/__init__.py`. Write tools go only in `WRITE_TOOLSETS`.
4. Prefer concrete defaults over `X | None = None` for new parameters (nullable schemas
   confuse small models; see `ROADMAP.md` item 1).

## Test conventions

- Import helpers with `from conftest import write, symlink_or_skip, numbered`.
- Create test files with `write(path, content)`. It writes exact bytes, so tests behave
  the same on Linux, macOS, and Windows. Do not rely on platform newline translation.
- Use the `root` fixture for the workspace and `outside` for a directory that must stay
  unreachable. Use `make_ctx(**settings)` to change limits.
- Create symlinks only through `symlink_or_skip(link, target)` (Windows may forbid them).
- Mark OS-specific tests with `pytest.mark.skipif(...)`.
- Test file names must be unique across all test directories (there are no
  `__init__.py` files).
- No network access in tests.

## Commits (only when asked)

- Format: [Conventional Commits](https://www.conventionalcommits.org/), e.g.
  `feat(outline): add TOML provider`, `fix(security): ...`, `docs: ...`.
- One logical change per commit; every commit must pass the one-line check.
- Explain *why* in the commit body, not only what.

## When to stop and ask the human

- A new dependency, or a change to an existing dependency's version range.
- Any trade-off that relaxes a security control, even temporarily.
- Renaming or removing a tool, a tool parameter, a CLI flag, or a setting (breaking change).
- Anything destructive outside the repository, or touching git history.
- The task is ambiguous and a wrong guess would waste significant work.

## If this server's own tools are available to you

Use them the way this project intends: locate with `grep` (try `output_mode="count"`),
`find_files`, or `outline` first; then read only the needed part with `read_symbol`,
`read_lines`, or `read_between`. Do not re-read lines that are already in your context;
compare etags with `file_info` to check whether a file changed.
