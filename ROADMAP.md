# Roadmap

Planned improvements, **in implementation order**. Work on one item at a time. Each item
lands as its own focused change with tests and a `CHANGELOG.md` entry, and its checkbox is
ticked in the same change.

Guiding constraint: every new tool or parameter makes tool calling harder for small local
models. Item 1 introduces profiles so that later additions can be gated.

## 1. Small-model profile and schema slimming

- [x] **Status:** done

**What**
- Replace every `X | None = None` tool parameter with a concrete default (e.g.
  `max_matches: int = 100`, `end: int = -1`). This removes all `anyOf`/`null` constructs
  from the input schemas.
- Add `--no-output-schema` (env `MCP_FS_OUTPUT_SCHEMA=false`). It omits the output schemas
  (larger than the input schemas) while still returning the compact text.
- Add `--profile lite|full` (default `full`) and a `--tools name,name` allowlist. `lite`
  exposes the core tools with only their essential parameters.
- Add an approximate token count to `file_info` (e.g. `~tokens: 12.4k`, characters / 4) so
  the agent can weigh the cost of reading a whole file.

**Why:** Local models in LM Studio sometimes fail with
`Failed to parse tool call: Expected "<parameter=", but got "<required>["`. The model
echoed schema text into its tool call. Smaller, flatter schemas reduce this. Profiles
resolve the tension between versatility and small-model friendliness.

**Done when:** no `anyOf` appears in any input schema; profile and allowlist are covered by
integration tests (`list_tools` shows exactly the expected tools and parameters); README
documents the profiles.

## 2. Audit log and evaluation harness

- [ ] **Status:** audit log done; evaluation harness deferred until a profile comparison
  is needed (current local models are 27B+, so `full` with `--no-output-schema` is the
  default; real-usage data comes from the audit log).

**What**
- Optional `--audit-log PATH`: one JSON line per tool call with timestamp, tool, arguments,
  result size and the error's type and message. File contents are never logged as
  arguments.
- `scripts/eval/`: runs fixed tasks against an OpenAI-compatible endpoint (e.g. LM Studio)
  and reports tool-call parse failures, number of calls, characters read versus file
  sizes, and task success.

**Why:** design decisions are currently based on intuition. Measure whether models really
read partially and whether item 1 lowers parse errors. The log also supports security
auditing.

**Done when:** the log format is documented and tested; the harness runs with one command
and prints a summary table.

## 3. Tree-sitter outline backbone

- [x] **Status:** done. Markdown, JSON, TOML, YAML (incl. Helm/Go templates), shell
  scripts (incl. shebang detection) and Dockerfiles in core; JS/TS, Go and Java in the
  `[code]` extra. `tree-sitter` is pinned below 0.26 because 0.26.0 corrupts the heap.

**What**
- Add `tree-sitter` plus the Markdown, JSON, TOML and YAML grammars as **core
  dependencies** (approved). All are small, MIT-licensed and ship wheels for Linux, macOS
  and Windows.
- A generic tree-sitter `OutlineProvider` driven by a per-language mapping (or query) of
  which syntax nodes count as symbols. Reuse the grammars' bundled tag queries where they
  exist.
- JSON, TOML and YAML outlines: a key tree with line spans, so
  `read_symbol("tool.ruff.lint")` returns just that table. (The standard library's `json`
  and `tomllib` cannot do this: they return values without line positions.)
- Markdown moves to tree-sitter, which also handles setext headings and HTML blocks.
  Keep the current regex provider as a tested fallback.
- Shell scripts via `tree-sitter-bash` (core dependency): functions, with files without
  a suffix matched by their shebang (`#!/usr/bin/env bash`).
- Helm/Go templates in YAML: `{{ ... }}` directives are masked (same byte length) before
  parsing, so templated Kubernetes manifests outline like plain YAML.
- JavaScript/TypeScript, Go and Java behind an optional extra, `mcp-file-system[code]`,
  using the individual grammar packages (`tree-sitter-javascript`,
  `tree-sitter-typescript`, `tree-sitter-go`, `tree-sitter-java`). Not
  `tree-sitter-language-pack`: it downloads grammars at runtime, i.e. unpinned native
  code fetched while the server runs.
- Dockerfiles via a small pure-Python provider (stages and instructions; the format is
  line-based). The `tree-sitter-dockerfile` package has no Windows wheels.
- Python stays on the standard library `ast` (no dependency, richer signatures).

**Why:** `outline` and `read_symbol` save the most tokens, but when this item was written
they covered only Python and Markdown (the status above lists what is supported now).
Config files are the most frequently over-read files, and a line-aware parser is
required for their outlines. One parsing mechanism for code and flat files compounds:
every grammar added here also extends the enclosing-symbol hints (item 4) and the
skeleton view (item 5). Plain text and logs have no grammar; items 6 and 8 cover them.

