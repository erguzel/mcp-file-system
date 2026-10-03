# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's "Report a vulnerability" (security
advisories) feature of this repository, not through public issues. Include a description, a
reproduction and the affected version. You can expect an initial response within a week.

## Threat model

The server runs with the privileges of the user who starts it, and executes tool calls
chosen by an AI agent. The agent is **not trusted**: its inputs may be influenced by
prompt injection from files, web pages or other tools. The goal is that whatever the agent
asks for, it cannot:

1. read or modify anything outside the configured roots,
2. read or modify secrets inside the roots that match the deny list,
3. learn whether paths outside the roots exist,
4. exhaust the server with expensive patterns or huge outputs,
5. modify files at all unless the operator passed `--allow-write`.

### Controls

| Threat | Control |
|---|---|
| Path traversal (`../`, absolute paths) | `PathGuard` resolves every path and requires the real path to be inside a root. |
| Symlink / junction escapes | Paths are fully resolved before the containment check. Walks never descend into symlinked directories and drop links whose targets fail the guard. |
| Existence probing outside roots | Confinement is checked before existence, so missing and existing outside paths produce the same error. |
| Secret files inside roots | Gitignore-style deny list, applied to both the requested and the resolved path, matched case-insensitively (case-insensitive filesystems would otherwise allow `.ENV`). |
| NTFS alternate data streams (`file::$DATA`) | Colons outside a drive prefix are rejected on Windows. |
| ReDoS | Patterns are compiled with the `regex` module. Every evaluation draws on a shared wall-clock deadline, and pattern length is limited. |
| Resource exhaustion | Limits on lines, output characters, matches, listed paths, file size for full loads, and walk depth. Binary files are skipped. FIFOs and devices are never opened. |
| Stale or racing edits | Edits require (or verify) an etag, re-checked immediately before an atomic replace. |
| Accidental writes | Editing tools are not even registered unless writes are enabled. |
| Information leakage in errors | Unexpected `OSError`s are reduced to their description. Other unexpected exceptions reach the client only as a generic failure. |
| Supply chain | Locked dependencies (`uv.lock`), CI installs with `--locked`, `pip-audit` on every run, Dependabot, actions pinned by commit SHA, least-privilege workflow permissions. |

### Known limitations

- **TOCTOU:** checks happen before file access. A local process that can modify the
  roots concurrently (for example, by swapping a directory for a symlink between the check
  and the open) is outside this threat model. If that matters, run the server in a
  container or as a dedicated user.
- **Gitignore is not a security boundary.** It only hides paths from walks and searches.
  Use `--deny` for anything that must stay unreadable.
- **The deny list is a blocklist.** Review the defaults (`config.DEFAULT_DENY_GLOBS`) and
  add project-specific patterns with `--deny`.
- **HTTP transport** is not exposed yet. Before serving over a network, add
  authentication and TLS (see `docs/ARCHITECTURE.md`).
