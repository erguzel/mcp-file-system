# Roadmap

Planned improvements, **in implementation order**. Work on one item at a time. Each item
lands as its own focused change with tests and a `CHANGELOG.md` entry, and its checkbox is
ticked in the same change.

Guiding constraint: every new tool or parameter makes tool calling harder for small local
models. Item 1 introduces profiles so that later additions can be gated.

## 1. Small-model profile and schema slimming

- [ ] **Status:** not started

**What**
- Replace every `X | None = None` tool parameter with a concrete default (e.g.
  `max_matches: int = 100`, `end: int = -1`). This removes all `anyOf`/`null` constructs
  from the input schemas.
- Add `--no-output-schema` (env `MCP_FS_OUTPUT_SCHEMA=false`). It omits the output schemas
  (≈10k characters across all tools, more than the input schemas) while still returning
  the compact text.
- Add `--profile lite|full` (default `full`) and a `--tools name,name` allowlist. `lite`
  exposes the core tools with only their essential parameters.

**Why:** Local models in LM Studio sometimes fail with
`Failed to parse tool call: Expected "<parameter=", but got "<required>["`. The model
echoed schema text into its tool call. Smaller, flatter schemas reduce this. Profiles
resolve the tension between versatility and small-model friendliness.

**Done when:** no `anyOf` appears in any input schema; profile and allowlist are covered by
integration tests (`list_tools` shows exactly the expected tools and parameters); README
documents the profiles.

## 2. Audit log and evaluation harness

- [ ] **Status:** not started

**What**
- Optional `--audit-log PATH`: one JSON line per tool call with timestamp, tool, arguments,
  result size and error flag. File contents are never logged.
- `scripts/eval/`: runs fixed tasks against an OpenAI-compatible endpoint (e.g. LM Studio)
  and reports tool-call parse failures, number of calls, characters read versus file
  sizes, and task success.

**Why:** design decisions are currently based on intuition. Measure whether models really
read partially and whether item 1 lowers parse errors. The log also supports security
auditing.

**Done when:** the log format is documented and tested; the harness runs with one command
and prints a summary table.

## 3. More outline languages

- [ ] **Status:** not started

**What**
- JSON and TOML outlines via the standard library (`json`, `tomllib`): a key tree with line
  spans, so `read_symbol("tool.ruff.lint")` returns just that table.
- YAML outline behind an optional extra (`mcp-file-system[yaml]`).
- Tree-sitter provider behind an optional extra (`mcp-file-system[treesitter]`) for
  JavaScript/TypeScript, Go, Rust and Java.

**Why:** `outline` and `read_symbol` save the most tokens, but only for Python and Markdown
today. Config files are the most frequently over-read files. The `OutlineRegistry` already
supports new providers without changing existing code.

**Done when:** each provider has unit tests including malformed input; the server works
when an optional extra is not installed.

## 4. Server-side "already read" tracking

- [ ] **Status:** not started

**What:** per session, remember which `(path, line range, etag)` were returned. When an
unchanged range is requested again, still return it, but prepend a note such as
"already returned earlier in this session; file unchanged".

**Why:** agents re-read content that is already in their context. Prompts alone are
unreliable on small models. Never refuse the read: the model may have lost the content
from its context window.

**Done when:** tests cover a repeated read, a read after modification (no note) and
partially overlapping ranges.

## 5. Line-focused reads

- [ ] **Status:** not started

**What:** `read_lines(path, around=N, radius=R)`, available in the `full` profile only.

**Why:** line numbers usually come from error messages, stack traces or grep output, and
small models miscompute `start`/`end` windows.

**Done when:** tested edge cases include `around` near the start and end of a file and
`radius=0`.

## 6. Multi-edit and undo

- [ ] **Status:** not started

**What**
- Apply several literal replacements to one file atomically in one call; all succeed or
  none are applied.
- `undo_last_edit(path)`: restores the version before the most recent edit made by this
  server, guarded by an etag.

**Why:** local models make editing mistakes. Undo is a cheap safety net before git, and
atomic multi-edits avoid half-applied edit sequences.

**Done when:** tests cover a failing edit in the middle of a batch (file unchanged) and
undo after an external modification (rejected).

## 7. Optional git toolset

- [ ] **Status:** not started

**What:** `--enable-git` adds `git_status`, `git_diff` and reading a file at a given
revision. They run fixed, read-only argument lists via `subprocess` without a shell, with
paths validated by `PathGuard`.

**Why:** "what changed?" is the most common question a coding agent asks.

**Done when:** tests cover argument injection attempts (e.g. revisions starting with `-`)
and paths outside the roots.

## 8. Ripgrep backend and huge-file line index

- [ ] **Status:** not started. Only start once a real performance problem is measured.

**What:** a `SearchBackend` that passes the vetted file list to `rg`, and a cached
line-offset index for random access in very large files.

**Why:** pure-Python grep may be slow on large repositories. The `SearchBackend` protocol
exists for exactly this.

## Deferred until requested

- **HTTP deployment:** a `--transport streamable-http` option, mandatory authentication,
  TLS behind a reverse proxy, a non-root container image and a health endpoint. See
  `docs/ARCHITECTURE.md`.

## Not planned

- **Shell or command execution:** it would break the security model. It belongs in a
  separate server.
- **Embedding-based semantic search:** it needs heavy dependencies and an index to keep in
  sync, which conflicts with the lightweight, secure focus. If needed, build it as a
  separate package.
- **Adding tools without profile gating:** every unconditional tool makes small models
  worse.