**Done when:** each provider has unit tests including malformed input; JSON/TOML/YAML
symbols resolve with correct line spans; the server starts and the core formats work when
the `[code]` extra is not installed; CI stays green on all three operating systems
(macOS has run on manual dispatch only since the CI matrix was trimmed).

## 4. Change-aware reads and navigation hints

- [x] **Status:** done

**What**
- **Not-modified reads:** `read_lines` and `read_symbol` accept `if_changed_since=<etag>`.
  If the file is unchanged, they return a one-line "unchanged" answer instead of the
  content (like HTTP 304 Not Modified).
- **Already-read tracking:** per session, remember which `(path, line range, etag)` were
  returned. When an unchanged range is requested again, still return it, but prepend a
  note such as "already returned earlier in this session; file unchanged".
- **Enclosing symbol in grep hits:** for files with an outline provider, show the symbol
  that contains each hit, e.g. a `[Parser.parse]` line above the hits inside that method,
  so the agent can go straight to `read_symbol`.

**Why:** agents re-read content that is already in their context, and prompts alone are
unreliable on small models; this solves it on the server. Never refuse a read: the model
may have lost the content from its context window. The enclosing symbol saves the extra
`outline` round trip after a search. These are the two features from this roadmap that
belong in the `lite` profile: they extend existing tools instead of adding new ones.

**Done when:** tests cover a not-modified read, a repeated read, a read after modification
(no note), partially overlapping ranges, and grep hits inside, between and outside
symbols.

## 5. Skeleton view

- [x] **Status:** done

**What:** `read_skeleton(path)` returns the file with function and method bodies folded
to one `<start>-<end> ...` row each, keeping imports, signatures, docstrings and class
structure, like a header file. Python (via `ast`) and shell, plus JS/TS, Go and Java with
the `[code]` extra. Providers report fold ranges; rendering is language-independent.

**Why:** an outline is only a list. A skeleton shows how a module is used (its API) with
far fewer tokens than reading it: about half the size on this repository's modules, which
are docstring-heavy (docstrings stay visible); more on code with long bodies.

**Done when:** the skeleton of every module in this repository parses as valid Python
(tested); nested classes, decorators and async functions are tested.

## 6. Structured data preview

