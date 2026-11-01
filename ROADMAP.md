# Roadmap

Known bugs, then planned improvements **in implementation order**. Work on one item at a
time; an item whose status defers it (until a condition is met) is skipped until then. Each
numbered item lands as its own focused change with tests and a `CHANGELOG.md` entry, and its
checkbox is ticked in the same change.

Guiding constraint: every new tool or parameter makes tool calling harder for small local
models. Item 1 introduces profiles so that later additions can be gated.

## Known bugs and cleanup

Fix the **Behavior bugs** before starting a numbered item below, in the order listed, one per
change with a test that fails without the fix, and tick the box in the same change. The other
groups here do not block numbered items. They come from the documentation audits of October
2026; IDs such as B-F18 are their handles, and PARTIAL marks a finding where only part of the
original claim held. Unless noted, an entry was confirmed by reading the code, not by running
it, so reproduce it first.

**Behavior bugs**

- [x] **`apply_patch` misplaces a marked insertion (B-F18).** A hunk with only added lines
  and a `\ No newline at end of file` marker is placed at its header's line instead of being
  tied to the end of the file, and the file loses its final newline: `a\nb\nc\n` with
  `@@ -1,0 +2,1 @@`, `+x` and the marker becomes `a\nx\nb\nc`. Silent data change, reproduced
  by running it. `_locate` in `core/patch.py` returns before its `at_eof` checks. Reject such
  a hunk unless the insertion point is the end of the file. The same loss came from a hunk
  after a marked one (`+x` and the marker, then `+y`, both at the end, gave `a\nb\nc\nx\ny`),
  so any hunk after one whose new side carries the marker is rejected too.
- [ ] **`peek` raises a raw error on some JSON that strict parsing rejects (C-5).** When
  `json.loads` rejects a file, `_to_value` in `core/peek/structured.py` calls `json.loads` on
  the string and number tokens the tree-sitter grammar accepted, and only `RecursionError` is
  caught. A raw control character in a string or a bare `\u` should escape as
  `JSONDecodeError`, a generic tool error, instead of `ParseError`. The trigger rests on the
  upstream grammar and was not run.
- [ ] **`read_ranges` overruns its character budget (B-F1).** After a range is cut,
  `chars_left` usually stays above zero and `number_lines` always emits one row, so later
  ranges still return lines: output can exceed `max_output_chars` and short ranges miss `cut`.
- [ ] **One unreadable file aborts `read_ranges` (B-F2).** Only `FsError` is caught per range,
  so a `PermissionError` ends the whole batch, although the docstring says a failing range
  does not stop the others.
- [ ] **Python outlines are out of source order in `try` blocks (A-R1).** `_visit` in
  `core/outline/python.py` visits `except` bodies after `else` and `finally`, so
  `OutlineResult.next_line` can skip a symbol when the cut falls inside one.

**Lower priority**

- [ ] **A deleted file gives different errors (B-F5).** A file deleted after the read gives
  `NotFoundError` from `resolve_file` in the edit tools, a clear message, but `StaleFileError`
  in `write_file`; decide whether they should match. Deleted within the call, the `stat` in
  `FileEditor._load` or in `ensure_etag` (`core/etag.py`) lets a raw `FileNotFoundError` through.
- [ ] **TOML depth limit (C-3).** `_table` and `_array` in
  `core/outline/treesitter/structured.py` lack the `MAX_DEPTH` check, which may also allow a
  `RecursionError` on deeply nested arrays. Not run.
- [ ] **TOML array-table spans (C-4).** In `_extend_parent_tables`, a `[[bin]]` element is named
  `bin[0]` while a following `[bin.meta]` is `bin.meta`, so the element's span never covers it.
- [ ] **A missing core grammar stops the server (C-1).** The guarded import in
  `registry._tree_sitter_factories` is not reached: `core/peek/structured.py`,
  `core/query/service.py` and `core/query/nodes.py` import `outline.treesitter` at module level,
  and its `__init__.py` loads every grammar, so the server fails at import instead of skipping
  that format.
