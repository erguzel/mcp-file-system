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
  edit must match its `expected_count` (default 1, 0 = any number but at least one); a
  failing edit leaves the file unchanged and the error names it (`edit 2 of 3: ...`).
  The growing text is
  checked against `max_file_bytes` before every edit. The edits are logged as their
  count and total length only.
- `apply_patch` tool (full profile, `--allow-write`): applies a unified diff to one file,
  all hunks or none, guarded by the file's etag. Context and removed lines must match
  exactly; hunks whose line numbers are off are placed at the nearest exact match (no
  fuzz) and the offset is reported. Missing or ambiguous context fails the patch and the
  error names the failed hunks (the first 15, the rest counted); `dry_run=true` reports
  which hunks would fail and why.
  Untouched lines keep their own line endings. An insertion with a `\ No newline at end of
  file` marker fails unless it is at the end of the file, and so does any hunk after one
  whose new side carries that marker. The diff is logged as its length only.
- `read_ranges` tool (full profile): reads up to 20 `(path, start, end)` ranges from one
  or more files in one call, saving round trips. The ranges share one budget
  (`max_read_lines`, `max_output_chars`), spent in the given order; ranges cut by it
  are listed with the lines still to read and a ready-made `read_ranges` call to
  continue. A failing range (missing file, denied path, invalid range, unreadable file)
  is reported without stopping the others.
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
- `peek` tool (full profile): the shape of a data file without its content. CSV/TSV
  (columns, guessed types, row count, sample rows), JSON (comments and `.jsonc` included)
  and TOML (key tree with types and array lengths, no values), logs (time span, level
  counts), manifests (`pyproject.toml`, `package.json`, `Cargo.toml`: name, version,
  dependencies) and Markdown (front matter, heading counts). CSV and logs are streamed
  with fixed scan budgets; JSON and TOML honor `max_file_bytes`. A JSON string or number
  value it reads but cannot decode gives a parse error.
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
- `--help` describes the limit options (`--max-file-bytes`, `--max-read-lines`,
  `--max-output-chars`, `--max-matches`, `--max-results`, `--search-timeout`) and their
  defaults.

### Fixed

- `query` rejects a JSON file with several top-level values, such as newline-delimited JSON. It
  used to return the first value as the whole document with no note, and a key of a later value
  was reported missing. This includes newline-delimited JSON with a broken record. When junk
  that fits no value sits between the values, as in `true+5`, the file still gives its first value
  with a note.
- A lone surrogate in a tool's output no longer stops the server. It came from a JSON or YAML
  `"\ud800"` escape in a file, a file name that is not valid UTF-8, or such a name in the list
  argument of `read_ranges` sent as a string. The SDK could not send the result, so the call got no
  reply and the server exited at the next message; a `grep` hit under such a key was enough.
  Output now shows U+FFFD in its place. `write_file` refuses to create a file or directory with U+FFFD in its
  name, because writing to a name shown that way would create a second file beside the listed one.
- `query` and `outline` read a JSON number with `+` in its exponent, such as `1e+5`, as one
  number. The grammar split it, so on valid JSON `query` gave `1` for that key and said the value
  parsed cleanly, `outline` showed `a: 1`, and key and item counts and later array indexes came
  out too high. `peek` no longer rejects such a number in a file with comments.
- `peek` rejects a JSON file with several top-level values, such as newline-delimited JSON. It
  used to summarize the first value as if it were the whole file, with no note, and bad tokens
  in the later values went unreported. This covers `package.json` too.
- `outline` lists Python definitions in a `try` block in source order. Those in `except`
  blocks used to come after `else` and `finally`, so the line named after a cut could skip one.
- `read_ranges` reports an unreadable file as a failing range. A filesystem error such as
  `PermissionError` on one range used to end the whole call, and the other ranges were lost.
  Like any failing range, it does not fail the call, so the audit log records `error: null`.
