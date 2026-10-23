# Contributing

Use Python 3.12+ and uv. Work through the public database, reader and writer seams;
the SQLite schema is also a public format seam and may be checked with an independent
`sqlite3` connection.

Run:

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m mypy.stubtest golded_ftn_sqlite
uv build
uv run twine check dist/*
uv run python scripts/verify_distribution.py
```

Use synthetic messages. Keep private archives, credentials and access material out
of tests and distributions. Schema changes require a migration and a new documented
`user_version`; editing version 1 in place after release is not compatibility.