- [ ] **The audit log can fail a tool call (A-D2).** `AuditLog.record` says it never raises, but
  catches only `OSError`, so a lone surrogate in a logged value raises `UnicodeEncodeError`.
  Arguments cannot carry one over stdio, which is why this is on the HTTP checklist in
  `docs/ARCHITECTURE.md`; an OS error message naming a non-UTF-8 file might. Not run.

**Misleading docstrings**

- [ ] Fix these after the behavior bugs, so they describe the corrected behavior, and delete a
  bullet here when it is fixed. Four also break the Raises rule in `AGENTS.md`, as may
  `AppContext.from_settings`; `docs/ARCHITECTURE.md`'s Budgets line does not mirror the
  continuation-hint rule in `AGENTS.md`.
  - A-D3 `config.py` DEFAULT_DENY_GLOBS "always blocked, even for direct access" — `--deny
    '!GLOB'` re-allows, MCP_FS_DENY_GLOBS replaces.
  - B-F4 `etag.py` module "editors require it back" / `FileInfo.etag` "required by edit tools"
    — optional for replace/multi_replace/write-create.
  - B-F6 `editor.py` module "preserves newline style" — regex replacements are not normalized.
  - B-F12 `finder.GlobMatcher` "a glob with a slash is anchored" — trailing-slash-only `dir/`
    matches at any depth (gitignore semantics).
  - B-F14 `search._collect_templates` "count every matching line" / `GrepResult.ungrouped` —
    with only_matching, matches are counted.
  - C-8 `peek/__init__` and `Peeker` "without their content" — sample rows, timestamps,
    Markdown title, manifest name/version/deps are returned; TOML and Markdown missing from the
    list.
  - B-F8 `FileEditor.write` Raises lists only StaleFileError (also PathTypeError,
    AlreadyExistsError, InvalidArgumentError, FileTooLargeError, BinaryFileError) — also violates
    the AGENTS.md Raises rule.
  - B-F17 `LogSummarizer.summarize` Raises omits BinaryFileError and path errors; blank lines
    skipped; limit capped — violates the Raises rule.
  - C-6 `Peeker.peek` Raises omits BinaryFileError, FileTooLargeError, path errors — violates the
    Raises rule.
  - C-7 `DataQuery.query` Raises omits FileTooLargeError, BinaryFileError, path errors — violates
    the Raises rule.

<details>
<summary>Cosmetic docstring findings: batch them with the above or leave them</summary>

- A-C2 `errors.py` module "every error raised deliberately derives from FsError" — startup
  ValueError, ToolError in translate_errors, `_base.parse` ValueError.
- A-C3 `errors.AccessDeniedError` "blocked by the access policy" — also unresolvable in-root
  paths (`paths.py` "Cannot resolve path").
- A-C4 `errors.InvalidArgumentError` "syntactically valid but semantically wrong" — also bad
  hunk headers, key-path syntax.
- A-C5 `errors.NoMatchError` "did not match often enough" — also ambiguous/overlapping patch
  hunks.
- A-C6 `server.create_server` settings "read from the environment when omitted" — ignored when
  `context` is given.
- A-C7 CHANGELOG next_end sentence names only "cut before its end pattern"; a no-end-pattern
  read_between cut at max_lines also has no next_end.
- A-C8 key_path hint: empty keys need `""` too.
- A-C9 `search_timeout_s` "one search or regex operation" — also log_summary scan and
  read_between.
- PARTIAL A-C1 `context.from_settings` Raises lists only OSError; YAML grammar ValueError can
  arise in DataQuery (JSON/missing grammar fail earlier, at import — see C-1 above).
- B-F3 `reader._count_entries` / `FileInfo.entry_count` — not capped by max_results (unlike
  list_directory).
- PARTIAL B-F7 `editor.py` "refuses to run against a stale view" — only when an etag is given
  (the pre-write re-check always runs).
- PARTIAL B-F9 `EditResult.etag` "after the edit" — wording loose on dry runs / undone creation
  (values are right).
- B-F10 `EditResult.changes` — hunks for apply_patch, 1 for write/undo.
- B-F11 `LinesResult.start_line/end_line` "(0 if empty)" — unchanged results carry the
  confirmed range.
- B-F13 `search._annotate_symbols` "never exceed the search budget" — deadline only between
  files.