- `read_ranges` keeps its numbered lines within `max_output_chars`, and only the first row
  of a call may exceed it. A later range used to return a row that did not fit, and a short
  range after a cut was still read. Now a range that is cut or does not fit waits in `cut`
  with every range after it, unread, so a failing range among them is reported by the call
  that continues.
- `log_summary` no longer tells the agent to raise `limit` when the server's
  `max_results` cut the list; the note names the server limit instead.
- `write_file` with an etag no longer creates a new file when the file was deleted or
  moved since the etag was read; it fails as stale (also as a dry run).
- `read_symbol` with an ambiguous plain name lists at most 15 candidates instead of every
  match, so the error stays small on files such as `package-lock.json`.
- The continuation hint after a cut `read_lines`, `read_between` or `read_symbol` carries
  the requested end line (`next_end`) whenever that end is known, so following it no
  longer reads on to the end of the file. A `read_between` block cut before its end
  pattern has no known end and keeps the start-only hint.
- Editing a file with text its encoding cannot store (e.g. `ş` or `€` in a legacy file
  read as Latin-1) no longer fails with a generic tool error and a stack trace on stderr.
  All editing tools now report which encoding is involved and what to do, and leave the
  file unchanged. New text holding a lone surrogate the file did not already hold is refused
  too, with its own message: one in U+DC80..U+DCFF, from a list argument sent as a string or
  a `\udce9` escape in a regex replacement, used to be written as a raw byte, which made a
  UTF-8 file invalid and put a silent character such as `é` into a Latin-1 file.
- A UTF-16 or UTF-32 file that is not valid in its encoding, such as one with a lone
  surrogate or a cut-off last unit, can be edited. Every editing tool used to fail on it,
  with a generic tool error and a stack trace, or with a message about an unpaired surrogate
  the new text did not hold. Its invalid bytes are now kept as they are, as in UTF-8 files.
  An edit that would shift them or join them into a character, so that the file would read
  differently than the diff shows, is refused with its own message. So is an edit that would
  make any UTF-16 or UTF-32 file read as another encoding, such as U+0000 put first in a
  little-endian UTF-16 file, whose start then looks like a UTF-32 byte-order mark.
- An edit can no longer leave a file that the next read takes as binary or as another
  encoding without a word. Each read guesses the encoding from the first 8 KiB, so a NUL
  byte there made the file unreadable for the text tools, and a byte-order mark at the start
  (U+FEFF, or `ÿþ` first in a Latin-1 file) made all of it read as UTF-16 or UTF-8 with BOM.
  Such edits are now refused. An edit that makes a file without BOM read as UTF-8 instead of
  Latin-1, or the reverse (for example by deleting the top lines), still goes ahead, and its
  result carries a `note` saying so, since its other non-ASCII characters may read
  differently.
- `.gitignore` files are split into patterns at line ends only. Characters such as a form
  feed inside a pattern no longer split it into two patterns that could hide unrelated
  paths from walks and searches.
- Editing a big-endian UTF-16 or UTF-32 file no longer rewrites it as little-endian. Every
  editing tool wrote the machine's byte order, so the BOM and every byte of the file
  changed; the file now keeps its BOM and byte order.
- The `--deny` help, the `deny_globs` description and the README said the patterns only
  add to the defaults. They now say that the last matching pattern wins, so
  `--deny '!PATTERN'` re-allows a path the defaults block. The behavior is unchanged.
- Hints that `lite` tools return no longer suggest tools or parameters `lite` lacks
  (`read_between`, `expected_count`, `overwrite`, `max_matches`, `include_hidden`,
  `only_matching`), which small models then tried and failed on. Content too large to
  write now says so instead of suggesting how to read it, and the regex timeout hint no
  longer suggests `fixed_string`, which `replace_in_file` does not have.
- The server instructions follow the `--tools` allowlist: they name only exposed tools,
  and say the server is read-only when `--allow-write` is set but no editing tool is
  exposed.
