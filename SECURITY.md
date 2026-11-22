# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's private vulnerability reporting: on the
repository's **Security** tab, choose **Report a vulnerability**
(<https://github.com/erguzel/mcp-file-system/security/advisories/new>). Do not open a
public issue. Include a description, a reproduction and the affected version.

If you cannot use GitHub, email the maintainer instead (address in `pyproject.toml`).

## Threat model

The server runs with the privileges of the user who starts it, and executes tool calls
chosen by an AI agent. The agent is **not trusted**: its inputs may be influenced by
prompt injection from files, web pages or other tools. The goal is that whatever the agent
asks for, it cannot:

1. read or modify anything outside the configured roots,
2. read or modify secrets inside the roots that match the deny list,
3. learn whether paths outside the roots, or denied paths inside them, exist,
4. exhaust the server with expensive patterns or huge outputs,
5. modify files at all unless the operator enabled writes (`--allow-write` or
   `MCP_FS_ALLOW_WRITE`).

### Controls

| Threat | Control |
|---|---|
| Path traversal (`../`, absolute paths) | `PathGuard` resolves every path and requires the real path to be inside a root. |
| Symlink / junction escapes | Paths are fully resolved before the containment check. Walks never descend into symlinked directories and drop links whose targets fail the guard. Windows junctions (and volume mount points) are treated the same way. |
| Existence probing | Confinement and the deny policy are checked before existence, so a missing and an existing path produce the same error when outside the roots or denied (e.g. whether a `.env` exists cannot be probed). This also holds when resolving fails for another reason: an unreadable directory, a symlink loop, or a file used as a directory (`../outside/file.txt/x`, `.env/x`). Directory metadata counts only entries a walk may show and withholds the directory's size and modification time, which change with denied entries. |
| Secret files inside roots | Gitignore-style deny list, applied to both the requested and the resolved path, matched case-insensitively (case-insensitive filesystems would otherwise allow `.ENV`). |
| NTFS alternate data streams (`file::$DATA`) | Colons outside a drive prefix are rejected on Windows. |
| ReDoS | Patterns are compiled with the `regex` module. Every evaluation draws on a shared wall-clock deadline, and pattern length is limited. Globs (`find_files`, `grep`) share the length limit but are matched by pathspec without a deadline. |
| Resource exhaustion | Limits on lines, output characters, matches, listed paths, file size for full loads, and walk depth. Binary files are skipped. FIFOs and devices are never opened. |
| Stale or racing edits | Edits require (or verify) an etag, re-checked immediately before an atomic replace. |
| Tampering with the audit trail | The optional audit log must be outside the roots (startup fails otherwise), so the agent can neither read nor edit it. Content-carrying arguments are reduced to their length; error messages are logged (at most 500 characters): domain errors as the agent sees them, which can include short excerpts such as key or symbol names, and filesystem errors with the full OS message and absolute path, which only the operator can read. A failing `read_ranges` range does not fail the call, so the call's record has `error: null` and the reason is not logged. Calls rejected before a tool runs (arguments that fail the schema, unknown or disabled tools) are logged too, with field names and lengths instead of the rejected values. |
| Accidental writes | Editing tools are not even registered unless writes are enabled. |
| Information leakage in errors | Unexpected `OSError`s are reduced to their description. Other unexpected exceptions reach the client only as a generic failure (behavior of the `mcp` 2.x SDK). |
| Supply chain | Locked dependencies (`uv.lock`), CI syncs and runs with `UV_LOCKED=1` (the wheel smoke test resolves fresh by design), `pip-audit` on every run, Dependabot, actions pinned by commit SHA, least-privilege workflow permissions. |

### Known limitations

- **TOCTOU:** checks happen before file access. A local process that can modify the
  roots concurrently (for example, by swapping a directory for a symlink between the check
  and the open) is outside this threat model. If that matters, run the server in a
  container or as a dedicated user.
- **Gitignore is not a security boundary.** It only hides paths from walks and searches.
  Use `--deny` for anything that must stay unreadable.
- **The deny list is a blocklist.** Review the defaults (`config.DEFAULT_DENY_GLOBS`) and
  add project-specific patterns with `--deny`. Setting `MCP_FS_DENY_GLOBS` replaces the
  defaults instead of adding to them. A `--deny '!PATTERN'` re-allows what the defaults
  block (the last matching pattern wins), so review such patterns as carefully as the
  list itself.
- **HTTP transport** is not exposed yet. Before serving over a network, work through
  the checklist in `docs/ARCHITECTURE.md`: authentication, TLS, per-session state and a
  re-check of lone surrogates in arguments.