- [x] **Status:** done. CSV/TSV, JSON, TOML, logs, manifests and Markdown. JSON and TOML
  are limited by `max_file_bytes`; CSV and logs are streamed (first 50,000 rows / 200,000
  lines scanned; the last log timestamp is read from the file's tail).

**What:** `peek(path)` returns the *shape* of a file instead of its content, through
per-format providers (same registry pattern as outlines):

| Format | Returns |
|---|---|
| CSV/TSV | header, column and row counts, 3 sample rows, guessed column types |
| JSON | key tree with types but no values; array lengths (`items: array[1532] of {id: int, name: str}`) |
| Logs | first and last timestamp, level counts (`ERROR: 12, WARN: 340`) |
| Manifests (`pyproject.toml`, `package.json`, `Cargo.toml`) | name, version, dependency list |
| Markdown | front matter, heading count |

**Why:** agents often open data files only to learn their structure. The schema of a
50 MB JSON file needs none of its values.

**Done when:** each provider is tested, including huge and malformed inputs (bounded time
and output).

## 7. Path queries for structured files

- [x] **Status:** done. Tree-sitter views for JSON/JSONC and YAML (line spans, Helm
  templates, multi-document streams), `tomllib` for TOML. YAML aliases and merge keys
  are shown as written, not resolved.

**What:** `query(path, "dependencies.react")` or `query(path, "paths./users.get")` for
JSON, TOML and YAML. It returns only that subtree, bounded by depth and size.

**Why:** grep is a poor fit for huge structured files such as `package-lock.json` or
OpenAPI specs. This is the natural continuation of the config outlines from item 3.

**Done when:** tests cover missing keys (actionable error), array indexes, keys
containing dots, and output limits.

## 8. Collapse repeated lines

- [x] **Status:** done. Shipped as `output_mode="collapsed"` instead of a
  `collapse_similar` flag: no extra parameter, no meaningless combination with the other
  modes, and the `lite` grep keeps its three modes.

**What**
- `grep(..., output_mode="collapsed")`: group hits whose text differs only in numbers, hex
  values, UUIDs, IPs or timestamps, e.g. `[x532] Connection timeout after <N>ms to <IP>`.
- `log_summary(path)`: the most frequent line templates with counts and first/last
  occurrence.

**Why:** in log analysis, thousands of near-identical lines shrink to a few templates,
which is the largest token saving this toolset can offer.

**Done when:** masking rules are tested individually; summaries stay bounded on files
with millions of lines (time budget respected).

## 9. Line-focused and batch reads

- [x] **Status:** done. Batch reads shipped as `read_ranges` (at most 20 ranges,
  budget spent in order).

**What**
- `read_lines(path, around=N, radius=R)`, available in the `full` profile only.
- Batch reads: several `(path, start, end)` ranges in one call, sharing a single output
  budget (`full` profile only).

**Why:** line numbers usually come from error messages, stack traces or grep output, and
small models miscompute `start`/`end` windows. Batch reads cut round trips, which are
slow with local models. Idea from j0hanz/filesystem-mcp.

**Done when:** tested edge cases include `around` near the start and end of a file and
`radius=0`; a batch that exceeds the budget reports which ranges were cut and how to
continue them.

## 10. Multi-edit, patch and undo

- [x] **Status:** done. Shipped as `multi_replace`, `apply_patch` (offset search, no fuzz)
  and `undo_last_edit` (one level, in memory; a created file is renamed aside, not
  deleted).

**What**
- Apply several literal replacements to one file atomically in one call; all succeed or
  none are applied.
- `apply_patch(path, diff, expected_etag)`: applies a unified diff atomically (all hunks
  or none), with a dry run that reports which hunks would fail and why.
- `undo_last_edit(path)`: restores the version before the most recent edit made by this
  server, guarded by an etag.

**Why:** local models make editing mistakes. Undo is a cheap safety net before git, and
atomic multi-edits avoid half-applied edit sequences. Agents are trained to produce
unified diffs, so a patch tool is a natural way for them to edit several regions at once
(idea from j0hanz/filesystem-mcp).

**Done when:** tests cover a failing edit in the middle of a batch (file unchanged), a
patch with one non-applying hunk (file unchanged, the hunk named in the error), and undo
after an external modification (rejected).

## 11. Optional git toolset

- [ ] **Status:** not started

**What:** `--enable-git` adds `git_status`, `git_diff` and reading a file at a given
revision. They run fixed, read-only argument lists via `subprocess` without a shell, with
paths validated by `PathGuard`.

**Why:** "what changed?" is the most common question a coding agent asks.

**Done when:** tests cover argument injection attempts (e.g. revisions starting with `-`)
and paths outside the roots.

## 12. Ripgrep backend and huge-file line index

- [ ] **Status:** not started. Only start once a real performance problem is measured.

**What:** a `SearchBackend` that passes the vetted file list to `rg`, and a cached
line-offset index for random access in very large files.

**Why:** pure-Python grep may be slow on large repositories. The `SearchBackend` protocol
exists for exactly this.

## 13. Optional RE2 regex engine

- [ ] **Status:** not started

**What:** `--regex-engine re2` uses `google-re2` (an optional extra,
`mcp-file-system[re2]`) for agent-supplied patterns. The default stays `regex` with time
budgets.

**Why:** RE2 matches in linear time, so ReDoS is impossible by construction rather than
cut off by a deadline (approach used by j0hanz/filesystem-mcp). The trade-off is that RE2 has no
backreferences or lookaround, so it stays opt-in. Invalid-for-RE2 patterns must fail with
an actionable message.

**Done when:** the regex test suite passes on both engines (except features RE2 lacks,
which are tested to fail clearly); the server starts normally when the extra is missing
and `re2` was not requested.

## Under consideration

- **Project map** (`project_summary`): file counts per language, top-level structure, key
  files (README, manifests, entry points) and size statistics in one call. It replaces
  the many `list_directory` calls an agent makes when entering a project.
- **File diff** (`diff_files(a, b)`): only the differences between two files, without
  reading both. Overlaps with the git toolset (item 11) but works without git.
- **Legacy encodings:** files that are not valid UTF-8 (and have no BOM) are read as
  Latin-1, so a Windows-1254 file shows `þ ð ý` instead of `ş ğ ı`. Writing characters
  Latin-1 cannot store is reported as an error and changes nothing. Better detection
  needs a heuristic or a dependency; start only when such files actually come up.
- **UTF-16 byte order (unverified):** an edited UTF-16 BE file may be written back as LE
  (found in review, not reproduced). Undo restores the original bytes. Verify with a
  test before fixing.

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
- **Semantic code intelligence** (find references, rename, refactoring): out of scope,
  because it needs language servers (LSP), which conflicts with the lightweight design.
  Use an LSP-based MCP server alongside this one; this server stays at the syntax level
  (outlines, skeletons, symbol reads).