- `apply_patch` accepts at most 500 hunks per call, and the text of its error and dry-run
  report names at most 15 failing hunks and counts the rest, so neither grows with the
  patch (the structured dry-run result still lists every hunk).
- A cut `outline` names the line where the first unlisted symbol starts (`next_line`) and
  says to continue with `read_lines` from there, instead of only that it was cut.
- Edit results report `truncated` when the diff was cut at the output limit and give the
  next step that fits: re-read the changed lines after an applied edit; nothing to do
  after a dry run or the undo of a creation. The audit log records the flag.

### Security

- Denied paths inside the roots no longer reveal whether they exist: a missing `.env`
  now reports "denied by policy" like an existing one, instead of "No such file".
- Paths that cannot be resolved no longer reveal what exists outside the roots. An
  unreadable directory, a symlink loop or a file used as a directory there (e.g.
  `../outside/file.txt/x`) produced "Cannot resolve path" instead of "outside the allowed
  roots", and `.env/x` answered differently depending on whether `.env` exists.
- The default deny list now covers direnv's `.envrc` and `.envrc.*` (e.g. `.envrc.local`),
  except their `.example`, `.sample` and `.template` templates. These files often export
  secrets. They can no longer be read and no longer appear in `list_directory`,
  `find_files` or grep results; `--deny '!.envrc'` re-allows a single file.
- On Windows, directory walks followed junctions. A junction inside a root that pointed
  outside let `list_directory` and `find_files` list, and `grep` read, files outside the
  roots, and `file_info` counted it; a junction pointing back into the root made walks
  loop until the depth limit. Walks now treat junctions like symlinks. Direct reads were
  not affected.
- `file_info` on a directory no longer reveals denied entries. It counted every entry
  (a directory holding only `.env` showed "entries: 1"), and its size and modification
  time change with each entry. It now counts what `list_directory(include_hidden=true)`
  shows, so gitignored entries, FIFOs and sockets, and symlinks leaving the roots are no
  longer counted either; `size_bytes` and `modified` are `null` for directories.
- An invalid regex passed to `replace_in_file` was written to the audit log in full as
  part of the error message, although the log records that pattern as its length only.
  Regex error messages (`grep`, `read_between`, `replace_in_file`) no longer quote the
  pattern; the agent still gets the error and its position.
- The audit log now records calls the SDK rejects before a tool runs: arguments that
  fail the input schema, and unknown tool names, including tools hidden by a missing
  `--allow-write` or by `--tools`. These calls used to leave no trace. Their raw
  arguments are logged as sizes unless they are declared, passed validation and hold a
  single value, and the error names fields, never the rejected values.
- Outlining a Markdown or shell file can no longer crash the server on Linux. The
  tree-sitter-markdown and tree-sitter-bash scanners pass characters above U+00FF to
  `isdigit()`, which reads outside glibc's lookup table (e.g. `1` or `{` followed by an
  emoji). These grammars now parse a copy in which such characters are replaced with
  ASCII of the same byte length; symbol names still come from the original text.
- Globs passed to `find_files` and `grep` are limited to `max_pattern_length` characters;
  they are compiled by pathspec into stdlib regexes, which have no time budget.

### Changed

- New core dependencies: `tree-sitter` (pinned `>=0.25.2,<0.26`: 0.26.0 crashes with heap
  corruption during tree walks) and the Markdown, JSON, TOML, YAML and Bash grammars.
- Tool parameters no longer use nullable types, so input schemas contain no `anyOf`.
  "Unset" values are now `""` or `0` (for example `read_lines(end=-1)`,
  `grep(glob="")`, `replace_in_file(expected_count=0)` for any number of matches, at
  least one). Results are unchanged.
- CI matrix trimmed: macOS runs on manual dispatch only and Windows on Python 3.12 and
  3.14; Linux keeps 3.12 to 3.14.

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
