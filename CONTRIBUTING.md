# Contributing

## Setup

```bash
uv sync                    # Python 3.12 virtual environment with all dev tools
uv run pre-commit install  # run ruff and mypy on every commit
```

## Checks

All of these run in CI on Linux, macOS and Windows with Python 3.12–3.14:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov        # fails below 90% coverage
```

## Guidelines

- Code, docstrings and documentation are in English. Public functions and classes have
  Google-style docstrings.
- Keep `core/` and `security/` free of MCP imports. Tool adapters in `tools/` stay thin.
- Raise `errors.FsError` subclasses with messages that tell the agent what to do next.
  Never include paths outside the roots in messages.
- Every behavior change needs tests. Security-relevant changes also need a test in
  `tests/security/`.
- Write test files with explicit bytes (see `tests/conftest.py::write`) so they behave
  the same on every OS.
- Update `CHANGELOG.md` under "Unreleased".
- Planned work lives in `ROADMAP.md`; AI-assisted contributions follow `AGENTS.md`.
