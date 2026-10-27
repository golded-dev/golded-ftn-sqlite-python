from __future__ import annotations

import os
import sqlite3
from os import PathLike
from pathlib import Path
from typing import Self, cast

from golded_ftn import FtnAddress

from .models import AreaDefinition, AreaKind

APPLICATION_ID = 0x47465453
SCHEMA_VERSION = 1

_SCHEMA = f"""
PRAGMA application_id = {APPLICATION_ID};
PRAGMA user_version = {SCHEMA_VERSION};

CREATE TABLE areas (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('netmail', 'echomail')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    meta_key TEXT,
    origin_address TEXT
        CHECK (
            origin_address IS NULL
            OR (origin_address != '' AND origin_address = trim(origin_address))
        ),
    next_message_number INTEGER NOT NULL DEFAULT 1
        CHECK (next_message_number > 0)
) STRICT;

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    area_id INTEGER NOT NULL REFERENCES areas(id),
    msgno INTEGER NOT NULL CHECK (msgno > 0),
    from_name TEXT NOT NULL,
    to_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    posted_at TEXT,
    external_id TEXT,
    attributes_raw INTEGER NOT NULL,
    from_address TEXT
        CHECK (
            from_address IS NULL
            OR (from_address != '' AND from_address = trim(from_address))
        ),
    to_address TEXT
        CHECK (
            to_address IS NULL
            OR (to_address != '' AND to_address = trim(to_address))
        ),
    reply_to_msgno INTEGER,
    reply1st_msgno INTEGER,
    reply_next_msgno INTEGER,
    source_type TEXT,
    source_path TEXT,
    source_id TEXT,
    source_offset INTEGER,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    deleted_at TEXT,
    UNIQUE (area_id, msgno),
    CHECK (
        source_type IS NOT NULL
        OR (source_path IS NULL AND source_id IS NULL AND source_offset IS NULL)
    )
) STRICT;

CREATE INDEX messages_area_posted
    ON messages (area_id, posted_at, msgno)
    WHERE deleted_at IS NULL;

CREATE INDEX messages_external_id
    ON messages (external_id)
    WHERE external_id IS NOT NULL AND deleted_at IS NULL;

CREATE TRIGGER messages_netmail_addresses_insert
BEFORE INSERT ON messages
WHEN (SELECT kind FROM areas WHERE id = NEW.area_id) = 'netmail'
    AND (NEW.from_address IS NULL OR NEW.to_address IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'netmail requires origin and destination addresses');
END;

CREATE TRIGGER messages_netmail_addresses_update
BEFORE UPDATE OF area_id, from_address, to_address ON messages
WHEN (SELECT kind FROM areas WHERE id = NEW.area_id) = 'netmail'
    AND (NEW.from_address IS NULL OR NEW.to_address IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'netmail requires origin and destination addresses');
END;

CREATE TABLE control_lines (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    name TEXT NOT NULL COLLATE NOCASE,
    value TEXT NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (message_id, position)
) WITHOUT ROWID, STRICT;

CREATE INDEX control_lines_name
    ON control_lines (name, message_id);

CREATE TABLE routing_entries (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('seen_by', 'path')),
    position INTEGER NOT NULL CHECK (position >= 0),
    value TEXT NOT NULL,
    PRIMARY KEY (message_id, kind, position)
) WITHOUT ROWID, STRICT;

CREATE TABLE reply_list_entries (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    msgno INTEGER NOT NULL CHECK (msgno > 0),
    PRIMARY KEY (message_id, position)
) WITHOUT ROWID, STRICT;
"""


def format_address(address: FtnAddress | None) -> str | None:
    return None if address is None else str(address)


def parse_address(value: str | None) -> FtnAddress | None:
    return None if value is None else FtnAddress.from_string(value)


class SqliteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def open(cls, path: str | PathLike[str]) -> Self:
        database_path = Path(path).resolve()
        connection = connect_database(database_path, read_only=True)
        connection.close()
        return cls(database_path)

    @classmethod
    def create(
        cls,
        path: str | PathLike[str],
        *,
        area: AreaDefinition,
    ) -> Self:
        database_path = Path(path).resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(database_path)
        try:
            with connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(_SCHEMA)
                connection.execute(
                    """
                    INSERT INTO areas (
                        code, name, kind, sort_order, meta_key, origin_address
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        area.code,
                        area.name,
                        area.kind.value,
                        area.sort_order,
                        area.meta_key,
                        format_address(area.default_origin),
                    ),
                )
        except BaseException:
            database_path.unlink(missing_ok=True)
            raise
        finally:
            connection.close()
        return cls(database_path)

    def add_area(self, area: AreaDefinition) -> None:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO areas (
                        code, name, kind, sort_order, meta_key, origin_address
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        area.code,
                        area.name,
                        area.kind.value,
                        area.sort_order,
                        area.meta_key,
                        format_address(area.default_origin),
                    ),
                )
        finally:
            connection.close()

    def areas(self) -> tuple[AreaDefinition, ...]:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                rows = connection.execute(
                    """
                    SELECT code, name, kind, sort_order, meta_key, origin_address
                    FROM areas
                    ORDER BY id
                    """
                ).fetchall()
        finally:
            connection.close()
        return tuple(
            AreaDefinition(
                code=row[0],
                name=row[1],
                kind=AreaKind(row[2]),
                default_origin=parse_address(row[5]),
                sort_order=row[3],
                meta_key=row[4],
            )
            for row in rows
        )


def connect_database(
    path: Path, *, read_only: bool, timeout: float = 5.0
) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    if read_only:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=timeout,
            isolation_level=None,
        )
    else:
        connection = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {round(timeout * 1000)}")
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if application_id != APPLICATION_ID or schema_version != SCHEMA_VERSION:
        connection.close()
        raise ValueError("not a supported golded-ftn-sqlite database")
    return connection


def select_area(connection: sqlite3.Connection, area: str | None) -> sqlite3.Row:
    if area is not None:
        row = connection.execute(
            "SELECT * FROM areas WHERE code = ?", (area,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown area [{area}]")
        return cast(sqlite3.Row, row)
    rows = connection.execute("SELECT * FROM areas ORDER BY id LIMIT 2").fetchall()
    if len(rows) != 1:
        raise ValueError("area is required unless the database has one area")
    return cast(sqlite3.Row, rows[0])
