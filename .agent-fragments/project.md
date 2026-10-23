# golded-ftn-sqlite

This Python package defines a public SQLite message-base format for FTN netmail and
echomail through golded-ftn models. The documented default is one area per database;
keep the schema capable of multiple explicitly selected areas.

Treat schema version 1 as a public compatibility boundary. Preserve ordered and
repeated controls, routing, reply metadata and import provenance. Message numbers
are monotonic within an area and survive soft deletion. Netmail requires origin and
destination addresses. Writer revisions cover the message row and related ordered
metadata rows.

Use TDD at the agreed public seams: the database/catalog API, reader, writer session
and independent SQLite format consumer. Run pytest, Ruff lint/format, strict mypy,
stubtest and scripts/verify_distribution.py. Use synthetic messages and keep private
archives out of tests.

Edit this fragment or agent-compose.toml, then preview, build and check. Commits,
remotes, tags and publication require an explicit request.
