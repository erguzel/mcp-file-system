# Contributing

## Setup

```bash
uv sync --all-extras       # Python 3.12 environment with dev tools and optional grammars
uv run pre-commit install  # ruff, mypy and file checks (e.g. private keys) on every commit
```

## Checks

All of these run in CI. Tests run on Linux (Python 3.12–3.14) and Windows (3.12 and 3.14),
and on macOS on manual dispatch; ruff and mypy run once, on Linux with Python 3.12:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov        # fails below 90% coverage
```

## Guidelines

- Code, docstrings and documentation are in English. Public functions and classes have
  Google-style docstrings.
- Only `tools/`, `server.py` and `cli.py` import `mcp` (a test enforces this). Tool adapters
  in `tools/` stay thin.
- Raise `errors.FsError` subclasses with messages that tell the agent what to do next.
  Never include paths outside the roots in messages. Hints that `lite` tools can return
  name only tools and parameters the `lite` profile has.
- Every behavior change needs tests. Security-relevant changes also need a test in
  `tests/security/`.
- Write test files with explicit bytes (see `tests/conftest.py::write`) so they behave
  the same on every OS.
- Update `CHANGELOG.md` under "Unreleased".
- Planned work lives in `ROADMAP.md`; AI-assisted contributions follow `AGENTS.md`.