- B-F15 `templates.py` "free of nested quantifiers" — `_IPV6` nests (still linear).
- B-F16 `LogSummarizer` "streamed once" — count_lines makes a separate cached pass first.
- PARTIAL B-F19 `patch.parse_patch` Raises omits invalid header / empty hunk / misplaced marker
  (preamble lines are skipped, hunk-body lines are checked).
- B-F20 `tracking.ReadTracker` "least recently used" — only record() refreshes recency.
- B-F21 `render.truncate_text` "at a line boundary to fit max_chars" — mid-line without
  newline; result exceeds max_chars by the hint.
- B-F22 `FileInfo.size_bytes/modified` say "hidden entries" where "denied entries" is meant.
- B-F23 `models.py` module "each model is a tool's structured output" — output_schema off for
  lite; several input/sub-models.
- PARTIAL B-F24 `textio.count_lines` "memoized on the file's identity" — key also includes the
  format.
- C-9 `peek/structured.py` module "(no values)" — manifest metadata are values.
- C-10 `key_tree` "one row per top-level key" — at most MAX_KEYS plus a "more" row.
- C-11 `summarize_log` "over the first scanned lines" — last timestamp from the file tail.
- C-12 `code._header` "without annotations" — only leading ones; nested parentheses break the
  regex.
- C-13 `DataNode.scalar` "as written" — TOML scalars rendered from parsed values (0xff → 255).
- C-14 `shell.py` "bash and POSIX sh" — also zsh, ksh, dash via the Bash grammar.
- C-15 `python._parse` comment "e.g. NUL bytes" — SyntaxError since CPython 3.12 (not run).
- PARTIAL C-16 `_base._extract` "node_text(node, source)" — structured.py omits `source` in 7
  places (no behavior impact).
- C-17 `structured.py` "every key becomes a symbol" — limited by MAX_DEPTH,
  MAX_ITEMS_PER_ARRAY.
- C-18 `peek/text.py` `_LEVEL_ORDER` "WARNING" entry is dead (normalized to WARN).

</details>

**Before the next release** (only when cutting one)

- [ ] `CHANGELOG.md`: move entries under Fixed and Security that fix features unreleased in
  0.1.0 into their Added entries, and write the version heading before tagging (the `[0.1.0]`
  heading was added after its tag).

## 1. Small-model profile and schema slimming

- [x] **Status:** done

**What**
- Replace every `X | None = None` tool parameter with a concrete default (e.g.
  `max_matches: int = 100`, `end: int = -1`). This removes all `anyOf`/`null` constructs
  from the input schemas.
- Add `--[no-]output-schema` (env `MCP_FS_OUTPUT_SCHEMA`), on by default in `full` and off
  in `lite`. Without the output schemas (larger than the input schemas) the tools still
  return the compact text.
- Add `--profile lite|full` (default `full`) and a `--tools name,name` allowlist. `lite`
  exposes the core tools with only their essential parameters.
- Add an approximate token count to `file_info` (e.g. `~12.4k tokens`, bytes / 4) so
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
  setup in use; real-usage data comes from the audit log).

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
- A shared tree-sitter base class, `TreeSitterOutlineProvider`, that parses and walks the
  syntax tree; each language subclass decides which nodes count as symbols.
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
the `[code]` extra is not installed; CI stays green on Linux and Windows on every push
to main and every pull request, and on macOS on manual dispatch (the matrix was trimmed).

## 4. Change-aware reads and navigation hints

- [x] **Status:** done

**What**
- **Not-modified reads:** `read_lines` and `read_symbol` accept `if_changed_since=<etag>`.
  If the file is unchanged and those lines were returned earlier in this session, they
  return a one-line "unchanged" answer instead of the content (like HTTP 304 Not
  Modified); otherwise the content is sent with a note.
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

## Deferred until requested

- **HTTP deployment:** a `--transport streamable-http` option, mandatory authentication,
  TLS behind a reverse proxy, a non-root container image, a health endpoint, per-session
  state for `ReadTracker` and `EditHistory`, and a re-check of lone surrogates in
  arguments. The full checklist is in `docs/ARCHITECTURE.md`.

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
